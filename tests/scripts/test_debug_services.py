"""Direct tests for debug service modules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import scripts.debug.services.system as system_services
import scripts.debug.services.tasks as task_services
import scripts.debug.services.workers as worker_services


@dataclass
class _FakeResult:
    data: List[Dict[str, Any]]


class _FakeQuery:
    def __init__(self, data: List[Dict[str, Any]]) -> None:
        self._data = data

    def select(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def eq(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def neq(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def gte(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def lte(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def ilike(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def like(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def limit(self, *_args: Any, **_kwargs: Any) -> "_FakeQuery":
        return self

    def execute(self) -> _FakeResult:
        return _FakeResult(self._data)


class _FakeSupabase:
    def __init__(self, table_data: Dict[str, List[Dict[str, Any]]]) -> None:
        self._table_data = table_data

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self._table_data.get(name, []))


class _FakeDb:
    def __init__(self, table_data: Dict[str, List[Dict[str, Any]]]) -> None:
        self.supabase = _FakeSupabase(table_data)


class _FakeLogClient:
    def __init__(self, logs: List[Dict[str, Any]]) -> None:
        self._logs = logs

    def get_task_timeline(self, _task_id: str) -> List[Dict[str, Any]]:
        return self._logs

    def get_logs(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return self._logs


def test_task_services_return_expected_models() -> None:
    task = {
        "id": "task-1",
        "status": "Queued",
        "task_type": "qwen_image",
        "created_at": "2026-02-16T00:00:00Z",
        "generation_started_at": "2026-02-16T00:00:02Z",
        "generation_processed_at": "2026-02-16T00:00:04Z",
    }
    db = _FakeDb({"tasks": [task]})
    logs = [{"task_id": "task-1", "timestamp": "2026-02-16T00:00:05Z", "message": "boom", "worker_id": "w1"}]
    log_client = _FakeLogClient(logs)

    info = task_services.get_task_info(db, log_client, "task-1")
    summary = task_services.get_recent_tasks(db, log_client, {"limit": 10, "hours": 1})

    assert info.task_id == "task-1"
    assert summary.total_count == 1
    assert summary.status_distribution["Queued"] == 1


def test_worker_services_return_expected_models(monkeypatch: Any) -> None:
    worker = {
        "id": "worker-1",
        "status": "active",
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "metadata": {"runpod_id": "pod-1"},
    }
    db = _FakeDb({"workers": [worker], "tasks": [{"id": "task-1", "status": "In Progress", "task_type": "qwen"}]})
    logs = [{"timestamp": datetime.now(timezone.utc).isoformat(), "message": "ok", "log_level": "INFO"}]
    log_client = _FakeLogClient(logs)

    class _FakeRunpodClient:
        def execute_command_on_worker(self, *_args: Any, **_kwargs: Any) -> tuple[int, str]:
            return (0, "Filesystem 95%")

    monkeypatch.setattr(worker_services, "create_runpod_client", lambda: _FakeRunpodClient())

    info = worker_services.get_worker_info(db, log_client, "worker-1", hours=1, startup=False)
    logging_status = worker_services.check_worker_logging(log_client, "worker-1")
    disk_status = worker_services.check_worker_disk_space(db, "worker-1")
    summary = worker_services.get_workers_summary(db, hours=1)

    assert info.worker_id == "worker-1"
    assert logging_status["is_logging"] is True
    assert disk_status["available"] is True
    assert summary.total_count == 1


def test_system_services_return_health_and_orchestrator_status() -> None:
    now = datetime.now(timezone.utc)
    workers = [
        {
            "id": "worker-1",
            "status": "active",
            "last_heartbeat": (now - timedelta(seconds=10)).isoformat(),
            "created_at": (now - timedelta(minutes=5)).isoformat(),
        }
    ]
    tasks = [{"status": "Queued"}, {"status": "In Progress"}]
    logs = [{"timestamp": now.isoformat(), "log_level": "INFO", "message": "Starting orchestrator cycle", "cycle_number": 12}]
    db = _FakeDb({"workers": workers, "tasks": tasks})
    log_client = _FakeLogClient(logs)

    health = system_services.get_system_health(db, log_client)
    status = system_services.get_orchestrator_status(log_client, hours=1)

    assert health.workers_active == 1
    assert health.tasks_queued == 1
    assert status.status in {"HEALTHY", "WARNING", "STALE"}
