from gpu_orchestrator.runpod import storage


def test_storage_mixin_exists():
    assert hasattr(storage, "RunpodStorageMixin")
