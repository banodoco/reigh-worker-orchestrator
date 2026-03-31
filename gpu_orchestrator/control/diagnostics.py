"""Diagnostics capture, classification, and error transition helpers."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from gpu_orchestrator.logging_config import set_current_worker

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gpu_orchestrator.control.contracts import ControlHost


class WorkerDiagnosticsMixin:
    """Worker diagnostics and error handling helpers."""

    async def _mark_worker_error_by_id(self: "ControlHost", worker_id: str, reason: str) -> None:
        """Mark a worker as error by ID."""
        worker = await self.db.get_worker_by_id(worker_id)
        if worker:
            await self._mark_worker_error(worker, reason)
        else:
            logger.error(f"Could not find worker {worker_id} to mark as error")

    async def _mark_worker_error(
        self: "ControlHost",
        worker: Dict[str, Any],
        reason: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mark a worker as error and attempt to terminate it."""
        worker_id = worker["id"]
        error_code = self._classify_error_code(reason)
        set_current_worker(worker_id)

        logger.error(f"WORKER_LIFECYCLE [Worker {worker_id}] MARKING AS ERROR: {reason}")

        diagnostics = await self._collect_worker_diagnostics(worker, reason)
        diagnostics["error_code"] = error_code
        if context:
            diagnostics["context"] = context
        diagnostics["timeouts"] = {
            "startup_grace_period_sec": self.config.startup_grace_period_sec,
            "ready_not_claiming_timeout_sec": self.config.ready_not_claiming_timeout_sec,
            "gpu_not_detected_timeout_sec": self.config.gpu_not_detected_timeout_sec,
            "gpu_idle_timeout_sec": self.config.gpu_idle_timeout_sec,
            "spawning_timeout_sec": self.config.spawning_timeout_sec,
        }

        try:
            if diagnostics.get("startup_log_tail"):
                self._emit_large_worker_log_to_system_logs(
                    worker_id,
                    "startup/apt tails",
                    diagnostics.get("startup_log_tail", ""),
                )
            if diagnostics.get("startup_log_stderr"):
                self._emit_large_worker_log_to_system_logs(
                    worker_id,
                    "startup stderr",
                    diagnostics.get("startup_log_stderr", ""),
                )
        except Exception as exc:
            logger.debug(
                "Failed to emit startup tails for worker %s: %s",
                worker_id,
                exc,
            )

        try:
            metadata_update = {
                "error_reason": reason,
                "error_code": error_code,
                "error_time": datetime.now(timezone.utc).isoformat(),
                "diagnostics": diagnostics,
            }
        except Exception as exc:
            logger.error(f"Failed to prepare diagnostic metadata: {exc}")
            metadata_update = {
                "error_reason": reason,
                "error_code": error_code,
                "error_time": datetime.now(timezone.utc).isoformat(),
            }

        reset_count = await self.db.reset_orphaned_tasks([worker_id])
        if reset_count > 0:
            logger.info(f"WORKER_LIFECYCLE [Worker {worker_id}] Reset {reset_count} orphaned tasks")

        success = await self.db.update_worker_status(worker_id, "error", metadata_update)
        if success:
            logger.info(f"WORKER_LIFECYCLE [Worker {worker_id}] Database updated with error status")
        else:
            logger.error(f"WORKER_LIFECYCLE [Worker {worker_id}] Failed to update database")

        worker["status"] = "error"

        runpod_id = worker.get("metadata", {}).get("runpod_id")
        if runpod_id:
            logger.info(f"WORKER_LIFECYCLE [Worker {worker_id}] Terminating RunPod instance: {runpod_id}")
            try:
                success = self.runpod.terminate_worker(runpod_id)
                if success:
                    logger.info(f"WORKER_LIFECYCLE [Worker {worker_id}] RunPod terminated successfully")
                else:
                    logger.error(f"WORKER_LIFECYCLE [Worker {worker_id}] Failed to terminate RunPod")
            except Exception as exc:
                logger.error(f"WORKER_LIFECYCLE [Worker {worker_id}] Exception terminating RunPod: {exc}")

        set_current_worker(None)

    def _classify_error_code(self: "ControlHost", reason: str) -> str:
        """Map a human reason string to a stable error code."""
        lowered = (reason or "").lower()
        if lowered.startswith("gpu ready but never claimed"):
            return "GPU_READY_NOT_CLAIMING"
        if lowered.startswith("gpu not detected"):
            return "GPU_NOT_DETECTED"
        if lowered.startswith("never initialized"):
            return "STARTUP_NEVER_READY"
        if "stale heartbeat with active tasks" in lowered:
            return "STALE_HEARTBEAT_ACTIVE_TASK"
        if lowered.startswith("idle with tasks queued"):
            return "IDLE_WITH_TASKS_QUEUED"
        if lowered.startswith("no heartbeat or activity"):
            return "NO_HEARTBEAT"
        if lowered.startswith("early termination: over-capacity"):
            return "OVERCAPACITY_EARLY_TERMINATION"
        if lowered.startswith("failsafe:"):
            return "FAILSAFE"
        return "UNKNOWN"

    async def _collect_worker_diagnostics(
        self: "ControlHost",
        worker: Dict[str, Any],
        reason: str,
    ) -> Dict[str, Any]:
        """Collect comprehensive diagnostic information from a failing worker."""
        worker_id = worker["id"]
        runpod_id = worker.get("metadata", {}).get("runpod_id")

        diagnostics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "worker_id": worker_id,
            "runpod_id": runpod_id,
            "error_reason": reason,
        }
        collection_success = True

        logger.info(f"DIAGNOSTICS [Worker {worker_id}] Collecting diagnostic information")

        try:
            metadata = worker.get("metadata", {})
            diagnostics["last_heartbeat"] = worker.get("last_heartbeat")
            diagnostics["worker_status"] = worker.get("status")
            diagnostics["worker_created_at"] = worker.get("created_at")

            if isinstance(metadata, dict) and metadata.get("startup_script_launched_at"):
                diagnostics["startup_script_launched_at"] = metadata.get("startup_script_launched_at")

            if "vram_total_mb" in metadata:
                diagnostics["vram_total_mb"] = metadata["vram_total_mb"]
                diagnostics["vram_used_mb"] = metadata.get("vram_used_mb")
                diagnostics["vram_timestamp"] = metadata.get("vram_timestamp")

            try:
                running_tasks = await self.db.get_running_tasks_for_worker(worker_id)
                diagnostics["running_tasks_count"] = len(running_tasks)
                diagnostics["running_tasks"] = [
                    {
                        "id": str(task["id"]),
                        "task_type": task.get("task_type"),
                        "started_at": task.get("generation_started_at"),
                    }
                    for task in running_tasks[:5]
                ]
            except Exception as exc:
                diagnostics["running_tasks_error"] = str(exc)

            if runpod_id:
                try:
                    pod_status = self.runpod.get_pod_status(runpod_id)
                    if pod_status:
                        diagnostics["pod_status"] = {
                            "desired_status": pod_status.get("desired_status"),
                            "actual_status": pod_status.get("actual_status"),
                            "uptime_seconds": pod_status.get("uptime_seconds"),
                        }
                except Exception as exc:
                    diagnostics["pod_status_error"] = str(exc)

        except Exception as exc:
            logger.error(f"DIAGNOSTICS [Worker {worker_id}] Error collecting: {exc}")
            collection_success = False
            diagnostics["collection_error"] = str(exc)

        diagnostics["collection_success"] = collection_success
        return diagnostics

    def _redact_for_system_logs(self: "ControlHost", text: str) -> str:
        """Best-effort redaction for secrets."""
        try:
            redacted = text or ""
            redacted = re.sub(
                r"eyJ[a-zA-Z0-9_\\-]{20,}\\.[a-zA-Z0-9_\\-]{20,}\\.[a-zA-Z0-9_\\-]{20,}",
                "[REDACTED_JWT]",
                redacted,
            )
            redacted = re.sub(
                r"Bearer\\s+[A-Za-z0-9_\\-\\.]{20,}",
                "Bearer [REDACTED]",
                redacted,
                flags=re.IGNORECASE,
            )
            redacted = re.sub(
                r"(SUPABASE_(SERVICE_ROLE_KEY|SERVICE_KEY|ANON_KEY)=)\"?[^\n\" ]+\"?",
                r"\\1[REDACTED]",
                redacted,
            )
            return redacted
        except Exception:
            return text

    def _emit_large_worker_log_to_system_logs(
        self: "ControlHost",
        worker_id: str,
        title: str,
        content: str,
        max_chunk: int = 3500,
    ) -> None:
        """Emit large text content to system_logs."""
        if not content:
            return
        safe = self._redact_for_system_logs(content)
        safe = safe.replace("\r\n", "\n")
        total_cap = 16000
        if len(safe) > total_cap:
            safe = safe[-total_cap:]
        chunks = [safe[i : i + max_chunk] for i in range(0, len(safe), max_chunk)]
        for idx, chunk in enumerate(chunks, start=1):
            logger.info(f"[WORKER_LOG_TAIL] {title} ({idx}/{len(chunks)})\n{chunk}")


__all__ = ["WorkerDiagnosticsMixin"]
