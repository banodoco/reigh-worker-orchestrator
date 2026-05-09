# Sprint 11B Canary Rollback Runbook - App And Shared Routes

## Purpose

Protect app-facing completion, billing, claim, and active non-RayWorker routes during Sprint 11B route-cohort canary. This runbook owns alert anchors for app/shared-path failures referenced by `config/alerts/section11-canary.yaml`.

Current mechanical state:

- Active live canary cohort: `UNKNOWN`.
- Non-RayWorker live/staging observations: `UNKNOWN`; fixtures exist for `video_enhance`, `image-upscale`, `animate_character`, and `flux_klein_edit`, but no recent live observation manifest was mechanically verified.
- Dashboard validation prerequisite: `NO_GO` until `scripts/dashboard.py` imports without `NameError: name 'DatabaseClient' is not defined`.
- Shadow isolation: `UNKNOWN`; output-divergence auto-rollback remains disabled unless isolated shadow evidence proves no completion, billing, upload, or user-visible side effects.
- Rollback PR references: `ROLLBACK_PR_APP_PLACEHOLDER`, replace when mechanically verified.

## Required Placeholders

```bash
export SUPABASE_URL="<SUPABASE_URL>"
export SUPABASE_SERVICE_ROLE_KEY="<SUPABASE_SERVICE_ROLE_KEY>"
export STABLE_SELECTOR_NAMESPACE="production"
export STABLE_SELECTOR_VERSION="<stable-selector-version-or-empty>"
export STABLE_WORKER_POOL="gpu-wgp-production"
export ROLLBACK_ROUTE_KEYS_JSON='["<route-key-1>","<route-key-2>"]'
export ROLLBACK_PR_APP="ROLLBACK_PR_APP_PLACEHOLDER"
```

Do not paste service-role keys, bearer tokens, raw task payloads, private media URLs, or unredacted user IDs into this file or committed evidence.

## Claim Suppression

Alert anchor: `#claim-suppression`.

Claim suppression means selected WGP workers cannot claim work after rollback or the canary selector has made a route unclaimable. Treat this as `NO_GO` for promotion until route-level selected pool totals and claim logs are green.

Immediate actions:

1. Confirm affected route keys from the alert payload.
2. Flip those route keys back to WGP if they are not already selected on WGP.
3. Confirm WGP selected pool totals for the route keys.
4. Keep VibeComfy canary pool scaled to zero until claim suppression clears.

Expected redacted evidence:

```json
{
  "status": "NO_GO",
  "alert": "canary_claim_suppression",
  "rollback_action": "route_selector_flip_to_wgp",
  "route_keys": ["<route-key-1>"],
  "selector_namespace": "production",
  "selector_version": "<stable-selector-version-or-empty>",
  "selected_backend": "wgp",
  "selected_pool_totals": {
    "worker_pool": "gpu-wgp-production",
    "claimable": true
  },
  "source_ref": {
    "kind": "redacted_claim_log",
    "id": "<replace-with-log-ref>"
  },
  "rollback_pr": "ROLLBACK_PR_APP_PLACEHOLDER"
}
```

## Completion Billing Failure

Alert anchor: `#completion-billing-failure`.

Completion or billing drift on any promoted route is a hard stop for that route cohort. Shadow checks must not create duplicate completion, billing, upload, or user-visible side effects. If the evidence cannot prove isolation, mark the route `NO_GO` and keep output-divergence rollback sampled/offline only.

Immediate actions:

1. Freeze promotion for the affected route keys.
2. Flip affected RayWorker route keys back to WGP.
3. Leave non-RayWorker routes on their current owner/runtime unless their own smoke evidence is red.
4. Attach redacted completion and billing evidence to the readiness package.

Expected redacted evidence:

```json
{
  "status": "NO_GO",
  "alert": "canary_completion_billing_failure",
  "route_keys": ["<route-key-1>"],
  "selected_backend_after": "wgp",
  "completion_evidence": {
    "status": "failed",
    "source_ref": {
      "kind": "redacted_completion_log",
      "id": "<replace-with-log-ref>"
    }
  },
  "billing_evidence": {
    "status": "failed",
    "source_ref": {
      "kind": "redacted_billing_log",
      "id": "<replace-with-log-ref>"
    }
  },
  "redaction": {
    "task_ids": "redacted",
    "user_ids": "redacted",
    "secrets": "redacted"
  },
  "rollback_pr": "ROLLBACK_PR_APP_PLACEHOLDER"
}
```

## Non-RayWorker Route Smoke

Alert anchor: `#non-rayworker-route-smoke`.

The active non-RayWorker routes are:

- `video_enhance`
- `image-upscale`
- `animate_character`
- `flux_klein_edit`

Mechanical fixture registry status: `reigh-worker/scripts/dual_run_compare/fixtures/non_rayworker/registry_snapshot.json` maps all four routes to `api_orchestrator`. No recent live/staging observation manifest was mechanically verified in T2, so Sprint 11B evidence must mark their current smoke state as `UNKNOWN` or `NO_GO` until redacted live observations exist.

Immediate actions:

1. Check the alert payload route key.
2. Verify completion and billing evidence for that route from redacted system logs or a live/staging observation manifest.
3. Do not move non-RayWorker routes to RayWorker or VibeComfy as part of Sprint 11B rollback.
4. If any required route lacks recent live/staging evidence, mark canary readiness `PAUSED` or `NO_GO`.

Expected redacted evidence:

```json
{
  "status": "PAUSED",
  "alert": "canary_non_rayworker_route_smoke",
  "routes": [
    {
      "route_key": "video_enhance",
      "runtime": "api_orchestrator",
      "status": "UNKNOWN",
      "completion_evidence": {
        "status": "UNKNOWN"
      },
      "billing_evidence": {
        "status": "UNKNOWN"
      },
      "source_ref": {
        "kind": "fixture_registry",
        "path": "reigh-worker/scripts/dual_run_compare/fixtures/non_rayworker/registry_snapshot.json"
      }
    }
  ],
  "redaction": {
    "secrets": "redacted",
    "task_ids": "redacted"
  }
}
```

## Evidence Checklist

Attach this app/shared-path evidence to the Sprint 11B readiness package:

- Affected route keys and current selected backend.
- Selector namespace/version before and after rollback, or `UNKNOWN` if not mechanically verified.
- Completion evidence status and source ref.
- Billing evidence status and source ref.
- Non-RayWorker route smoke status for all four active routes.
- Explicit `UNKNOWN`, `PAUSED`, or `NO_GO` values for missing live observations.
- Rollback PR placeholder or final URL.
