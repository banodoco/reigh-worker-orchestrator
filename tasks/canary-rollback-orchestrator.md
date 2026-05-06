# Sprint 10 Canary Rollback Draft - Orchestrator

## Purpose

Rollback the orchestrator-controlled canary surface before production promotion. This is a draft PR artifact: replace placeholders with staging/canary values, execute in the listed order, and attach the expected JSON evidence to the canary readiness package.

Required placeholders:

```bash
export RAILWAY_PROJECT="<RAILWAY_PROJECT_ID>"
export RAILWAY_ENVIRONMENT="<staging-or-canary-environment>"
export ORCHESTRATOR_SERVICE="<reigh-worker-orchestrator-service>"
export SUPABASE_URL="<SUPABASE_URL>"
export SUPABASE_SERVICE_ROLE_KEY="<SUPABASE_SERVICE_ROLE_KEY>"
export STABLE_SELECTOR_NAMESPACE="production"
export STABLE_SELECTOR_VERSION="<stable-selector-version-or-empty>"
export STABLE_WORKER_POOL="gpu-wgp-production"
export CANARY_WORKER_POOL="<gpu-vibecomfy-canary-pool>"
```

Do not paste real service-role keys, production task IDs, worker IDs, or RunPod IDs into this file.

## Rollback Steps

### 1. Flip orchestrator route contract back to stable WGP

```bash
railway variables \
  --project "$RAILWAY_PROJECT" \
  --environment "$RAILWAY_ENVIRONMENT" \
  --service "$ORCHESTRATOR_SERVICE" \
  --set REIGH_BACKEND=wgp \
  --set WORKER_BACKEND=wgp \
  --set REIGH_WORKER_PROFILE=1 \
  --set WGP_PROFILE=1 \
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

Expected evidence:

```json
{
  "route_contract": {
    "worker_backend": "wgp",
    "worker_profile": "1",
    "worker_pool": "gpu-wgp-production",
    "selector_namespace": "production",
    "selector_version": "<stable-selector-version-or-empty>"
  },
  "status": "applied"
}
```

### 2. Scale VibeComfy canary pool to zero

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
  "selected_pool_totals": {
    "route_filter": {
      "worker_backend": "wgp",
      "worker_pool": "gpu-wgp-production",
      "selector_namespace": "production"
    }
  },
  "canary_pool": {
    "worker_pool": "<gpu-vibecomfy-canary-pool>",
    "desired_active_gpus": 0
  }
}
```

### 3. Drain active route-stale workers and terminate idle route-stale workers

Use the dashboard export from T7 after the stable route contract is active:

```bash
python scripts/dashboard.py \
  --export /tmp/canary-rollback-dashboard.json \
  --import-live-evidence /tmp/redacted-live-evidence.json
```

Inspect stale route workers:

```bash
jq '.canary_panels.route_worker_health' /tmp/canary-rollback-dashboard.json
```

Terminate only idle stale workers from the canary pool:

```bash
curl --fail-with-body -sS \
  "$SUPABASE_URL/rest/v1/workers?worker_pool=eq.$CANARY_WORKER_POOL&status=in.(idle,ready)&select=id,metadata" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
| jq -r '.[].id' \
| while read -r WORKER_ID; do
    curl --fail-with-body -sS -X PATCH \
      "$SUPABASE_URL/rest/v1/workers?id=eq.$WORKER_ID" \
      -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
      -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
      -H "Content-Type: application/json" \
      -d '{"should_terminate":true,"termination_reason":"sprint10_canary_rollback_idle_route_stale"}'
  done
```

Expected evidence:

```json
{
  "route_worker_health": {
    "<route_key>": {
      "active_workers": 0,
      "stale_workers": 0,
      "workers": []
    }
  },
  "drain_policy": "active route-stale workers finish in-flight work; idle route-stale workers terminate"
}
```

### 4. Verify task-count and claim gate behavior

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
| tee /tmp/canary-rollback-task-counts.json
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

## Alert Runbook Anchors

These anchors are referenced by `config/alerts/section11-canary.yaml`:

- `#latency-p95-ratio`: trigger rollback if canary p95 latency ratio remains above gate after one sample window.
- `#vram-peak-ratio`: trigger rollback if canary VRAM peak ratio remains above gate after one sample window.
- `#oom-count`: trigger rollback immediately on nonzero canary OOM count.
- `#missing-runtime-evidence`: keep promotion blocked until standardized runtime observations are attached.
- `#stale-route-workers`: drain active stale workers and terminate idle stale workers as above.
- `#quota-alerts`: scale canary pool to zero and keep stable pool selected until quota clears.
