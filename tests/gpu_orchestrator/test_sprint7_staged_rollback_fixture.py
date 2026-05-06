"""Sprint 7 staged rollback dry-run fixture.

This test intentionally composes the selected-route, capacity, readiness,
artifact/resource cleanup, and telemetry contracts in one canary/rollback
scenario. The subsystem-specific tests cover individual details; this fixture
guards the end-to-end invariants that must hold together during rollout.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gpu_orchestrator.control.phases.worker_capacity import WorkerCapacityPhaseMixin
from gpu_orchestrator.database import apply_selected_pool_totals, selected_pool_route_filter
from gpu_orchestrator.worker_state import (
    CycleSummary,
    TaskCounts,
    WorkerLifecycle,
    calculate_scaling_decision_pure,
    derive_worker_state,
)
from tests.scaling_decision_helpers import make_config


def test_sprint7_staged_rollback_dry_run_fixture(monkeypatch):
    monkeypatch.setenv("REIGH_BACKEND", "vibecomfy")
    monkeypatch.setenv("REIGH_WORKER_PROFILE", "3")
    monkeypatch.setenv("REIGH_WORKER_POOL", "gpu-vibecomfy-canary")
    monkeypatch.setenv("REIGH_SELECTOR_NAMESPACE", "production")
    monkeypatch.setenv("REIGH_SELECTOR_VERSION", "canary-v2")
    monkeypatch.setenv("REIGH_WORKER_CONTRACT_VERSION", "1")
    monkeypatch.setenv("REIGH_WORKER_RUN_ID", "rollout-run-13")

    config = make_config(
        worker_backend="vibecomfy",
        worker_profile="3",
        worker_pool="gpu-vibecomfy-canary",
        selector_namespace="production",
        selector_version="canary-v2",
        worker_run_id="rollout-run-13",
        machines_to_keep_idle=0,
        min_active_gpus=0,
        max_active_gpus=5,
    )

    fixture = _rollback_fixture()

    assert {task["origin"] for task in fixture["queued_tasks"]} == {
        "frontend",
        "api",
        "worker_passthrough",
    }
    assert all(task["params"]["route_contract"]["selector_version"] == "canary-v2" for task in fixture["queued_tasks"])

    counts = apply_selected_pool_totals(fixture["task_counts"])
    selected = counts["selected_pool_totals"]
    assert selected["route_filter"] == selected_pool_route_filter()
    assert selected["queued_only"] == 3
    assert selected["active_only"] == 1
    assert selected["blocked_by_capacity"] == 1
    assert selected["potentially_claimable"] == 3
    assert selected["route_keys"] == ["z_image_turbo"]

    worker_states = [
        _derive_worker(
            entry,
            config,
            has_ever_claimed=entry.get("has_ever_claimed", entry["has_active_task"]),
            has_active_task=entry["has_active_task"],
        )
        for entry in fixture["workers"]
    ]
    states_by_id = {state.worker_id: state for state in worker_states}

    assert states_by_id["new-idle"].lifecycle == WorkerLifecycle.ACTIVE_READY
    assert states_by_id["new-idle"].is_route_stale is False
    assert states_by_id["old-idle"].is_route_stale is True
    assert states_by_id["old-idle"].termination_reason.startswith("STALE_ROUTE_CONTRACT")
    assert states_by_id["rollback-reserve-active"].is_route_stale is True
    assert states_by_id["rollback-reserve-active"].has_active_task is True
    assert states_by_id["rollback-reserve-active"].should_terminate is False

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
    assert decision.uncovered_tasks == 2
    assert decision.workers_to_spawn == 2
    assert decision.spawn_reason_tasks == 2

    summary = asyncio.run(_run_stale_route_drain_and_kill(worker_states))
    assert summary.workers_terminated == 1

    preflight = fixture["preflight"]
    assert preflight["ready_for_tasks"] is True
    assert preflight["preflight_status"] == "passed"
    assert preflight["checks"]["vibecomfy_templates"] == "passed"
    assert preflight["checks"]["wgp_path"] == "passed"

    artifacts = fixture["artifacts"]
    _assert_artifact_contract(artifacts)

    resource_pressure = fixture["resource_pressure"]
    assert resource_pressure["quota_alert"] is True
    assert resource_pressure["cleanup"]["artifacts"]["removed_files"] == 2
    assert resource_pressure["cleanup"]["lora"]["removed_count"] == 1

    telemetry = fixture["telemetry_labels"]
    assert telemetry["backend"] == "vibecomfy"
    assert telemetry["profile"] == "3"
    assert telemetry["route_key"] == "z_image_turbo"
    assert telemetry["template_id"] == "image/z_image"
    assert telemetry["run_id"] == "rollout-run-13"
    assert telemetry["selector_namespace"] == "production"
    assert telemetry["selector_version"] == "canary-v2"
    assert telemetry["preflight_status"] == "passed"
    assert telemetry["disk_status"] == "near_full"
    assert telemetry["quota_alert"] is True
    assert "api_key" not in telemetry
    assert fixture["system_log_metadata"]["heartbeat_labels"]["route_key"] == "z_image_turbo"


def _rollback_fixture() -> dict:
    route_contract = {
        "selected_backend": "vibecomfy",
        "selected_profile": "3",
        "worker_backend": "vibecomfy",
        "worker_profile": "3",
        "worker_pool": "gpu-vibecomfy-canary",
        "selector_namespace": "production",
        "selector_version": "canary-v2",
        "worker_contract_version": 1,
        "route_key": "z_image_turbo",
        "template_id": "image/z_image",
        "route_run_id": "rollout-run-13",
    }
    old_route_contract = {
        **route_contract,
        "selector_version": "stable-v1",
        "route_run_id": "rollback-reserve",
    }
    return {
        "queued_tasks": [
            {"id": "task-frontend", "origin": "frontend", "params": {"route_contract": route_contract}},
            {"id": "task-api", "origin": "api", "params": {"route_contract": route_contract}},
            {
                "id": "task-worker",
                "origin": "worker_passthrough",
                "params": {"route_contract": route_contract},
            },
        ],
        "task_counts": {
            "totals": {"queued_only": 5, "active_only": 2, "queued_plus_active": 7},
            "route_totals": [
                {
                    "selected_backend": "vibecomfy",
                    "selected_profile": "3",
                    "selector_namespace": "production",
                    "selector_version": "canary-v2",
                    "route_key": "z_image_turbo",
                    "worker_contract_version": 1,
                    "queued_only": 3,
                    "active_only": 1,
                    "blocked_by_capacity": 1,
                    "potentially_claimable": 3,
                },
                {
                    "selected_backend": "wgp",
                    "selected_profile": "1",
                    "selector_namespace": "production",
                    "selector_version": "stable-v1",
                    "route_key": "travel_segment",
                    "worker_contract_version": 1,
                    "queued_only": 2,
                    "active_only": 1,
                    "blocked_by_capacity": 0,
                    "potentially_claimable": 2,
                },
            ],
        },
        "workers": [
            {
                "id": "new-idle",
                "route_contract": route_contract,
                "has_ever_claimed": True,
                "has_active_task": False,
            },
            {"id": "old-idle", "route_contract": old_route_contract, "has_active_task": False},
            {
                "id": "rollback-reserve-active",
                "route_contract": old_route_contract,
                "has_active_task": True,
            },
        ],
        "preflight": {
            "ready_for_tasks": True,
            "preflight_status": "passed",
            "checks": {
                "wgp_path": "passed",
                "vibecomfy_templates": "passed",
                "custom_nodes": "passed",
                "model_cache": "passed",
            },
        },
        "artifacts": {
            "final": "user-canary/tasks/task-api/final/output.mp4",
            "intermediate": "user-canary/tasks/task-api/intermediate/frame-cache.bin",
            "thumbnail": "user-canary/tasks/task-api/thumbnail/thumb.jpg",
            "debug_bundle": "user-canary/tasks/task-worker/debug_bundle/debug.zip",
            "lora_cache_metadata": "user-canary/tasks/task-worker/lora_cache_metadata/lora.json",
            "temp": "user-canary/tasks/task-worker/temp/scratch.bin",
            "ttl": {"intermediate_days": 2, "debug_bundle_days": 14},
            "debug_retention": {"retain_debug_bundles": True},
        },
        "resource_pressure": {
            "disk_status": "near_full",
            "quota_alert": True,
            "cleanup": {
                "artifacts": {"removed_files": 2, "recovered_bytes": 2048},
                "lora": {"removed_count": 1, "recovered_bytes": 4096},
            },
        },
        "telemetry_labels": {
            "backend": "vibecomfy",
            "profile": "3",
            "route_key": "z_image_turbo",
            "template_id": "image/z_image",
            "run_id": "rollout-run-13",
            "selector_namespace": "production",
            "selector_version": "canary-v2",
            "preflight_status": "passed",
            "disk_status": "near_full",
            "resource_pressure_status": "recovered",
            "quota_alert": True,
        },
        "system_log_metadata": {
            "heartbeat_labels": {
                "backend": "vibecomfy",
                "profile": "3",
                "route_key": "z_image_turbo",
                "template_id": "image/z_image",
                "run_id": "rollout-run-13",
                "selector_namespace": "production",
                "selector_version": "canary-v2",
                "preflight_status": "passed",
                "disk_status": "near_full",
                "quota_alert": True,
            }
        },
    }


def _assert_artifact_contract(artifacts: dict) -> None:
    expected_paths = {
        "final": "user-canary/tasks/task-api/final/output.mp4",
        "intermediate": "user-canary/tasks/task-api/intermediate/frame-cache.bin",
        "thumbnail": "user-canary/tasks/task-api/thumbnail/thumb.jpg",
        "debug_bundle": "user-canary/tasks/task-worker/debug_bundle/debug.zip",
        "lora_cache_metadata": "user-canary/tasks/task-worker/lora_cache_metadata/lora.json",
        "temp": "user-canary/tasks/task-worker/temp/scratch.bin",
    }
    assert {key for key in artifacts if key not in {"ttl", "debug_retention"}} == set(expected_paths)

    for artifact_class, expected_path in expected_paths.items():
        path = artifacts[artifact_class]
        assert path == expected_path
        assert f"/{artifact_class}/" in path

    assert artifacts["debug_retention"]["retain_debug_bundles"] is True
    assert artifacts["ttl"]["debug_bundle_days"] >= artifacts["ttl"]["intermediate_days"]


def _derive_worker(entry: dict, config, *, has_ever_claimed: bool, has_active_task: bool):
    now = datetime.now(timezone.utc)
    return derive_worker_state(
        {
            "id": entry["id"],
            "status": "active",
            "created_at": (now - timedelta(minutes=10)).isoformat(),
            "last_heartbeat": now.isoformat(),
            "metadata": {
                "runpod_id": f"pod-{entry['id']}",
                "startup_script_launched": True,
                "startup_script_launched_at": (now - timedelta(minutes=9)).isoformat(),
                "ready_for_tasks": True,
                "preflight_status": "passed",
                "vram_total_mb": 24000,
                "vram_used_mb": 1000,
                "vram_timestamp": now.isoformat(),
                "route_contract": entry["route_contract"],
            },
        },
        config,
        now=now,
        has_ever_claimed=has_ever_claimed,
        has_active_task=has_active_task,
        queued_count=3,
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
                "id": "old-idle",
                "metadata": {"runpod_id": "pod-old-idle"},
                "created_at": "2026-05-06T00:00:00+00:00",
            }
        )
    )
    host._terminate_worker = AsyncMock(return_value=True)
    summary = CycleSummary()

    await host._handle_stale_route_workers(worker_states, summary)

    host.db.get_worker_by_id.assert_awaited_once_with("old-idle")
    host._terminate_worker.assert_awaited_once()
    terminated_worker, = host._terminate_worker.await_args.args
    assert terminated_worker["id"] == "old-idle"
    assert host._terminate_worker.await_args.kwargs["reason"].startswith("STALE_ROUTE_CONTRACT")
    return summary
