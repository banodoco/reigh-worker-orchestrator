from gpu_orchestrator.runpod.storage import RunpodStorageMixin


def test_direct_runpod_storage_module_reference():
    assert RunpodStorageMixin.__name__ == "RunpodStorageMixin"
