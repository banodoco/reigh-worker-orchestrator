# Implementation Plan: Single Source of Truth for Worker Health Verdicts

## Overview

The GPU orchestrator has **two** places that independently re-derive health verdicts already computed by `DerivedWorkerState`:

1. **`_health_check_active_workers()`** (`control_loop.py:484-615`) — Contains inline checks that **duplicate** verdicts from `derive_worker_state()`, plus dead-code branches that are unreachable when using derived state, alongside **legitimate** async checks that must remain.
2. **`_failsafe_stale_worker_check()`** (`control_loop.py:1577-1641`) — Raw datetime arithmetic on heartbeats, completely independent of `DerivedWorkerState`. Has its own `startup_phase` gate that duplicates `worker_state.py:308`.

**Corrected root cause (vs. v1 plan):** `_reconcile_orphaned_tasks()` (`control_loop.py:766-795`) is NOT a competing health path — it only resets tasks for workers already in `ERROR`/`TERMINATED` state. The actual duplication is narrower than v1 assumed:

- `derive_worker_state()` (`worker_state.py:191-223`) **already handles** stale-heartbeat-with-queued-tasks (line 197-200), idle-with-queued-tasks (line 209-223), GPU-not-detected timeout, spawning timeout, and startup grace period.
- The health phase re-checks stale+queued at lines 539-547 (pure duplicate) and has unreachable no-heartbeat branches at lines 521-528 and 556-562 (active workers always have heartbeats by definition of `_determine_lifecycle`).
- The health phase's **legitimate non-derived logic** that must stay: idle-timeout-with-no-work (line 564-573, uses async `_is_worker_idle_with_timeout` DB queries), `_check_stuck_tasks`, `_check_worker_task_failure_streak`, and `_perform_basic_health_check` (heartbeat < 60s tighter check).

**Startup phase safety constraint:** `_determine_lifecycle()` only checks `startup_phase` inside the `if has_heartbeat:` branch (line 298-308). Pre-heartbeat workers in startup phases (e.g., `deps_installing`) fall into the `SPAWNING_*` path. The failsafe's separate `startup_phase` guard (lines 1592-1595) protects these workers when their DB status is `active` but they haven't heartbeated yet. **This guard cannot simply be deleted** — it must be preserved in derived state.

**Scope:** This plan consolidates health verdicts only. God-object decomposition (mixin wiring) is a separate follow-up — the mixin infrastructure already exists in `control/phases/` and doesn't need to be bundled here.

## Main Phase

### Step 1: Add `in_startup_phase` to DerivedWorkerState (`gpu_orchestrator/worker_state.py:40-108`)
**Scope:** Small

The failsafe currently reads raw `metadata.startup_phase` to skip workers in startup. To make failsafe consume derived state without losing this protection, we need to surface the signal.

1. **Add** field `in_startup_phase: bool` to `DerivedWorkerState` (after `ready_for_tasks`, around line 68).
2. **Set** it in `derive_worker_state()` — `in_startup_phase = startup_phase in ('deps_installing', 'deps_verified', 'worker_starting')`. This is set **regardless of heartbeat status**, capturing both the heartbeat and no-heartbeat paths.
3. **Set** it in the return statement (`worker_state.py:250-274`).

This is the key safety addition: failsafe stale detection can now skip startup workers by reading `ws.in_startup_phase` instead of parsing raw metadata.

### Step 2: Remove duplicate and dead-code health checks from `_health_check_active_workers` (`gpu_orchestrator/control_loop.py:484-615`)
**Scope:** Medium

Precise identification of what to remove vs. keep:

**Remove — duplicate of derived state (lines 539-547):**
```python
if task_counts.queued > 0:
    if ws.has_heartbeat and ws.heartbeat_age_sec is not None:
        if ws.heartbeat_age_sec > self.config.gpu_idle_timeout_sec:
            # This is STALE_HEARTBEAT_TASKS_QUEUED — already in derive_worker_state line 197-200
```
This entire stale+queued branch fires only when `ACTIVE_STALE` + queued > 0, which `derive_worker_state` already sets as `should_terminate=True` with error code `STALE_HEARTBEAT_TASKS_QUEUED`. The `ws.should_terminate` check at line 499 handles it.

**Remove — unreachable dead code (lines 521-528):**
```python
if ws.has_active_task:
    if not ws.has_heartbeat:  # Can never be True for active workers
```
`is_active` (line 491 filter) requires `ACTIVE_*` lifecycle. `_determine_lifecycle` only returns `ACTIVE_*` when `has_heartbeat=True` (line 298). So `not ws.has_heartbeat` is always False here.

**Remove — unreachable dead code (lines 556-562):**
```python
else:  # No heartbeat while tasks are queued
    worker_age = ...
```
Same reasoning — active workers always have heartbeats. This branch is unreachable.

**Keep — legitimate async/runtime checks:**
- Lines 549-553: `_perform_basic_health_check` (tighter 60s heartbeat check for fresh-but-not-claiming workers with queued tasks). Not a duplicate — `derive_worker_state` uses `gpu_idle_timeout_sec` for ACTIVE_STALE, while this uses a tighter 60s window.
- Lines 564-573: idle-timeout-with-no-work via `_is_worker_idle_with_timeout()`. This queries task completion history (async DB call at `control_loop.py:1271-1302`) and is used by scale-down. Cannot be pre-derived.
- Lines 592-610: `_check_stuck_tasks` and `_check_worker_task_failure_streak` — async DB queries, side-effect producing.
- Lines 587-589: `_perform_basic_health_check` for idle workers with fresh heartbeat (supplementary signal).

### Step 3: Rewrite `_failsafe_stale_worker_check` to use derived state (`gpu_orchestrator/control_loop.py:1577-1641`)
**Scope:** Medium

1. **Change signature** to accept `worker_states: List[DerivedWorkerState]` instead of raw `workers: List[Dict]`.
2. **Replace** the raw datetime loop (lines 1583-1613) with a filter on derived state:
   ```python
   stale_workers = []
   for ws in worker_states:
       if ws.is_terminal:
           continue
       if ws.in_startup_phase:
           continue  # Preserve startup protection — matches current line 1592-1595
       if ws.should_terminate:
           continue  # Already handled by health phase
       # Use derived heartbeat_age_sec and effective_age_sec instead of raw datetime parsing
       cutoff_sec = self.config.spawning_timeout_sec if ws.is_spawning else self.config.gpu_idle_timeout_sec
       age = ws.heartbeat_age_sec if ws.has_heartbeat else ws.effective_age_sec
       if age is not None and age > cutoff_sec:
           stale_workers.append(ws)
   ```
3. **Update** the termination loop (lines 1615-1638) to use `ws.runpod_id` and `ws.worker_id` instead of raw dict access.
4. **Update** the caller in `_run_periodic_checks` to pass `worker_states` instead of raw `workers`.

**Safety note:** The `in_startup_phase` check preserves the exact semantics of the current `startup_phase` guard at lines 1592-1595, including protection for pre-heartbeat workers whose `_determine_lifecycle` path doesn't check `startup_phase`.

### Step 4: Update `_run_periodic_checks` caller (`gpu_orchestrator/control_loop.py`)
**Scope:** Small

1. **Find** the `_run_periodic_checks` method and update its call to `_failsafe_stale_worker_check` to pass `worker_states` (derived) instead of raw `workers`.
2. **Verify** that `worker_states` is available in scope at the call site — it's passed as a parameter to `_run_periodic_checks` already or can be threaded through from `run_single_cycle`.

### Step 5: Update stale phase copies (`gpu_orchestrator/control/phases/health.py`, `gpu_orchestrator/control/phases/periodic.py`)
**Scope:** Small

The `control/phases/` files contain stale copies of the old logic. Update them to match the changes from Steps 2-3 so they don't drift further:

1. **Update** `phases/health.py` `_health_check_active_workers` — remove the same duplicate/dead-code branches removed in Step 2.
2. **Update** `phases/periodic.py` `_failsafe_stale_worker_check` — apply the same derived-state rewrite from Step 3.

### Step 6: Add tests for the changes (`tests/gpu_orchestrator/`)
**Scope:** Medium

1. **Add** unit tests for `derive_worker_state()`:
   - `test_in_startup_phase_set_with_heartbeat` — startup_phase='deps_installing' + has_heartbeat → `in_startup_phase=True`
   - `test_in_startup_phase_set_without_heartbeat` — startup_phase='deps_installing' + no heartbeat → `in_startup_phase=True` (the key safety case)
   - `test_in_startup_phase_false_when_none` — no startup_phase → `in_startup_phase=False`
2. **Add** regression test for the startup-phase failsafe protection:
   - `test_failsafe_skips_startup_phase_workers` — worker with `in_startup_phase=True` is not marked stale by failsafe
3. **Verify** existing tests still pass — the derived state changes are additive (new field), and health check changes only remove duplicate/dead paths.
4. **Run** full test suite.

## Execution Order

1. Step 1 first — additive change to DerivedWorkerState, zero risk, enables everything else.
2. Steps 2-4 together — remove duplicates from health phase and rewrite failsafe. These are coupled.
3. Step 5 — sync stale phase copies after the canonical code is updated.
4. Step 6 — tests to lock in behavior.

## Validation Order

1. Unit tests for `derive_worker_state()` with new `in_startup_phase` field (Step 6.1-6.3).
2. Grep for raw `startup_phase` metadata access outside `worker_state.py` — after Step 3, the failsafe should read `ws.in_startup_phase` not `metadata.get('startup_phase')`.
3. Grep for raw heartbeat datetime arithmetic in `_failsafe_stale_worker_check` — should be zero after Step 3.
4. Manual verification: read final `_health_check_active_workers` and confirm the only verdict-computing branch is `ws.should_terminate` at the top. The remaining branches should be async DB queries or runtime probes, not heartbeat arithmetic.
5. Full test suite pass.
