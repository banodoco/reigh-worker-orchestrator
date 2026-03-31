"""Output formatting facade for debug data."""

from __future__ import annotations

import json

from scripts.debug.models import (
    OrchestratorStatus,
    SystemHealth,
    TaskInfo,
    TasksSummary,
    WorkerInfo,
    WorkersSummary,
)
from scripts.debug.presenters.summary_presenter import format_tasks_summary_text, format_workers_summary_text
from scripts.debug.presenters.system_presenter import format_health_text, format_orchestrator_text
from scripts.debug.presenters.task_presenter import format_task_logs_only_text, format_task_text
from scripts.debug.presenters.worker_presenter import format_worker_logs_only_text, format_worker_text


class Formatter:
    """Output formatting for debug data."""

    @staticmethod
    def format_task(info: TaskInfo, format_type: str = "text", logs_only: bool = False) -> str:
        if format_type == "json":
            return json.dumps(info.to_dict(), indent=2, default=str)
        if logs_only:
            return format_task_logs_only_text(info)
        return format_task_text(info)

    @staticmethod
    def format_worker(info: WorkerInfo, format_type: str = "text", logs_only: bool = False) -> str:
        if format_type == "json":
            return json.dumps(info.to_dict(), indent=2, default=str)
        if logs_only:
            return format_worker_logs_only_text(info)
        return format_worker_text(info)

    @staticmethod
    def format_tasks_summary(summary: TasksSummary, format_type: str = "text") -> str:
        if format_type == "json":
            return json.dumps(summary.to_dict(), indent=2, default=str)
        return format_tasks_summary_text(summary)

    @staticmethod
    def format_workers_summary(summary: WorkersSummary, format_type: str = "text") -> str:
        if format_type == "json":
            return json.dumps(summary.to_dict(), indent=2, default=str)
        return format_workers_summary_text(summary)

    @staticmethod
    def format_health(health: SystemHealth, format_type: str = "text") -> str:
        if format_type == "json":
            return json.dumps(health.to_dict(), indent=2, default=str)
        return format_health_text(health)

    @staticmethod
    def format_orchestrator(status: OrchestratorStatus, format_type: str = "text") -> str:
        if format_type == "json":
            return json.dumps(status.to_dict(), indent=2, default=str)
        return format_orchestrator_text(status)
