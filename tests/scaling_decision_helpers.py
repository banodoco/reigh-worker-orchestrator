"""Shared fixtures/builders for scaling-decision tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gpu_orchestrator.config import OrchestratorConfig
from gpu_orchestrator.worker_state import DerivedWorkerState, WorkerLifecycle


def make_config(**overrides) -> OrchestratorConfig:
    """Create a config for testing."""
    config_values = {
        "min_active_gpus": 0,
        "max_active_gpus": 10,
        "machines_to_keep_idle": 1,
        "tasks_per_gpu_threshold": 3,
        "scale_up_multiplier": 1.0,
        "scale_down_multiplier": 0.9,
        "min_scaling_interval_sec": 45,
        "spawning_grace_period_sec": 180,
        "scale_down_grace_period_sec": 60,
        "spawning_timeout_sec": 600,
        "script_running_timeout_sec": 1800,
        "gpu_idle_timeout_sec": 600,
        "overcapacity_idle_timeout_sec": 30,
        "task_stuck_timeout_sec": 1200,
        "graceful_shutdown_timeout_sec": 600,
        "excluded_worker_max_lifetime_sec": 7200,
        "startup_grace_period_sec": 600,
        "ready_not_claiming_timeout_sec": 180,
        "gpu_not_detected_timeout_sec": 300,
        "heartbeat_promotion_threshold_sec": 90,
        "max_worker_failure_rate": 0.8,
        "failure_window_minutes": 5,
        "min_workers_for_rate_check": 5,
        "storage_check_interval_cycles": 10,
        "storage_min_free_gb": 50,
        "storage_max_percent_used": 85,
        "storage_expansion_increment_gb": 50,
        "error_cleanup_grace_period_sec": 600,
        "orchestrator_poll_sec": 30,
        "max_consecutive_task_failures": 3,
        "task_failure_window_minutes": 30,
        "auto_start_worker_process": True,
        "use_new_scaling_logic": True,
        "worker_backend": "wgp",
        "worker_profile": "1",
        "worker_pool": "gpu-wgp-production",
        "selector_namespace": "production",
        "selector_version": None,
        "worker_contract_version": 1,
        "worker_run_id": None,
    }
    config_values.update(overrides)
    return OrchestratorConfig(**config_values)


def make_worker_state(
    worker_id: str = "worker-1",
    lifecycle: WorkerLifecycle = WorkerLifecycle.ACTIVE_READY,
    has_active_task: bool = False,
    should_terminate: bool = False,
    in_startup_phase: bool = False,
    is_route_stale: bool = False,
    excluded_from_capacity_control: bool = False,
    max_lifetime_sec: int | None = None,
) -> DerivedWorkerState:
    """Create a mock worker state for testing."""
    now = datetime.now(timezone.utc)
    return DerivedWorkerState(
        worker_id=worker_id,
        runpod_id=f"runpod-{worker_id}",
        lifecycle=lifecycle,
        db_status="active" if lifecycle.value.startswith("active") else "spawning",
        created_at=now - timedelta(minutes=5),
        script_launched_at=now - timedelta(minutes=4),
        effective_age_sec=240,
        heartbeat_age_sec=10 if lifecycle.value.startswith("active") else None,
        has_heartbeat=lifecycle.value.startswith("active"),
        heartbeat_is_recent=lifecycle.value.startswith("active"),
        vram_total_mb=24000 if lifecycle == WorkerLifecycle.ACTIVE_READY else None,
        vram_used_mb=1000 if lifecycle == WorkerLifecycle.ACTIVE_READY else None,
        vram_reported=lifecycle == WorkerLifecycle.ACTIVE_READY,
        ready_for_tasks=lifecycle == WorkerLifecycle.ACTIVE_READY,
        in_startup_phase=in_startup_phase,
        has_ever_claimed_task=has_active_task,
        has_active_task=has_active_task,
        worker_backend="wgp",
        worker_profile="1",
        worker_pool="gpu-wgp-production",
        selector_namespace="production",
        selector_version=None,
        worker_contract_version=1,
        worker_run_id=None,
        is_gpu_broken=False,
        is_not_claiming=False,
        is_stale=False,
        is_route_stale=is_route_stale,
        should_promote_to_active=False,
        should_terminate=should_terminate,
        termination_reason=None,
        error_code=None,
        excluded_from_capacity_control=excluded_from_capacity_control,
        max_lifetime_sec=max_lifetime_sec,
    )
