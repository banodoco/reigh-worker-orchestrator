"""Async RunPod adapter.

All RunPod-touching public methods stay async except ``generate_worker_id()``.
This layer must not perform Supabase writes; control-flow code keeps ownership
of ``create_worker_record()`` and ``update_worker_status()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime
from io import StringIO
from typing import Any, Optional, TypeAlias

import paramiko
import requests
from dotenv import load_dotenv
from runpod_lifecycle import RunPodConfig, get_network_volumes, launch, terminate
from runpod_lifecycle import api as runpod_api

from gpu_orchestrator.runpod import startup_script

logger = logging.getLogger(__name__)

SSHResult: TypeAlias = tuple[int, str, str] | None

LEGACY_STORAGE_VOLUMES = ("Peter", "EU-NO-1", "EU-CZ-1", "EUR-IS-1")
STORAGE_CHECK_COMMAND = """
            echo "=== WORKSPACE STORAGE ==="
            df -h /workspace 2>/dev/null | tail -1
            echo ""
            echo "=== WORKSPACE USAGE DETAILS ==="
            df -BG /workspace 2>/dev/null | tail -1
            echo ""
            echo "=== LARGEST DIRECTORIES ==="
            du -sh /workspace/*/ 2>/dev/null | sort -rh | head -10
            """
class WorkerSpawnerAdapter:
    def __init__(self, config: Any, db: Any, runpod_config: RunPodConfig) -> None:
        self.config = config
        self.db = db
        self.runpod_config = self._normalize_runpod_config(runpod_config)
        self.api_key = self.runpod_config.api_key
        self.gpu_type = self.runpod_config.gpu_type
        self.max_task_wait_minutes = int(os.getenv("MAX_TASK_WAIT_MINUTES", "5"))

    async def spawn_worker(
        self,
        worker_id: str,
        worker_env: Optional[dict[str, str]] = None,
    ) -> Optional[dict[str, Any]]:
        load_dotenv()
        env_vars = self._build_worker_env(worker_id, worker_env)
        startup_script.render_startup_script(
            worker_id=worker_id,
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", ""),
            supabase_service_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            replicate_api_token=os.getenv("REPLICATE_API_TOKEN", ""),
            max_task_wait_minutes=self.max_task_wait_minutes,
            has_pending_tasks=False,
        )
        launch_config = self.runpod_config.merge(env_vars=env_vars)
        try:
            pod = await launch(launch_config, name=worker_id)
        except Exception as exc:
            logger.error("Failed to launch worker %s: %s", worker_id, exc)
            return None

        pod_status = await self.get_pod_status(pod.id) or {"id": pod.id}
        storage_volume_id = getattr(pod, "_storage_volume", None)
        volumes = await asyncio.to_thread(get_network_volumes, self.api_key)
        storage_name = next(
            (volume.get("name") for volume in volumes if volume.get("id") == storage_volume_id),
            storage_volume_id,
        )
        return {
            "worker_id": worker_id,
            "runpod_id": pod.id,
            "gpu_type": self.gpu_type,
            "status": "spawning",
            "created_at": time.time(),
            "pod_details": pod_status,
            "ram_tier": getattr(pod, "_ram_tier", None),
            "storage_volume": storage_name,
            "storage_volume_id": storage_volume_id,
        }

    async def start_worker_process(
        self,
        runpod_id: str,
        worker_id: str,
        has_pending_tasks: bool = False,
    ) -> bool:
        load_dotenv()
        script_content = startup_script.render_startup_script(
            worker_id=worker_id,
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", ""),
            supabase_service_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            replicate_api_token=os.getenv("REPLICATE_API_TOKEN", ""),
            max_task_wait_minutes=self.max_task_wait_minutes,
            has_pending_tasks=has_pending_tasks,
        )
        script_path = f"/tmp/start_worker_{worker_id}.sh"
        create_command = startup_script.build_create_script_command(script_path, script_content)
        create_result = await self.execute_command_on_worker(runpod_id, create_command, timeout=10)
        if not create_result or create_result[0] != 0:
            logger.error("Failed to create startup script for worker %s: %s", worker_id, create_result)
            return False

        launch_command = startup_script.build_launch_command(script_path, worker_id)
        launch_result = await self.execute_command_on_worker(runpod_id, launch_command, timeout=10)
        if not launch_result:
            logger.error("Failed to launch startup script for worker %s", worker_id)
            return False

        exit_code, _stdout, stderr = launch_result
        if exit_code != 0:
            logger.error("Startup launch failed for %s: %s", worker_id, stderr.strip())
            return False
        return True

    async def check_and_initialize_worker(self, worker_id: str, runpod_id: str) -> dict[str, Any]:
        pod_status = await self.get_pod_status(runpod_id)
        if pod_status is None:
            logger.warning("Pod %s status returned None (pod may still be provisioning)", runpod_id)
            return {"status": "spawning", "message": "Pod status not available yet"}

        desired_status = pod_status.get("desired_status")
        ports = pod_status.get("ports") or []
        if desired_status == "RUNNING" and any(port.get("privatePort") == 22 for port in ports):
            ssh_details = await asyncio.to_thread(runpod_api.get_pod_ssh_details, runpod_id, self.api_key)
            if ssh_details and ssh_details.get("ip") and ssh_details.get("port"):
                return {
                    "status": "spawning",
                    "ssh_details": ssh_details,
                    "ready": True,
                    "message": "Pod ready, startup script can be launched",
                }

            for port_map in ports:
                if port_map.get("privatePort") == 22:
                    return {
                        "status": "spawning",
                        "ssh_details": {
                            "ip": port_map.get("ip"),
                            "port": port_map.get("publicPort"),
                            "password": pod_status.get("ssh_password") or "runpod",
                        },
                        "ready": True,
                        "message": "Pod ready, startup script can be launched",
                    }

            logger.warning("Pod %s is running but SSH details are incomplete", runpod_id)
            return {"status": "spawning", "message": "Waiting for complete SSH access"}

        if desired_status in {"FAILED", "TERMINATED"}:
            return {"status": "error", "error": f"Pod {desired_status.lower()}"}
        return {"status": "spawning", "message": f"Pod status: {desired_status}"}

    async def terminate_worker(self, runpod_id: str) -> bool:
        try:
            await terminate(runpod_id, self.api_key)
            return True
        except Exception as exc:
            logger.error("Error terminating pod %s: %s", runpod_id, exc)
            return False

    async def get_pod_status(self, runpod_id: str) -> Optional[dict[str, Any]]:
        return await asyncio.to_thread(runpod_api.get_pod_status, runpod_id, self.api_key)

    async def execute_command_on_worker(
        self,
        runpod_id: str,
        command: str,
        timeout: int = 600,
    ) -> SSHResult:
        ssh_details = await asyncio.to_thread(runpod_api.get_pod_ssh_details, runpod_id, self.api_key)
        if not ssh_details:
            logger.error("Could not get SSH details for pod %s", runpod_id)
            return None

        try:
            return await asyncio.to_thread(self._execute_ssh_command, ssh_details, command, timeout)
        except Exception as exc:
            logger.error("SSH execution failed for pod %s: %s", runpod_id, exc)
            return None

    def generate_worker_id(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"gpu-{timestamp}-{str(uuid.uuid4())[:8]}"

    async def check_storage_health(
        self,
        storage_name: str,
        volume_id: str | None = None,
        active_runpod_id: str | None = None,
        min_free_gb: int = 50,
        max_percent_used: int = 85,
    ) -> dict[str, Any]:
        volume_id = volume_id or await self.get_storage_volume_id(storage_name)
        if not volume_id or not active_runpod_id:
            return {"healthy": False, "needs_expansion": False, "message": "Missing volume_id or active_runpod_id", "error": True}

        volumes = await asyncio.to_thread(get_network_volumes, self.api_key)
        volume_info = next((v for v in volumes if v.get("id") == volume_id), None)
        api_total_gb = volume_info.get("size") if volume_info else None
        result = await self.execute_command_on_worker(active_runpod_id, STORAGE_CHECK_COMMAND, timeout=30)
        if not result or result[0] != 0:
            return {"healthy": False, "needs_expansion": False, "message": f"SSH check failed: {result}", "error": True}

        raw_output = result[1] or ""
        parsed = self._parse_df_output(raw_output)
        free_gb = parsed["free_gb"]
        percent_used = parsed["percent_used"]
        healthy = percent_used < max_percent_used and free_gb >= min_free_gb
        needs_expansion = not healthy
        if percent_used >= max_percent_used:
            message = f"CRITICAL: {percent_used}% used, only {free_gb}GB free!"
        elif free_gb < min_free_gb:
            message = f"LOW SPACE: Only {free_gb}GB free (need {min_free_gb}GB)"
        else:
            message = f"OK: {free_gb}GB free ({100 - percent_used}% available)"
        return {
            "healthy": healthy,
            "needs_expansion": needs_expansion,
            "total_gb": parsed["total_gb"],
            "used_gb": parsed["used_gb"],
            "free_gb": parsed["free_gb"],
            "percent_used": parsed["percent_used"],
            "message": message,
            "raw_df": raw_output,
            "api_total_gb": api_total_gb,
        }

    async def get_storage_volume_id(self, storage_name: str) -> Optional[str]:
        volumes = await asyncio.to_thread(get_network_volumes, self.api_key)
        for volume in volumes:
            if volume.get("name") == storage_name:
                return volume.get("id")
        return None

    async def expand_network_volume(self, volume_id: str, new_size: int) -> bool:
        return await asyncio.to_thread(self._expand_network_volume_sync, volume_id, new_size)

    def _build_worker_env(
        self,
        worker_id: str,
        worker_env: Optional[dict[str, str]],
    ) -> dict[str, str]:
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        env_vars = dict(self.runpod_config.env_vars)
        env_vars.update(
            {
                "WORKER_ID": worker_id,
                "SUPABASE_URL": supabase_url,
                "SUPABASE_SERVICE_ROLE_KEY": os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
                "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY", ""),
                "REPLICATE_API_TOKEN": os.getenv("REPLICATE_API_TOKEN", ""),
                "SUPABASE_EDGE_COMPLETE_TASK_URL": f"{supabase_url}/functions/v1/complete_task" if supabase_url else "",
                "SUPABASE_EDGE_MARK_FAILED_URL": f"{supabase_url}/functions/v1/mark-task-failed" if supabase_url else "",
            }
        )
        if worker_env:
            env_vars.update(worker_env)
        return env_vars

    @staticmethod
    def _normalize_runpod_config(runpod_config: RunPodConfig) -> RunPodConfig:
        overrides: dict[str, Any] = {}
        if not runpod_config.storage_volumes:
            overrides["storage_volumes"] = LEGACY_STORAGE_VOLUMES
        if "RUNPOD_DISK_SIZE_GB" not in os.environ:
            overrides["disk_size_gb"] = 50
        if "RUNPOD_CONTAINER_DISK_GB" not in os.environ:
            overrides["container_disk_gb"] = 50
        return runpod_config.merge(**overrides) if overrides else runpod_config

    def _expand_network_volume_sync(self, volume_id: str, new_size_gb: int) -> bool:
        try:
            response = requests.patch(
                f"https://rest.runpod.io/v1/networkvolumes/{volume_id}",
                json={"size": new_size_gb},
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=30,
            )
            return response.status_code == 200
        except Exception as exc:
            logger.error("Error expanding volume %s: %s", volume_id, exc)
            return False

    def _execute_ssh_command(
        self,
        ssh_details: dict[str, Any],
        command: str,
        timeout: int,
    ) -> tuple[int, str, str]:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict[str, Any] = {
            "hostname": ssh_details["ip"],
            "port": ssh_details["port"],
            "username": "root",
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        pkey = self._load_pkey()
        if pkey is not None:
            connect_kwargs["pkey"] = pkey
        else:
            connect_kwargs["password"] = ssh_details.get("password", "runpod")

        client.connect(**connect_kwargs)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            channel = stdout.channel
            started_at = time.time()
            while not channel.exit_status_ready():
                if time.time() - started_at > timeout:
                    channel.close()
                    return -1, "", f"Command timed out after {timeout} seconds"
                time.sleep(0.1)
            return channel.recv_exit_status(), stdout.read().decode(), stderr.read().decode()
        finally:
            client.close()

    def _load_pkey(self) -> paramiko.PKey | None:
        if self.runpod_config.ssh_private_key:
            return self._load_private_key(self.runpod_config.ssh_private_key)
        if self.runpod_config.ssh_private_key_path:
            expanded = os.path.expanduser(self.runpod_config.ssh_private_key_path)
            if os.path.exists(expanded):
                return self._load_private_key(expanded, from_file=True)
        return None

    @staticmethod
    def _load_private_key(value: str, from_file: bool = False) -> paramiko.PKey:
        for loader in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                if from_file:
                    return loader.from_private_key_file(value)
                return loader.from_private_key(StringIO(value))
            except Exception:
                continue
        source = value if from_file else "environment variable"
        raise RuntimeError(f"Failed to load private key from {source}")

    @staticmethod
    def _parse_df_output(raw_output: str) -> dict[str, int]:
        parsed = {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent_used": 0}
        for line in raw_output.splitlines():
            stripped = line.strip()
            if "===" in stripped or "/workspace" not in stripped:
                continue
            parts = stripped.split()
            if len(parts) < 5:
                continue
            try:
                parsed["total_gb"] = WorkerSpawnerAdapter._parse_size(parts[1])
                parsed["used_gb"] = WorkerSpawnerAdapter._parse_size(parts[2])
                parsed["free_gb"] = WorkerSpawnerAdapter._parse_size(parts[3])
                parsed["percent_used"] = int(parts[4].rstrip("%"))
                break
            except (ValueError, IndexError):
                continue
        return parsed

    @staticmethod
    def _parse_size(size_value: str) -> int:
        normalized = size_value.strip()
        if normalized.endswith("T"):
            return int(float(normalized[:-1]) * 1024)
        if normalized.endswith("G"):
            return int(float(normalized[:-1]))
        if normalized.endswith("M"):
            return int(float(normalized[:-1]) / 1024)
        if normalized.endswith("K"):
            return 0
        return int(float(normalized))


def create_worker_spawner(config: Any, db: Any) -> WorkerSpawnerAdapter:
    overrides: dict[str, Any] = {}
    if "RUNPOD_STORAGE_VOLUMES" not in os.environ:
        overrides["storage_volumes"] = LEGACY_STORAGE_VOLUMES
    if "RUNPOD_DISK_SIZE_GB" not in os.environ:
        overrides["disk_size_gb"] = 50
    if "RUNPOD_CONTAINER_DISK_GB" not in os.environ:
        overrides["container_disk_gb"] = 50
    runpod_config = RunPodConfig.from_env(**overrides)
    return WorkerSpawnerAdapter(config, db, runpod_config)


__all__ = ["SSHResult", "WorkerSpawnerAdapter", "create_worker_spawner"]
