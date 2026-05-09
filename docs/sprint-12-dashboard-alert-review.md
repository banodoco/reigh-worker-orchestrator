# Sprint 12 Dashboard And Alert Review

This review follows the Sprint 12 dashboard import repair and
`section11-canary.yaml` alert metadata update. The dashboard export is importable
without a live database client, and the alert watch notes now keep unresolved
live-evidence gaps red instead of carrying stale Sprint 11B `NO_GO` wording.

## Dashboard Panels

| Required panel | Export surface | Status |
| --- | --- | --- |
| Selected pool totals | `build_export_payload()["canary_panels"]["selected_pool_totals"]` | present |
| Route totals | `build_export_payload()["canary_panels"]["route_totals"]` | present |
| Route worker health | `build_export_payload()["canary_panels"]["route_worker_health"]` | present |
| Claim suppression | `build_export_payload()["canary_panels"]["claim_suppression"]` | present |
| Quota | `build_export_payload()["canary_panels"]["quota_alerts"]` | present |
| Preflight | `build_export_payload()["canary_panels"]["preflight_status"]` | present |
| Warm cache | `build_export_payload()["canary_panels"]["warm_cache_status"]` | present |
| Non-RayWorker health | `build_export_payload()["canary_panels"]["non_rayworker_route_health"]` | present |

The selected-pool and route-total panels are the primary views for proving a
selector flip: selected-pool totals must count only matching
backend/profile/selector workers and route totals must continue showing stale
or blocked capacity separately.

## Alert Coverage

| Required alert category | Machine-readable source | Status |
| --- | --- | --- |
| Latency | `canary_latency_p95_ratio_high` | present |
| VRAM | `canary_vram_peak_ratio_high` | present |
| OOM | `canary_oom_count_nonzero` | present |
| Output divergence | `canary_output_divergence_detected` | present |
| Missing runtime evidence | `canary_missing_runtime_evidence` | present |
| Stale route workers | `canary_stale_route_workers` | present |
| Quota | `canary_quota_alerts_active` | present |
| Claim suppression | `canary_claim_suppression_active` | present |
| Completion/billing failure | `canary_completion_billing_failure` | present |
| Smoke failure | `canary_non_rayworker_route_smoke_failure` | present |

## Non-RayWorker Routes

The non-RayWorker watch note remains red until real live or staging observations
are attached. The route handler refs are:

| Route key | Handler ref | Smoke state |
| --- | --- | --- |
| `video_enhance` | `handlers/fal.py::handle_video_enhance` | `RED` |
| `image-upscale` | `handlers/fal.py::handle_image_upscale` | `RED` |
| `animate_character` | `handlers/wavespeed.py::handle_animate_character` | `RED` |
| `flux_klein_edit` | `handlers/fal.py::handle_flux_klein_edit` | `RED` |

The dashboard import prerequisite is resolved. Shadow isolation and
non-RayWorker live evidence remain red because this environment does not supply
real live/staging observations.
