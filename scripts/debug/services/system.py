"""System-level debug data services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from scripts.debug.models import OrchestratorStatus, SystemHealth

logger = logging.getLogger(__name__)
SECRET_KEYS = {
    "authorization",
    "authorization_header",
    "api_key",
    "apikey",
    "service_role",
    "service-role",
    "access_token",
    "refresh_token",
    "pat",
    "token",
}
COMPLETION_HANDLER = "complete_task/generation-handlers.ts"
BILLING_HANDLER = "complete_task/billing.ts"


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


def export_canary_readiness_observations(
    *,
    workers: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    system_logs: list[dict[str, Any]],
    environment: str = "staging",
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Format redacted canary evidence from existing worker/task/log metadata."""
    logs_by_task = {
        str(log.get("task_id") or _metadata(log).get("task_id")): log
        for log in system_logs
        if log.get("task_id") or _metadata(log).get("task_id")
    }
    workers_by_id = {str(worker.get("id")): worker for worker in workers if worker.get("id")}
    return [
        _redact(
            _observation_from_rows(
                task,
                worker=workers_by_id.get(str(task.get("worker_id") or "")),
                system_log=logs_by_task.get(str(task.get("id") or task.get("task_id") or "")),
                environment=environment,
                observed_at=observed_at,
            )
        )
        for task in tasks
    ]


def _observation_from_rows(
    task: dict[str, Any],
    *,
    worker: dict[str, Any] | None,
    system_log: dict[str, Any] | None,
    environment: str,
    observed_at: str | None,
) -> dict[str, Any]:
    task_metadata = _metadata(task)
    worker_metadata = _metadata(worker)
    log_metadata = _metadata(system_log)
    route_contract = _route_contract(task_metadata, log_metadata)
    route_key = _coalesce(
        route_contract.get("route_key"),
        task_metadata.get("route_key"),
        log_metadata.get("route_key"),
        task.get("task_type"),
    )
    selected_backend = _coalesce(
        route_contract.get("selected_backend"),
        task_metadata.get("selected_backend"),
        worker_metadata.get("backend"),
        log_metadata.get("selected_backend"),
    )
    return {
        "environment": environment,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
        "task_id": str(task.get("id") or task.get("task_id") or ""),
        "task_type": str(task.get("task_type") or route_key or ""),
        "route_key": str(route_key or ""),
        "worker_backend": selected_backend,
        "selector_namespace": _coalesce(
            task_metadata.get("selector_namespace"),
            log_metadata.get("selector_namespace"),
            "canary",
        ),
        "selector_version": _coalesce(
            route_contract.get("selector_version"),
            task_metadata.get("selector_version"),
            log_metadata.get("selector_version"),
        ),
        "status": task.get("status"),
        "completion_evidence": {
            "handler": COMPLETION_HANDLER,
            "status": _coalesce(task_metadata.get("completion_status"), task.get("status")),
            "source": "tasks.metadata",
        },
        "billing_evidence": {
            "handler": BILLING_HANDLER,
            "status": _coalesce(task_metadata.get("billing_status"), task_metadata.get("billing_evidence_status")),
            "source": "tasks.metadata",
        },
        "runtime": {
            "backend": selected_backend,
            "pool": _coalesce(worker_metadata.get("pool"), log_metadata.get("pool")),
            "worker_id": _coalesce(
                task.get("worker_id"),
                worker.get("id") if isinstance(worker, dict) else None,
                log_metadata.get("worker_id"),
            ),
            "worker_health": {
                "status": worker.get("status") if isinstance(worker, dict) else None,
                "last_heartbeat": worker.get("last_heartbeat") if isinstance(worker, dict) else None,
            },
            "quota_alert": _coalesce(task_metadata.get("quota_alert"), log_metadata.get("quota_alert")),
            "warm_cache": _coalesce(worker_metadata.get("warm_cache"), log_metadata.get("warm_cache")),
            "preflight": _coalesce(worker_metadata.get("preflight"), log_metadata.get("preflight")),
            "route_contract": route_contract,
        },
        "source_ref": {
            "kind": "debug_export",
            "task_id": task.get("id") or task.get("task_id"),
            "worker_id": worker.get("id") if isinstance(worker, dict) else None,
            "system_log_id": system_log.get("id") if isinstance(system_log, dict) else None,
            "paths": ["tasks.metadata", "workers.metadata", "system_logs.metadata"],
        },
        "redaction": {"status": "redacted", "secret_scan": "passed"},
    }


def _metadata(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _route_contract(task_metadata: dict[str, Any], log_metadata: dict[str, Any]) -> dict[str, Any]:
    contract = task_metadata.get("route_contract") if isinstance(task_metadata.get("route_contract"), dict) else {}
    snapshot = (
        contract.get("route_selection_snapshot")
        if isinstance(contract.get("route_selection_snapshot"), dict)
        else {}
    )
    return {
        "route_key": _coalesce(contract.get("route_key"), snapshot.get("route_key"), log_metadata.get("route_key")),
        "selected_backend": _coalesce(
            contract.get("selected_backend"),
            snapshot.get("selected_backend"),
            log_metadata.get("selected_backend"),
        ),
        "selector_version": _coalesce(
            contract.get("selector_version"),
            snapshot.get("selector_version"),
            log_metadata.get("selector_version"),
        ),
        "selected_profile": _coalesce(
            contract.get("selected_profile"),
            snapshot.get("selected_profile"),
            log_metadata.get("selected_profile"),
        ),
    }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            redacted[key] = "[REDACTED]" if _secret_key(key) else _redact(child)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and _secret_value(value):
        return "[REDACTED]"
    return value


def _secret_key(key: str) -> bool:
    normalized = key.lower()
    return normalized in SECRET_KEYS or normalized.endswith("_token") or normalized.endswith("_api_key")


def _secret_value(value: str) -> bool:
    normalized = value.lower()
    return (
        "authorization:" in normalized
        or "bearer " in normalized
        or "service_role" in normalized
        or "service-role" in normalized
        or normalized.startswith(("sk-proj-", "sk-live-", "github_pat_"))
    )


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


__all__ = [
    "export_canary_readiness_observations",
    "get_orchestrator_status",
    "get_system_health",
]
