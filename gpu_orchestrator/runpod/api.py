"""RunPod SDK and HTTP primitives used by the orchestrator."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests
import runpod

logger = logging.getLogger(__name__)


def get_network_volumes(api_key: str) -> list[Dict[str, Any]]:
    """Return the account's RunPod network volumes."""
    runpod.api_key = api_key
    try:
        if hasattr(runpod, "get_network_volumes"):
            volumes = runpod.get_network_volumes()
            return volumes if isinstance(volumes, list) else []

        endpoints = [
            "https://api.runpod.io/v1/networkvolumes",
            "https://api.runpod.io/graphql",
        ]
        headers = {"Authorization": f"Bearer {api_key}"}

        for url in endpoints:
            try:
                if "graphql" in url:
                    query = """
                    query {
                        myself {
                            networkVolumes {
                                id
                                name
                                size
                                dataCenterId
                            }
                        }
                    }
                    """
                    response = requests.post(url, json={"query": query}, headers=headers, timeout=30)
                else:
                    response = requests.get(url, headers=headers, timeout=30)

                if response.status_code != 200:
                    continue

                data = response.json()
                if "data" in data and "myself" in data["data"]:
                    return data["data"]["myself"].get("networkVolumes", [])

                if isinstance(data, list):
                    return data
            except Exception:
                continue

        logger.warning("Could not fetch network volumes from any endpoint")
        return []
    except Exception as exc:
        logger.error(f"Error fetching network volumes: {exc}")
        return []


def find_gpu_type(gpu_display_name: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Find a GPU type by display name or ID."""
    runpod.api_key = api_key
    try:
        gpus = runpod.get_gpus()
    except Exception as exc:
        logger.error(f"Error retrieving GPU list from RunPod: {exc}")
        return None

    for gpu in gpus:
        if gpu_display_name in (gpu.get("displayName"), gpu.get("id")):
            return gpu
    return None


def create_pod_and_wait(
    api_key: str,
    gpu_type_id: str,
    image_name: str,
    name: str = "worker-pod",
    network_volume_id: str | None = None,
    volume_mount_path: str = "/workspace",
    disk_in_gb: int = 20,
    container_disk_in_gb: int = 10,
    wait_timeout: int = 600,
    public_key_string: str | None = None,
    env_vars: Optional[Dict[str, str]] = None,
    min_vcpu_count: int = 8,
    min_memory_in_gb: int = 32,
    template_id: str | None = None,
) -> Optional[Dict[str, Any]]:
    """Create a RunPod pod and return provision metadata immediately."""
    del wait_timeout
    runpod.api_key = api_key

    params: Dict[str, Any] = {
        "name": name,
        "image_name": image_name,
        "gpu_type_id": gpu_type_id,
        "gpu_count": 1,
        "cloud_type": "SECURE",
        "volume_in_gb": disk_in_gb,
        "container_disk_in_gb": container_disk_in_gb,
        "min_vcpu_count": min_vcpu_count,
        "min_memory_in_gb": min_memory_in_gb,
        "ports": "22/tcp,8888/http",
    }

    if template_id:
        params["template_id"] = template_id

    if network_volume_id:
        params["network_volume_id"] = network_volume_id
        params["volume_mount_path"] = volume_mount_path

    pod_env: Dict[str, str] = {}
    if env_vars:
        pod_env.update(env_vars)

    if public_key_string:
        pod_env["PUBLIC_KEY"] = public_key_string

    if pod_env:
        params["env"] = pod_env

    try:
        pod = runpod.create_pod(**params)
    except Exception as exc:
        logger.error(f"Error creating pod: {exc}")
        return None

    if isinstance(pod, dict) and "data" in pod:
        pod_data = pod["data"].get("podFindAndDeployOnDemand", {})
        pod_id = pod_data.get("id")
    else:
        pod_id = pod.get("id") if isinstance(pod, dict) else None

    if not pod_id:
        logger.error("Pod creation failed (no pod ID returned)")
        return None

    logger.info(f"Pod created with ID: {pod_id}")
    return {
        "id": pod_id,
        "desiredStatus": "PROVISIONING",
        "name": name,
        "gpu_type_id": gpu_type_id,
        "created": True,
    }


def get_pod_ssh_details(pod_id: str, api_key: str) -> Optional[Dict[str, Any]]:
    """Return SSH details (ip, port, password) for a running pod."""
    runpod.api_key = api_key

    try:
        status = runpod.get_pod(pod_id)
        if status and isinstance(status, dict):
            runtime = status.get("runtime", {})
            if runtime and isinstance(runtime, dict):
                for port_map in runtime.get("ports", []):
                    if port_map.get("privatePort") == 22:
                        return {
                            "ip": port_map.get("ip"),
                            "port": port_map.get("publicPort"),
                            "password": runtime.get("sshPassword", "runpod"),
                        }
    except Exception as exc:
        logger.warning(f"RunPod SDK get_pod failed for {pod_id}: {exc}")

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        query = f"""
        {{
          pod(input: {{podId: \"{pod_id}\"}}) {{
            id
            desiredStatus
            runtime {{
              ports {{
                ip
                publicPort
                privatePort
                type
              }}
            }}
          }}
        }}
        """

        response = requests.post(
            "https://api.runpod.io/graphql",
            json={"query": query},
            headers=headers,
            timeout=30,
        )
        if response.status_code == 200:
            data = response.json()
            pod = data.get("data", {}).get("pod")
            if pod:
                runtime = pod.get("runtime", {})
                if runtime:
                    for port_map in runtime.get("ports", []):
                        if port_map.get("privatePort") == 22:
                            return {
                                "ip": port_map.get("ip"),
                                "port": port_map.get("publicPort"),
                                "password": "runpod",
                            }
        else:
            logger.warning(f"GraphQL API failed for pod {pod_id}: {response.status_code}")
    except Exception as exc:
        logger.warning(f"GraphQL fallback failed for pod {pod_id}: {exc}")

    logger.error(f"Could not get SSH details for pod {pod_id} via SDK or GraphQL API")
    return None


def terminate_pod(pod_id: str, api_key: str) -> None:
    """Terminate a RunPod pod to stop billing."""
    runpod.api_key = api_key
    try:
        runpod.terminate_pod(pod_id)
    except Exception as exc:
        logger.error(f"Error terminating pod: {exc}")


__all__ = [
    "create_pod_and_wait",
    "find_gpu_type",
    "get_network_volumes",
    "get_pod_ssh_details",
    "terminate_pod",
]
