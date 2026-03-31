"""Decomposed RunPod client package with stable public entrypoints."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .api import create_pod_and_wait, find_gpu_type, get_network_volumes, get_pod_ssh_details, terminate_pod
from .client import RunpodClient
from .ssh import SSHClient


def create_runpod_client() -> RunpodClient:
    """Create a RunPod client using environment configuration."""
    api_key = os.getenv("RUNPOD_API_KEY")
    if not api_key:
        raise ValueError("RUNPOD_API_KEY environment variable is required")

    return RunpodClient(api_key)


async def spawn_runpod_gpu(worker_id: str) -> Optional[str]:
    """Spawn a GPU worker and return RunPod pod ID."""
    client = create_runpod_client()
    result = client.spawn_worker(worker_id)
    if result:
        return result["runpod_id"]
    return None


async def terminate_runpod_gpu(runpod_id: str) -> bool:
    """Terminate a GPU worker pod."""
    client = create_runpod_client()
    return client.terminate_worker(runpod_id)


async def get_runpod_status(runpod_id: str) -> Optional[Dict[str, Any]]:
    """Get status for a RunPod worker pod."""
    client = create_runpod_client()
    return client.get_pod_status(runpod_id)


__all__ = [
    "RunpodClient",
    "SSHClient",
    "create_pod_and_wait",
    "create_runpod_client",
    "find_gpu_type",
    "get_network_volumes",
    "get_pod_ssh_details",
    "get_runpod_status",
    "spawn_runpod_gpu",
    "terminate_pod",
    "terminate_runpod_gpu",
]
