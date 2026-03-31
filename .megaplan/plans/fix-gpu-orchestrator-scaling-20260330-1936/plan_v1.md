# Implementation Plan: Fix Premature Idle Worker Killing & Refactor Scaling/Termination

## Overview

The GPU orchestrator has a cascading failure mode: idle workers get killed prematurely, then new tasks arrive with no warm workers, RunPod can't spawn fast enough, spawn failures trip the circuit breaker, and all spawning is blocked.

**Root causes:**
1. **Health check (PATH A) marks idle timeouts as `error`** (`control_loop.py:546` calls `_mark_worker_error`), poisoning the circuit breaker's failure rate. Normal scale-down should use `terminated` status, not `error`.
2. **`desired_floor` undervalues idle workers.** With `MACHINES_TO_KEEP_IDLE=0`, `desired_floor = max(min_active_gpus, busy_count + 0)`. When workers are busy, idle workers appear "over capacity" and get killed after just 30s (`overcapacity_idle_timeout_sec`), even though they're the only ones that can claim new tasks.
3. **Three overlapping termination paths** independently evaluate and kill idle workers: health check (PATH A, `control_loop.py:540-548`), scaling execution (PATH B, `control_loop.py:921-954`), and periodic failsafe (PATH C, `control_loop.py:1546-1594`). PATH A uses `error` status; PATHs B and C use `terminated`.

**Repository shape:** `gpu_orchestrator/control_loop.py` (1655 lines) is the main loop with legacy + refactored phase mixins. Pure scaling logic lives in `worker_state.py:414-542` (`calculate_scaling_decision_pure`). Phase mixins in `gpu_orchestrator/control/phases/` provide structure but the actual logic still lives in `control_loop.py`. Tests in `tests/test_scaling_decision_spawn.py` and `tests/test_scaling_decision_limits.py` cover the pure function.

## Phase 1: Fix the Immediate Bugs (Idle Kill + Circuit Breaker Poisoning)

### Step 1: Stop health check from marking idle timeouts as `error` (`gpu_orchestrator/control_loop.py:540-548`)
**Scope:** Small
1. **Remove** the idle timeout termination from the health check path entirely. The health check (PATH A) should NOT terminate workers for being idle — that's the scaling execution's job (PATH B). Lines 539-548 handle the "no tasks queued" idle timeout case; remove this block.
2. **Keep** the health check's other responsibilities intact: basic health checks for idle workers with tasks queued (lines 531-537), heartbeat validation for busy workers (lines 521-523), and stuck task detection (line 567).
3. **Rationale:** This eliminates the `error` status poisoning. Idle termination becomes the sole responsibility of PATH B (`execute_scaling`), which correctly uses `terminated` status.

### Step 2: Fix `desired_floor` to account for idle workers covering future tasks (`gpu_orchestrator/worker_state.py:461`)
**Scope:** Small
1. **Change** `desired_floor` calculation from:
   ```python
   desired_floor = max(config.min_active_gpus, busy_count + config.machines_to_keep_idle)
   ```
   to:
   ```python
   desired_floor = max(config.min_active_gpus, busy_count + max(config.machines_to_keep_idle, 1))
   ```
   This ensures at least 1 idle worker is always "desired" above the busy count, preventing the 30s overcapacity timeout from killing the last idle worker. When `machines_to_keep_idle` is explicitly set > 1, it's respected.
2. **Also update** the legacy `desired` calculation at line 488-492 for consistency:
   ```python
   buffer_based = busy_count + max(config.machines_to_keep_idle, 1)
   ```
3. **Rationale:** The core bug: with `MACHINES_TO_KEEP_IDLE=0`, busy workers make idle workers look "over capacity" → 30s kill timeout → no warm workers for new tasks. Forcing at least 1 idle buffer prevents this without requiring config changes.

### Step 3: Update tests for the new floor behavior (`tests/test_scaling_decision_spawn.py`, `tests/test_scaling_decision_limits.py`)
**Scope:** Medium
1. **Update** buffer maintenance tests in `test_scaling_decision_spawn.py:98-135` (`TestBufferMaintenance`) to reflect that buffer is now `max(machines_to_keep_idle, 1)` instead of `machines_to_keep_idle`.
2. **Update** scale-down tests in `test_scaling_decision_limits.py:87-113` (`TestScaleDown`) — the `test_no_scale_down_with_active_tasks` test may need adjustment since `desired` now includes a +1 buffer.
3. **Add** a new test case: `test_idle_worker_not_killed_when_only_warm_capacity` — verifies that a single idle worker alongside busy workers is NOT marked for termination.
4. **Add** a test case: `test_overcapacity_respects_buffer` — verifies that `workers_to_terminate` correctly accounts for the buffer so the last idle worker isn't killed.
5. **Update** `scaling_decision_helpers.py` if any default config values change.

### Step 4: Validate Phase 1 fixes
**Scope:** Small
1. **Run** `python -m pytest tests/test_scaling_decision_spawn.py tests/test_scaling_decision_limits.py -v` to confirm the pure function tests pass.
2. **Run** `python -m pytest tests/gpu_orchestrator/ -v` to confirm phase mixin tests still pass.
3. **Run** the full test suite: `python -m pytest tests/ -v`.

## Phase 2: Consolidate Termination Paths

### Step 5: Audit all three termination paths and document the desired ownership (`gpu_orchestrator/control_loop.py`)
**Scope:** Small
1. **Confirm** that after Step 1, PATH A (health check) no longer does idle termination.
2. **Verify** PATH C (failsafe, `control_loop.py:1546-1594`) only fires for genuinely stale workers (heartbeat too old) and not for normally idle workers. Check the `cutoff_sec` logic at line 1561 — it uses `gpu_idle_timeout_sec` for non-spawning workers, which could overlap with PATH B. If it does overlap, add a guard to skip workers that are `is_active and not has_active_task` (idle workers that PATH B should handle).
3. **Document** with brief inline comments the intended ownership:
   - PATH B (scaling execution): idle worker termination based on capacity decisions
   - PATH C (failsafe): stuck/stale workers only (spawning timeout, lost heartbeat)

### Step 6: Guard PATH C failsafe against idle workers (`gpu_orchestrator/control_loop.py:1546-1594`)
**Scope:** Small
1. **Add** a condition to the failsafe stale worker check: skip workers that are active, healthy, and idle (no active task). These are PATH B's responsibility.
   ```python
   # In the stale worker loop, after getting worker state:
   if ws.is_active and not ws.has_active_task and ws.heartbeat_is_recent:
       continue  # Idle healthy workers are handled by scaling execution
   ```
2. **This prevents** the failsafe from racing with or double-terminating workers that the scaling path should manage.

### Step 7: Test the consolidated termination paths
**Scope:** Small
1. **Update** `tests/gpu_orchestrator/control/phases/test_periodic.py` if the failsafe behavior changes.
2. **Run** `python -m pytest tests/ -v` for full validation.

## Phase 3: Clean Up Circuit Breaker Conflation

### Step 8: Verify circuit breaker no longer conflates idle kills with failures (`gpu_orchestrator/control_loop.py:1384-1436`)
**Scope:** Small
1. **After Step 1**, health check no longer marks idle workers as `error`, so idle terminations won't inflate the failure rate. **Verify** this by reading `_check_worker_failure_rate` (lines 1384-1436) and confirming it only counts `status == 'error'`.
2. **No code changes needed** if Step 1 was applied correctly — the fix is upstream (health check no longer creates false `error` entries).
3. **Add** a test case in `tests/test_scaling_decision_limits.py`: `test_terminated_workers_dont_affect_failure_rate` — mock a scenario with many `terminated` workers and verify `failure_rate_ok` stays `True`.

## Phase 4: Remove Legacy Duplication (Optional Cleanup)

### Step 9: Assess and flag legacy scaling path (`gpu_orchestrator/control_loop.py:787-861`, `gpu_orchestrator/control/phases/scaling_decision.py`)
**Scope:** Small
1. **Check** if `use_new_scaling_logic` config flag exists and what it defaults to. If it defaults to `True` and the legacy path is dead code, mark it with a `# TODO: Remove legacy scaling path` comment.
2. **Do NOT remove** the legacy path in this PR — it's a separate risk. Flag it for a follow-up.
3. **If** the legacy path is still active (flag defaults to `False`), apply the same `max(machines_to_keep_idle, 1)` fix to the legacy `_calculate_scaling_decision_legacy` method for consistency.

### Step 10: Final validation
**Scope:** Small
1. **Run** full test suite: `python -m pytest tests/ -v`.
2. **Review** all changes for consistency: grep for `_mark_worker_error` to confirm no path marks idle timeouts as errors.
3. **Grep** for `machines_to_keep_idle` to confirm all uses are updated.

## Execution Order
1. Fix the two immediate bugs first (Steps 1-2) — these are the root cause of the vicious cycle.
2. Update tests to match (Step 3-4) — cheap validation before proceeding.
3. Consolidate termination paths (Steps 5-7) — reduces surface area for future bugs.
4. Verify circuit breaker (Step 8) — confirms the cascade is broken.
5. Flag legacy code (Step 9-10) — cleanup without risk.

## Validation Order
1. Unit tests on `calculate_scaling_decision_pure` first (fastest feedback).
2. Phase mixin tests second.
3. Full test suite last.
4. Manual verification: grep for `_mark_worker_error` calls to confirm none are for idle timeouts.
