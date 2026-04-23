"""Storage health check command."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from runpod_lifecycle import get_network_volumes

from gpu_orchestrator.config import OrchestratorConfig
from gpu_orchestrator.worker_spawner import create_worker_spawner
from scripts.debug.client import DebugClient


def _print_volume_catalog(volumes: List[Dict[str, Any]]) -> None:
    print("\n📊 RunPod Network Volumes:\n")
    if not volumes:
        print("   No network volumes found")
        return
    for vol in volumes:
        name = vol.get("name", "Unknown")
        vol_id = vol.get("id", "Unknown")
        size_gb = vol.get("size", 0)
        dc_name = vol.get("dataCenter", {}).get("name", "Unknown")
        print(f"   📁 {name}")
        print(f"      ID: {vol_id}")
        print(f"      Size: {size_gb} GB")
        print(f"      Location: {dc_name}")
        print()


def _group_workers_by_storage(client: DebugClient) -> Dict[str, List[Dict[str, Any]]]:
    workers_result = client.db.supabase.table("workers").select("*").eq("status", "active").execute()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for worker in workers_result.data or []:
        storage = worker.get("metadata", {}).get("storage_volume")
        if not storage:
            continue
        if storage not in grouped:
            grouped[storage] = []
        grouped[storage].append(worker)
    return grouped


def _select_worker_with_runpod_id(storage_workers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for worker in storage_workers:
        if worker.get("metadata", {}).get("runpod_id"):
            return worker
    return None


def _print_health_result(storage_name: str, worker: Dict[str, Any], health: Dict[str, Any]) -> None:
    print(f"   {'=' * 60}")
    print(f"   📦 {storage_name}")
    print(f"   {'=' * 60}")
    print(f"      Checking via worker: {worker['id']}")
    print(f"      RunPod ID: {worker.get('metadata', {}).get('runpod_id')}")
    print()

    if health.get("error"):
        print(f"      ❌ Error: {health.get('message')}")
        print()
        return

    status = "✅ HEALTHY" if health.get("healthy") else "❌ UNHEALTHY"
    print(f"      Status: {status}")
    print(f"      Total: {health.get('total_gb', 'N/A')} GB")
    print(f"      Used: {health.get('used_gb', 'N/A')} GB ({health.get('percent_used', 'N/A')}%)")
    print(f"      Free: {health.get('free_gb', 'N/A')} GB")
    print(f"      Message: {health.get('message')}")
    if health.get("needs_expansion"):
        print()
        print("      ⚠️  NEEDS EXPANSION!")
        print(f"      Run: python scripts/debug.py storage --expand {storage_name}")
    print()


def _check_storage_health(runpod_client: Any, workers_by_storage: Dict[str, List[Dict[str, Any]]]) -> None:
    if not workers_by_storage:
        print("   No workers have storage_volume metadata")
        return
    print(f"   Found workers on {len(workers_by_storage)} storage volume(s): {list(workers_by_storage.keys())}\n")

    for storage_name, storage_workers in workers_by_storage.items():
        volume_id = asyncio.run(runpod_client.get_storage_volume_id(storage_name))
        if not volume_id:
            print(f"   {'=' * 60}")
            print(f"   📦 {storage_name}")
            print(f"   {'=' * 60}")
            print("      ❌ Could not find volume ID")
            print()
            continue

        check_worker = _select_worker_with_runpod_id(storage_workers)
        if not check_worker:
            print(f"   {'=' * 60}")
            print(f"   📦 {storage_name}")
            print(f"   {'=' * 60}")
            print("      ❌ No worker available to SSH")
            print()
            continue

        runpod_id = check_worker.get("metadata", {}).get("runpod_id")
        health = asyncio.run(
            runpod_client.check_storage_health(
                storage_name=storage_name,
                volume_id=volume_id,
                active_runpod_id=runpod_id,
                min_free_gb=int(os.getenv("STORAGE_MIN_FREE_GB", "50")),
                max_percent_used=int(os.getenv("STORAGE_MAX_PERCENT_USED", "85")),
            )
        )
        _print_health_result(storage_name, check_worker, health)


def _expand_storage_if_requested(
    runpod_client: Any,
    expand_target: Optional[str],
    volumes: List[Dict[str, Any]],
) -> None:
    if not expand_target:
        return

    print(f"\n🔧 Expanding storage '{expand_target}'...")
    volume_id = asyncio.run(runpod_client.get_storage_volume_id(expand_target))
    if not volume_id:
        print(f"   ❌ Could not find volume ID for '{expand_target}'")
        return

    volume_info = next((vol for vol in volumes if vol.get("name") == expand_target), None)
    if not volume_info:
        print(f"   ❌ Could not find volume info for '{expand_target}'")
        return

    current_size = volume_info.get("size", 100)
    increment = int(os.getenv("STORAGE_EXPANSION_INCREMENT_GB", "50"))
    new_size = current_size + increment
    print(f"   Current size: {current_size} GB")
    print(f"   New size: {new_size} GB (+{increment} GB)")

    if asyncio.run(runpod_client.expand_network_volume(volume_id, new_size)):
        print(f"   ✅ Successfully expanded to {new_size} GB!")
    else:
        print("   ❌ Expansion failed!")


def run(client: DebugClient, options: dict) -> None:
    """Handle 'debug.py storage' command - check all storage volumes."""
    load_dotenv()
    print("=" * 80)
    print("📦 STORAGE HEALTH CHECK")
    print("=" * 80)

    try:
        runpod_client = create_worker_spawner(OrchestratorConfig.from_env(), client.db)
        volumes = get_network_volumes(runpod_client.api_key)
        _print_volume_catalog(volumes)
        if not volumes:
            print()
            print("=" * 80)
            return

        print("\n🔍 Checking Actual Usage via Active Workers:\n")
        workers_by_storage = _group_workers_by_storage(client)
        if not workers_by_storage:
            print("   No active workers to check storage via SSH")
            print("   (Need an active worker to SSH and check actual disk usage)")
        else:
            _check_storage_health(runpod_client, workers_by_storage)

        _expand_storage_if_requested(runpod_client, options.get("expand"), volumes)
    except Exception as exc:
        print(f"❌ Error checking storage: {exc}")
        if options.get("debug"):
            import traceback

            traceback.print_exc()

    print()
    print("=" * 80)
