"""Decision rendering and task/scaling breakdown logging for orchestrator cycles."""

from __future__ import annotations

import logging
import sys
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

        print(f"\n{'=' * 80}", file=sys.stderr)
        print(f"ORCHESTRATOR CYCLE #{self.cycle_count} - TASK COUNT", file=sys.stderr)

        if potentially_claimable is not None:
            queued_only = totals.get("queued_only", 0)
            blocked_capacity = totals.get("blocked_by_capacity", 0)
            blocked_deps = totals.get("blocked_by_deps", 0)
            blocked_settings = totals.get("blocked_by_settings", 0)

            print(
                f"  For scaling: {scaling_queued} "
                f"(queued_only={queued_only} + blocked_by_capacity={blocked_capacity})",
                file=sys.stderr,
            )
            print(f"  Active (cloud): {active}", file=sys.stderr)
            print(
                "  NOT scaling for: "
                f"blocked_by_deps={blocked_deps}, blocked_by_settings={blocked_settings}",
                file=sys.stderr,
            )
        else:
            print(f"  Queued only: {scaling_queued}", file=sys.stderr)
            print(f"  Active (cloud): {active}", file=sys.stderr)
            print(f"  Total workload: {total}", file=sys.stderr)

        print(f"{'=' * 80}\n", file=sys.stderr)

        logger.info("DETAILED TASK BREAKDOWN:")

        if potentially_claimable is not None:
            queued_only = totals.get("queued_only", 0)
            blocked_capacity = totals.get("blocked_by_capacity", 0)
            blocked_deps = totals.get("blocked_by_deps", 0)
            blocked_settings = totals.get("blocked_by_settings", 0)

            logger.info(f"   Queued only (claimable now): {queued_only}")
            logger.info(f"   Blocked by capacity (scaling): {blocked_capacity}")
            logger.info(f"   Blocked by dependencies (NOT scaling): {blocked_deps}")
            logger.info(f"   Blocked by settings (NOT scaling): {blocked_settings}")
            logger.info(f"   --> For scaling: {scaling_queued}")
        else:
            logger.info(f"   Queued only: {scaling_queued}")

        logger.info(f"   Active (cloud-claimed): {active}")
        logger.info(f"   Total for scaling: {total}")

        if "global_task_breakdown" in detailed_counts:
            breakdown = detailed_counts["global_task_breakdown"]
            logger.info(f"   In Progress (total): {breakdown.get('in_progress_total', 0)}")
            logger.info(f"   In Progress (cloud): {breakdown.get('in_progress_cloud', 0)}")
            logger.info(f"   In Progress (local): {breakdown.get('in_progress_local', 0)}")
            logger.info(f"   Orchestrator tasks: {breakdown.get('orchestrator_tasks', 0)}")

        users = detailed_counts.get("users", [])
        if users:
            logger.info(f"   Users with tasks: {len(users)}")
            at_limit_users = [u for u in users if u.get("at_limit", False)]
            if at_limit_users:
                logger.info(f"   Users at concurrency limit: {len(at_limit_users)}")

    def _log_scaling_decision(self: "ControlHost", decision: ScalingDecision, task_counts: TaskCounts) -> None:
        """Log scaling decision details."""
        logger.critical(
            f"SCALING DECISION (Cycle #{self.cycle_count}): "
            f"Current={decision.active_count}+{decision.spawning_count}, "
            f"Desired={decision.desired_workers}"
        )
        logger.info("SCALING DECISION ANALYSIS:")
        logger.info(f"   Task-based: {decision.task_based_workers} workers (workload: {task_counts.total_workload})")
        logger.info(
            f"   Buffer-based: {decision.buffer_based_workers} workers "
            f"(busy: {decision.busy_count}, buffer: {self.config.machines_to_keep_idle})"
        )
        logger.info(f"   Min-based: {decision.min_based_workers} workers")
        logger.info(f"   FINAL DESIRED: {decision.desired_workers} workers")
        logger.info(
            f"   Current: {decision.idle_count} idle + {decision.busy_count} busy = "
            f"{decision.active_count} active, {decision.spawning_count} spawning"
        )
        logger.info(f"   Failure rate check: {'PASS' if decision.failure_rate_ok else 'FAIL (blocking scale-up)'}")

        if self.config.use_new_scaling_logic:
            logger.info("   NEW SCALING LOGIC:")
            logger.info(f"     Queued tasks: {task_counts.queued}")
            logger.info(f"     Workers covering tasks: {decision.workers_covering_tasks} (idle + spawning)")
            logger.info(f"     Uncovered tasks: {decision.uncovered_tasks}")
            logger.info(f"     Spawn for tasks: {decision.spawn_reason_tasks}")
            logger.info(f"     Spawn for floor: {decision.spawn_reason_floor}")

        print(f"\n{'=' * 80}", file=sys.stderr)
        print(f"SCALING DECISION (Cycle #{self.cycle_count})", file=sys.stderr)
        print(
            f"  Tasks: {task_counts.queued} queued + {task_counts.active_cloud} active = "
            f"{task_counts.total_workload} total",
            file=sys.stderr,
        )
        print(
            f"  Current: {decision.active_count} active + {decision.spawning_count} spawning = "
            f"{decision.current_capacity} total",
            file=sys.stderr,
        )

        if self.config.use_new_scaling_logic:
            print(
                f"  Task coverage: {decision.workers_covering_tasks} workers can cover "
                f"{task_counts.queued} queued ({decision.uncovered_tasks} uncovered)",
                file=sys.stderr,
            )
            print(
                f"  Spawn breakdown: {decision.spawn_reason_tasks} for tasks + "
                f"{decision.spawn_reason_floor} for floor = {decision.workers_to_spawn} total",
                file=sys.stderr,
            )
        else:
            print(f"  Desired: {decision.desired_workers} workers", file=sys.stderr)

        if decision.should_scale_up:
            print(f"  Decision: SCALE UP by {decision.workers_to_spawn}", file=sys.stderr)
        elif decision.should_scale_down:
            print(f"  Decision: SCALE DOWN by {decision.workers_to_terminate}", file=sys.stderr)
        else:
            print("  Decision: MAINTAIN (at capacity)", file=sys.stderr)
        print(f"{'=' * 80}\n", file=sys.stderr)


__all__ = ["DecisionLoggingMixin"]
