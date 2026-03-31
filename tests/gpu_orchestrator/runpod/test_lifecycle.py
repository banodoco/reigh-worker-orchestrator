from gpu_orchestrator.runpod import lifecycle


def test_lifecycle_mixin_exists():
    assert hasattr(lifecycle, "RunpodLifecycleMixin")
