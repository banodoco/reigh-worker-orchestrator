"""System-level debug data services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from scripts.debug.models import OrchestratorStatus, SystemHealth

logger = logging.getLogger(__name__)


def get_system_health(db: Any, log_client: Any) -> SystemHealth:
    """Get overall system health."""
    now = datetime.now(timezone.utc)
    workers_result = db.supabase.table("workers").select("*").neq("status", "terminated").execute()
    workers = workers_result.data or []

    workers_active = len([worker for worker in workers if worker["status"] == "active"])
    workers_spawning = len([worker for worker in workers if worker["status"] == "spawning"])

    workers_healthy = 0
    for worker in workers:
        if worker.get("status") != "active" or not worker.get("last_heartbeat"):
            continue
        try:
            hb_time = datetime.fromisoformat(worker["last_heartbeat"].replace("Z", "+00:00"))
            if (now - hb_time).total_seconds() < 60:
                workers_healthy += 1
        except (AttributeError, TypeError, ValueError) as exc:
            logger.debug("Skipping worker heartbeat parse for %s: %s", worker.get("id"), exc)
            continue

    tasks_result = db.supabase.table("tasks").select("status").execute()
    tasks = tasks_result.data or []
    tasks_queued = len([task for task in tasks if task["status"] == "Queued"])
    tasks_in_progress = len([task for task in tasks if task["status"] == "In Progress"])

    recent_errors = log_client.get_logs(
        start_time=now - timedelta(hours=1),
        log_level="ERROR",
        limit=50,
    )[:10]

    recent_workers_result = db.supabase.table("workers").select("*").gte(
        "created_at", (now - timedelta(minutes=30)).isoformat()
    ).execute()
    recent_workers = recent_workers_result.data or []

    failure_rate = None
    failure_rate_status = "OK"
    if len(recent_workers) >= 5:
        failed = len([worker for worker in recent_workers if worker["status"] in ["error", "terminated"]])
        failure_rate = failed / len(recent_workers)
        if failure_rate > 0.8:
            failure_rate_status = "BLOCKED"
        elif failure_rate > 0.5:
            failure_rate_status = "WARNING"

    return SystemHealth(
        timestamp=now,
        workers_active=workers_active,
        workers_spawning=workers_spawning,
        workers_healthy=workers_healthy,
        tasks_queued=tasks_queued,
        tasks_in_progress=tasks_in_progress,
        recent_errors=recent_errors,
        failure_rate=failure_rate,
        failure_rate_status=failure_rate_status,
    )


def get_orchestrator_status(log_client: Any, hours: int = 1) -> OrchestratorStatus:
    """Get orchestrator status from logs."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    logs = log_client.get_logs(
        start_time=cutoff,
        source_type="orchestrator_gpu",
        limit=1000,
        order_desc=True,
    )

    last_activity = None
    last_cycle = None
    if logs:
        last_activity = datetime.fromisoformat(logs[0]["timestamp"].replace("Z", "+00:00"))
        last_cycle = logs[0].get("cycle_number")

    if last_activity:
        age_minutes = (datetime.now(timezone.utc) - last_activity).total_seconds() / 60
        if age_minutes < 5:
            status = "HEALTHY"
        elif age_minutes < 15:
            status = "WARNING"
        else:
            status = "STALE"
    else:
        status = "NO_LOGS"

    cycle_starts = [log for log in logs if "Starting orchestrator cycle" in log.get("message", "")]
    recent_cycles = [
        {
            "cycle_number": log.get("cycle_number"),
            "timestamp": log["timestamp"],
            "message": log["message"],
        }
        for log in cycle_starts[:10]
    ]

    return OrchestratorStatus(
        last_activity=last_activity,
        last_cycle=last_cycle,
        status=status,
        recent_cycles=recent_cycles,
        recent_logs=logs[:50],
    )


__all__ = ["get_orchestrator_status", "get_system_health"]
