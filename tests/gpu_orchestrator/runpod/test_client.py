from gpu_orchestrator.runpod.client import RunpodClient


def test_client_type_exists():
    assert RunpodClient.__name__ == "RunpodClient"
