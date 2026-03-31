from gpu_orchestrator.runpod.lifecycle import RunpodLifecycleMixin


def test_direct_runpod_lifecycle_module_reference():
    assert RunpodLifecycleMixin.__name__ == "RunpodLifecycleMixin"
