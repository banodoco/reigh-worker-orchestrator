"""Direct tests for debug presenter modules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scripts.debug.models import OrchestratorStatus, SystemHealth, TaskInfo, TasksSummary, WorkerInfo, WorkersSummary
from scripts.debug.presenters.summary_presenter import format_tasks_summary_text, format_workers_summary_text
from scripts.debug.presenters.system_presenter import format_health_text, format_orchestrator_text
from scripts.debug.presenters.task_presenter import format_task_logs_only_text, format_task_text
from scripts.debug.presenters.worker_presenter import format_worker_logs_only_text, format_worker_text


def _sample_task_info() -> TaskInfo:
    return TaskInfo(
        task_id="task-1",
        state={
            "status": "In Progress",
            "task_type": "qwen_image_edit",
            "worker_id": "worker-1",
            "attempts": 1,
            "created_at": "2026-02-16T00:00:00Z",
            "generation_started_at": "2026-02-16T00:00:05Z",
            "params": {"prompt": "test"},
            "output_location": "https://example.com/output.png",
        },
        logs=[
            {
                "timestamp": "2026-02-16T00:00:06Z",
                "log_level": "INFO",
                "source_id": "worker-1",
                "message": "processing started",
            }
        ],
    )


def _sample_worker_info() -> WorkerInfo:
    now = datetime.now(timezone.utc)
    return WorkerInfo(
        worker_id="worker-1",
        state={
            "id": "worker-1",
            "status": "active",
            "created_at": (now - timedelta(minutes=5)).isoformat(),
            "last_heartbeat": (now - timedelta(seconds=15)).isoformat(),
            "metadata": {"runpod_id": "pod-1", "ram_tier": 80},
        },
        logs=[
            {
                "timestamp": now.isoformat(),
                "log_level": "INFO",
                "message": "heartbeat",
            }
        ],
        tasks=[{"id": "abc12345", "task_type": "qwen_image_edit", "status": "In Progress"}],
    )


def test_task_presenter_outputs_expected_sections() -> None:
    info = _sample_task_info()
    full = format_task_text(info)
    logs_only = format_task_logs_only_text(info)
    assert "TASK: task-1" in full
    assert "Overview" in full
    assert "Event Timeline" in logs_only


def test_worker_presenter_outputs_expected_sections() -> None:
    info = _sample_worker_info()
    full = format_worker_text(info)
    logs_only = format_worker_logs_only_text(info)
    assert "WORKER: worker-1" in full
    assert "Last Heartbeat" in full
    assert "Event Timeline" in logs_only


def test_summary_presenters_render_counts() -> None:
    tasks_summary = TasksSummary(
        tasks=[{"created_at": "2026-02-16T00:00:00Z"}],
        total_count=1,
        status_distribution={"Queued": 1},
        task_type_distribution={"qwen_image": 1},
        timing_stats={"avg_queue_seconds": 1.0, "total_with_timing": 1},
        recent_failures=[],
    )
    workers_summary = WorkersSummary(
        workers=[{"id": "worker-1", "status": "active", "created_at": "2026-02-16T00:00:00Z", "last_heartbeat": "never"}],
        total_count=1,
        status_counts={"active": 1},
        active_healthy=1,
        active_stale=0,
        recent_failures=[],
        failure_rate=0.0,
    )
    tasks_text = format_tasks_summary_text(tasks_summary)
    workers_text = format_workers_summary_text(workers_summary)
    assert "RECENT TASKS ANALYSIS" in tasks_text
    assert "WORKERS STATUS" in workers_text


def test_system_presenters_render_status() -> None:
    now = datetime.now(timezone.utc)
    health = SystemHealth(
        timestamp=now,
        workers_active=1,
        workers_spawning=0,
        workers_healthy=1,
        tasks_queued=0,
        tasks_in_progress=1,
        recent_errors=[],
        failure_rate=0.1,
        failure_rate_status="OK",
    )
    orchestrator = OrchestratorStatus(
        last_activity=now,
        last_cycle=42,
        status="HEALTHY",
        recent_cycles=[{"timestamp": now.isoformat(), "cycle_number": 42}],
        recent_logs=[{"timestamp": now.isoformat(), "log_level": "INFO", "message": "cycle start"}],
    )
    health_text = format_health_text(health)
    orchestrator_text = format_orchestrator_text(orchestrator)
    assert "SYSTEM HEALTH CHECK" in health_text
    assert "ORCHESTRATOR STATUS" in orchestrator_text
