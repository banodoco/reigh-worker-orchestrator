"""RunPod client coordinator built from focused mixins."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import runpod

from .api import find_gpu_type, get_network_volumes, get_pod_ssh_details
from .lifecycle import RunpodLifecycleMixin
from .ssh import SSHClient
from .storage import RunpodStorageMixin

logger = logging.getLogger(__name__)


class RunpodClient(RunpodStorageMixin, RunpodLifecycleMixin):
    """Client for managing RunPod GPU instances."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        runpod.api_key = api_key

        self.gpu_type = os.getenv("RUNPOD_GPU_TYPE", "NVIDIA GeForce RTX 4090")
        self.worker_image = os.getenv(
            "RUNPOD_WORKER_IMAGE",
            "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04",
        )
        self.template_id = os.getenv("RUNPOD_TEMPLATE_ID", "runpod-torch-v240")
        self.storage_name = os.getenv("RUNPOD_STORAGE_NAME")
        self.volume_mount_path = os.getenv("RUNPOD_VOLUME_MOUNT_PATH", "/workspace")
        self.disk_size_gb = int(os.getenv("RUNPOD_DISK_SIZE_GB", "50"))
        self.container_disk_gb = int(os.getenv("RUNPOD_CONTAINER_DISK_GB", "50"))
        self.min_vcpu_count = int(os.getenv("RUNPOD_MIN_VCPU_COUNT", "8"))
        self.min_memory_gb = int(os.getenv("RUNPOD_MIN_MEMORY_GB", "32"))
        self.max_task_wait_minutes = int(os.getenv("MAX_TASK_WAIT_MINUTES", "5"))

        self.storage_volumes = ["Peter", "EU-NO-1", "EU-CZ-1", "EUR-IS-1"]

        self.ram_tiers_enabled = os.getenv("RUNPOD_RAM_TIER_FALLBACK", "true").lower() == "true"
        self.ram_tiers = [72, 60, 48, 32, 16]

        self.ssh_public_key_path = os.getenv("RUNPOD_SSH_PUBLIC_KEY_PATH")
        self.ssh_private_key_path = os.getenv("RUNPOD_SSH_PRIVATE_KEY_PATH")

        self._storage_volume_id: Optional[str] = None
        self._gpu_type_info: Optional[Dict[str, Any]] = None

    def _get_gpu_type_info(self) -> Optional[Dict[str, Any]]:
        if self._gpu_type_info is not None:
            return self._gpu_type_info

        self._gpu_type_info = find_gpu_type(self.gpu_type, self.api_key)
        if self._gpu_type_info:
            logger.info(
                "Found GPU type: %s (ID: %s)",
                self._gpu_type_info.get("displayName"),
                self._gpu_type_info.get("id"),
            )
        else:
            logger.error(f"GPU type '{self.gpu_type}' not found")

        return self._gpu_type_info

    def _get_public_key_content(self) -> Optional[str]:
        public_key_env = os.getenv("RUNPOD_SSH_PUBLIC_KEY")
        if public_key_env:
            logger.info("[SSH_DEBUG] Using SSH public key from RUNPOD_SSH_PUBLIC_KEY environment variable")
            logger.info(f"[SSH_DEBUG] Key preview: {public_key_env[:50]}...{public_key_env[-20:]}")
            return public_key_env.strip()

        if not self.ssh_public_key_path:
            logger.warning(
                "No SSH public key configured. Set RUNPOD_SSH_PUBLIC_KEY environment variable or RUNPOD_SSH_PUBLIC_KEY_PATH"
            )
            return None

        pub_path = os.path.expanduser(self.ssh_public_key_path)
        if not os.path.exists(pub_path):
            logger.warning(f"SSH public key not found at {pub_path}")
            return None

        try:
            with open(pub_path, "r", encoding="utf-8") as handle:
                logger.debug(f"Using SSH public key from file: {pub_path}")
                return handle.read().strip()
        except Exception as exc:
            logger.error(f"Error reading SSH public key: {exc}")
            return None

    def get_pod_status(self, runpod_id: str) -> Optional[Dict[str, Any]]:
        runpod.api_key = self.api_key
        try:
            status = runpod.get_pod(runpod_id)
            if not status:
                return None

            runtime = status.get("runtime", {})
            return {
                "runpod_id": runpod_id,
                "desired_status": status.get("desiredStatus"),
                "actual_status": status.get("actualStatus"),
                "ip": runtime.get("ip"),
                "ports": runtime.get("ports", []),
                "ssh_password": runtime.get("sshPassword"),
                "created_at": status.get("createdAt"),
                "last_status_change": status.get("lastStatusChange"),
                "uptime_seconds": runtime.get("uptimeInSeconds", 0),
                "cost_per_hr": status.get("costPerHr"),
            }
        except Exception as exc:
            logger.error(f"Error getting pod status for {runpod_id}: {exc}")
            return None

    def get_ssh_client(self, runpod_id: str) -> Optional[SSHClient]:
        logger.info(f"🔐 SSH_AUTH [Pod {runpod_id}] Getting SSH client - starting authentication flow")

        ssh_details = get_pod_ssh_details(runpod_id, self.api_key)
        if not ssh_details:
            logger.error(f"🔐 SSH_AUTH [Pod {runpod_id}] ❌ FAILED: Could not get SSH details from RunPod API")
            return None

        logger.info(
            f"🔐 SSH_AUTH [Pod {runpod_id}] SSH details obtained - IP: {ssh_details['ip']}, Port: {ssh_details['port']}"
        )

        private_key_env = os.getenv("RUNPOD_SSH_PRIVATE_KEY")
        public_key_env = os.getenv("RUNPOD_SSH_PUBLIC_KEY")
        private_key_path_env = os.getenv("RUNPOD_SSH_PRIVATE_KEY_PATH")

        logger.info(f"🔐 SSH_AUTH [Pod {runpod_id}] Environment check:")
        logger.info(
            f"🔐 SSH_AUTH [Pod {runpod_id}]   - inline material: {'✅ SET' if private_key_env else '❌ MISSING'}"
        )
        logger.info(
            f"🔐 SSH_AUTH [Pod {runpod_id}]   - public material: {'✅ SET' if public_key_env else '❌ MISSING'}"
        )
        logger.info(
            f"🔐 SSH_AUTH [Pod {runpod_id}]   - file material: {'✅ SET' if private_key_path_env else '❌ MISSING'}"
        )

        if private_key_env:
            logger.info(f"🔐 SSH_AUTH [Pod {runpod_id}] ✅ Using inline material from environment")
            return SSHClient(
                hostname=ssh_details["ip"],
                port=ssh_details["port"],
                username="root",
                private_key_content=private_key_env,
            )

        if self.ssh_private_key_path and os.path.exists(os.path.expanduser(self.ssh_private_key_path)):
            logger.info(f"🔐 SSH_AUTH [Pod {runpod_id}] ✅ Using file-based material")
            return SSHClient(
                hostname=ssh_details["ip"],
                port=ssh_details["port"],
                username="root",
                private_key_path=self.ssh_private_key_path,
            )

        logger.warning(f"🔐 SSH_AUTH [Pod {runpod_id}] ⚠️  Falling back to alternate login mode")
        logger.warning(
            f"🔐 SSH_AUTH [Pod {runpod_id}] This will likely fail because file-based auth is preferred"
        )
        return SSHClient(
            hostname=ssh_details["ip"],
            port=ssh_details["port"],
            username="root",
            password=ssh_details.get("password", "runpod"),
        )

    def execute_command_on_worker(
        self,
        runpod_id: str,
        command: str,
        timeout: int = 600,
    ) -> Optional[tuple[int, str, str]]:
        logger.info(f"🔐 SSH_EXEC [Pod {runpod_id}] Executing command: {command[:100]}...")

        ssh_client = self.get_ssh_client(runpod_id)
        if not ssh_client:
            logger.error(f"🔐 SSH_EXEC [Pod {runpod_id}] ❌ FAILED: Could not get SSH client")
            return None

        try:
            logger.info(f"🔐 SSH_EXEC [Pod {runpod_id}] Attempting SSH connection...")
            ssh_client.connect()
            logger.info(f"🔐 SSH_EXEC [Pod {runpod_id}] ✅ SSH connection successful!")

            exit_code, stdout, stderr = ssh_client.execute_command(command, timeout)
            logger.info(f"🔐 SSH_EXEC [Pod {runpod_id}] Command completed - Exit code: {exit_code}")
            if stderr:
                logger.warning(f"🔐 SSH_EXEC [Pod {runpod_id}] Command stderr: {stderr[:200]}...")
            return exit_code, stdout, stderr
        except Exception as exc:
            logger.error(f"🔐 SSH_EXEC [Pod {runpod_id}] ❌ SSH EXECUTION FAILED: {exc}")
            logger.error(f"🔐 SSH_EXEC [Pod {runpod_id}] This indicates SSH authentication or connection issues")
            return None
        finally:
            ssh_client.disconnect()

    def get_network_volumes(self) -> list[Dict[str, Any]]:
        return get_network_volumes(self.api_key)

    def generate_worker_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"gpu-{timestamp}-{str(uuid.uuid4())[:8]}"


__all__ = ["RunpodClient"]
