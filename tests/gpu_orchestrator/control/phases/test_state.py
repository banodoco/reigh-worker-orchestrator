from datetime import datetime, timezone

from gpu_orchestrator.control.phases import state
from gpu_orchestrator.worker_state import WorkerLifecycle, derive_worker_state
from tests.scaling_decision_helpers import make_config


def test_state_phase_mixin_exists():
    assert hasattr(state, "StatePhaseMixin")


def _make_worker(*, startup_phase=None, last_heartbeat=None):
    metadata = {
        "runpod_id": "runpod-worker-1",
        "startup_script_launched": True,
        "startup_script_launched_at": "2026-03-30T00:00:10+00:00",
        "ready_for_tasks": False,
    }
    if startup_phase is not None:
        metadata["startup_phase"] = startup_phase

    return {
        "id": "worker-1",
        "status": "active",
        "created_at": "2026-03-30T00:00:00+00:00",
        "last_heartbeat": last_heartbeat,
        "metadata": metadata,
    }


def _derive_state(*, startup_phase=None, last_heartbeat=None):
    return derive_worker_state(
        worker=_make_worker(startup_phase=startup_phase, last_heartbeat=last_heartbeat),
        config=make_config(),
        now=datetime(2026, 3, 30, 0, 10, 0, tzinfo=timezone.utc),
        has_ever_claimed=False,
        has_active_task=False,
        queued_count=0,
    )


def test_in_startup_phase_cleared_once_heartbeating():
    """Once a worker has a real heartbeat, it's past startup — stale phase metadata is ignored."""
    derived = _derive_state(
        startup_phase="deps_installing",
        last_heartbeat="2026-03-30T00:09:30+00:00",
    )

    assert derived.has_heartbeat is True
    assert derived.in_startup_phase is False
    assert derived.lifecycle == WorkerLifecycle.ACTIVE_INITIALIZING


def test_in_startup_phase_set_without_heartbeat():
    derived = _derive_state(startup_phase="deps_installing", last_heartbeat=None)

    assert derived.has_heartbeat is False
    assert derived.in_startup_phase is True
    assert derived.lifecycle == WorkerLifecycle.SPAWNING_SCRIPT_RUNNING


def test_in_startup_phase_false_when_none():
    derived = _derive_state(startup_phase=None, last_heartbeat=None)

    assert derived.in_startup_phase is False


def test_in_startup_phase_false_when_running():
    derived = _derive_state(
        startup_phase="running",
        last_heartbeat="2026-03-30T00:09:30+00:00",
    )

    assert derived.has_heartbeat is True
    assert derived.in_startup_phase is False
    assert derived.lifecycle == WorkerLifecycle.ACTIVE_INITIALIZING
