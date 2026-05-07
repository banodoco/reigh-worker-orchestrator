from __future__ import annotations

import scripts.dashboard as dashboard


def test_dashboard_module_imports_without_database_client_annotation_error() -> None:
    assert callable(dashboard.build_export_payload)


def test_dashboard_export_includes_canary_panels_without_live_database() -> None:
    payload = dashboard.build_export_payload(
        {
            "status": {
                "queued_tasks": 2,
                "task_counts": {
                    "selected_pool_totals": {
                        "queued": 2,
                        "running": 1,
                        "route_keys": ["z_image_turbo"],
                    }
                },
                "claim_suppression": {"suppressed": 1, "reason": "pool_at_capacity"},
                "quota_alerts": [{"kind": "runpod_quota", "severity": "warning"}],
                "preflight_status": {"status": "passed"},
                "warm_cache_status": {"status": "warm"},
            },
            "worker_health": [
                {
                    "id": "worker-1",
                    "status": "active",
                    "health_status": "HEALTHY",
                    "metadata": {
                        "route_key": "z_image_turbo",
                        "worker_backend": "vibecomfy",
                        "worker_profile": "canary",
                        "selector_namespace": "canary",
                        "selector_version": "2026-05-06",
                    },
                },
                {
                    "id": "worker-2",
                    "status": "active",
                    "health_status": "STALE_HEARTBEAT",
                    "route_key": "z_image_turbo",
                    "worker_backend": "vibecomfy",
                    "worker_profile": "canary",
                    "selector_namespace": "canary",
                    "selector_version": "2026-05-06",
                },
            ],
            "tasks": [
                {
                    "id": "task-1",
                    "route_key": "z_image_turbo",
                    "status": "Queued",
                    "worker_backend": "vibecomfy",
                    "worker_profile": "canary",
                    "selector_namespace": "canary",
                    "selector_version": "2026-05-06",
                }
            ],
            "recent_metrics": {"completed_last_hour": 1},
        },
        imported_live_evidence={
            "routes": [
                {
                    "route_key": "video_enhance",
                    "status": "green",
                    "task_id": "task-video",
                }
            ]
        },
        export_timestamp="2026-05-06T12:00:00",
    )

    panels = payload["canary_panels"]
    assert payload["success"] is True
    assert panels["selected_pool_totals"]["route_keys"] == ["z_image_turbo"]
    assert panels["route_totals"]["z_image_turbo"]["by_backend"]["vibecomfy"] == 3
    assert panels["route_totals"]["z_image_turbo"]["by_profile"]["canary"] == 3
    assert panels["route_totals"]["z_image_turbo"]["by_selector"]["canary/2026-05-06"] == 3
    assert panels["route_worker_health"]["z_image_turbo"]["active_workers"] == 2
    assert panels["route_worker_health"]["z_image_turbo"]["stale_workers"] == 1
    assert panels["claim_suppression"]["reason"] == "pool_at_capacity"
    assert panels["quota_alerts"][0]["kind"] == "runpod_quota"
    assert panels["preflight_status"]["status"] == "passed"
    assert panels["warm_cache_status"]["status"] == "warm"
    assert panels["non_rayworker_route_health"]["video_enhance"]["task_id"] == "task-video"


def test_dashboard_export_accepts_dict_non_rayworker_evidence() -> None:
    payload = dashboard.build_export_payload(
        {"status": {}, "worker_health": [], "tasks": []},
        imported_live_evidence={
            "routes": {
                "image-upscale": {
                    "route_key": "image-upscale",
                    "status": "green",
                }
            }
        },
        export_timestamp="2026-05-06T12:00:00",
    )

    assert payload["canary_panels"]["non_rayworker_route_health"]["image-upscale"]["status"] == "green"
