"""Tests for control-loop protocol contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from gpu_orchestrator.control.contracts import ControlHost


class _ValidControlHost:
    def __init__(self) -> None:
        self.db = object()
        self.runpod = object()
        self.config = object()
        self.cycle_count = 1
        self.last_scale_down_at = datetime.now(timezone.utc)
        self.last_scale_up_at = None

    def _log_detailed_task_breakdown(
        self,
        detailed_counts: Dict[str, Any],
        scaling_queued: int,
        active: int,
        total: int,
    ) -> None:
        _ = (detailed_counts, scaling_queued, active, total)

    def _log_scaling_decision(self, decision: Any, task_counts: Any) -> None:
        _ = (decision, task_counts)

    async def _check_worker_failure_rate(self) -> bool:
        return False

    async def _mark_worker_error_by_id(self, worker_id: str, reason: str) -> None:
        _ = (worker_id, reason)

    async def _check_error_worker_cleanup(self, worker: Dict[str, Any]) -> None:
        _ = worker

    async def _terminate_worker(self, worker: Dict[str, Any], reason: str = "scale_down") -> bool:
        _ = (worker, reason)
        return True

    async def _spawn_worker(self, queued_count: int = 0) -> bool:
        _ = queued_count
        return True

    async def _derive_all_worker_states(
        self,
        workers: List[Dict[str, Any]],
        queued_count: int,
    ) -> List[Any]:
        _ = (workers, queued_count)
        return []


class _InvalidControlHost:
    db = object()
    runpod = object()
    config = object()
    cycle_count = 0
    last_scale_down_at = None
    last_scale_up_at = None


def test_control_host_runtime_protocol_accepts_valid_host() -> None:
    assert isinstance(_ValidControlHost(), ControlHost)


def test_control_host_runtime_protocol_rejects_incomplete_host() -> None:
    assert not isinstance(_InvalidControlHost(), ControlHost)
