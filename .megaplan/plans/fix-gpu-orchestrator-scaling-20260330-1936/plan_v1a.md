# Implementation Plan: Fix Premature Idle Worker Termination & Refactor Scaling Logic

## Overview

The GPU orchestrator has a scaling logic bug that kills idle workers prematurely, creating a vicious cycle: kill warm worker → task arrives → can't spawn replacement → circuit breaker trips → all spawning blocked.

**Root cause chain:**
1. `desired` (legacy, used for termination) = `max(min_active_gpus, task_based, busy_count + MACHINES_TO_KEEP_IDLE)`. With `MACHINES_TO_KEEP_IDLE=0` and no queued tasks, `desired = max(2, 0, busy_count)`. Two busy + one idle worker → `desired=2`, `current=3`, so `workers_to_terminate=1`.
2. When over-capacity, `overcapacity_idle_timeout_sec=30s` kicks in instead of the normal 600s timeout, so the idle worker dies after 30 seconds.
3. New task arrives → no idle workers → spawn attempt → RunPod may fail → error worker.
4. Circuit breaker (`_check_worker_failure_rate`) counts ALL error workers (spawn failures + crashes) in the last 5 minutes → trips at 80% → blocks all spawning.

**Secondary issues:** (a) Overlapping termination paths in health.py, worker_capacity.py, and lifecycle.py; (b) 1654-line control_loop.py with duplicate code alongside refactored phases; (c) non-obvious config interactions between `overcapacity_idle_timeout_sec` and `MACHINES_TO_KEEP_IDLE`.

**Repo shape:** Pure function `calculate_scaling_decision_pure()` in `worker_state.py` computes scaling decisions. Execution happens via mixin phases. `control_loop.py` has legacy copies of all phase methods. Tests use `scaling_decision_helpers.py` fixtures.

## Phase 1: Fix the Scaling Decision Bug

### Step 1: Fix `desired` calculation to preserve idle buffer (`gpu_orchestrator/worker_state.py:488-519`)
**Scope:** Small

1. **Inspect** `calculate_scaling_decision_pure()` lines 488-519. The `desired` field drives `workers_to_terminate` but uses `buffer_based = busy_count + machines_to_keep_idle` (line 488), which with `MACHINES_TO_KEEP_IDLE=0` counts zero buffer. The new `desired_floor` on line 461 has the same formula so has the same problem.
2. **Fix** the `desired` calculation to account for idle workers that should be retained. The key insight: idle workers ARE the capacity to handle new tasks. They should only be considered excess when they exceed `min_active_gpus` AND there are no queued tasks AND the normal idle timeout (600s) has passed — not the 30s overcapacity timeout.
   - Change `buffer_based` to: `busy_count + max(config.machines_to_keep_idle, 1)` — always keep at least 1 idle worker as buffer when there are busy workers. This ensures `desired >= busy_count + 1` whenever work is actively being processed.
   - Alternative (simpler): change `overcapacity_idle_timeout_sec` default from 30 to match `gpu_idle_timeout_sec` (600). But this doesn't fix the conceptual issue that the floor doesn't account for needing idle capacity.
   - **Chosen approach**: Fix `buffer_based` to use `max(machines_to_keep_idle, 1)` when `busy_count > 0`. This means: "if workers are busy processing tasks, always desire at least 1 idle worker as buffer." When `busy_count == 0`, the minimum floor (`min_active_gpus`) still applies.
3. **Update** `desired_floor` on line 461 with the same fix for consistency between spawn and terminate decisions:
   ```python
   effective_idle_buffer = max(config.machines_to_keep_idle, 1) if busy_count > 0 else config.machines_to_keep_idle
   desired_floor = max(config.min_active_gpus, busy_count + effective_idle_buffer)
   ```

### Step 2: Remove the health-phase idle termination path (`gpu_orchestrator/control/phases/health.py:97-135`)
**Scope:** Small

1. **Inspect** `_handle_worker_without_active_task()` in health.py:121-133. This is a second idle-kill path that bypasses the scaling decision entirely — it calls `_mark_worker_error()` (status=`error`) instead of `_terminate_worker()` (status=`terminated`).
2. **Remove** the idle timeout termination block (lines 121-133). Idle worker termination should only happen through the scaling decision → `_execute_scaling()` path, which already respects `min_active_gpus`, `machines_to_keep_idle`, and grace periods.
3. **Keep** the queued-tasks health check block (lines 110-119) — that's checking if an idle worker is actually broken when there's work it should be claiming.

### Step 3: Fix circuit breaker to distinguish spawn failures from worker crashes (`gpu_orchestrator/control/phases/worker_capacity.py:279-325`)
**Scope:** Medium

1. **Inspect** `_check_worker_failure_rate()`. It counts all `status=error` workers in the last 5 minutes, regardless of why they errored. A spawn failure (RunPod unavailable) and a worker crash (OOM, GPU failure) are very different signals.
2. **Add** a `spawn_failure` flag or error code filter. Workers that error during spawning (never reached `active` status) should be counted separately from workers that errored while active.
   - Filter: check if worker was ever `active` (has `last_heartbeat` or `promoted_to_active_at` in metadata). If not, it's a spawn failure.
   - Spawn failures should have their own rate limit (or be excluded from the general failure rate). The current behavior means 5 spawn failures in 5 minutes blocks all spawning — exactly when you need spawning most.
3. **Implement**: Split the count into `spawn_failures` and `runtime_failures`. Only `runtime_failures` count toward the circuit breaker rate. Log both counts for observability.

### Step 4: Update and add tests (`tests/test_scaling_decision_spawn.py`, `tests/test_scaling_decision_limits.py`, `tests/scaling_decision_helpers.py`)
**Scope:** Medium

1. **Update** existing `TestBufferMaintenance` tests to verify the new idle buffer behavior: with 2 busy + 1 idle + `MACHINES_TO_KEEP_IDLE=0`, `workers_to_terminate` should be 0 (not 1).
2. **Add** test: "idle worker preserved when busy workers exist" — 3 busy + 1 idle + `MACHINES_TO_KEEP_IDLE=0` → `desired=4`, no termination.
3. **Add** test: "idle workers still terminated when no busy workers and over minimum" — 0 busy + 3 idle + `MIN_ACTIVE_GPUS=2` → `desired=2`, terminate 1.
4. **Add** test: circuit breaker ignores spawn failures — create worker states with spawn failures and verify `_check_worker_failure_rate()` still returns True.
5. **Run** `python -m pytest tests/test_scaling_decision_spawn.py tests/test_scaling_decision_limits.py -v` to validate.

## Phase 2: Consolidate Termination Paths

### Step 5: Audit all termination entry points
**Scope:** Small

1. **Catalog** the three termination paths and their status transitions:
   - Health-derived termination → `_mark_worker_error()` → status=`error` + RunPod terminate
   - Scale-down termination → `_terminate_worker()` → status=`terminated` + RunPod terminate
   - Early termination → direct RunPod terminate + status=`terminated`
2. **Identify** which paths can overlap for the same worker in a single cycle. The phase ordering (3: early term → 5: health → 9: execute scaling) means a worker could be hit by health AND scaling in the same cycle.

### Step 6: Add idempotency guard to termination (`gpu_orchestrator/control/phases/health.py`, `gpu_orchestrator/control/phases/worker_capacity.py`)
**Scope:** Small

1. **Add** a set `terminated_this_cycle` to `CycleSummary` (or pass it through the phase chain) that tracks worker IDs already terminated in this cycle.
2. **Check** this set before any termination call. Skip if already terminated.
3. This is simpler than restructuring the phases and prevents duplicate RunPod API calls.

### Step 7: Sync `control_loop.py` legacy methods with phase fixes
**Scope:** Medium

1. **Apply** the same fixes from Steps 1-3, 6 to the corresponding methods in `control_loop.py` (lines 909-954 for `_execute_scaling`, lines 497-530 for health checks, etc.).
2. The legacy `control_loop.py` methods and the phase mixin methods are currently duplicated. Both paths must have the fixes until the legacy code is removed.
3. **Verify** with `python -m py_compile gpu_orchestrator/control_loop.py`.

### Step 8: Run full test suite
**Scope:** Small

1. **Run** `python -m pytest tests/ -v --tb=short` to verify no regressions.
2. **Fix** any test failures caused by the changed scaling logic or termination behavior.

## Execution Order
1. Fix the scaling decision first (Step 1) — this is the critical bug fix.
2. Remove the health-phase idle kill (Step 2) — eliminates the bypass path.
3. Fix the circuit breaker (Step 3) — prevents the cascading failure.
4. Tests (Step 4) — validate the core fixes before touching termination paths.
5. Consolidation (Steps 5-7) — lower risk, builds on validated fixes.
6. Full suite (Step 8) — final validation.

## Validation Order
1. Unit tests for `calculate_scaling_decision_pure()` first — fastest feedback loop.
2. Circuit breaker test in isolation.
3. Full `pytest tests/` run after all changes.
