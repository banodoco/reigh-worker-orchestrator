from gpu_orchestrator.runpod import api


def test_api_exports():
    assert callable(api.get_network_volumes)
    assert callable(api.create_pod_and_wait)
