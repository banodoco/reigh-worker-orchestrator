# Implementation Plan: Single Source of Truth for Worker Health Verdicts

## Overview

The GPU orchestrator has **three overlapping paths** that independently assess worker health:

1. **`derive_worker_state()`** (`worker_state.py:110-274`) — Pure function computing `DerivedWorkerState` with `should_terminate`, `termination_reason`, lifecycle state. This is the *intended* single source of truth.
2. **`_health_check_active_workers()`** (`control_loop.py:484-615`) — *Re-derives* health decisions inline: checks heartbeat staleness (line 543), idle-with-tasks-queued (line 539-562), idle timeout (line 566-573), performs `_basic_health_check()`, and checks failure streaks — all of which duplicate or conflict with `DerivedWorkerState` verdicts.
3. **`_failsafe_stale_worker_check()`** (`control_loop.py:1577-1641`) — Raw datetime arithmetic on heartbeats, completely independent of `DerivedWorkerState`. Has its own `startup_phase` gate (lines 1592-1595) that duplicates `worker_state.py:308`.

The `control/phases/` directory has mixin implementations of these methods but they are **not wired in** — `OrchestratorControlLoop` still has all 1699 lines inline.

**Goal:** `DerivedWorkerState.should_terminate` (+ a new `should_terminate_failsafe`) is the ONE verdict. `_health_check_active_workers` becomes a thin executor. `_failsafe_stale_worker_check` reads derived state instead of doing its own math. Then wire in the phase mixins to decompose the god object.

## Phase 1: Consolidate Health Verdicts into DerivedWorkerState

### Step 1: Extend DerivedWorkerState with missing health signals (`gpu_orchestrator/worker_state.py`)
**Scope:** Medium

Currently `derive_worker_state()` sets `should_terminate` for timeout conditions but doesn't cover:
- Idle-with-tasks-queued (currently only in `_health_check_active_workers` line 539-562)
- Task failure streak (currently only checked in health phase line 595-610)
- Basic health check failure (currently `_perform_basic_health_check`, line 550-553)

1. **Add** `idle_with_queued_tasks: bool` field to `DerivedWorkerState` — set when worker is active, has no active task, heartbeat is fresh, but `queued_count > 0`. The health phase already receives `task_counts.queued`, and `derive_worker_state` already takes a `queued_count` parameter (it's available but unused for this check).
2. **Move** the idle-with-stale-heartbeat-while-tasks-queued termination logic from `_health_check_active_workers` (lines 543-547) into `derive_worker_state()` as a new termination condition. When `lifecycle == ACTIVE_STALE` and `queued_count > 0`, set `should_terminate = True, termination_reason = "Idle with stale heartbeat while tasks queued"`.
3. **Move** the no-heartbeat-while-tasks-queued check (lines 556-562) into `derive_worker_state()` — when active, no heartbeat, effective_age > idle_timeout, and queued > 0.
4. **Add** `is_failsafe_stale: bool` field to `DerivedWorkerState` — set when heartbeat age exceeds the failsafe cutoff regardless of lifecycle. This replaces the raw datetime logic in `_failsafe_stale_worker_check`.
5. **Verify** that `startup_phase` gating for failsafe is already handled: if `startup_phase` is active, `_determine_lifecycle` returns `ACTIVE_INITIALIZING`, which has its own timeout (`startup_grace_period_sec`). The failsafe's separate `startup_phase` check becomes redundant once we use derived state.

### Step 2: Simplify `_health_check_active_workers` to a thin executor (`gpu_orchestrator/control_loop.py:484-615`)
**Scope:** Medium

1. **Remove** all inline health re-derivation from `_health_check_active_workers`:
   - Delete the idle-with-tasks-queued block (lines 538-562) — now in derived state
   - Delete the idle-timeout-with-no-work block (lines 564-573) — already in derived state as `is_not_claiming`
   - Keep the `ws.should_terminate` check at the top (lines 499-518) — this is the correct pattern
2. **Simplify** the has-active-task path (lines 521-530): keep as-is since it validates heartbeat *existence* (a real-time check, not a verdict)
3. **Keep** `_check_stuck_tasks` and `_check_worker_task_failure_streak` calls — these are side-effect-producing checks that query the DB for task history. They can stay in the health phase for now, but their *verdicts* should flow through DerivedWorkerState in a future pass.
4. **Remove** the redundant `_perform_basic_health_check` call at line 587-589 — this checks `heartbeat_age < 60s`, which is already captured by `heartbeat_is_recent` in derived state.

### Step 3: Rewrite `_failsafe_stale_worker_check` to use derived state (`gpu_orchestrator/control_loop.py:1577-1641`)
**Scope:** Small

1. **Change signature** to accept `worker_states: List[DerivedWorkerState]` instead of raw `workers: List[Dict]`.
2. **Replace** the raw datetime loop (lines 1583-1613) with a filter: `stale = [ws for ws in worker_states if ws.is_failsafe_stale and not ws.is_terminal]`.
3. **Keep** the termination action loop (lines 1615-1638) but use `ws.runpod_id` and `ws.worker_id` from derived state.
4. **Delete** the inline `startup_phase` gate (lines 1592-1595) — redundant with `_determine_lifecycle` handling.

### Step 4: Add/update tests for consolidated verdicts (`tests/gpu_orchestrator/`)
**Scope:** Medium

1. **Add** unit tests for the new `derive_worker_state()` conditions:
   - `test_idle_stale_heartbeat_with_queued_tasks_should_terminate`
   - `test_no_heartbeat_with_queued_tasks_should_terminate`
   - `test_is_failsafe_stale_set_correctly`
   - `test_startup_phase_prevents_failsafe_stale`
2. **Update** existing tests in `tests/test_scaling_decision_spawn.py` and `tests/test_scaling_decision_limits.py` if they reference the old health check paths.
3. **Run** the full test suite to verify no regressions.

## Phase 2: Wire in Phase Mixins (Decompose the God Object)

### Step 5: Create mixin base and wire phase modules into `OrchestratorControlLoop` (`gpu_orchestrator/control_loop.py`, `gpu_orchestrator/control/phases/*.py`)
**Scope:** Large

1. **Update** each phase mixin to use the simplified logic from Phase 1 (not the old copied code that still has inline health checks).
2. **Make** `OrchestratorControlLoop` inherit from the mixins:
   ```python
   class OrchestratorControlLoop(
       StatePhaseMixin,
       HealthPhaseMixin,
       LifecyclePhaseMixin,
       WorkerCapacityPhaseMixin,
       ScalingDecisionPhaseMixin,
       PeriodicChecksMixin,
   ):
   ```
3. **Delete** the corresponding inline methods from `control_loop.py` as each mixin method is verified to work. Target: reduce `control_loop.py` from 1699 lines to ~300 (init, `run_single_cycle`, shared helpers like `_mark_worker_error`).
4. **Move** shared helpers (`_mark_worker_error`, `_mark_worker_error_by_id`, `_log_detailed_task_breakdown`, `_log_scaling_decision`) to a `control/phases/shared.py` mixin or keep them on the host class — they satisfy the `ControlHost` protocol.
5. **Verify** `ControlHost` protocol (`control/contracts.py`) is satisfied by the resulting class.

### Step 6: Integration test the full cycle (`tests/gpu_orchestrator/`)
**Scope:** Medium

1. **Run** existing integration tests that exercise `run_single_cycle`.
2. **Add** a test that verifies the mixin composition: instantiate `OrchestratorControlLoop` and confirm all phase methods resolve.
3. **Run** `_failsafe_stale_worker_check` test with mocked derived states to confirm it no longer does raw datetime parsing.

## Execution Order

1. Steps 1-3 first (Phase 1) — consolidate verdicts. This is the high-value change and can land independently.
2. Step 4 — tests to lock in the new behavior before wiring mixins.
3. Steps 5-6 (Phase 2) — decompose the god object. This is mechanical refactoring that's lower risk once verdicts are consolidated.

## Validation Order

1. Unit tests for `derive_worker_state()` (Step 4) — cheapest, most targeted.
2. Grep for any remaining raw heartbeat datetime arithmetic outside `worker_state.py` — there should be none after Step 3.
3. Full test suite after each step to catch regressions.
4. Manual verification: read the final `_health_check_active_workers` and confirm it makes NO health decisions — only executes verdicts from `DerivedWorkerState`.
