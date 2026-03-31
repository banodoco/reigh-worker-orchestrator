"""Task-oriented debug data services."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict

from scripts.debug.models import TaskInfo, TasksSummary

logger = logging.getLogger(__name__)


def get_task_info(db: Any, log_client: Any, task_id: str) -> TaskInfo:
    """Get complete task information: logs + DB state."""
    logs = log_client.get_task_timeline(task_id)
    result = db.supabase.table("tasks").select("*").eq("id", task_id).execute()
    state = result.data[0] if result.data else None
    return TaskInfo(task_id=task_id, state=state, logs=logs)


def get_recent_tasks(
    db: Any,
    log_client: Any,
    options: Dict[str, Any],
) -> TasksSummary:
    """Get recent tasks with analysis."""
    limit = options.get("limit", 50)
    status = options.get("status")
    task_type = options.get("task_type")
    worker_id = options.get("worker_id")
    hours = options.get("hours")

    query = db.supabase.table("tasks").select("*")
    if status:
        query = query.eq("status", status)
    if task_type:
        query = query.eq("task_type", task_type)
    if worker_id:
        query = query.eq("worker_id", worker_id)
    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = query.gte("created_at", cutoff.isoformat())
    tasks = query.order("created_at", desc=True).limit(limit).execute().data or []

    status_dist = Counter(task.get("status") for task in tasks)
    type_dist = Counter(task.get("task_type") for task in tasks)
    processing_times = []
    queue_times = []
    for task in tasks:
        if task.get("generation_started_at") and task.get("generation_processed_at"):
            try:
                started = datetime.fromisoformat(task["generation_started_at"].replace("Z", "+00:00"))
                processed = datetime.fromisoformat(task["generation_processed_at"].replace("Z", "+00:00"))
                processing_times.append((processed - started).total_seconds())
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.debug("Skipping processing-time calculation for task %s: %s", task.get("id"), exc)
                continue
        if task.get("created_at") and task.get("generation_started_at"):
            try:
                created = datetime.fromisoformat(task["created_at"].replace("Z", "+00:00"))
                started = datetime.fromisoformat(task["generation_started_at"].replace("Z", "+00:00"))
                queue_times.append((started - created).total_seconds())
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.debug("Skipping queue-time calculation for task %s: %s", task.get("id"), exc)
                continue

    timing_stats = {
        "avg_processing_seconds": sum(processing_times) / len(processing_times) if processing_times else None,
        "avg_queue_seconds": sum(queue_times) / len(queue_times) if queue_times else None,
        "total_with_timing": len(processing_times),
    }

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours or 24)
    error_logs = log_client.get_logs(start_time=cutoff, log_level="ERROR", limit=100)
    recent_failures = [
        {
            "task_id": log["task_id"],
            "timestamp": log["timestamp"],
            "message": log["message"],
            "worker_id": log.get("worker_id"),
        }
        for log in error_logs
        if log.get("task_id")
    ]

    return TasksSummary(
        tasks=tasks,
        total_count=len(tasks),
        status_distribution=dict(status_dist),
        task_type_distribution=dict(type_dist),
        timing_stats=timing_stats,
        recent_failures=recent_failures[:10],
    )


__all__ = ["get_recent_tasks", "get_task_info"]
