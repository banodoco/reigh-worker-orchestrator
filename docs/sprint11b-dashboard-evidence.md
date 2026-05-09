# Sprint 11B Dashboard Evidence

Static redacted dashboard evidence is recorded in `docs/sprint11b-dashboard-evidence.redacted.json`.

This artifact uses the existing dashboard export shape from `scripts/dashboard.py`:

- `selected_pool_totals`
- `route_totals`
- `route_worker_health`
- `claim_suppression`
- `quota_alerts`
- `preflight_status`
- `warm_cache_status`
- `non_rayworker_route_health`

Dashboard validation is `NO_GO` for Sprint 11B because `scripts/dashboard.py` cannot currently be imported: `NameError: name 'DatabaseClient' is not defined` at the `get_system_status(db: DatabaseClient)` annotation. Do not run or modify dashboard code for Sprint 11B until the Sprint 11A prerequisite is fixed.

## Cohort State

The route universe comes from `reigh-worker/scripts/dual_run_compare/migration-thresholds.yaml` at version `0B-2026-05-05`.

| Cohort | Route keys | Evidence state |
| --- | --- | --- |
| A | `z_image_turbo`, `z_image_turbo_i2i`, `qwen_image`, `qwen_image_2512`, `wan_2_2_t2i` | `UNKNOWN` or `NO_GO`; no live promoted VibeComfy cohort was mechanically verified. |
| B | `qwen_image_edit`, `qwen_image_style`, `image_inpaint`, `annotated_image_edit` | `NO_GO`; threshold source keeps these routes WGP-only. |
| E | `individual_travel_segment__model-wan22_i2v__guidance-none__continuity-first_last__profile-default`, `travel_segment__model-wan22_i2v__guidance-none__continuity-first_last__profile-default`, `travel_segment__model-wan22_vace__guidance-vace__continuity-video_source__profile-default`, `join_clips_segment__model-wan22_vace__guidance-vace__continuity-join_bridge__profile-default`, `travel_segment__model-ltx2_distilled__guidance-ltx_anchor__continuity-video_source__profile-default` | `UNKNOWN` or `NO_GO`; Wan/VACE dry-run evidence keeps VACE routes WGP-only. |

Active live cohort, selector namespace/version, promoted route keys, and 48-hour hold windows are `UNKNOWN`. Sprint 11B readiness remains `PAUSED` until live evidence is mechanically verified.

## Non-RayWorker Smoke

The static dashboard export includes `non_rayworker_route_health` entries shaped like `build_non_rayworker_gate` route reports from `reigh-worker/scripts/canary_readiness/non_rayworker.py`.

| Route key | Runtime | Smoke state | Reason |
| --- | --- | --- | --- |
| `video_enhance` | `api_orchestrator` | `red` / `UNKNOWN` | Missing recent live/staging observation. |
| `image-upscale` | `api_orchestrator` | `red` / `UNKNOWN` | Missing recent live/staging observation. |
| `animate_character` | `api_orchestrator` | `red` / `UNKNOWN` | Missing recent live/staging observation. |
| `flux_klein_edit` | `api_orchestrator` | `red` / `UNKNOWN` | Missing recent live/staging observation. |

Fixture registry coverage is present, but fixture-only data is not enough to mark these routes green.
