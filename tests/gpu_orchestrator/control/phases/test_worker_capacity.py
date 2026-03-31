from gpu_orchestrator.control.phases import worker_capacity


def test_worker_capacity_phase_mixin_exists():
    assert hasattr(worker_capacity, "WorkerCapacityPhaseMixin")
