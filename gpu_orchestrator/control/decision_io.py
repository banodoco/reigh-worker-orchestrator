"""Decision rendering and task/scaling breakdown logging for orchestrator cycles."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict

from gpu_orchestrator.worker_state import ScalingDecision, TaskCounts

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gpu_orchestrator.control.contracts import ControlHost


class DecisionLoggingMixin:
    """Logging helpers for detailed task and scaling decisions."""

    def _log_detailed_task_breakdown(
        self: "ControlHost",
        detailed_counts: Dict[str, Any],
        scaling_queued: int,
        active: int,
        total: int,
    ) -> None:
        """Log detailed task breakdown for debugging."""
        totals = detailed_counts.get("totals", {})

        potentially_claimable = totals.get("potentially_claimable")

        logger.debug(f"ORCHESTRATOR CYCLE #{self.cycle_count} - TASK COUNT")

        if potentially_claimable is not None:
            queued_only = totals.get("queued_only", 0)
            blocked_capacity = totals.get("blocked_by_capacity", 0)
            blocked_deps = totals.get("blocked_by_deps", 0)
            blocked_settings = totals.get("blocked_by_settings", 0)

            logger.debug(
                f"  For scaling: {scaling_queued} "
                f"(queued_only={queued_only} + blocked_by_capacity={blocked_capacity})"
            )
            logger.debug(f"  Active (cloud): {active}")
            logger.debug(
                "  NOT scaling for: "
                f"blocked_by_deps={blocked_deps}, blocked_by_settings={blocked_settings}"
            )
        else:
            logger.debug(f"  Queued only: {scaling_queued}")
            logger.debug(f"  Active (cloud): {active}")
            logger.debug(f"  Total workload: {total}")

        logger.debug("DETAILED TASK BREAKDOWN:")

        if potentially_claimable is not None:
            queued_only = totals.get("queued_only", 0)
            blocked_capacity = totals.get("blocked_by_capacity", 0)
            blocked_deps = totals.get("blocked_by_deps", 0)
            blocked_settings = totals.get("blocked_by_settings", 0)

            logger.debug(f"   Queued only (claimable now): {queued_only}")
            logger.debug(f"   Blocked by capacity (scaling): {blocked_capacity}")
            logger.debug(f"   Blocked by dependencies (NOT scaling): {blocked_deps}")
            logger.debug(f"   Blocked by settings (NOT scaling): {blocked_settings}")
            logger.debug(f"   --> For scaling: {scaling_queued}")
        else:
            logger.debug(f"   Queued only: {scaling_queued}")

        logger.debug(f"   Active (cloud-claimed): {active}")
        logger.debug(f"   Total for scaling: {total}")

        if "global_task_breakdown" in detailed_counts:
            breakdown = detailed_counts["global_task_breakdown"]
            logger.debug(f"   In Progress (total): {breakdown.get('in_progress_total', 0)}")
            logger.debug(f"   In Progress (cloud): {breakdown.get('in_progress_cloud', 0)}")
            logger.debug(f"   In Progress (local): {breakdown.get('in_progress_local', 0)}")
            logger.debug(f"   Orchestrator tasks: {breakdown.get('orchestrator_tasks', 0)}")

        users = detailed_counts.get("users", [])
        if users:
            logger.debug(f"   Users with tasks: {len(users)}")
            at_limit_users = [u for u in users if u.get("at_limit", False)]
            if at_limit_users:
                logger.debug(f"   Users at concurrency limit: {len(at_limit_users)}")

    def _log_scaling_decision(self: "ControlHost", decision: ScalingDecision, task_counts: TaskCounts) -> None:
        """Log scaling decision details."""
        logger.debug(
            f"SCALING DECISION (Cycle #{self.cycle_count}): "
            f"Current={decision.active_count}+{decision.spawning_count}, "
            f"Desired={decision.desired_workers}"
        )
        logger.debug("SCALING DECISION ANALYSIS:")
        logger.debug(f"   Task-based: {decision.task_based_workers} workers (workload: {task_counts.total_workload})")
        logger.debug(
            f"   Buffer-based: {decision.buffer_based_workers} workers "
            f"(busy: {decision.busy_count}, buffer: {self.config.machines_to_keep_idle})"
        )
        logger.debug(f"   Min-based: {decision.min_based_workers} workers")
        logger.debug(f"   FINAL DESIRED: {decision.desired_workers} workers")
        logger.debug(
            f"   Current: {decision.idle_count} idle + {decision.busy_count} busy = "
            f"{decision.active_count} active, {decision.spawning_count} spawning"
        )
        logger.debug(f"   Failure rate check: {'PASS' if decision.failure_rate_ok else 'FAIL (blocking scale-up)'}")

        if self.config.use_new_scaling_logic:
            logger.debug("   NEW SCALING LOGIC:")
            logger.debug(f"     Queued tasks: {task_counts.queued}")
            logger.debug(f"     Workers covering tasks: {decision.workers_covering_tasks} (idle + spawning)")
            logger.debug(f"     Uncovered tasks: {decision.uncovered_tasks}")
            logger.debug(f"     Spawn for tasks: {decision.spawn_reason_tasks}")
            logger.debug(f"     Spawn for floor: {decision.spawn_reason_floor}")

        logger.debug(
            f"SCALING DECISION (Cycle #{self.cycle_count}) | "
            f"Tasks={task_counts.queued}+{task_counts.active_cloud}=>{task_counts.total_workload} | "
            f"Current={decision.active_count}+{decision.spawning_count}=>{decision.current_capacity}"
        )

        if self.config.use_new_scaling_logic:
            logger.debug(
                f"  Task coverage: {decision.workers_covering_tasks} workers can cover "
                f"{task_counts.queued} queued ({decision.uncovered_tasks} uncovered)"
            )
            logger.debug(
                f"  Spawn breakdown: {decision.spawn_reason_tasks} for tasks + "
                f"{decision.spawn_reason_floor} for floor = {decision.workers_to_spawn} total"
            )
        else:
            logger.debug(f"  Desired: {decision.desired_workers} workers")

        if decision.should_scale_up:
            logger.debug(f"  Decision: SCALE UP by {decision.workers_to_spawn}")
        elif decision.should_scale_down:
            logger.debug(f"  Decision: SCALE DOWN by {decision.workers_to_terminate}")
        else:
            logger.debug("  Decision: MAINTAIN (at capacity)")


__all__ = ["DecisionLoggingMixin"]
