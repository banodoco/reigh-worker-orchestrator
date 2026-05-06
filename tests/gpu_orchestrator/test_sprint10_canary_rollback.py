"""Sprint 10 canary rollback exercise fixture.

This extends the Sprint 7 staged rollback coverage with the canary readiness
evidence that must exist before production promotion: concurrent claims,
selector flip with in-flight work, worker restart, cache evidence references,
and rollback exercise metadata accepted by the readiness package contract.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gpu_orchestrator.control.phases.worker_capacity import WorkerCapacityPhaseMixin
from gpu_orchestrator.worker_state import (
    CycleSummary,
    TaskCounts,
    WorkerLifecycle,
    calculate_scaling_decision_pure,
    derive_worker_state,
)
from tests.scaling_decision_helpers import make_config


WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
REIGH_WORKER_ROOT = WORKSPACE_ROOT / "reigh-worker"
SOAK_PATH = REIGH_WORKER_ROOT / "scripts" / "canary_readiness" / "soak.py"

spec = importlib.util.spec_from_file_location("canary_readiness_soak_for_sprint10", SOAK_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Unable to load canary readiness soak module from {SOAK_PATH}")
soak_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = soak_module
spec.loader.exec_module(soak_module)

REQUIRED_SOAK_SCENARIOS = soak_module.REQUIRED_SOAK_SCENARIOS
validate_soak_scenarios = soak_module.validate_soak_scenarios


NOW = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)


def test_sprint10_canary_rollback_exercise_covers_required_operational_gates() -> None:
    fixture = _sprint10_fixture()
    config = make_config(
        worker_backend="wgp",
        worker_profile="1",
        worker_pool="gpu-wgp-production",
        selector_namespace="production",
        selector_version="stable-v1",
        worker_run_id="rollback-run-17",
        machines_to_keep_idle=0,
        min_active_gpus=0,
        max_active_gpus=5,
    )

    selected = _selected_pool_totals(fixture["task_counts"], config)
    assert selected["route_keys"] == ["travel_segment", "z_image_turbo"]
    assert selected["potentially_claimable"] == 7
    assert selected["blocked_by_capacity"] == 0

    worker_states = [
        _derive_worker(
            entry,
            config,
            has_ever_claimed=entry.get("has_ever_claimed", entry["has_active_task"]),
            has_active_task=entry["has_active_task"],
        )
        for entry in fixture["workers"]
    ]
    states = {state.worker_id: state for state in worker_states}
    assert states["stable-ready"].is_route_stale is False
    assert states["stable-restart"].is_route_stale is False
    assert states["canary-idle"].is_route_stale is True
    assert states["canary-idle"].termination_reason.startswith("STALE_ROUTE_CONTRACT")
    assert states["canary-active"].is_route_stale is True
    assert states["canary-active"].has_active_task is True
    assert states["canary-active"].should_terminate is False

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
    assert decision.current_capacity == 2
    assert decision.workers_covering_tasks == 2
    assert decision.uncovered_tasks == 5
    assert decision.desired_workers == 5
    assert decision.workers_to_spawn == 3

    summary = asyncio.run(_run_stale_route_drain_and_kill(worker_states))
    assert summary.workers_terminated == 1

    claims = fixture["concurrent_claims"]
    assert len({claim["task_id"] for claim in claims}) == len(claims)
    assert len({claim["worker_id"] for claim in claims}) == len(claims)
    assert {claim["claim_decision_reason"] for claim in claims} == {"eligible"}
    assert all(claim["selector_version"] == "stable-v1" for claim in claims)

    selector_flip = fixture["selector_flip_in_flight"]
    assert selector_flip["from"]["worker_backend"] == "vibecomfy"
    assert selector_flip["to"]["worker_backend"] == "wgp"
    assert selector_flip["in_flight_task_id"] == "task-canary-active"
    assert selector_flip["in_flight_policy"] == "drain_not_kill"

    restart = fixture["worker_kill_restart"]
    assert restart["killed_worker_id"] == "canary-crashed"
    assert restart["replacement_worker_id"] == "stable-restart"
    assert restart["replacement_route_contract"]["worker_backend"] == "wgp"
    assert restart["evidence_refs"][0]["kind"] == "system_log"

    assert fixture["preflight"]["preflight_status"] == "passed"
    assert fixture["preflight"]["checks"]["wgp_path"] == "passed"
    assert fixture["preflight"]["checks"]["vibecomfy_templates"] == "passed"
    _assert_artifact_contract(fixture["artifacts"])
    assert fixture["resource_pressure"]["quota_alert"] is True
    assert fixture["resource_pressure"]["cleanup"]["artifacts"]["removed_files"] == 2
    assert fixture["telemetry_labels"]["worker_backend"] == "wgp"
    assert fixture["telemetry_labels"]["selector_version"] == "stable-v1"
    assert fixture["telemetry_labels"]["warm_cache_status"] == "warm"
    assert fixture["telemetry_labels"]["preflight_status"] == "passed"

    scenarios = validate_soak_scenarios(fixture["soak_scenarios"], now=NOW)
    assert {scenario["scenario"] for scenario in scenarios} == set(REQUIRED_SOAK_SCENARIOS)
    cold_warm = next(scenario for scenario in scenarios if scenario["scenario"] == "cold_warm_cache")
    assert {ref["kind"] for ref in cold_warm["evidence_refs"]} == {"cold_cache", "warm_cache"}

    accepted = _readiness_rollback_section(fixture["rollback_exercise"])
    assert accepted["status"] == "green"
    assert accepted["evidence_refs"] == ["system_logs:rollback-exercise-17"]


def test_rollback_exercise_metadata_rejects_missing_required_fields() -> None:
    complete = _sprint10_fixture()["rollback_exercise"]
    for field in ("observed_at", "environment", "operator", "source_ref", "status"):
        candidate = dict(complete)
        candidate.pop(field)
        section = _readiness_rollback_section(candidate)
        assert section["status"] == "red"
        assert any(field in reason for reason in section["reasons"])

    failed = dict(complete, status="fail")
    section = _readiness_rollback_section(failed)
    assert section["status"] == "red"
    assert section["reasons"] == ["rollback exercise status is fail"]


def _sprint10_fixture() -> dict:
    stable_contract = {
        "selected_backend": "wgp",
        "selected_profile": "1",
        "worker_backend": "wgp",
        "worker_profile": "1",
        "worker_pool": "gpu-wgp-production",
        "selector_namespace": "production",
        "selector_version": "stable-v1",
        "worker_contract_version": 1,
        "route_run_id": "rollback-run-17",
    }
    canary_contract = {
        "selected_backend": "vibecomfy",
        "selected_profile": "3",
        "worker_backend": "vibecomfy",
        "worker_profile": "3",
        "worker_pool": "gpu-vibecomfy-canary",
        "selector_namespace": "production",
        "selector_version": "canary-v3",
        "worker_contract_version": 1,
        "route_run_id": "canary-run-17",
    }
    return {
        "task_counts": {
            "route_totals": [
                {
                    **stable_contract,
                    "route_key": "travel_segment",
                    "queued_only": 4,
                    "active_only": 1,
                    "blocked_by_capacity": 0,
                    "potentially_claimable": 4,
                },
                {
                    **stable_contract,
                    "route_key": "z_image_turbo",
                    "queued_only": 3,
                    "active_only": 0,
                    "blocked_by_capacity": 0,
                    "potentially_claimable": 3,
                },
                {
                    **canary_contract,
                    "route_key": "z_image_turbo",
                    "queued_only": 2,
                    "active_only": 1,
                    "blocked_by_capacity": 2,
                    "potentially_claimable": 0,
                },
            ],
        },
        "workers": [
            {
                "id": "stable-ready",
                "route_contract": {**stable_contract, "route_key": "travel_segment"},
                "has_ever_claimed": True,
                "has_active_task": False,
            },
            {
                "id": "stable-restart",
                "route_contract": {**stable_contract, "route_key": "z_image_turbo"},
                "has_ever_claimed": True,
                "has_active_task": False,
            },
            {
                "id": "canary-idle",
                "route_contract": {**canary_contract, "route_key": "z_image_turbo"},
                "has_ever_claimed": True,
                "has_active_task": False,
            },
            {
                "id": "canary-active",
                "route_contract": {**canary_contract, "route_key": "z_image_turbo"},
                "has_ever_claimed": True,
                "has_active_task": True,
            },
        ],
        "concurrent_claims": [
            {
                "worker_id": "stable-ready",
                "task_id": "task-stable-1",
                "route_key": "travel_segment",
                "selector_version": "stable-v1",
                "claim_decision_reason": "eligible",
                "source_ref": {"kind": "claim-next-task", "path": "system_logs.metadata.claim"},
            },
            {
                "worker_id": "stable-restart",
                "task_id": "task-stable-2",
                "route_key": "z_image_turbo",
                "selector_version": "stable-v1",
                "claim_decision_reason": "eligible",
                "source_ref": {"kind": "claim-next-task", "path": "system_logs.metadata.claim"},
            },
            {
                "worker_id": "stable-extra",
                "task_id": "task-stable-3",
                "route_key": "travel_segment",
                "selector_version": "stable-v1",
                "claim_decision_reason": "eligible",
                "source_ref": {"kind": "claim-next-task", "path": "system_logs.metadata.claim"},
            },
        ],
        "selector_flip_in_flight": {
            "from": canary_contract,
            "to": stable_contract,
            "in_flight_task_id": "task-canary-active",
            "in_flight_worker_id": "canary-active",
            "in_flight_policy": "drain_not_kill",
            "evidence_refs": [{"kind": "dashboard", "path": "canary_panels.route_worker_health"}],
        },
        "worker_kill_restart": {
            "killed_worker_id": "canary-crashed",
            "replacement_worker_id": "stable-restart",
            "replacement_route_contract": stable_contract,
            "evidence_refs": [{"kind": "system_log", "path": "system_logs.metadata.worker_restart"}],
        },
        "preflight": {
            "preflight_status": "passed",
            "checks": {
                "wgp_path": "passed",
                "vibecomfy_templates": "passed",
                "model_cache": "passed",
            },
        },
        "artifacts": {
            "final": "user-canary/tasks/task-stable-1/final/output.mp4",
            "intermediate": "user-canary/tasks/task-stable-1/intermediate/frame-cache.bin",
            "thumbnail": "user-canary/tasks/task-stable-1/thumbnail/thumb.jpg",
            "debug_bundle": "user-canary/tasks/task-stable-2/debug_bundle/debug.zip",
            "lora_cache_metadata": "user-canary/tasks/task-stable-2/lora_cache_metadata/lora.json",
            "temp": "user-canary/tasks/task-stable-2/temp/scratch.bin",
            "ttl": {"intermediate_days": 2, "debug_bundle_days": 14},
        },
        "resource_pressure": {
            "disk_status": "near_full",
            "quota_alert": True,
            "cleanup": {
                "artifacts": {"removed_files": 2, "recovered_bytes": 4096},
                "lora": {"removed_count": 1, "recovered_bytes": 2048},
            },
        },
        "telemetry_labels": {
            "worker_backend": "wgp",
            "worker_profile": "1",
            "worker_pool": "gpu-wgp-production",
            "selector_namespace": "production",
            "selector_version": "stable-v1",
            "preflight_status": "passed",
            "warm_cache_status": "warm",
            "disk_status": "near_full",
            "quota_alert": True,
        },
        "soak_scenarios": _soak_scenarios(stable_contract),
        "rollback_exercise": {
            "observed_at": NOW.isoformat(),
            "environment": "staging",
            "operator": "sprint10-fixture",
            "source_ref": {
                "kind": "system_logs",
                "id": "rollback-exercise-17",
                "path": "system_logs.metadata.rollback_exercise",
            },
            "status": "pass",
        },
    }


def _soak_scenarios(stable_contract: dict) -> list[dict]:
    base = {
        "observed_at": NOW.isoformat(),
        "status": "pass",
        "route_labels": {
            "route_key": "travel_segment",
            "selector_namespace": stable_contract["selector_namespace"],
            "selector_version": stable_contract["selector_version"],
        },
        "pool_labels": {
            "worker_backend": stable_contract["worker_backend"],
            "worker_pool": stable_contract["worker_pool"],
        },
    }
    evidence_by_scenario = {
        "mixed_pools": [{"kind": "dashboard", "path": "canary_panels.route_totals"}],
        "concurrent_claims": [{"kind": "claim-next-task", "path": "system_logs.metadata.claim"}],
        "selector_flip_in_flight": [{"kind": "dashboard", "path": "canary_panels.route_worker_health"}],
        "worker_kill_restart": [{"kind": "system_log", "path": "system_logs.metadata.worker_restart"}],
        "cold_warm_cache": [
            {"kind": "cold_cache", "path": "workers.metadata.cache.cold_start"},
            {"kind": "warm_cache", "path": "workers.metadata.cache.warm_hit"},
        ],
        "disk_near_full": [{"kind": "resource_pressure", "path": "workers.metadata.resource_pressure"}],
    }
    return [
        {**base, "scenario": scenario, "evidence_refs": evidence_refs}
        for scenario, evidence_refs in evidence_by_scenario.items()
    ]


def _selected_pool_totals(task_counts: dict, config) -> dict:
    selected = {
        "queued_only": 0,
        "active_only": 0,
        "queued_plus_active": 0,
        "blocked_by_capacity": 0,
        "potentially_claimable": 0,
        "route_keys": [],
    }
    for route in task_counts["route_totals"]:
        if route["selected_backend"] != config.worker_backend:
            continue
        if str(route["selected_profile"]) not in {config.worker_profile, "default"}:
            continue
        if route["worker_pool"] != config.worker_pool:
            continue
        if route["selector_namespace"] != config.selector_namespace:
            continue
        if str(route.get("selector_version") or "") != str(config.selector_version or ""):
            continue
        selected["queued_only"] += route["queued_only"]
        selected["active_only"] += route["active_only"]
        selected["blocked_by_capacity"] += route["blocked_by_capacity"]
        selected["potentially_claimable"] += route["potentially_claimable"]
        selected["route_keys"].append(route["route_key"])
    selected["queued_plus_active"] = selected["queued_only"] + selected["active_only"]
    selected["route_keys"].sort()
    return selected


def _derive_worker(entry: dict, config, *, has_ever_claimed: bool, has_active_task: bool):
    return derive_worker_state(
        {
            "id": entry["id"],
            "status": "active",
            "created_at": (NOW - timedelta(minutes=10)).isoformat(),
            "last_heartbeat": NOW.isoformat(),
            "metadata": {
                "runpod_id": f"pod-{entry['id']}",
                "startup_script_launched": True,
                "startup_script_launched_at": (NOW - timedelta(minutes=9)).isoformat(),
                "ready_for_tasks": True,
                "preflight_status": "passed",
                "vram_total_mb": 24000,
                "vram_used_mb": 1000,
                "vram_timestamp": NOW.isoformat(),
                "route_contract": entry["route_contract"],
            },
        },
        config,
        now=NOW,
        has_ever_claimed=has_ever_claimed,
        has_active_task=has_active_task,
        queued_count=7,
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
                "id": "canary-idle",
                "metadata": {"runpod_id": "pod-canary-idle"},
                "created_at": "2026-05-06T00:00:00+00:00",
            }
        )
    )
    host._terminate_worker = AsyncMock(return_value=True)
    summary = CycleSummary()

    await host._handle_stale_route_workers(worker_states, summary)

    host.db.get_worker_by_id.assert_awaited_once_with("canary-idle")
    host._terminate_worker.assert_awaited_once()
    terminated_worker, = host._terminate_worker.await_args.args
    assert terminated_worker["id"] == "canary-idle"
    assert host._terminate_worker.await_args.kwargs["reason"].startswith("STALE_ROUTE_CONTRACT")
    return summary


def _assert_artifact_contract(artifacts: dict) -> None:
    for artifact_class in ("final", "intermediate", "thumbnail", "debug_bundle", "lora_cache_metadata", "temp"):
        path = artifacts[artifact_class]
        assert f"/{artifact_class}/" in path
        assert path.startswith("user-canary/tasks/")
    assert artifacts["ttl"]["debug_bundle_days"] >= artifacts["ttl"]["intermediate_days"]


def _readiness_rollback_section(metadata: dict) -> dict:
    reasons: list[str] = []
    for field in ("observed_at", "environment", "operator", "source_ref", "status"):
        if not metadata.get(field):
            reasons.append(f"{field} is required")

    if metadata.get("status") == "fail":
        reasons.append("rollback exercise status is fail")
    elif metadata.get("status") not in {"pass", "fail", None}:
        reasons.append("status must be pass or fail")

    try:
        datetime.fromisoformat(str(metadata.get("observed_at", "")).replace("Z", "+00:00"))
    except ValueError:
        if metadata.get("observed_at"):
            reasons.append("observed_at must be an ISO-8601 timestamp")

    source_ref = metadata.get("source_ref")
    if source_ref and not isinstance(source_ref, dict):
        reasons.append("source_ref must be structured")

    return {
        "key": "rollback_exercise",
        "status": "red" if reasons else "green",
        "reasons": reasons,
        "evidence_refs": [f"{source_ref['kind']}:{source_ref['id']}"] if isinstance(source_ref, dict) and "id" in source_ref else [],
    }
