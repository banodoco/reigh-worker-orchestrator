from __future__ import annotations

import scripts.debug.services.system as system_services


def test_system_services_export_redacted_canary_readiness_observations() -> None:
    task = {
        "id": "task-video",
        "task_type": "video_enhance",
        "status": "completed",
        "worker_id": "worker-1",
        "metadata": {
            "selector_namespace": "canary",
            "billing_status": "charged",
            "authorization_header": "Authorization: Bearer secret-token",
            "route_contract": {
                "route_key": "video_enhance",
                "selected_backend": "api_orchestrator",
                "selector_version": "selector-v1",
            },
        },
    }
    worker = {
        "id": "worker-1",
        "status": "active",
        "last_heartbeat": "2026-05-06T11:30:00+00:00",
        "metadata": {
            "pool": "api-shared",
            "warm_cache": {"status": "warm"},
            "preflight": {"status": "pass"},
            "service_role": "service_role_secret",
        },
    }
    log = {
        "id": "log-1",
        "task_id": "task-video",
        "metadata": {"quota_alert": {"status": "clear"}, "api_key": "sk-live-secret"},
    }

    observations = system_services.export_canary_readiness_observations(
        workers=[worker],
        tasks=[task],
        system_logs=[log],
        observed_at="2026-05-06T11:30:00+00:00",
    )

    assert observations[0]["route_key"] == "video_enhance"
    assert observations[0]["completion_evidence"]["handler"] == "complete_task/generation-handlers.ts"
    assert observations[0]["billing_evidence"]["handler"] == "complete_task/billing.ts"
    assert observations[0]["runtime"]["warm_cache"]["status"] == "warm"
    assert observations[0]["runtime"]["preflight"]["status"] == "pass"
    assert observations[0]["source_ref"]["paths"] == [
        "tasks.metadata",
        "workers.metadata",
        "system_logs.metadata",
    ]
    serialized = str(observations)
    assert "secret-token" not in serialized
    assert "service_role_secret" not in serialized
    assert "sk-live-secret" not in serialized
