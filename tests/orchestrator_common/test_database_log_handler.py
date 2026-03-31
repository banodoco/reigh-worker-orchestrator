"""Direct tests for the shared DatabaseLogHandler."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

from orchestrator_common.database_log_handler import DatabaseLogHandler


class _FakeRpcResult:
    def __init__(self, data: Dict[str, Any] | None = None) -> None:
        self.data = data or {"errors": 0}


class _FakeRpcCall:
    def __init__(self, sink: Dict[str, Any], payload: Dict[str, Any]) -> None:
        self._sink = sink
        self._payload = payload

    def execute(self) -> _FakeRpcResult:
        self._sink["last_payload"] = self._payload
        self._sink["execute_calls"] = self._sink.get("execute_calls", 0) + 1
        return _FakeRpcResult()


class _FakeSupabase:
    def __init__(self) -> None:
        self.calls: Dict[str, Any] = {}

    def rpc(self, name: str, payload: Dict[str, Any]) -> _FakeRpcCall:
        self.calls["name"] = name
        self.calls["payload"] = payload
        return _FakeRpcCall(self.calls, payload)


def test_database_log_handler_emits_and_flushes_batch() -> None:
    fake_supabase = _FakeSupabase()
    handler = DatabaseLogHandler(
        supabase_client=fake_supabase,
        source_type="orchestrator_gpu",
        source_id="unit-test",
        batch_size=1,
        flush_interval=0.05,
        max_queue_size=10,
    )
    try:
        handler.set_current_cycle(7)
        handler.set_current_task("task-abc")
        handler.set_current_worker("worker-123")

        record = logging.LogRecord(
            name="unit.logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg="hello from test",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        deadline = time.time() + 2.0
        while time.time() < deadline and handler.total_logs_sent < 1:
            time.sleep(0.02)

        stats = handler.get_stats()
        assert stats["total_logs_queued"] == 1
        assert stats["total_logs_sent"] >= 1
        assert fake_supabase.calls["name"] == "func_insert_logs_batch"
        sent_logs = fake_supabase.calls["payload"]["logs"]
        assert sent_logs[0]["cycle_number"] == 7
        assert sent_logs[0]["task_id"] == "task-abc"
        assert sent_logs[0]["worker_id"] == "worker-123"
        assert "DatabaseLogHandler(" in repr(handler)
    finally:
        handler.close()
