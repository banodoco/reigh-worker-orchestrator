import sys
from types import ModuleType

aiohttp_stub = ModuleType("aiohttp")
sys.modules.setdefault("aiohttp", aiohttp_stub)
dotenv_stub = ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", dotenv_stub)
supabase_stub = ModuleType("supabase")
supabase_stub.Client = object
supabase_stub.create_client = lambda *args, **kwargs: object()
sys.modules.setdefault("supabase", supabase_stub)

from gpu_orchestrator.database import apply_selected_pool_totals, selected_pool_route_filter


def test_selected_pool_totals_match_backend_profile_selector(monkeypatch):
    monkeypatch.setenv("REIGH_BACKEND", "vibecomfy")
    monkeypatch.setenv("REIGH_WORKER_PROFILE", "3")
    monkeypatch.setenv("REIGH_SELECTOR_NAMESPACE", "canary")
    monkeypatch.setenv("REIGH_SELECTOR_VERSION", "42")
    monkeypatch.setenv("REIGH_WORKER_CONTRACT_VERSION", "1")

    data = apply_selected_pool_totals(
        {
            "totals": {"queued_only": 10, "active_only": 0, "queued_plus_active": 10},
            "route_totals": [
                {
                    "selected_backend": "vibecomfy",
                    "selected_profile": "3",
                    "selector_namespace": "canary",
                    "selector_version": "42",
                    "route_key": "z_image_turbo",
                    "worker_contract_version": 1,
                    "queued_only": 2,
                    "active_only": 1,
                    "blocked_by_capacity": 1,
                    "potentially_claimable": 3,
                },
                {
                    "selected_backend": "wgp",
                    "selected_profile": "default",
                    "selector_namespace": "canary",
                    "selector_version": "42",
                    "route_key": "travel_segment",
                    "worker_contract_version": 1,
                    "queued_only": 8,
                    "active_only": 0,
                    "potentially_claimable": 8,
                },
            ],
        }
    )

    assert data["selected_pool_totals"]["queued_only"] == 2
    assert data["selected_pool_totals"]["active_only"] == 1
    assert data["selected_pool_totals"]["queued_plus_active"] == 3
    assert data["selected_pool_totals"]["blocked_by_capacity"] == 1
    assert data["selected_pool_totals"]["potentially_claimable"] == 3
    assert data["selected_pool_totals"]["route_keys"] == ["z_image_turbo"]


def test_selected_pool_profile_default_is_compatible_with_profile_one(monkeypatch):
    monkeypatch.setenv("REIGH_BACKEND", "wgp")
    monkeypatch.setenv("REIGH_WORKER_PROFILE", "1")
    monkeypatch.setenv("REIGH_SELECTOR_NAMESPACE", "production")
    monkeypatch.delenv("REIGH_SELECTOR_VERSION", raising=False)

    data = apply_selected_pool_totals(
        {
            "totals": {"queued_only": 1, "active_only": 0, "queued_plus_active": 1},
            "route_totals": [
                {
                    "selected_backend": "wgp",
                    "selected_profile": "default",
                    "selector_namespace": "production",
                    "selector_version": None,
                    "route_key": "legacy_custom_task",
                    "worker_contract_version": 1,
                    "queued_only": 1,
                    "active_only": 0,
                    "potentially_claimable": 1,
                }
            ],
        }
    )

    assert data["selected_pool_totals"]["queued_only"] == 1
    assert selected_pool_route_filter()["worker_profile"] == "1"
