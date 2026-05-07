# Sprint 11B Review Evidence Crosswalk

This crosswalk makes the review-only criteria mechanically inspectable without changing Sprint 11B production runtime or script code.

## Dashboard Import Prerequisite

Status: `NO_GO`.

Mechanical evidence:

- `reigh-worker-orchestrator/scripts/dashboard.py:61` still evaluates `get_system_status(db: DatabaseClient)` before `DatabaseClient` is defined.
- Local import and dashboard export collection both fail with `NameError: name 'DatabaseClient' is not defined`.
- The readiness manifest records this as `dashboard_import_prerequisite.status = NO_GO`.

Sprint 11B dashboard artifacts are static paused/no-go evidence only:

- `reigh-worker-orchestrator/docs/sprint11b-dashboard-evidence.redacted.json`
- `reigh-worker-orchestrator/docs/sprint11b-dashboard-evidence.md`

## Cohort Scope

Status: `PAUSED`.

The route universe is cohort-scoped from `reigh-worker/scripts/dual_run_compare/migration-thresholds.yaml` version `0B-2026-05-05`.

| Cohort | Route keys | Evidence state |
| --- | --- | --- |
| A | `z_image_turbo`, `z_image_turbo_i2i`, `qwen_image`, `qwen_image_2512`, `wan_2_2_t2i` | `UNKNOWN`/`NO_GO`; no live promoted VibeComfy cohort was mechanically verified. |
| B | `qwen_image_edit`, `qwen_image_style`, `image_inpaint`, `annotated_image_edit` | `NO_GO`; current threshold evidence keeps these WGP-only. |
| E | `individual_travel_segment__model-wan22_i2v__guidance-none__continuity-first_last__profile-default`, `travel_segment__model-wan22_i2v__guidance-none__continuity-first_last__profile-default`, `travel_segment__model-wan22_vace__guidance-vace__continuity-video_source__profile-default`, `join_clips_segment__model-wan22_vace__guidance-vace__continuity-join_bridge__profile-default`, `travel_segment__model-ltx2_distilled__guidance-ltx_anchor__continuity-video_source__profile-default` | `UNKNOWN`/`NO_GO`; live cohort, selector namespace/version, and 48-hour hold windows are not mechanically verified. |

Promotion and rollback remain route-cohort operations, not a broad backend switch.

## Rollback Runbooks

Status: `PAUSED`.

Emergency rollback is documented as a route selector flip back to WGP:

- `reigh-worker/tasks/canary-rollback-worker.md`
- `reigh-worker-orchestrator/tasks/canary-rollback-orchestrator.md`
- `reigh-worker-orchestrator/tasks/canary-rollback-app.md`

The runbooks include selector namespace/version placeholders, WGP pool/profile placeholders, stale VibeComfy worker drain or termination policy, and redacted evidence expectations for selector state, worker pool state, task-count state, claim suppression, completion, and billing.

Rollback PR references remain explicit placeholders because final PR URLs were not mechanically verified:

- `ROLLBACK_PR_WORKER_PLACEHOLDER`
- `ROLLBACK_PR_ORCHESTRATOR_PLACEHOLDER`
- `ROLLBACK_PR_APP_PLACEHOLDER`

## Shadow Isolation

Status: `UNKNOWN`.

No isolated shadow evidence proved absence of completion, billing, upload, or user-visible side effects. Output-divergence auto-rollback therefore remains `NO_GO` and sampled/offline-only until isolated shadow evidence exists.

## Non-RayWorker Smoke

Status: `NO_GO`.

The four active non-RayWorker smoke/watch routes are present in alerts, dashboard evidence, and readiness manifest records:

| Route key | Runtime | Evidence state |
| --- | --- | --- |
| `video_enhance` | `api_orchestrator` | `NO_GO` / `UNKNOWN`; no recent live or staging completion and billing observation was mechanically verified. |
| `image-upscale` | `api_orchestrator` | `NO_GO` / `UNKNOWN`; no recent live or staging completion and billing observation was mechanically verified. |
| `animate_character` | `api_orchestrator` | `NO_GO` / `UNKNOWN`; no recent live or staging completion and billing observation was mechanically verified. |
| `flux_klein_edit` | `api_orchestrator` | `NO_GO` / `UNKNOWN`; no recent live or staging completion and billing observation was mechanically verified. |

The fixture registry proves route coverage, but fixture-only data is not treated as green live evidence.

## Operator Sign-Off

Status: `UNKNOWN`.

No operator or stakeholder sign-off was mechanically discovered. Sprint 11B remains paused/no-go until sign-off and live observation evidence are recorded.

## Secret Handling

Status: `redacted`.

Committed Sprint 11B evidence uses placeholders and redacted paths only. Evidence artifacts must not include service-role keys, API keys, bearer tokens, authorization header values, or live credentials.
