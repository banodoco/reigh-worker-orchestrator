from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import gpu_orchestrator.runpod as runpod_pkg
from gpu_orchestrator.runpod import api as api_module
from gpu_orchestrator.runpod import client as client_module
from gpu_orchestrator.runpod import lifecycle as lifecycle_module
from gpu_orchestrator.runpod import startup_script as startup_module
from gpu_orchestrator.runpod import storage as storage_module
from gpu_orchestrator.runpod.ssh import SSHClient


@dataclass
class _Response:
    status_code: int
    payload: Any = None
    text: str = ""

    def json(self):
        return self.payload


def test_api_get_network_volumes_prefers_sdk(monkeypatch):
    monkeypatch.setattr(api_module.runpod, "get_network_volumes", lambda: [{"id": "vol-1"}], raising=False)

    volumes = api_module.get_network_volumes("api-key")

    assert volumes == [{"id": "vol-1"}]
    assert api_module.runpod.api_key == "api-key"


def test_api_create_pod_and_wait_handles_nested_response(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_create_pod(**kwargs):
        captured.update(kwargs)
        return {"data": {"podFindAndDeployOnDemand": {"id": "pod-123"}}}

    monkeypatch.setattr(api_module.runpod, "create_pod", _fake_create_pod)

    result = api_module.create_pod_and_wait(
        api_key="api-key",
        gpu_type_id="gpu-id",
        image_name="image-name",
        env_vars={"FOO": "BAR"},
        public_key_string="ssh-rsa AAAA",
    )

    assert result and result["id"] == "pod-123"
    assert captured["env"]["FOO"] == "BAR"
    assert captured["env"]["PUBLIC_KEY"] == "ssh-rsa AAAA"


def test_api_get_pod_ssh_details_uses_sdk_when_available(monkeypatch):
    monkeypatch.setattr(
        api_module.runpod,
        "get_pod",
        lambda _pod_id: {
            "runtime": {
                "sshPassword": "secret",
                "ports": [{"privatePort": 22, "publicPort": 31000, "ip": "1.2.3.4"}],
            }
        },
    )

    details = api_module.get_pod_ssh_details("pod-id", "api-key")

    assert details == {"ip": "1.2.3.4", "port": 31000, "password": "secret"}


def test_storage_expand_network_volume_success(monkeypatch):
    class _StorageClient(storage_module.RunpodStorageMixin):
        api_key = "api-key"

    monkeypatch.setattr(storage_module.requests, "patch", lambda *args, **kwargs: _Response(status_code=200))

    assert _StorageClient()._expand_network_volume("vol-1", 120) is True


def test_startup_script_renderer_replaces_placeholders():
    script = startup_module.render_startup_script(
        worker_id="gpu-worker-1",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=False,
    )

    assert "__WORKER_ID__" not in script
    assert "gpu-worker-1" in script
    assert "--preload-model wan_2_2_i2v_lightning_baseline_2_2_2" in script
    assert 'export MAX_TASK_WAIT_MINUTES="7"' in script


def test_lifecycle_start_worker_process_builds_and_launches(monkeypatch):
    commands: list[str] = []

    class _LifecycleClient(lifecycle_module.RunpodLifecycleMixin):
        def execute_command_on_worker(self, runpod_id: str, command: str, timeout: int = 600):
            commands.append(command)
            if "nohup" in command:
                return (0, "4242\n", "")
            return (0, "", "")

    monkeypatch.setattr(lifecycle_module, "load_dotenv", lambda: None)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service")
    monkeypatch.setenv("REPLICATE_API_TOKEN", "replicate")
    monkeypatch.setenv("MAX_TASK_WAIT_MINUTES", "9")

    ok = _LifecycleClient().start_worker_process("pod-1", "gpu-worker-1")

    assert ok is True
    assert any(command.startswith("cat > ") for command in commands)
    assert any("nohup" in command for command in commands)
    assert any('MAX_TASK_WAIT_MINUTES="9"' in command for command in commands if command.startswith("cat > "))


def test_client_get_ssh_client_prefers_inline_key(monkeypatch):
    monkeypatch.setattr(
        client_module,
        "get_pod_ssh_details",
        lambda runpod_id, api_key: {"ip": "1.2.3.4", "port": 2200, "password": "pw"},
    )
    monkeypatch.setenv("RUNPOD_SSH_PRIVATE_KEY", "inline-private-key")

    client = client_module.RunpodClient("api-key")
    ssh_client = client.get_ssh_client("pod-id")

    assert ssh_client is not None
    assert ssh_client.private_key_content == "inline-private-key"


def test_runpod_init_create_client_uses_env(monkeypatch):
    created: dict[str, Any] = {}

    class _DummyClient:
        def __init__(self, api_key: str):
            created["api_key"] = api_key

    monkeypatch.setenv("RUNPOD_API_KEY", "api-key")
    monkeypatch.setattr(runpod_pkg, "RunpodClient", _DummyClient)

    client = runpod_pkg.create_runpod_client()

    assert isinstance(client, _DummyClient)
    assert created["api_key"] == "api-key"


def test_ssh_client_requires_connect_before_execute():
    ssh_client = SSHClient(hostname="localhost", port=22, username="root")

    with pytest.raises(RuntimeError):
        ssh_client.execute_command("echo hello")
