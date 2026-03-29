"""Active-worker health monitoring and task-failure detection phase."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List

from gpu_orchestrator.logging_config import set_current_worker
from gpu_orchestrator.worker_state import CycleSummary, DerivedWorkerState, TaskCounts, WorkerLifecycle

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gpu_orchestrator.control.contracts import ControlHost


class HealthPhaseMixin:
    """Health-check methods for active workers."""

    async def _health_check_active_workers(
        self: "ControlHost",
        worker_states: List[DerivedWorkerState],
        task_counts: TaskCounts,
        summary: CycleSummary,
    ) -> None:
        """Check health of active workers based on derived state."""
        active_states = [ws for ws in worker_states if ws.is_active]

        for ws in active_states:
            try:
                set_current_worker(ws.worker_id)

                if await self._handle_derived_termination(ws, summary):
                    continue

                if ws.has_active_task:
                    if await self._handle_worker_with_active_task(ws, summary):
                        continue
                else:
                    if await self._handle_worker_without_active_task(ws, task_counts, active_states, summary):
                        continue

                self._log_worker_lifecycle_state(ws)

                if await self._run_post_health_checks(ws, summary):
                    continue

            except Exception as exc:
                logger.error(f"Error checking health of worker {ws.worker_id}: {exc}")
            finally:
                set_current_worker(None)

    async def _handle_derived_termination(
        self: "ControlHost",
        ws: DerivedWorkerState,
        summary: CycleSummary,
    ) -> bool:
        """Handle workers already marked for termination by derived state."""
        if not ws.should_terminate:
            return False

        worker_id = ws.worker_id
        logger.warning(f"[WORKER_HEALTH] Worker {worker_id}: {ws.termination_reason}")
        worker = await self.db.get_worker_by_id(worker_id)
        if not worker:
            return True

        await self._mark_worker_error(
            worker,
            ws.termination_reason or "Health check failed",
            context={
                "error_code": ws.error_code,
                "lifecycle": ws.lifecycle.value,
                "effective_age_sec": ws.effective_age_sec,
                "heartbeat_age_sec": ws.heartbeat_age_sec,
                "vram_total_mb": ws.vram_total_mb,
                "has_ever_claimed": ws.has_ever_claimed_task,
                "has_active_task": ws.has_active_task,
            },
        )
        summary.workers_failed += 1
        return True

    async def _handle_worker_with_active_task(
        self: "ControlHost",
        ws: DerivedWorkerState,
        summary: CycleSummary,
    ) -> bool:
        """Handle active-task workers where heartbeat is mandatory."""
        logger.debug(
            f"Worker {ws.worker_id} has active tasks and heartbeat "
            f"({ws.heartbeat_age_sec:.0f}s old)"
        )
        return False

    async def _handle_worker_without_active_task(
        self: "ControlHost",
        ws: DerivedWorkerState,
        task_counts: TaskCounts,
        active_states: List[DerivedWorkerState],
        summary: CycleSummary,
    ) -> bool:
        """Handle idle/no-active-task workers and queued-work expectations."""
        worker_id = ws.worker_id
        worker = await self.db.get_worker_by_id(worker_id)
        if not worker:
            return True

        if task_counts.queued > 0:
            if not await self._perform_basic_health_check(worker):
                logger.warning(
                    f"[WORKER_HEALTH] Worker {worker_id}: "
                    "Failed basic health check with tasks queued"
                )
                await self._mark_worker_error(worker, "Failed basic health check with tasks queued")
                summary.workers_failed += 1
                return True
            return False

        if await self._is_worker_idle_with_timeout(worker, self.config.gpu_idle_timeout_sec):
            stable_active = len([state for state in active_states if state.is_healthy])
            if stable_active > self.config.min_active_gpus:
                logger.info(
                    f"[WORKER_HEALTH] Worker {worker_id}: "
                    "Idle timeout with no work, terminating"
                )
                await self._mark_worker_error(
                    worker,
                    f"Idle timeout ({self.config.gpu_idle_timeout_sec}s) with no work available",
                )
                summary.workers_failed += 1
                return True

        return False

    def _log_worker_lifecycle_state(self: "ControlHost", ws: DerivedWorkerState) -> None:
        """Log healthy/initializing worker state diagnostics."""
        worker_id = ws.worker_id

        if ws.lifecycle == WorkerLifecycle.ACTIVE_READY:
            logger.debug(f"Worker {worker_id}: healthy (VRAM: {ws.vram_total_mb}MB, ready_for_tasks=True)")
        elif ws.lifecycle == WorkerLifecycle.ACTIVE_INITIALIZING:
            vram_status = f"VRAM: {ws.vram_total_mb}MB" if ws.vram_reported else "no VRAM"
            ready_status = (
                "ready_for_tasks=True"
                if ws.ready_for_tasks
                else "waiting for ready_for_tasks"
            )
            logger.info(
                f"Worker {worker_id}: initializing ({ws.effective_age_sec:.0f}s, "
                f"{vram_status}, {ready_status})"
            )
        elif ws.lifecycle == WorkerLifecycle.ACTIVE_GPU_NOT_DETECTED:
            logger.info(
                f"Worker {worker_id}: GPU not detected yet "
                f"({ws.effective_age_sec:.0f}s / {self.config.gpu_not_detected_timeout_sec}s)"
            )

    async def _run_post_health_checks(
        self: "ControlHost",
        ws: DerivedWorkerState,
        summary: CycleSummary,
    ) -> bool:
        """Run health checks after primary lifecycle gates; return True if worker was failed."""
        worker_id = ws.worker_id

        if not ws.has_active_task and ws.heartbeat_is_recent:
            worker = await self.db.get_worker_by_id(worker_id)
            if worker and not await self._perform_basic_health_check(worker):
                logger.warning(f"[WORKER_HEALTH] Worker {worker_id}: Failed basic health check")

        await self._check_stuck_tasks(ws, summary)

        failure_check = await self._check_worker_task_failure_streak(worker_id)
        if failure_check["should_restart"]:
            worker = await self.db.get_worker_by_id(worker_id)
            if worker:
                await self._mark_worker_error(
                    worker,
                    failure_check["reason"],
                    context={
                        "error_code": "task_failure_loop",
                        "consecutive_failures": failure_check["consecutive_failures"],
                        "total_failures": failure_check.get("total_failures", 0),
                        "failure_rate": failure_check.get("failure_rate", 0),
                    },
                )
                summary.workers_failed += 1
            return True

        return False

    async def _check_stuck_tasks(
        self: "ControlHost",
        ws: DerivedWorkerState,
        summary: CycleSummary,
    ) -> None:
        """Check if worker has stuck tasks."""
        if not ws.has_active_task:
            return

        running_tasks = await self.db.get_running_tasks_for_worker(ws.worker_id)
        for task in running_tasks:
            task_type = task.get("task_type", "")

            if "_orchestrator" in task_type.lower():
                continue

            if task.get("generation_started_at"):
                task_start = datetime.fromisoformat(task["generation_started_at"].replace("Z", "+00:00"))
                if task_start.tzinfo is None:
                    task_start = task_start.replace(tzinfo=timezone.utc)

                task_age = (datetime.now(timezone.utc) - task_start).total_seconds()
                if task_age > self.config.task_stuck_timeout_sec:
                    worker = await self.db.get_worker_by_id(ws.worker_id)
                    if worker:
                        await self._mark_worker_error(
                            worker,
                            f'Stuck task {task["id"]} (timeout: {self.config.task_stuck_timeout_sec}s)',
                        )
                        summary.workers_failed += 1
                    return

    async def _perform_basic_health_check(self: "ControlHost", worker: Dict[str, Any]) -> bool:
        """Check heartbeat freshness for idle workers."""
        if not worker:
            return False

        worker_id = worker["id"]
        logger.debug(f"HEALTH_CHECK [Worker {worker_id}] Starting basic health check")

        try:
            if worker.get("last_heartbeat"):
                heartbeat_time = datetime.fromisoformat(worker["last_heartbeat"].replace("Z", "+00:00"))
                heartbeat_age = (datetime.now(timezone.utc) - heartbeat_time).total_seconds()

                if heartbeat_age < 60:
                    logger.debug(
                        f"HEALTH_CHECK [Worker {worker_id}] "
                        f"Recent heartbeat ({heartbeat_age:.1f}s old) - worker is healthy"
                    )
                    return True

                logger.warning(f"HEALTH_CHECK [Worker {worker_id}] Stale heartbeat ({heartbeat_age:.1f}s old)")
                return False

            logger.warning(f"HEALTH_CHECK [Worker {worker_id}] No heartbeat received")
            return False

        except Exception as exc:
            logger.error(f"HEALTH_CHECK [Worker {worker_id}] Health check failed: {exc}")
            return False


__all__ = ["HealthPhaseMixin"]
