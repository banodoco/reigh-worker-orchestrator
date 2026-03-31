"""Unified client for debug data access."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from supabase import create_client

from gpu_orchestrator.database import DatabaseClient
from scripts.debug.models import OrchestratorStatus, SystemHealth, TaskInfo, TasksSummary, WorkerInfo, WorkersSummary
from scripts.debug.services.system import get_orchestrator_status, get_system_health
from scripts.debug.services.tasks import get_recent_tasks, get_task_info
from scripts.debug.services.workers import check_worker_disk_space, check_worker_logging, get_worker_info, get_workers_summary


class LogQueryClient:
    """Client for querying system logs from Supabase."""

    def __init__(self) -> None:
        load_dotenv()
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in environment")
        self.supabase = create_client(supabase_url, supabase_key)

    def get_logs(
        self,
        start_time: datetime = None,
        end_time: datetime = None,
        source_type: str = None,
        source_id: str = None,
        worker_id: str = None,
        task_id: str = None,
        log_level: str = None,
        cycle_number: int = None,
        search_term: str = None,
        limit: int = 1000,
        order_desc: bool = True,
    ) -> List[Dict[str, Any]]:
        """Query logs with flexible filters."""
        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(hours=24)
        if not end_time:
            end_time = datetime.now(timezone.utc)

        query = self.supabase.table("system_logs").select("*")
        query = query.gte("timestamp", start_time.isoformat()).lte("timestamp", end_time.isoformat())
        if source_type:
            query = query.eq("source_type", source_type)
        if source_id:
            query = query.eq("source_id", source_id)
        if worker_id:
            query = query.eq("worker_id", worker_id)
        if task_id:
            query = query.eq("task_id", task_id)
        if log_level:
            query = query.eq("log_level", log_level)
        if cycle_number is not None:
            query = query.eq("cycle_number", cycle_number)
        if search_term:
            query = query.ilike("message", f"%{search_term}%")

        return query.order("timestamp", desc=order_desc).limit(limit).execute().data or []

    def get_task_timeline(self, task_id: str) -> List[Dict[str, Any]]:
        """Get timeline of logs for a specific task."""
        return self.get_logs(task_id=task_id, limit=1000, order_desc=False)


class DebugClient:
    """Unified client for all debugging data."""

    def __init__(self) -> None:
        self.log_client = LogQueryClient()
        self.db = DatabaseClient()

    def get_task_info(self, task_id: str) -> TaskInfo:
        return get_task_info(self.db, self.log_client, task_id)

    def get_worker_info(self, worker_id: str, hours: int = 24, startup: bool = False) -> WorkerInfo:
        return get_worker_info(self.db, self.log_client, worker_id, hours=hours, startup=startup)

    def check_worker_logging(self, worker_id: str) -> Dict[str, Any]:
        return check_worker_logging(self.log_client, worker_id)

    def check_worker_disk_space(self, worker_id: str) -> Dict[str, Any]:
        return check_worker_disk_space(self.db, worker_id)

    def get_recent_tasks(
        self,
        limit: int = 50,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        worker_id: Optional[str] = None,
        hours: Optional[int] = None,
    ) -> TasksSummary:
        return get_recent_tasks(
            self.db,
            self.log_client,
            {
                "limit": limit,
                "status": status,
                "task_type": task_type,
                "worker_id": worker_id,
                "hours": hours,
            },
        )

    def get_workers_summary(self, hours: int = 2, detailed: bool = False) -> WorkersSummary:
        _ = detailed
        return get_workers_summary(self.db, hours=hours)

    def get_system_health(self) -> SystemHealth:
        return get_system_health(self.db, self.log_client)

    def get_orchestrator_status(self, hours: int = 1) -> OrchestratorStatus:
        return get_orchestrator_status(self.log_client, hours=hours)
