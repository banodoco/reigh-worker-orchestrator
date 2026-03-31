# Implementation Plan: Fix Premature Idle Worker Killing & Refactor Scaling/Termination

## Overview

The GPU orchestrator has a cascading failure mode: idle workers get killed prematurely, then new tasks arrive with no warm workers, RunPod can't spawn fast enough, spawn failures trip the circuit breaker, and all spawning is blocked.

**Root causes:**
1. **Health check (PATH A) marks idle timeouts as `error`** (`control_loop.py:546` and `health.py:128` call `_mark_worker_error`), poisoning the circuit breaker's failure rate.
2. **`desired_floor` undervalues idle workers.** With `MACHINES_TO_KEEP_IDLE=0`, `desired_floor = max(min_active_gpus, busy_count + 0)`. Idle workers appear "over capacity" and get killed after 30s.
3. **Early termination path uses the same broken formula.** `control_loop.py:346` and `lifecycle.py:45` compute `early_desired = max(min_active_gpus, task_based, machines_to_keep_idle)` — with `MACHINES_TO_KEEP_IDLE=0`, spawning workers can be killed back to zero warm buffer.
4. **Spawn failures mark `error`** (`control_loop.py:1336` and `worker_capacity.py:239`), so RunPod outages trip the circuit breaker even though the workers themselves didn't fail.
5. **Three overlapping termination paths** independently kill idle workers. PATH A uses `error` status; PATHs B and C use `terminated`.

**Repository shape:** Logic is duplicated between `control_loop.py` (1655 lines) and phase mixins in `control/phases/`. Both paths are live — `control_loop.py` methods are the ones called at runtime, while the mixin methods in `control/phases/` mirror the same logic. The `use_new_scaling_logic` flag defaults to `true`; the legacy scaling path is kept for rollback. All fixes must be applied to **both** `control_loop.py` and the corresponding phase mixin.

**Settled decisions (from v1 review):**
- DECISION-001: Use `max(machines_to_keep_idle, 1)` as the buffer floor in code, not a config change.
- DECISION-002: Remove idle-timeout termination from health check; scaling execution (PATH B) is sole owner.
- DECISION-003: Do not remove legacy scaling path in this PR — flag for follow-up only.

## Phase 1: Fix the Immediate Bugs

### Step 1: Remove idle-timeout termination from health check (`control_loop.py:539-548`, `health.py:121-133`)
**Scope:** Small
1. **In `control_loop.py`**: Remove the `else` block at lines 539-548 that calls `_mark_worker_error` for idle timeout. The health check should only handle: (a) basic health checks when tasks are queued (lines 531-537), (b) heartbeat validation for busy workers (lines 521-523), (c) stuck task detection (line 567).
2. **In `control/phases/health.py`**: Remove the matching block at lines 121-133 that calls `_mark_worker_error` for idle timeout (`_is_worker_idle_with_timeout` check).
3. **Rationale:** Eliminates `error` status poisoning. Idle termination becomes the sole responsibility of scaling execution (PATH B), which correctly uses `terminated` status.

### Step 2: Fix `desired_floor` in scaling decision (`worker_state.py:461`, `worker_state.py:488`)
**Scope:** Small
1. **Change** `desired_floor` at `worker_state.py:461` from:
   ```python
   desired_floor = max(config.min_active_gpus, busy_count + config.machines_to_keep_idle)
   ```
   to:
   ```python
   desired_floor = max(config.min_active_gpus, busy_count + max(config.machines_to_keep_idle, 1))
   ```
2. **Update** legacy `buffer_based` at `worker_state.py:488` for consistency:
   ```python
   buffer_based = busy_count + max(config.machines_to_keep_idle, 1)
   ```

### Step 3: Fix `early_desired` in early termination path (`control_loop.py:346`, `lifecycle.py:45`)
**Scope:** Small
1. **In `control_loop.py:346`**, change:
   ```python
   early_desired = max(config.min_active_gpus, task_based, config.machines_to_keep_idle)
   ```
   to:
   ```python
   early_desired = max(config.min_active_gpus, task_based, max(config.machines_to_keep_idle, 1))
   ```
2. **In `lifecycle.py:45`**, apply the identical change:
   ```python
   early_desired = max(config.min_active_gpus, task_based, max(config.machines_to_keep_idle, 1))
   ```
3. **Rationale:** Without this, spawning workers get killed when `MACHINES_TO_KEEP_IDLE=0` because `early_desired` can be 0 (if no tasks and `min_active_gpus=0`), making all spawning workers "excess."

### Step 4: Fix spawn failures poisoning circuit breaker (`control_loop.py:1336`, `worker_capacity.py:239`)
**Scope:** Small
1. **In `control_loop.py:1336`**, change the spawn failure handling from `mark_worker_error` to `update_worker_status` with `terminated` status and a clear reason:
   ```python
   await self.db.update_worker_status(worker_id, 'terminated', {
       'termination_reason': 'spawn_failed',
       'terminated_at': datetime.now(timezone.utc).isoformat(),
   })
   ```
2. **In `worker_capacity.py:239`**, apply the identical change.
3. **Rationale:** Spawn failures are infrastructure issues (RunPod unavailable), not worker defects. Marking them as `error` makes the circuit breaker block all spawning during exactly the scenario where retrying (after RunPod recovers) is the correct behavior. Using `terminated` with a `spawn_failed` reason preserves observability without triggering the breaker.

### Step 5: Fix scale-down buffer in execute_scaling (`control_loop.py:941`, `worker_capacity.py:48`)
**Scope:** Small
1. **In `control_loop.py:941`**, change:
   ```python
   max_by_buffer = max(0, decision.idle_count - config.machines_to_keep_idle)
   ```
   to:
   ```python
   max_by_buffer = max(0, decision.idle_count - max(config.machines_to_keep_idle, 1))
   ```
2. **In `worker_capacity.py:48`**, apply the identical change.
3. **Rationale:** This is the enforcement point — even if the scaling decision says to scale down, this ensures at least 1 idle worker is preserved as warm capacity.

## Phase 2: Update Tests

### Step 6: Update existing scaling decision tests (`tests/test_scaling_decision_spawn.py`, `tests/test_scaling_decision_limits.py`)
**Scope:** Medium
1. **Update** buffer maintenance tests in `test_scaling_decision_spawn.py:98-135` (`TestBufferMaintenance`) — buffer is now `max(machines_to_keep_idle, 1)`, so tests with `machines_to_keep_idle=0` will see buffer=1 behavior.
2. **Update** scale-down tests in `test_scaling_decision_limits.py:87-113` (`TestScaleDown`) — `desired` now includes a +1 buffer minimum, so `workers_to_terminate` counts may change.
3. **Update** `scaling_decision_helpers.py` if any default config values change.

### Step 7: Add new test cases for the fixes
**Scope:** Medium
1. **Add** `test_idle_worker_not_killed_when_only_warm_capacity` to `test_scaling_decision_spawn.py`: 2 busy + 1 idle worker with `machines_to_keep_idle=0` → `workers_to_terminate=0` (the idle worker is preserved).
2. **Add** `test_overcapacity_respects_buffer_minimum` to `test_scaling_decision_spawn.py`: 2 busy + 3 idle with `machines_to_keep_idle=0` → `workers_to_terminate=2` (keeps 1 idle).
3. **Add** `test_early_desired_respects_buffer_minimum` to `tests/gpu_orchestrator/control/phases/test_lifecycle.py`: unit test that the early termination path uses `max(machines_to_keep_idle, 1)`.
4. **Add** `test_spawn_failure_does_not_poison_failure_rate` to a new file `tests/test_circuit_breaker.py`: mock `db.get_workers` to return workers with mixed `terminated` (including `spawn_failed` reason) and `active` statuses, call `_check_worker_failure_rate()`, verify it returns `True`. This tests the actual circuit breaker method, not the pure scaling function.

### Step 8: Validate Phase 1+2 fixes
**Scope:** Small
1. **Run** `python -m pytest tests/test_scaling_decision_spawn.py tests/test_scaling_decision_limits.py -v`
2. **Run** `python -m pytest tests/gpu_orchestrator/ -v`
3. **Run** `python -m pytest tests/ -v` (full suite)

## Phase 3: Consolidate Termination Paths

### Step 9: Guard PATH C failsafe against healthy idle workers (`control_loop.py:1546-1594`)
**Scope:** Small
1. **Add** a condition in the failsafe stale worker loop: skip workers that are active, idle (no active task), and have a recent heartbeat. These are PATH B's responsibility.
   ```python
   if ws.is_active and not ws.has_active_task and ws.heartbeat_is_recent:
       continue  # Idle healthy workers handled by scaling execution
   ```
2. **Add** brief inline comments documenting termination path ownership:
   - PATH B (scaling execution): idle worker termination based on capacity
   - PATH C (failsafe): stuck/stale workers only (spawning timeout, lost heartbeat)

### Step 10: Final validation
**Scope:** Small
1. **Run** full test suite: `python -m pytest tests/ -v`.
2. **Grep** for `_mark_worker_error` to confirm no path marks idle timeouts or spawn failures as errors.
3. **Grep** for `machines_to_keep_idle` to confirm all uses apply the `max(..., 1)` buffer.
4. **Verify** the legacy scaling path at `control_loop.py:801-861` — if `use_new_scaling_logic` defaults to `true` (confirmed: `config.py:148`), add a `# TODO: Remove legacy scaling path` comment. Apply the same `max(machines_to_keep_idle, 1)` fix to `buffer_based` in the legacy method for safety.

## Execution Order
1. Steps 1-5: Fix all bugs in both `control_loop.py` and phase mixins simultaneously.
2. Steps 6-8: Update and add tests, validate.
3. Steps 9-10: Consolidate failsafe path, final validation.

## Validation Order
1. Unit tests on `calculate_scaling_decision_pure` (fastest feedback).
2. Phase mixin and lifecycle tests.
3. New circuit breaker test.
4. Full test suite.
5. Manual grep for `_mark_worker_error` and `machines_to_keep_idle` to confirm completeness.
