"""Shared structural contracts for control-loop mixins."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from gpu_orchestrator.worker_state import DerivedWorkerState, ScalingDecision, TaskCounts


@runtime_checkable
class ControlHost(Protocol):
    """Minimum host contract expected by control-loop mixins."""

    db: Any
    runpod: Any
    config: Any

    cycle_count: int
    last_scale_down_at: Optional[datetime]
    last_scale_up_at: Optional[datetime]

    def _log_detailed_task_breakdown(
        self,
        detailed_counts: Dict[str, Any],
        scaling_queued: int,
        active: int,
        total: int,
    ) -> None: ...

    def _log_scaling_decision(self, decision: ScalingDecision, task_counts: TaskCounts) -> None: ...

    async def _check_worker_failure_rate(self) -> bool: ...

    async def _mark_worker_error_by_id(self, worker_id: str, reason: str) -> None: ...

    async def _check_error_worker_cleanup(self, worker: Dict[str, Any]) -> None: ...

    async def _terminate_worker(self, worker: Dict[str, Any], reason: str = "scale_down") -> bool: ...

    async def _spawn_worker(self, queued_count: int = 0) -> bool: ...

    async def _derive_all_worker_states(self, workers: List[Dict[str, Any]], queued_count: int) -> List[DerivedWorkerState]: ...


__all__ = ["ControlHost"]
