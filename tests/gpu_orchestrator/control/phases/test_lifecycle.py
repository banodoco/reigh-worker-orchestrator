import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from gpu_orchestrator.control.phases.lifecycle import LifecyclePhaseMixin
from gpu_orchestrator.worker_state import CycleSummary, TaskCounts, WorkerLifecycle
from tests.scaling_decision_helpers import make_config, make_worker_state


def test_lifecycle_phase_mixin_exists():
    assert LifecyclePhaseMixin.__name__ == "LifecyclePhaseMixin"


def test_early_desired_respects_buffer_minimum():
    spawning_state = make_worker_state("spawning-1", WorkerLifecycle.SPAWNING_SCRIPT_RUNNING)
    spawning_state = spawning_state.__class__(
        **{
            **spawning_state.__dict__,
            "created_at": datetime.now(timezone.utc) - timedelta(minutes=20),
        }
    )

    class Host(LifecyclePhaseMixin):
        def __init__(self):
            self.config = make_config(
                min_active_gpus=0,
                machines_to_keep_idle=0,
                max_active_gpus=10,
                scale_down_grace_period_sec=0,
                spawning_grace_period_sec=60,
            )
            self.db = SimpleNamespace(update_worker_status=AsyncMock())
            self.runpod = Mock()
            self.last_scale_down_at = None

    host = Host()
    summary = CycleSummary()
    task_counts = TaskCounts(queued=0, active_cloud=0, total=0)

    remaining = asyncio.run(
        host._handle_early_termination([spawning_state], task_counts, summary)
    )

    assert [ws.worker_id for ws in remaining] == ["spawning-1"]
    assert summary.workers_terminated == 0
    host.db.update_worker_status.assert_not_awaited()
    host.runpod.terminate_worker.assert_not_called()
