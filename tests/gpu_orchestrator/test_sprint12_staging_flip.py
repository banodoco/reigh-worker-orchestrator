from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

aiohttp_stub = ModuleType("aiohttp")
sys.modules.setdefault("aiohttp", aiohttp_stub)
dotenv_stub = ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", dotenv_stub)
supabase_stub = ModuleType("supabase")
supabase_stub.Client = object
supabase_stub.create_client = lambda *args, **kwargs: object()
sys.modules.setdefault("supabase", supabase_stub)

from gpu_orchestrator.control.phases.worker_capacity import WorkerCapacityPhaseMixin
from gpu_orchestrator.database import (
    apply_selected_pool_totals,
    selected_pool_route_filter,
    selected_pool_route_metadata,
)
from gpu_orchestrator.worker_state import (
    CycleSummary,
    TaskCounts,
    WorkerLifecycle,
    calculate_scaling_decision_pure,
    derive_worker_state,
)
from tests.scaling_decision_helpers import make_config


NOW = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("from_contract", "to_contract"),
    [
        (
            {
                "worker_backend": "wgp",
                "worker_profile": "1",
                "worker_pool": "gpu-wgp-staging",
                "selector_namespace": "staging",
                "selector_version": "wgp-before",
                "worker_contract_version": 1,
                "worker_run_id": "run-wgp-before",
            },
            {
                "worker_backend": "vibecomfy",
                "worker_profile": "3",
                "worker_pool": "gpu-vibecomfy-staging",
                "selector_namespace": "staging",
                "selector_version": "vibecomfy-after",
                "worker_contract_version": 1,
                "worker_run_id": "run-vibecomfy-after",
            },
        ),
        (
            {
                "worker_backend": "vibecomfy",
                "worker_profile": "3",
                "worker_pool": "gpu-vibecomfy-staging",
                "selector_namespace": "staging",
                "selector_version": "vibecomfy-before",
                "worker_contract_version": 1,
                "worker_run_id": "run-vibecomfy-before",
            },
            {
                "worker_backend": "wgp",
                "worker_profile": "1",
                "worker_pool": "gpu-wgp-staging",
                "selector_namespace": "staging",
                "selector_version": "wgp-after",
                "worker_contract_version": 1,
                "worker_run_id": "run-wgp-after",
            },
        ),
    ],
)
def test_sprint12_staging_flip_drains_stale_workers_and_counts_only_target_pool(
    monkeypatch, from_contract: dict, to_contract: dict
) -> None:
    _apply_route_env(monkeypatch, to_contract)
    config = make_config(**to_contract, machines_to_keep_idle=0, min_active_gpus=0, max_active_gpus=5)

    task_counts = apply_selected_pool_totals(_task_counts(from_contract, to_contract))
    selected = task_counts["selected_pool_totals"]
    assert selected["route_filter"] == selected_pool_route_filter()
    assert selected["route_keys"] == ["z_image_turbo"]
    assert selected["queued_only"] == 2
    assert selected["active_only"] == 1
    assert selected["blocked_by_capacity"] == 1
    assert selected["potentially_claimable"] == 2

    worker_states = [
        _derive_worker("target-idle", to_contract, config, has_active_task=False),
        _derive_worker("source-idle", from_contract, config, has_active_task=False),
        _derive_worker("source-active", from_contract, config, has_active_task=True),
    ]
    states = {state.worker_id: state for state in worker_states}
    assert states["target-idle"].is_route_stale is False
    assert states["source-idle"].is_route_stale is True
    assert states["source-idle"].termination_reason.startswith("STALE_ROUTE_CONTRACT")
    assert states["source-active"].is_route_stale is True
    assert states["source-active"].has_active_task is True
    assert states["source-active"].should_terminate is False

    decision = calculate_scaling_decision_pure(
        worker_states,
        TaskCounts(
            queued=selected["potentially_claimable"],
            active_cloud=selected["active_only"],
            total=selected["queued_plus_active"],
        ),
        config,
        failure_rate_ok=True,
    )
    assert decision.current_capacity == 1
    assert decision.workers_covering_tasks == 1
    assert decision.uncovered_tasks == 1
    assert decision.workers_to_spawn == 1

    metadata = selected_pool_route_metadata()
    assert metadata["worker_backend"] == to_contract["worker_backend"]
    assert metadata["worker_profile"] == to_contract["worker_profile"]
    assert metadata["worker_pool"] == to_contract["worker_pool"]
    assert metadata["selector_version"] == to_contract["selector_version"]
    assert metadata["worker_run_id"] == to_contract["worker_run_id"]
    assert metadata["route_contract"]["selected_backend"] == to_contract["worker_backend"]

    summary = asyncio.run(_run_stale_route_drain_and_kill(worker_states))
    assert summary.workers_terminated == 1


def _task_counts(from_contract: dict, to_contract: dict) -> dict:
    return {
        "route_totals": [
            {
                "selected_backend": to_contract["worker_backend"],
                "selected_profile": to_contract["worker_profile"],
                "selector_namespace": to_contract["selector_namespace"],
                "selector_version": to_contract["selector_version"],
                "worker_contract_version": to_contract["worker_contract_version"],
                "route_key": "z_image_turbo",
                "queued_only": 2,
                "active_only": 1,
                "blocked_by_capacity": 1,
                "potentially_claimable": 2,
            },
            {
                "selected_backend": from_contract["worker_backend"],
                "selected_profile": from_contract["worker_profile"],
                "selector_namespace": from_contract["selector_namespace"],
                "selector_version": from_contract["selector_version"],
                "worker_contract_version": from_contract["worker_contract_version"],
                "route_key": "z_image_turbo",
                "queued_only": 8,
                "active_only": 3,
                "blocked_by_capacity": 0,
                "potentially_claimable": 8,
            },
            {
                "selected_backend": to_contract["worker_backend"],
                "selected_profile": from_contract["worker_profile"],
                "selector_namespace": to_contract["selector_namespace"],
                "selector_version": to_contract["selector_version"],
                "worker_contract_version": to_contract["worker_contract_version"],
                "route_key": "z_image_turbo",
                "queued_only": 5,
                "active_only": 0,
                "blocked_by_capacity": 0,
                "potentially_claimable": 5,
            },
        ]
    }


def _derive_worker(worker_id: str, contract: dict, config, *, has_active_task: bool):
    worker = {
        "id": worker_id,
        "status": "active",
        "created_at": (NOW - timedelta(minutes=10)).isoformat(),
        "last_heartbeat": (NOW - timedelta(seconds=10)).isoformat(),
        "metadata": {
            "runpod_id": f"pod-{worker_id}",
            "startup_script_launched": True,
            "startup_script_launched_at": (NOW - timedelta(minutes=9)).isoformat(),
            "ready_for_tasks": True,
            "vram_total_mb": 24576,
            "vram_used_mb": 1024,
            "vram_timestamp": NOW.isoformat(),
            "route_contract": contract,
        },
    }
    return derive_worker_state(
        worker,
        config,
        now=NOW,
        has_ever_claimed=True,
        has_active_task=has_active_task,
        queued_count=2,
    )


async def _run_stale_route_drain_and_kill(worker_states) -> CycleSummary:
    class Host(WorkerCapacityPhaseMixin):
        pass

    host = Host()
    host.config = make_config()
    host.runpod = SimpleNamespace()
    host.db = SimpleNamespace(
        get_worker_by_id=AsyncMock(
            return_value={
                "id": "source-idle",
                "metadata": {"runpod_id": "pod-source-idle"},
                "created_at": NOW.isoformat(),
            }
        )
    )
    host._terminate_worker = AsyncMock(return_value=True)
    summary = CycleSummary()

    await host._handle_stale_route_workers(worker_states, summary)

    host.db.get_worker_by_id.assert_awaited_once_with("source-idle")
    host._terminate_worker.assert_awaited_once()
    terminated_worker, = host._terminate_worker.await_args.args
    assert terminated_worker["id"] == "source-idle"
    assert host._terminate_worker.await_args.kwargs["reason"].startswith("STALE_ROUTE_CONTRACT")
    return summary


def _apply_route_env(monkeypatch, contract: dict) -> None:
    monkeypatch.setenv("REIGH_BACKEND", contract["worker_backend"])
    monkeypatch.setenv("REIGH_WORKER_PROFILE", contract["worker_profile"])
    monkeypatch.setenv("REIGH_WORKER_POOL", contract["worker_pool"])
    monkeypatch.setenv("REIGH_SELECTOR_NAMESPACE", contract["selector_namespace"])
    monkeypatch.setenv("REIGH_SELECTOR_VERSION", contract["selector_version"])
    monkeypatch.setenv("REIGH_WORKER_CONTRACT_VERSION", str(contract["worker_contract_version"]))
    monkeypatch.setenv("REIGH_WORKER_RUN_ID", contract["worker_run_id"])
