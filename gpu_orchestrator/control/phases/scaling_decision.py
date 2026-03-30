"""Scaling decision calculation phase."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, List

from gpu_orchestrator.worker_state import (
    DerivedWorkerState,
    ScalingDecision,
    TaskCounts,
    calculate_scaling_decision_pure,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gpu_orchestrator.control.contracts import ControlHost


class ScalingDecisionPhaseMixin:
    """Compute desired capacity and scale actions."""

    async def _calculate_scaling_decision(
        self: "ControlHost",
        worker_states: List[DerivedWorkerState],
        task_counts: TaskCounts,
    ) -> ScalingDecision:
        """Calculate desired workers and scaling actions."""
        config = self.config

        failure_rate_ok = await self._check_worker_failure_rate()

        if config.use_new_scaling_logic:
            decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok)
        else:
            decision = self._calculate_scaling_decision_legacy(worker_states, task_counts, failure_rate_ok)

        self._log_scaling_decision(decision, task_counts)
        return decision

    def _calculate_scaling_decision_legacy(
        self: "ControlHost",
        worker_states: List[DerivedWorkerState],
        task_counts: TaskCounts,
        failure_rate_ok: bool,
    ) -> ScalingDecision:
        """Legacy scaling logic (before double-spawn fix)."""
        config = self.config

        active_states = [ws for ws in worker_states if ws.is_active and not ws.should_terminate]
        spawning_states = [ws for ws in worker_states if ws.is_spawning]

        active_count = len(active_states)
        spawning_count = len(spawning_states)
        current_capacity = active_count + spawning_count

        idle_count = len([ws for ws in active_states if not ws.has_active_task])
        busy_count = active_count - idle_count

        total_workload = task_counts.total_workload

        if total_workload > 0:
            up_scaled = math.ceil(total_workload * config.scale_up_multiplier)
            task_based = max(1, up_scaled)
        else:
            task_based = 0

        buffer_based = busy_count + max(config.machines_to_keep_idle, 1)
        min_based = config.min_active_gpus

        desired = max(min_based, task_based, buffer_based)
        desired = min(desired, config.max_active_gpus)

        workers_to_spawn = 0
        workers_to_terminate = 0

        if current_capacity < desired and failure_rate_ok:
            workers_to_spawn = min(desired - current_capacity, config.max_active_gpus - current_capacity)
        elif current_capacity > desired:
            workers_to_terminate = current_capacity - desired

        return ScalingDecision(
            desired_workers=desired,
            current_capacity=current_capacity,
            active_count=active_count,
            spawning_count=spawning_count,
            idle_count=idle_count,
            busy_count=busy_count,
            task_based_workers=task_based,
            buffer_based_workers=buffer_based,
            min_based_workers=min_based,
            workers_to_spawn=workers_to_spawn,
            workers_to_terminate=workers_to_terminate,
            failure_rate_ok=failure_rate_ok,
            at_max_capacity=current_capacity >= config.max_active_gpus,
            at_min_capacity=active_count <= config.min_active_gpus,
        )


__all__ = ["ScalingDecisionPhaseMixin"]
