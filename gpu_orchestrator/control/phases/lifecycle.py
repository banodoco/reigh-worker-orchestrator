"""Worker lifecycle management phases (spawning, early termination, cleanup, reconciliation)."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List

from gpu_orchestrator.worker_state import CycleSummary, DerivedWorkerState, TaskCounts, WorkerLifecycle

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gpu_orchestrator.control.contracts import ControlHost


class LifecyclePhaseMixin:
    """Lifecycle phase methods not directly tied to active-health probing."""

    async def _handle_early_termination(
        self: "ControlHost",
        worker_states: List[DerivedWorkerState],
        task_counts: TaskCounts,
        summary: CycleSummary,
    ) -> List[DerivedWorkerState]:
        """
        Terminate excess spawning workers before they finish starting up.
        Returns updated worker_states list with terminated workers removed.
        """
        now = datetime.now(timezone.utc)
        config = self.config

        spawning_states = [ws for ws in worker_states if ws.is_spawning and not ws.is_route_stale]
        active_states = [
            ws for ws in worker_states
            if ws.is_active and not ws.is_terminal and not ws.is_route_stale
        ]

        current_capacity = len(active_states) + len(spawning_states)

        if task_counts.total > 0:
            down_scaled = math.ceil(task_counts.total * config.scale_down_multiplier)
            task_based = max(1, down_scaled)
        else:
            task_based = 0

        early_desired = max(config.min_active_gpus, task_based, max(config.machines_to_keep_idle, 1))
        early_desired = min(early_desired, config.max_active_gpus)

        if current_capacity <= early_desired:
            return worker_states

        if task_counts.queued > 0:
            logger.debug("Skipping early termination because queued work exists")
            return worker_states

        time_since_last_down = None
        if self.last_scale_down_at:
            time_since_last_down = (now - self.last_scale_down_at).total_seconds()

        if (
            time_since_last_down is not None
            and time_since_last_down < config.scale_down_grace_period_sec
        ):
            logger.debug(
                "Skipping early termination due to scale-down grace period: "
                f"{time_since_last_down:.0f}s < {config.scale_down_grace_period_sec}s"
            )
            return worker_states

        excess = current_capacity - early_desired
        eligible: List[DerivedWorkerState] = []
        for ws in sorted(spawning_states, key=lambda x: x.created_at, reverse=True):
            age = (now - ws.created_at).total_seconds()
            if age > config.spawning_grace_period_sec:
                eligible.append(ws)

        to_terminate = min(excess, len(eligible))

        if to_terminate == 0:
            logger.debug("No spawning workers eligible for early termination (within grace period)")
            return worker_states

        logger.info(
            f"Early termination: {current_capacity} workers > {early_desired} desired, "
            f"terminating {to_terminate} spawning workers"
        )

        terminated_ids = set()
        for ws in eligible[:to_terminate]:
            if ws.runpod_id:
                success = await self.runpod.terminate_worker(ws.runpod_id)
                if success:
                    logger.info(f"Terminated spawning worker {ws.worker_id} (RunPod {ws.runpod_id})")
                else:
                    logger.warning(f"Failed to terminate spawning worker {ws.worker_id}")

            metadata_update = {
                "termination_reason": f"early_termination_over_capacity ({current_capacity} > {early_desired})",
                "terminated_at": now.isoformat(),
            }
            await self.db.update_worker_status(ws.worker_id, "terminated", metadata_update)
            summary.workers_terminated += 1
            terminated_ids.add(ws.worker_id)

        self.last_scale_down_at = now

        return [ws for ws in worker_states if ws.worker_id not in terminated_ids]

    async def _handle_spawning_workers(
        self: "ControlHost",
        worker_states: List[DerivedWorkerState],
        task_counts: TaskCounts,
        summary: CycleSummary,
    ) -> None:
        """Handle pod initialization, script launch, and promotion."""
        spawning_states = [
            ws for ws in worker_states if ws.is_spawning or ws.should_promote_to_active
        ]

        for ws in spawning_states:
            worker_id = ws.worker_id
            runpod_id = ws.runpod_id

            if not runpod_id:
                await self._mark_worker_error_by_id(worker_id, "No Runpod ID")
                summary.workers_failed += 1
                continue

            if ws.should_promote_to_active:
                logger.info(f"Worker {worker_id} signaled ready_for_tasks - promoting to active")
                metadata_update = {
                    "promoted_to_active_at": datetime.now(timezone.utc).isoformat(),
                    "promotion_reason": "ready_for_tasks",
                }
                await self.db.update_worker_status(worker_id, "active", metadata_update)
                summary.workers_promoted += 1
                continue

            status_update = await self.runpod.check_and_initialize_worker(worker_id, runpod_id)

            if status_update["status"] == "error":
                await self._mark_worker_error_by_id(worker_id, status_update.get("error", "Unknown error"))
                summary.workers_failed += 1
                continue

            if ws.should_terminate:
                await self._mark_worker_error_by_id(worker_id, ws.termination_reason or "Spawning timeout")
                summary.workers_failed += 1
                continue

            if status_update.get("ready") and status_update["status"] == "spawning":
                if ws.lifecycle == WorkerLifecycle.SPAWNING_SCRIPT_PENDING:
                    if self.config.auto_start_worker_process:
                        if await self.runpod.start_worker_process(
                            runpod_id,
                            worker_id,
                            has_pending_tasks=(task_counts.queued > 0),
                        ):
                            logger.info(f"Launched startup script for {worker_id}")
                            metadata_update = {
                                "ssh_details": status_update.get("ssh_details"),
                                "startup_script_launched": True,
                                "startup_script_launched_at": datetime.now(timezone.utc).isoformat(),
                                **_selected_route_metadata(self),
                            }
                            await self.db.update_worker_status(worker_id, "spawning", metadata_update)
                            summary.startup_scripts_launched += 1
                        else:
                            logger.warning(f"Failed to launch startup script for {worker_id}")

    async def _cleanup_error_workers(
        self: "ControlHost",
        worker_states: List[DerivedWorkerState],
        summary: CycleSummary,
    ) -> None:
        """Terminate RunPod instances for error workers."""
        error_states = [ws for ws in worker_states if ws.lifecycle == WorkerLifecycle.ERROR]

        for ws in error_states:
            try:
                worker = await self.db.get_worker_by_id(ws.worker_id)
                if worker:
                    await self._check_error_worker_cleanup(worker)
                    summary.workers_failed += 1
            except Exception as exc:
                logger.error(f"Error during error worker cleanup for {ws.worker_id}: {exc}")

    async def _reconcile_orphaned_tasks(
        self: "ControlHost",
        worker_states: List[DerivedWorkerState],
        summary: CycleSummary,
    ) -> None:
        """Reset tasks stuck on failed/terminated workers."""
        failed_ids = []
        for ws in worker_states:
            if ws.lifecycle in (WorkerLifecycle.ERROR, WorkerLifecycle.TERMINATED):
                failed_ids.append(ws.worker_id)

        if failed_ids:
            reset_count = await self.db.reset_orphaned_tasks(failed_ids[:100])
            summary.tasks_reset = reset_count

        unassigned_reset = await self.db.reset_unassigned_orphaned_tasks(timeout_minutes=15)
        summary.tasks_reset += unassigned_reset

        stale_reset = await self.db.reset_stale_assigned_tasks(timeout_minutes=30)
        summary.tasks_reset += stale_reset

    async def _check_worker_task_failure_streak(self: "ControlHost", worker_id: str) -> Dict[str, Any]:
        """
        Check if a worker is repeatedly failing tasks.

        Queries recent task outcomes to detect workers stuck in failure loops
        (e.g., OOM errors causing every task to fail).
        """
        try:
            stats = await self.db.get_worker_task_failure_streak(
                worker_id,
                window_minutes=self.config.task_failure_window_minutes,
            )

            consecutive = stats["consecutive_failures"]
            threshold = self.config.max_consecutive_task_failures

            if consecutive >= threshold:
                reason = (
                    f"Task failure loop detected: {consecutive} consecutive failures "
                    f"(threshold: {threshold}). Possible OOM or worker corruption."
                )
                logger.warning(f"[TASK_FAILURE_STREAK] Worker {worker_id}: {reason}")
                return {
                    "should_restart": True,
                    "consecutive_failures": consecutive,
                    "total_failures": stats["total_failures"],
                    "total_outcomes": stats["total_outcomes"],
                    "failure_rate": stats["failure_rate"],
                    "reason": reason,
                }

            if consecutive > 0:
                logger.debug(
                    f"[TASK_FAILURE_STREAK] Worker {worker_id}: {consecutive} consecutive failures "
                    f"(threshold: {threshold}, rate: {stats['failure_rate']:.0%})"
                )

            return {
                "should_restart": False,
                "consecutive_failures": consecutive,
                "total_failures": stats["total_failures"],
                "total_outcomes": stats["total_outcomes"],
                "failure_rate": stats["failure_rate"],
                "reason": None,
            }

        except Exception as exc:
            logger.error(f"[TASK_FAILURE_STREAK] Error checking worker {worker_id}: {exc}")
            return {
                "should_restart": False,
                "consecutive_failures": 0,
                "reason": None,
            }


__all__ = ["LifecyclePhaseMixin"]


def _selected_route_metadata(host: "ControlHost") -> dict[str, Any]:
    metadata_fn = getattr(host.runpod, "selected_route_metadata", None)
    if callable(metadata_fn):
        return metadata_fn()
    return {}
