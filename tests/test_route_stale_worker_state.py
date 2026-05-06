"""Route-stale worker state derivation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gpu_orchestrator.worker_state import WorkerLifecycle, derive_worker_state
from tests.scaling_decision_helpers import make_config


def _worker(metadata: dict, status: str = "active") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": "worker-1",
        "status": status,
        "created_at": (now - timedelta(minutes=10)).isoformat(),
        "last_heartbeat": now.isoformat(),
        "metadata": {
            "startup_script_launched": True,
            "startup_script_launched_at": (now - timedelta(minutes=9)).isoformat(),
            "ready_for_tasks": True,
            "vram_total_mb": 24000,
            "vram_used_mb": 1000,
            "vram_timestamp": now.isoformat(),
            **metadata,
        },
    }


def test_old_selector_version_worker_is_route_stale_without_health_failure():
    state = derive_worker_state(
        _worker(
            {
                "route_contract": {
                    "selected_backend": "wgp",
                    "selected_profile": "1",
                    "selector_namespace": "production",
                    "selector_version": "old",
                    "worker_contract_version": 1,
                }
            }
        ),
        make_config(selector_version="new"),
        now=datetime.now(timezone.utc),
        has_ever_claimed=False,
        has_active_task=False,
        queued_count=1,
    )

    assert state.lifecycle == WorkerLifecycle.ACTIVE_READY
    assert state.is_route_stale is True
    assert state.should_terminate is False
    assert state.error_code == "STALE_ROUTE_CONTRACT"
    assert state.termination_reason.startswith("STALE_ROUTE_CONTRACT")


def test_active_route_stale_worker_is_drained_not_marked_for_idle_termination():
    state = derive_worker_state(
        _worker(
            {
                "route_contract": {
                    "selected_backend": "wgp",
                    "selected_profile": "1",
                    "selector_namespace": "production",
                    "selector_version": "old",
                    "worker_contract_version": 1,
                }
            }
        ),
        make_config(selector_version="new"),
        now=datetime.now(timezone.utc),
        has_ever_claimed=True,
        has_active_task=True,
        queued_count=1,
    )

    assert state.is_route_stale is True
    assert state.has_active_task is True
    assert state.should_terminate is False
    assert state.termination_reason is None


def test_explicit_worker_pool_mismatch_is_route_stale():
    state = derive_worker_state(
        _worker(
            {
                "route_contract": {
                    "selected_backend": "wgp",
                    "selected_profile": "1",
                    "worker_pool": "gpu-wgp-old",
                    "selector_namespace": "production",
                    "worker_contract_version": 1,
                }
            }
        ),
        make_config(worker_pool="gpu-wgp-production"),
        now=datetime.now(timezone.utc),
        has_ever_claimed=True,
        has_active_task=False,
        queued_count=0,
    )

    assert state.is_route_stale is True
    assert state.termination_reason.startswith("STALE_ROUTE_CONTRACT")


def test_legacy_missing_route_metadata_remains_compatible_with_default_contract():
    state = derive_worker_state(
        _worker({}),
        make_config(selector_version=None),
        now=datetime.now(timezone.utc),
        has_ever_claimed=False,
        has_active_task=False,
        queued_count=1,
    )

    assert state.is_route_stale is False
    assert state.worker_backend == "wgp"
    assert state.worker_profile == "default"
