"""RunPod sync command."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Set, Tuple

from dotenv import load_dotenv


def _load_runpod_module():
    try:
        import runpod
    except ImportError:
        print("❌ Error: runpod module not installed")
        print("   Install with: pip install runpod")
        sys.exit(1)
    return runpod


def _configure_runpod_api(runpod_module: Any) -> None:
    runpod_api_key = os.getenv("RUNPOD_API_KEY")
    if not runpod_api_key:
        print("❌ Error: required RunPod environment variable is not set")
        sys.exit(1)
    runpod_module.api_key = runpod_api_key


def _get_active_runpod_pods(runpod_module: Any) -> List[Dict[str, Any]]:
    runpod_pods = runpod_module.get_pods()
    return [
        pod
        for pod in runpod_pods
        if pod.get("desiredStatus") in ["RUNNING", "PROVISIONING"]
        and (pod.get("name", "").startswith("gpu_") or pod.get("name", "").startswith("gpu-"))
    ]


def _get_active_db_workers(client: Any) -> List[Dict[str, Any]]:
    active_result = client.db.supabase.table("workers").select("*").eq("status", "active").execute()
    spawning_result = client.db.supabase.table("workers").select("*").eq("status", "spawning").execute()
    return (active_result.data or []) + (spawning_result.data or [])


def _partition_sync_diffs(
    active_runpod_pods: List[Dict[str, Any]],
    active_db_workers: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    db_runpod_ids: Set[str] = set()
    for worker in active_db_workers:
        runpod_id = worker.get("metadata", {}).get("runpod_id")
        if runpod_id:
            db_runpod_ids.add(runpod_id)

    orphaned_pods: List[Dict[str, Any]] = []
    runpod_pod_ids: Set[str] = set()
    for pod in active_runpod_pods:
        pod_id = pod.get("id")
        runpod_pod_ids.add(pod_id)
        if pod_id not in db_runpod_ids:
            orphaned_pods.append(pod)

    db_only_workers: List[Dict[str, Any]] = []
    for worker in active_db_workers:
        runpod_id = worker.get("metadata", {}).get("runpod_id")
        if runpod_id and runpod_id not in runpod_pod_ids:
            db_only_workers.append(worker)

    return orphaned_pods, db_only_workers


def _print_summary(
    active_runpod_pods: List[Dict[str, Any]],
    active_db_workers: List[Dict[str, Any]],
    orphaned_pods: List[Dict[str, Any]],
    db_only_workers: List[Dict[str, Any]],
) -> None:
    print("\n" + "=" * 80)
    print("☁️  RUNPOD SYNC STATUS")
    print("=" * 80)
    print("\n📊 Summary:")
    print(f"   RunPod active pods: {len(active_runpod_pods)}")
    print(f"   Database active workers: {len(active_db_workers)}")
    print(f"   Orphaned pods (RunPod only): {len(orphaned_pods)}")
    print(f"   Stale workers (DB only): {len(db_only_workers)}")


def _print_orphaned_pods(orphaned_pods: List[Dict[str, Any]]) -> float:
    if not orphaned_pods:
        print("\n✅ No orphaned pods found!")
        return 0.0

    print("\n🚨 ORPHANED PODS (in RunPod but not tracked in database):")
    print("-" * 80)
    total_cost = 0.0
    for pod in orphaned_pods:
        name = pod.get("name", "unnamed")
        pod_id = pod.get("id", "no-id")
        status = pod.get("desiredStatus", "unknown")
        cost_per_hr = pod.get("costPerHr", 0)
        created = pod.get("createdAt", "unknown")
        print(f"\n   Pod: {name}")
        print(f"   ID: {pod_id}")
        print(f"   Status: {status}")
        print(f"   Cost: ${cost_per_hr}/hr")
        print(f"   Created: {created}")
        total_cost += cost_per_hr

    print(f"\n💰 Total hourly cost of orphaned pods: ${total_cost:.3f}/hr")
    print(f"   Daily waste: ${total_cost * 24:.2f}")
    print(f"   Monthly waste: ${total_cost * 24 * 30:.2f}")
    return total_cost


def _maybe_terminate_orphaned(orphaned_pods: List[Dict[str, Any]], runpod_module: Any, should_terminate: bool) -> None:
    if not orphaned_pods:
        return
    if not should_terminate:
        print("\n💡 To terminate orphaned pods, run:")
        print("   python scripts/debug.py runpod --terminate")
        return

    print(f"\n⚠️  TERMINATING {len(orphaned_pods)} ORPHANED PODS...")
    for pod in orphaned_pods:
        pod_id = pod.get("id")
        print(f"   Terminating {pod_id}...")
        try:
            runpod_module.terminate_pod(pod_id)
            print(f"   ✅ Terminated {pod_id}")
        except Exception as exc:
            print(f"   ❌ Failed to terminate {pod_id}: {exc}")


def _print_db_only_workers(db_only_workers: List[Dict[str, Any]]) -> None:
    if not db_only_workers:
        return
    print("\n⚠️  STALE WORKERS (in database but not in RunPod):")
    print("-" * 80)
    for worker in db_only_workers:
        worker_id = worker["id"]
        status = worker["status"]
        created = worker.get("created_at", "unknown")
        runpod_id = worker.get("metadata", {}).get("runpod_id", "N/A")
        print(f"\n   Worker: {worker_id}")
        print(f"   Status: {status}")
        print(f"   RunPod ID: {runpod_id}")
        print(f"   Created: {created}")
    print("\n💡 These workers should be marked as terminated in the database.")


def run(client, options: dict) -> None:
    """Handle 'debug.py runpod' command."""
    load_dotenv()
    runpod_module = _load_runpod_module()
    _configure_runpod_api(runpod_module)

    try:
        print("🔍 Analyzing RunPod vs Database state...")
        active_runpod_pods = _get_active_runpod_pods(runpod_module)
        active_db_workers = _get_active_db_workers(client)
        orphaned_pods, db_only_workers = _partition_sync_diffs(active_runpod_pods, active_db_workers)

        _print_summary(active_runpod_pods, active_db_workers, orphaned_pods, db_only_workers)
        _print_orphaned_pods(orphaned_pods)
        _maybe_terminate_orphaned(orphaned_pods, runpod_module, options.get("terminate"))
        _print_db_only_workers(db_only_workers)
        print("\n" + "=" * 80)
    except Exception as exc:
        print(f"❌ Error: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
