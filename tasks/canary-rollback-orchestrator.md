# Sprint 11B Canary Rollback Runbook - Orchestrator

## Purpose

Rollback the orchestrator-controlled canary surface by route cohort. The emergency rollback is a route selector flip back to WGP for affected route keys, followed by WGP pool verification and stale VibeComfy worker drain. This runbook deliberately avoids broad backend switches as proof of rollback.

Current mechanical state for this runbook:

- Active live cohort: `UNKNOWN`.
- Selector namespace/version: `UNKNOWN`.
- Hold windows for Cohorts A/B/E: `UNKNOWN`.
- Dashboard validation prerequisite: `NO_GO` until `scripts/dashboard.py` imports without `NameError: name 'DatabaseClient' is not defined`.
- Shadow isolation: `UNKNOWN`; output-divergence auto-rollback stays disabled unless isolated shadow evidence proves no completion, billing, upload, or user-visible side effects.
- Rollback PR references: `ROLLBACK_PR_ORCHESTRATOR_PLACEHOLDER`, replace when mechanically verified.

## Required Placeholders

```bash
export RAILWAY_PROJECT="<RAILWAY_PROJECT_ID>"
export RAILWAY_ENVIRONMENT="<staging-or-canary-environment>"
export ORCHESTRATOR_SERVICE="<reigh-worker-orchestrator-service>"
export SUPABASE_URL="<SUPABASE_URL>"
export SUPABASE_SERVICE_ROLE_KEY="<SUPABASE_SERVICE_ROLE_KEY>"
export STABLE_SELECTOR_NAMESPACE="production"
export STABLE_SELECTOR_VERSION="<stable-selector-version-or-empty>"
export STABLE_WORKER_BACKEND="wgp"
export STABLE_WORKER_PROFILE="1"
export STABLE_WORKER_POOL="gpu-wgp-production"
export CANARY_SELECTOR_NAMESPACE="<canary-selector-namespace-or-UNKNOWN>"
export CANARY_SELECTOR_VERSION="<canary-selector-version-or-UNKNOWN>"
export CANARY_WORKER_POOL="<gpu-vibecomfy-canary-pool-or-UNKNOWN>"
export ROLLBACK_ROUTE_KEYS_JSON='["<route-key-1>","<route-key-2>"]'
export ROLLBACK_PR_ORCHESTRATOR="ROLLBACK_PR_ORCHESTRATOR_PLACEHOLDER"
```

Use route keys from `reigh-worker/scripts/dual_run_compare/migration-thresholds.yaml` (`version: 0B-2026-05-05`). If the live cohort cannot be mechanically verified, keep readiness `PAUSED` and document selector/hold-window fields as `UNKNOWN`.

## Emergency Selector Flip Back To WGP

Apply the route-key scoped selector update first. If live ops uses a database selector table, patch only the affected route keys. If live ops uses environment-pinned selector contracts, apply the stable WGP contract below and record the route keys it covers.

```bash
railway variables \
  --project "$RAILWAY_PROJECT" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --service "$ORCHESTRATOR_SERVICE" \
  --set REIGH_BACKEND="$STABLE_WORKER_BACKEND" \
  --set WORKER_BACKEND="$STABLE_WORKER_BACKEND" \
  --set REIGH_WORKER_PROFILE="$STABLE_WORKER_PROFILE" \
  --set WGP_PROFILE="$STABLE_WORKER_PROFILE" \
  --set REIGH_WORKER_POOL="$STABLE_WORKER_POOL" \
  --set WORKER_POOL="$STABLE_WORKER_POOL" \
  --set REIGH_SELECTOR_NAMESPACE="$STABLE_SELECTOR_NAMESPACE" \
  --set ROUTE_SELECTOR_NAMESPACE="$STABLE_SELECTOR_NAMESPACE" \
  --set REIGH_SELECTOR_VERSION="$STABLE_SELECTOR_VERSION" \
  --set ROUTE_SELECTOR_VERSION="$STABLE_SELECTOR_VERSION"
```

```bash
railway redeploy \
  --project "$RAILWAY_PROJECT" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --service "$ORCHESTRATOR_SERVICE"
```

Expected redacted evidence:

```json
{
  "status": "PAUSED",
  "rollback_action": "route_selector_flip_to_wgp",
  "route_keys": ["<route-key-1>"],
  "selector_namespace_before": "<canary-selector-namespace-or-UNKNOWN>",
  "selector_version_before": "<canary-selector-version-or-UNKNOWN>",
  "selector_namespace_after": "production",
  "selector_version_after": "<stable-selector-version-or-empty>",
  "worker_backend_after": "wgp",
  "worker_profile_after": "1",
  "worker_pool_after": "gpu-wgp-production",
  "rollback_pr": "ROLLBACK_PR_ORCHESTRATOR_PLACEHOLDER"
}
```

## Scale VibeComfy Canary Pool To Zero

Scale the canary pool to zero after the selector flip. This prevents fresh VibeComfy claims while WGP resumes service.

```bash
railway variables \
  --project "$RAILWAY_PROJECT" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --service "$ORCHESTRATOR_SERVICE" \
  --set MIN_ACTIVE_GPUS=0 \
  --set MACHINES_TO_KEEP_IDLE=0 \
  --set MAX_ACTIVE_GPUS=0
```

```bash
railway redeploy \
  --project "$RAILWAY_PROJECT" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --service "$ORCHESTRATOR_SERVICE"
```

Expected evidence:

```json
{
  "canary_pool": {
    "worker_pool": "<gpu-vibecomfy-canary-pool-or-UNKNOWN>",
    "desired_active_gpus": 0
  },
  "stable_pool": {
    "worker_backend": "wgp",
    "worker_pool": "gpu-wgp-production",
    "selector_namespace": "production"
  }
}
```

## Stale Route Workers

Alert anchor: `#stale-route-workers`.

Drain active stale-route workers and terminate idle or ready stale-route workers. Active workers may finish only work already claimed before the selector flip; they must not claim new tasks for the rolled-back route keys.

When the dashboard import prerequisite is green, use the dashboard export:

```bash
python scripts/dashboard.py \
  --export /tmp/sprint11b-rollback-dashboard.redacted.json \
  --import-live-evidence /tmp/redacted-live-evidence.json
```

When the dashboard import prerequisite is blocked, use a direct redacted worker query instead:

```bash
curl --fail-with-body -sS \
  "$SUPABASE_URL/rest/v1/workers?worker_backend=eq.vibecomfy&worker_pool=eq.$CANARY_WORKER_POOL&status=in.(ready,idle,active)&select=id,status,worker_pool,worker_backend,metadata" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
| tee /tmp/sprint11b-stale-route-workers.redacted.json
```

Terminate only idle or ready stale workers:

```bash
jq -r '.[] | select(.status == "idle" or .status == "ready") | .id' /tmp/sprint11b-stale-route-workers.redacted.json \
| while read -r WORKER_ID; do
    curl --fail-with-body -sS -X PATCH \
      "$SUPABASE_URL/rest/v1/workers?id=eq.$WORKER_ID" \
      -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
      -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
      -H "Content-Type: application/json" \
      -d '{"should_terminate":true,"termination_reason":"sprint11b_canary_rollback_idle_route_stale"}'
  done
```

Expected evidence:

```json
{
  "route_worker_health": {
    "<route_key>": {
      "stale_workers": 0,
      "new_claims_allowed": false
    }
  },
  "drain_policy": "active pre-flip claims may finish; idle/ready stale VibeComfy workers terminate or remain unclaimable"
}
```

## Verify Task Counts And Claim Gates

```bash
curl --fail-with-body -sS "$SUPABASE_URL/functions/v1/task-counts" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "worker_backend": "wgp",
    "worker_profile": "1",
    "worker_pool": "gpu-wgp-production",
    "selector_namespace": "production",
    "selector_version": "<stable-selector-version-or-empty>"
  }' \
| tee /tmp/sprint11b-rollback-task-counts.redacted.json
```

Expected evidence:

```json
{
  "selected_pool_totals": {
    "route_filter": {
      "worker_backend": "wgp",
      "worker_profile": "1",
      "worker_pool": "gpu-wgp-production",
      "selector_namespace": "production"
    },
    "blocked_by_capacity": 0
  },
  "claim_suppression": {
    "suppressed": 0
  }
}
```

## Latency P95 Ratio

Alert anchor: `#latency-p95-ratio`.

If canary p95 latency ratio breaches the threshold for a promoted route cohort, flip only the affected route keys back to WGP, keep unaffected stable cohorts unchanged, and attach the redacted route-level latency sample to the readiness package.

## VRAM Peak Ratio

Alert anchor: `#vram-peak-ratio`.

If canary VRAM peak ratio breaches the threshold, stop promotion for the affected route keys, scale the VibeComfy canary pool to zero, and keep WGP selected until a new profile/pool gate is mechanically verified.

## OOM Count

Alert anchor: `#oom-count`.

Any nonzero canary OOM count is an immediate `NO_GO` for the affected route keys. Flip those route keys back to WGP and include redacted worker id, route key, selector namespace/version, and source log reference.

## Output Divergence

Alert anchor: `#output-divergence`.

Output divergence is a `NO_GO` signal for the affected route keys. Keep output-divergence auto-rollback disabled unless isolated shadow evidence proves no completion, billing, upload, or user-visible side effects. With the current Sprint 11B mechanical evidence, treat divergence evidence as sampled/offline only, pause promotion, flip the affected route keys back to WGP, and attach the dual-run report ref plus threshold version to the readiness package.

## Missing Runtime Evidence

Alert anchor: `#missing-runtime-evidence`.

If standardized runtime observations are missing, keep promotion `PAUSED`. Do not infer green state from fixture-only data, dashboard placeholders, or non-isolated shadow checks.

## Quota Alerts

Alert anchor: `#quota-alerts`.

If quota alerts are active, scale the VibeComfy canary pool to zero, keep stable WGP selected, and record quota evidence as `PAUSED` or `NO_GO` until the owner-supplied quota gate clears.
