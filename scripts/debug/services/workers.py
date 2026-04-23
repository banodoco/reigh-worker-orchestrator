"""Worker-oriented debug data services."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict

from gpu_orchestrator.config import OrchestratorConfig
from gpu_orchestrator.worker_spawner import create_worker_spawner
from scripts.debug.models import WorkerInfo, WorkersSummary

logger = logging.getLogger(__name__)


def get_worker_info(db: Any, log_client: Any, worker_id: str, hours: int = 24, startup: bool = False) -> WorkerInfo:
    """Get complete worker information: logs + DB state + recent tasks."""
    start_time = datetime.now(timezone.utc) - timedelta(hours=hours)

    if startup:
        logs = log_client.get_logs(start_time=start_time, source_id=worker_id, limit=1000, order_desc=False)
        logs_sorted = [
            log
            for log in logs
            if any(keyword in log.get("message", "").lower() for keyword in ["startup", "initializ", "loading", "installing", "model", "cuda", "torch"])
        ]
    else:
        logs = log_client.get_logs(start_time=start_time, worker_id=worker_id, limit=5000, order_desc=False)
        source_logs = log_client.get_logs(start_time=start_time, source_id=worker_id, limit=5000, order_desc=False)
        all_logs = {log["timestamp"]: log for log in logs + source_logs}
        logs_sorted = sorted(all_logs.values(), key=lambda item: item["timestamp"])

    worker_result = db.supabase.table("workers").select("*").eq("id", worker_id).execute()
    state = worker_result.data[0] if worker_result.data else None
    tasks_result = db.supabase.table("tasks").select("*").eq("worker_id", worker_id).order("created_at", desc=True).limit(20).execute()

    return WorkerInfo(
        worker_id=worker_id,
        state=state,
        logs=logs_sorted,
        tasks=tasks_result.data or [],
    )


def check_worker_logging(log_client: Any, worker_id: str) -> Dict[str, Any]:
    """Check if worker has started logging."""
    logs = log_client.get_logs(worker_id=worker_id, limit=10, order_desc=True)
    return {
        "is_logging": len(logs) > 0,
        "log_count": len(logs),
        "recent_logs": logs[:5] if logs else [],
    }


def check_worker_disk_space(db: Any, worker_id: str) -> Dict[str, Any]:
    """Check disk space on a live worker via SSH."""
    result = db.supabase.table("workers").select("*").eq("id", worker_id).execute()
    if not result.data:
        return {"available": False, "error": "Worker not found"}

    worker = result.data[0]
    if worker["status"] in ["terminated", "error"]:
        return {"available": False, "error": f"Worker is {worker['status']} - cannot SSH"}

    runpod_id = worker.get("metadata", {}).get("runpod_id")
    if not runpod_id:
        return {"available": False, "error": "No RunPod ID found"}

    try:
        client = create_worker_spawner(OrchestratorConfig.from_env(), db)
        check_command = """
        echo "=== DISK SPACE ==="
        df -h / /tmp /var /workspace 2>/dev/null
        echo ""
        echo "=== LARGEST DIRS IN /tmp ==="
        du -sh /tmp/* 2>/dev/null | sort -rh | head -5
        echo ""
        echo "=== APT CACHE SIZE ==="
        du -sh /var/cache/apt 2>/dev/null || echo "N/A"
        """
        command_result = asyncio.run(client.execute_command_on_worker(runpod_id, check_command, timeout=15))
        if not command_result or command_result[0] != 0:
            return {"available": False, "error": "SSH command failed"}

        output = command_result[1] or ""
        issues = []
        for line in output.split("\n"):
            if "100%" in line or "99%" in line or "98%" in line:
                issues.append(f"CRITICAL: {line.strip()}")
                continue
            if "9" in line and "%" in line:
                try:
                    for part in line.split():
                        if part.endswith("%") and int(part.rstrip("%")) >= 90:
                            issues.append(f"WARNING: {line.strip()}")
                            break
                except ValueError:
                    continue

        return {
            "available": True,
            "disk_info": output,
            "issues": issues,
            "runpod_id": runpod_id,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def get_workers_summary(db: Any, hours: int = 2) -> WorkersSummary:
    """Get workers summary with health status."""
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    workers_result = db.supabase.table("workers").select("*").gte("created_at", cutoff_time.isoformat()).order("created_at", desc=True).execute()
    workers = workers_result.data or []

    now = datetime.now(timezone.utc)
    status_counts = Counter(worker.get("status") for worker in workers)
    active_healthy = 0
    active_stale = 0
    for worker in workers:
        if worker.get("status") != "active":
            continue
        last_hb = worker.get("last_heartbeat")
        if not last_hb:
            active_stale += 1
            continue
        try:
            hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
            age_seconds = (now - hb_time).total_seconds()
            if age_seconds < 60:
                active_healthy += 1
            else:
                active_stale += 1
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug("Failed to parse worker heartbeat for %s: %s", worker.get("id"), exc)
            active_stale += 1

    recent_failures = []
    for worker in workers:
        if worker.get("status") in ["error", "terminated"]:
            metadata = worker.get("metadata", {})
            recent_failures.append(
                {
                    "worker_id": worker["id"],
                    "status": worker["status"],
                    "created_at": worker.get("created_at"),
                    "error_reason": metadata.get("error_reason", "Unknown"),
                }
            )

    failure_rate = None
    if len(workers) >= 5:
        failed = len([worker for worker in workers if worker["status"] in ["error", "terminated"]])
        failure_rate = failed / len(workers)

    return WorkersSummary(
        workers=workers,
        total_count=len(workers),
        status_counts=dict(status_counts),
        active_healthy=active_healthy,
        active_stale=active_stale,
        recent_failures=recent_failures[:10],
        failure_rate=failure_rate,
    )


__all__ = [
    "check_worker_disk_space",
    "check_worker_logging",
    "get_worker_info",
    "get_workers_summary",
]
