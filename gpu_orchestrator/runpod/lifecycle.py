"""Worker lifecycle operations for RunPod-backed workers."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

import runpod
from dotenv import load_dotenv

from .api import create_pod_and_wait, get_pod_ssh_details, terminate_pod
from .startup_script import (
    build_create_script_command,
    build_launch_command,
    build_log_retrieval_command,
    build_startup_status_check_command,
    render_startup_script,
)

logger = logging.getLogger(__name__)


class RunpodLifecycleMixin:
    """Pod spawn/startup/reconciliation methods shared by RunpodClient."""

    def spawn_worker(
        self,
        worker_id: str,
        worker_env: Optional[Dict[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        gpu_info = self._get_gpu_type_info()
        if not gpu_info:
            logger.error("Cannot spawn worker: GPU type not available")
            return None

        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        env_vars = {
            "WORKER_ID": worker_id,
            "SUPABASE_URL": supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY", ""),
            "REPLICATE_API_TOKEN": os.getenv("REPLICATE_API_TOKEN", ""),
            "SUPABASE_EDGE_COMPLETE_TASK_URL": (
                f"{supabase_url}/functions/v1/complete_task" if supabase_url else ""
            ),
            "SUPABASE_EDGE_MARK_FAILED_URL": (
                f"{supabase_url}/functions/v1/mark-task-failed" if supabase_url else ""
            ),
        }

        if worker_env:
            env_vars.update(worker_env)

        public_key_content = self._get_public_key_content()
        if public_key_content:
            logger.info(f"[SSH_DEBUG] Will inject SSH public key into worker {worker_id}")
        else:
            logger.error(
                f"[SSH_DEBUG] No SSH public key available for worker {worker_id} - authentication will fail!"
            )

        if self.ram_tiers_enabled:
            ram_tiers = [tier for tier in self.ram_tiers if tier >= self.min_memory_gb]
            if not ram_tiers:
                ram_tiers = [self.min_memory_gb]

            logger.info("🎯 RAM tier fallback enabled (tries each tier across all storages)")
            logger.info(f"   RAM tiers (in order): {ram_tiers} GB")
            logger.info(f"   Storage volumes: {self.storage_volumes}")
        else:
            ram_tiers = [self.min_memory_gb]

        last_error = None
        for ram_tier in ram_tiers:
            logger.info(f"🔍 Trying {ram_tier}GB RAM across all storage volumes...")

            for storage_name in self.storage_volumes:
                storage_volume_id = self._get_storage_volume_id(storage_name)
                if not storage_volume_id:
                    logger.warning(f"⚠️  Storage '{storage_name}' not found, skipping...")
                    continue

                if ram_tier == ram_tiers[0]:
                    self._check_and_expand_storage(storage_name, storage_volume_id, min_free_gb=50)

                try:
                    logger.info(
                        f"🚀 Creating worker: {worker_id} (Storage: {storage_name}, RAM: {ram_tier} GB)"
                    )
                    pod_details = create_pod_and_wait(
                        api_key=self.api_key,
                        gpu_type_id=gpu_info["id"],
                        image_name=self.worker_image,
                        name=worker_id,
                        network_volume_id=storage_volume_id,
                        volume_mount_path=self.volume_mount_path,
                        disk_in_gb=self.disk_size_gb,
                        container_disk_in_gb=self.container_disk_gb,
                        min_vcpu_count=self.min_vcpu_count,
                        min_memory_in_gb=ram_tier,
                        public_key_string=public_key_content,
                        env_vars=env_vars,
                        template_id=self.template_id,
                    )

                    if pod_details and "id" in pod_details:
                        pod_id = pod_details["id"]
                        logger.info(
                            f"✅ SUCCESS: {worker_id} -> {pod_id} (Storage: {storage_name}, RAM: {ram_tier} GB)"
                        )
                        return {
                            "worker_id": worker_id,
                            "runpod_id": pod_id,
                            "gpu_type": gpu_info["displayName"],
                            "status": "spawning",
                            "created_at": time.time(),
                            "pod_details": pod_details,
                            "ram_tier": ram_tier,
                            "storage_volume": storage_name,
                        }

                    logger.warning(f"⚠️  Storage: {storage_name}, RAM: {ram_tier} GB - No ID returned")
                    last_error = "Pod creation returned no ID"
                except Exception as exc:
                    error_msg = str(exc)
                    if "no longer any instances available" in error_msg.lower():
                        logger.warning(
                            f"⚠️  Storage: {storage_name}, RAM: {ram_tier} GB - No instances available"
                        )
                        last_error = "No instances available"
                    else:
                        logger.warning(f"⚠️  Storage: {storage_name}, RAM: {ram_tier} GB - {error_msg}")
                        last_error = error_msg
                    continue

            if ram_tier != ram_tiers[-1]:
                next_tier = ram_tiers[ram_tiers.index(ram_tier) + 1]
                logger.warning(f"⚠️  {ram_tier}GB RAM not available in any storage, trying {next_tier}GB...")

        logger.error(f"❌ Failed to create pod for worker {worker_id}")
        logger.error(f"   Tried storages: {self.storage_volumes}")
        logger.error(f"   Tried RAM tiers: {ram_tiers}")
        logger.error(f"   Last error: {last_error}")
        return None

    def start_worker_process(self, runpod_id: str, worker_id: str, has_pending_tasks: bool = False) -> bool:
        load_dotenv()

        supabase_url = os.getenv("SUPABASE_URL", "")
        supabase_anon_key = os.getenv("SUPABASE_ANON_KEY", "")
        supabase_service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        replicate_api_token = os.getenv("REPLICATE_API_TOKEN", "")
        max_task_wait_minutes = getattr(self, "max_task_wait_minutes", int(os.getenv("MAX_TASK_WAIT_MINUTES", "5")))

        logger.info(f"Starting worker process on {runpod_id} with worker_id: {worker_id}")
        logger.debug(
            "Environment: SUPABASE_URL=%s, SERVICE_KEY=%s",
            supabase_url,
            "***" if supabase_service_key else "EMPTY",
        )

        startup_script = render_startup_script(
            worker_id=worker_id,
            supabase_url=supabase_url,
            supabase_anon_key=supabase_anon_key,
            supabase_service_key=supabase_service_key,
            replicate_api_token=replicate_api_token,
            max_task_wait_minutes=max_task_wait_minutes,
            has_pending_tasks=has_pending_tasks,
        )
        script_path = f"/tmp/start_worker_{worker_id}.sh"

        logger.info(f"Creating startup script at {script_path} for worker {worker_id}")
        create_script_command = build_create_script_command(script_path, startup_script)
        result = self.execute_command_on_worker(runpod_id, create_script_command, timeout=10)
        if not result or result[0] != 0:
            logger.error(f"Failed to create startup script for worker {worker_id}: {result}")
            return False

        logger.info("Startup script created successfully, launching in background...")
        launch_command = build_launch_command(script_path, worker_id)
        result = self.execute_command_on_worker(runpod_id, launch_command, timeout=10)

        if not result:
            logger.error("Failed to execute worker startup launch command")
            return False

        exit_code, stdout, stderr = result
        if exit_code != 0:
            logger.error(f"Failed to launch startup script: exit code {exit_code}")
            if stderr and stderr.strip():
                logger.error(f"Error: {stderr.strip()}")
            return False

        pid = stdout.strip()
        logger.info(f"✅ Worker {worker_id} startup script launched in background (PID: {pid})")
        logger.info("   Script will run for ~20 minutes to install dependencies")
        logger.info("   Worker will be checked periodically for completion")
        return True

    def check_worker_startup_status(self, worker_id: str, runpod_id: str) -> Dict[str, Any]:
        """Check status of a worker that is currently starting up."""
        try:
            from gpu_orchestrator.database import DatabaseClient

            db = DatabaseClient()
            logs = (
                db.supabase.table("system_logs")
                .select("id")
                .eq("source_type", "worker")
                .eq("worker_id", worker_id)
                .limit(1)
                .execute()
            )

            if logs.data:
                logger.info(f"✅ Worker {worker_id} is now logging - retrieving startup logs")
                log_retrieval_command = build_log_retrieval_command(worker_id)
                result = self.execute_command_on_worker(runpod_id, log_retrieval_command, timeout=15)

                if result and result[1]:
                    startup_logs = result[1]
                    logger.info(f"📋 Startup logs for {worker_id}:\n{startup_logs}")
                    return {
                        "status": "active",
                        "message": "Worker successfully started and logging",
                        "logs": startup_logs,
                    }

                return {
                    "status": "active",
                    "message": "Worker is logging but could not retrieve startup logs",
                    "logs": None,
                }

            check_command = build_startup_status_check_command(worker_id)
            result = self.execute_command_on_worker(runpod_id, check_command, timeout=10)
            if result and result[1]:
                output = result[1]

                if "100%" in output or "No space left" in output.lower():
                    logger.error(f"❌ DISK SPACE ISSUE on {worker_id}:\n{output}")
                    return {
                        "status": "initializing",
                        "message": "Disk space critically low!",
                        "logs": output,
                    }

                logger.info(f"📊 Worker {worker_id} system status:\n{output}")
                if "error" in output.lower() or "fail" in output.lower():
                    logger.warning(f"⚠️ Potential errors in {worker_id} startup:\n{output}")
                    return {
                        "status": "initializing",
                        "message": "Worker still initializing, potential errors detected",
                        "logs": output,
                    }

            return {
                "status": "initializing",
                "message": "Worker still installing dependencies",
                "logs": None,
            }
        except Exception as exc:
            logger.error(f"Error checking worker {worker_id} startup status: {exc}")
            return {
                "status": "unknown",
                "message": f"Error checking status: {exc}",
                "logs": None,
            }

    def terminate_worker(self, runpod_id: str) -> bool:
        try:
            logger.info(f"Terminating pod: {runpod_id}")
            terminate_pod(runpod_id, self.api_key)
            logger.info(f"Pod terminated: {runpod_id}")
            return True
        except Exception as exc:
            logger.error(f"Error terminating pod {runpod_id}: {exc}")
            return False

    def check_and_initialize_worker(self, worker_id: str, runpod_id: str) -> Dict[str, Any]:
        """Check if a spawning worker is ready for worker process startup."""
        try:
            runpod.api_key = self.api_key
            pod_status = runpod.get_pod(runpod_id)

            if pod_status is None:
                logger.warning(f"Pod {runpod_id} status returned None (pod may be provisioning)")
                return {"status": "spawning", "message": "Pod status not available yet"}

            if not isinstance(pod_status, dict):
                logger.error(f"Pod {runpod_id} status returned unexpected type: {type(pod_status)}")
                return {"status": "error", "error": f"Invalid pod status response: {type(pod_status)}"}

            desired_status = pod_status.get("desiredStatus")
            runtime = pod_status.get("runtime", {})
            if runtime is None or not isinstance(runtime, dict):
                if runtime is not None:
                    logger.warning(f"Pod {runpod_id} runtime is not a dict: {type(runtime)}")
                runtime = {}
            runtime.setdefault("ports", [])

            if desired_status == "RUNNING" and runtime.get("ports"):
                logger.info(f"Pod {runpod_id} is running, checking SSH access...")
                ssh_details = get_pod_ssh_details(runpod_id, self.api_key)

                if ssh_details and ssh_details.get("ip") and ssh_details.get("port"):
                    logger.info(f"SSH available for {worker_id}: {ssh_details['ip']}:{ssh_details['port']}")
                    return {
                        "status": "spawning",
                        "ssh_details": ssh_details,
                        "ready": True,
                        "message": "Pod ready, startup script can be launched",
                    }

                ssh_port = None
                ssh_ip = None
                for port_map in runtime.get("ports", []):
                    if port_map.get("privatePort") == 22:
                        ssh_ip = port_map.get("ip")
                        ssh_port = port_map.get("publicPort")
                        break

                if ssh_ip and ssh_port:
                    logger.info(f"SSH details found in runtime for {worker_id}: {ssh_ip}:{ssh_port}")
                    return {
                        "status": "spawning",
                        "ssh_details": {
                            "ip": ssh_ip,
                            "port": ssh_port,
                            "password": "runpod",
                        },
                        "ready": True,
                        "message": "Pod ready, startup script can be launched",
                    }

                logger.warning(f"Pod {runpod_id} is running but SSH details incomplete - waiting...")
                return {"status": "spawning", "message": "Waiting for complete SSH access"}

            if desired_status in ["FAILED", "TERMINATED"]:
                return {"status": "error", "error": f"Pod {desired_status.lower()}"}

            return {"status": "spawning", "message": f"Pod status: {desired_status}"}
        except Exception as exc:
            logger.error(f"Error checking worker {worker_id} (pod {runpod_id}): {exc}")
            logger.error(f"Exception type: {type(exc).__name__}")
            import traceback

            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "error": f"Exception in worker check: {str(exc)}"}


__all__ = ["RunpodLifecycleMixin"]
