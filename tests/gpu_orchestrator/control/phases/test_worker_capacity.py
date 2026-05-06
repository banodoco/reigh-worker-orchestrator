import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gpu_orchestrator.control.phases import worker_capacity
from gpu_orchestrator.control.phases.worker_capacity import WorkerCapacityPhaseMixin
from gpu_orchestrator.worker_state import CycleSummary, ScalingDecision, TaskCounts, WorkerLifecycle
from tests.scaling_decision_helpers import make_config, make_worker_state


def test_worker_capacity_phase_mixin_exists():
    assert hasattr(worker_capacity, "WorkerCapacityPhaseMixin")


def test_worker_capacity_terminates_idle_route_stale_worker_and_drains_busy_worker():
    asyncio.run(_run_stale_route_capacity_case())


async def _run_stale_route_capacity_case():
    class Host(WorkerCapacityPhaseMixin):
        pass

    host = Host()
    host.config = make_config()
    host.runpod = SimpleNamespace()
    host.db = SimpleNamespace(
        get_worker_by_id=AsyncMock(
            return_value={
                "id": "stale-idle",
                "metadata": {"runpod_id": "pod-stale-idle"},
                "created_at": "2026-05-06T00:00:00+00:00",
            }
        )
    )
    host._terminate_worker = AsyncMock(return_value=True)
    summary = CycleSummary()
    decision = ScalingDecision(
        desired_workers=0,
        current_capacity=0,
        active_count=0,
        spawning_count=0,
        idle_count=0,
        busy_count=0,
        task_based_workers=0,
        buffer_based_workers=0,
        min_based_workers=0,
        workers_to_spawn=0,
        workers_to_terminate=0,
        failure_rate_ok=True,
        at_max_capacity=False,
        at_min_capacity=True,
    )

    await host._execute_scaling(
        decision,
        [
            make_worker_state(
                "stale-idle",
                WorkerLifecycle.ACTIVE_READY,
                has_active_task=False,
                is_route_stale=True,
            ),
            make_worker_state(
                "stale-busy",
                WorkerLifecycle.ACTIVE_READY,
                has_active_task=True,
                is_route_stale=True,
            ),
        ],
        TaskCounts(queued=0, active_cloud=0, total=0),
        summary,
    )

    assert summary.workers_terminated == 1
    host.db.get_worker_by_id.assert_awaited_once_with("stale-idle")
    host._terminate_worker.assert_awaited_once()
    terminated_worker, = host._terminate_worker.await_args.args
    assert terminated_worker["id"] == "stale-idle"
    assert host._terminate_worker.await_args.kwargs["reason"].startswith("STALE_ROUTE_CONTRACT")
