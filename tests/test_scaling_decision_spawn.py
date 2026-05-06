"""Scaling decision tests focused on spawn behavior and task coverage."""

from __future__ import annotations

from gpu_orchestrator.worker_state import TaskCounts, WorkerLifecycle, calculate_scaling_decision_pure
from tests.scaling_decision_helpers import make_config, make_worker_state


class TestDoubleSpawnPrevention:
    """Tests for the core double-spawn fix."""

    def test_one_queued_one_spawning_no_new_spawn(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [make_worker_state("spawning-1", WorkerLifecycle.SPAWNING_SCRIPT_RUNNING)]
        task_counts = TaskCounts(queued=1, active_cloud=0, total=1)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 0
        assert decision.uncovered_tasks == 0
        assert decision.workers_covering_tasks == 1

    def test_two_queued_one_spawning_spawns_one(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [make_worker_state("spawning-1", WorkerLifecycle.SPAWNING_SCRIPT_RUNNING)]
        task_counts = TaskCounts(queued=2, active_cloud=0, total=2)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 1
        assert decision.spawn_reason_tasks == 1
        assert decision.uncovered_tasks == 1
        assert decision.workers_covering_tasks == 1


class TestTerminatingWorkersExcluded:
    """Tests for excluding workers marked for termination."""

    def test_terminating_spawning_worker_does_not_cover_task(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [
            make_worker_state(
                "spawning-timeout",
                WorkerLifecycle.SPAWNING_SCRIPT_RUNNING,
                should_terminate=True,
            ),
        ]
        task_counts = TaskCounts(queued=1, active_cloud=0, total=1)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 1
        assert decision.workers_covering_tasks == 0
        assert decision.spawning_count == 0


class TestRouteStaleWorkersExcluded:
    """Tests for excluding stale-route workers from selected-pool capacity."""

    def test_route_stale_idle_worker_does_not_cover_queued_task(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [
            make_worker_state(
                "stale-idle",
                WorkerLifecycle.ACTIVE_READY,
                has_active_task=False,
                is_route_stale=True,
            ),
        ]
        task_counts = TaskCounts(queued=1, active_cloud=0, total=1)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.active_count == 0
        assert decision.current_capacity == 0
        assert decision.workers_covering_tasks == 0
        assert decision.workers_to_spawn == 1


class TestIdleWorkerCoversTask:
    """Tests for idle workers covering queued tasks."""

    def test_idle_worker_covers_queued_task(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [make_worker_state("idle-1", WorkerLifecycle.ACTIVE_READY, has_active_task=False)]
        task_counts = TaskCounts(queued=1, active_cloud=0, total=1)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 0
        assert decision.uncovered_tasks == 0
        assert decision.workers_covering_tasks == 1

    def test_busy_worker_does_not_cover_queued_task(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [make_worker_state("busy-1", WorkerLifecycle.ACTIVE_READY, has_active_task=True)]
        task_counts = TaskCounts(queued=1, active_cloud=0, total=1)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 1
        assert decision.spawn_reason_tasks == 1
        assert decision.uncovered_tasks == 1
        assert decision.workers_covering_tasks == 0

    def test_mixed_idle_spawning_cover_tasks(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [
            make_worker_state("idle-1", WorkerLifecycle.ACTIVE_READY, has_active_task=False),
            make_worker_state("spawning-1", WorkerLifecycle.SPAWNING_SCRIPT_RUNNING),
        ]
        task_counts = TaskCounts(queued=3, active_cloud=0, total=3)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 1
        assert decision.uncovered_tasks == 1
        assert decision.workers_covering_tasks == 2


class TestBufferMaintenance:
    """Tests for proactive buffer/floor maintenance."""

    def test_buffer_spawn_when_no_spawning_in_progress(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=1)
        worker_states = [make_worker_state("busy-1", WorkerLifecycle.ACTIVE_READY, has_active_task=True)]
        task_counts = TaskCounts(queued=0, active_cloud=1, total=1)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 1
        assert decision.spawn_reason_floor == 1
        assert decision.spawn_reason_tasks == 0

    def test_buffer_suppressed_when_spawning_in_progress(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=1)
        worker_states = [
            make_worker_state("busy-1", WorkerLifecycle.ACTIVE_READY, has_active_task=True),
            make_worker_state("spawning-1", WorkerLifecycle.SPAWNING_SCRIPT_RUNNING),
        ]
        task_counts = TaskCounts(queued=0, active_cloud=1, total=1)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 0
        assert decision.spawn_reason_floor == 0

    def test_buffer_suppressed_when_spawning_for_tasks(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=1)
        worker_states = [make_worker_state("busy-1", WorkerLifecycle.ACTIVE_READY, has_active_task=True)]
        task_counts = TaskCounts(queued=2, active_cloud=1, total=3)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.workers_to_spawn == 2
        assert decision.spawn_reason_tasks == 2
        assert decision.spawn_reason_floor == 0

    def test_idle_worker_not_killed_when_only_warm_capacity(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [
            make_worker_state("busy-1", WorkerLifecycle.ACTIVE_READY, has_active_task=True),
            make_worker_state("busy-2", WorkerLifecycle.ACTIVE_READY, has_active_task=True),
            make_worker_state("idle-1", WorkerLifecycle.ACTIVE_READY, has_active_task=False),
        ]
        task_counts = TaskCounts(queued=0, active_cloud=2, total=2)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.busy_count == 2
        assert decision.idle_count == 1
        assert decision.buffer_based_workers == 3
        assert decision.workers_to_terminate == 0

    def test_overcapacity_respects_buffer_minimum(self):
        config = make_config(min_active_gpus=0, machines_to_keep_idle=0)
        worker_states = [
            make_worker_state("busy-1", WorkerLifecycle.ACTIVE_READY, has_active_task=True),
            make_worker_state("busy-2", WorkerLifecycle.ACTIVE_READY, has_active_task=True),
            make_worker_state("idle-1", WorkerLifecycle.ACTIVE_READY, has_active_task=False),
            make_worker_state("idle-2", WorkerLifecycle.ACTIVE_READY, has_active_task=False),
            make_worker_state("idle-3", WorkerLifecycle.ACTIVE_READY, has_active_task=False),
        ]
        task_counts = TaskCounts(queued=0, active_cloud=2, total=2)

        decision = calculate_scaling_decision_pure(worker_states, task_counts, config, failure_rate_ok=True)

        assert decision.busy_count == 2
        assert decision.idle_count == 3
        assert decision.buffer_based_workers == 3
        assert decision.workers_to_terminate == 2
