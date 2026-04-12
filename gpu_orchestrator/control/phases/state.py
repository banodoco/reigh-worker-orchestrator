"""State acquisition and derivation phase for orchestrator cycles."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from gpu_orchestrator.worker_state import DerivedWorkerState, TaskCounts, derive_worker_state

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gpu_orchestrator.control.contracts import ControlHost


class StatePhaseMixin:
    """Phase methods for fetching current system state and deriving worker lifecycle."""

    async def _fetch_current_state(
        self: "ControlHost",
    ) -> Tuple[List[Dict[str, Any]], TaskCounts, Optional[Dict[str, Any]]]:
        """Fetch workers and task counts from database. Returns (workers, task_counts, detailed_counts)."""
        # Only fetch non-terminated workers for the main control loop.
        # Terminated workers don't need lifecycle checks, and including them
        # bloats the .in_() queries in batch_check_ever_claimed / batch_check_active_tasks
        # past PostgREST's URL length limit, causing 400 errors.
        workers = await self.db.get_workers(status=["spawning", "active", "error"])

        detailed_counts = await self.db.get_detailed_task_counts_via_edge_function()

        if detailed_counts:
            totals = detailed_counts.get("totals", {})
            active_cloud_count = totals.get("active_only", 0)

            potentially_claimable = totals.get("potentially_claimable")

            if potentially_claimable is not None:
                queued_count = potentially_claimable
                total_tasks = potentially_claimable + active_cloud_count

                queued_only = totals.get("queued_only", 0)
                blocked_capacity = totals.get("blocked_by_capacity", 0)
                blocked_deps = totals.get("blocked_by_deps", 0)
                blocked_settings = totals.get("blocked_by_settings", 0)

                logger.debug(
                    f"TASK COUNT (Cycle #{self.cycle_count}): "
                    f"Scaling={potentially_claimable} "
                    f"(queued_only={queued_only} + capacity_blocked={blocked_capacity}), "
                    f"Active={active_cloud_count}, "
                    f"[NOT scaling for: deps_blocked={blocked_deps}, settings_blocked={blocked_settings}]"
                )
            else:
                queued_count = totals.get("queued_only", 0)
                total_tasks = totals.get("queued_plus_active", 0)
                logger.debug(
                    f"TASK COUNT (Cycle #{self.cycle_count}): "
                    f"Queued={queued_count}, Active={active_cloud_count}, Total={total_tasks}"
                )

            self._log_detailed_task_breakdown(detailed_counts, queued_count, active_cloud_count, total_tasks)
        else:
            logger.warning("Failed to get detailed task counts, falling back to simple counting")
            total_tasks = await self.db.count_available_tasks_via_edge_function(include_active=True)
            queued_count = await self.db.count_available_tasks_via_edge_function(include_active=False)
            active_cloud_count = total_tasks - queued_count
            logger.warning(f"Fallback returned: {total_tasks} total, {queued_count} queued")

        logger.info(f"Cycle #{self.cycle_count}: scaling={queued_count} active={active_cloud_count}")
        return workers, TaskCounts(queued=queued_count, active_cloud=active_cloud_count, total=total_tasks), detailed_counts

    async def _derive_all_worker_states(
        self: "ControlHost",
        workers: List[Dict[str, Any]],
        queued_count: int,
    ) -> List[DerivedWorkerState]:
        """Derive state for all workers using batch queries."""
        worker_ids = [w["id"] for w in workers]

        claimed_map, active_tasks_map = await asyncio.gather(
            self.db.batch_check_ever_claimed(worker_ids),
            self.db.batch_check_active_tasks(worker_ids),
        )

        now = datetime.now(timezone.utc)
        return [
            derive_worker_state(
                worker=w,
                config=self.config,
                now=now,
                has_ever_claimed=claimed_map.get(w["id"], False),
                has_active_task=active_tasks_map.get(w["id"], False),
                queued_count=queued_count,
            )
            for w in workers
        ]


__all__ = ["StatePhaseMixin"]
