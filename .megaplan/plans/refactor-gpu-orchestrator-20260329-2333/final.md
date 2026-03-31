# Execution Checklist

- [x] **T1:** Add `in_startup_phase: bool` field to DerivedWorkerState and set it in derive_worker_state(). In `gpu_orchestrator/worker_state.py`: (1) Add `in_startup_phase: bool` field to the DerivedWorkerState dataclass after `ready_for_tasks` (around line 65-67). (2) In `derive_worker_state()`, compute `in_startup_phase = startup_phase in ('deps_installing', 'deps_verified', 'worker_starting')` — this must be set BEFORE the `_determine_lifecycle` call and REGARDLESS of heartbeat status (this is the key safety property). The `startup_phase` variable is already extracted from metadata earlier in the function. (3) Include `in_startup_phase=in_startup_phase` in the DerivedWorkerState return statement (around lines 250-274).
  Executor notes: Added `in_startup_phase: bool` after `ready_for_tasks`, extracted `startup_phase` once, computed `in_startup_phase = startup_phase in ('deps_installing', 'deps_verified', 'worker_starting')` before `_determine_lifecycle`, and returned it in `DerivedWorkerState`. Verified the safety-critical pre-heartbeat case with a targeted `derive_worker_state()` call: `startup_phase='deps_installing'`, `last_heartbeat=None` yielded `in_startup_phase=True` and spawning lifecycle.
  Files changed:
    - gpu_orchestrator/worker_state.py
  Reviewer verdict: Pass. `gpu_orchestrator/worker_state.py` adds `in_startup_phase`, computes it before `_determine_lifecycle()`, and returns it on the derived state, preserving the pre-heartbeat startup safety case.
  Evidence files:
    - gpu_orchestrator/worker_state.py
    - tests/gpu_orchestrator/control/phases/test_state.py

- [x] **T2:** Remove duplicate and dead-code health checks from `_health_check_active_workers` in `gpu_orchestrator/control_loop.py` (lines 484-615). Three removals:

**REMOVE 1 — Duplicate stale+queued check (lines 539-547):** The block `if task_counts.queued > 0: if ws.has_heartbeat and ws.heartbeat_age_sec is not None: if ws.heartbeat_age_sec > self.config.gpu_idle_timeout_sec:` duplicates the STALE_HEARTBEAT_TASKS_QUEUED verdict already handled by `ws.should_terminate` at line 499. Remove this entire stale branch. KEEP the `else` at line 548-554 (_perform_basic_health_check for fresh heartbeat with queued tasks).

**REMOVE 2 — Unreachable no-heartbeat-with-active-task (lines 521-528):** `if ws.has_active_task: if not ws.has_heartbeat:` — active workers always have heartbeats (is_active requires ACTIVE_* lifecycle which requires has_heartbeat=True in _determine_lifecycle). This entire branch is dead code.

**REMOVE 3 — Unreachable no-heartbeat-with-queued-tasks (lines 555-562):** The `else:` branch under `if ws.has_heartbeat and ws.heartbeat_age_sec is not None:` — same reasoning, active workers always have heartbeats, so `else` (no heartbeat) is unreachable.

**KEEP:** (a) _perform_basic_health_check (lines 549-554), (b) idle-timeout-with-no-work (lines 564-573), (c) _check_stuck_tasks (line 592), (d) _check_worker_task_failure_streak (lines 595-610), (e) _perform_basic_health_check for idle workers (lines 587-589). After removal, the `if task_counts.queued > 0:` block should only contain the basic health check path (fresh heartbeat, not claiming, perform basic health check).
  Depends on: T1
  Executor notes: Removed the duplicate stale-heartbeat queued-task termination branch and both unreachable no-heartbeat branches from `_health_check_active_workers`. Verification on the method body confirms the queued-task path now only performs `_perform_basic_health_check(worker)`, and the removed strings `Idle with stale heartbeat`, `No heartbeat with active tasks`, and `No heartbeat or activity with tasks queued` are absent. `python -m py_compile gpu_orchestrator/control_loop.py` passed.
  Files changed:
    - gpu_orchestrator/control_loop.py
  Reviewer verdict: Pass. The duplicate stale+queued termination branch and both no-heartbeat dead branches are removed from `_health_check_active_workers()`, while the queued basic health check and no-work idle-timeout path remain.
  Evidence files:
    - gpu_orchestrator/control_loop.py

- [x] **T3:** Rewrite `_failsafe_stale_worker_check` (control_loop.py:1577-1641) to accept `List[DerivedWorkerState]` and use derived fields instead of raw datetime parsing. Also update `_run_periodic_checks` (control_loop.py:1032-1071) to pass `worker_states` directly instead of re-fetching raw workers.

**Step 3a — Change _failsafe_stale_worker_check signature** from `workers: List[Dict[str, Any]]` to `worker_states: List[DerivedWorkerState]`.

**Step 3b — Replace the stale detection loop (lines 1583-1613)** with derived-state logic:
- Skip `ws.is_terminal` workers (replaces `worker['status'] == 'terminated'`)
- Skip `ws.in_startup_phase` workers (replaces raw `metadata.get('startup_phase')` check at lines 1592-1595)
- Skip `ws.should_terminate` workers (already handled by health phase)
- Compute: `cutoff_sec = self.config.spawning_timeout_sec if ws.is_spawning else self.config.gpu_idle_timeout_sec`
- Compute: `age = ws.heartbeat_age_sec if ws.has_heartbeat else ws.effective_age_sec`
- If `age is not None and age > cutoff_sec`, mark as stale

**Step 3c — Update the termination loop (lines 1615-1638)** to use `ws.runpod_id` and `ws.worker_id` instead of raw dict access. This requires fetching the raw worker dict only for the actual termination action (db.update_worker_status, db.reset_orphaned_tasks, runpod.terminate_worker).

**Step 3d — Update _run_periodic_checks** (lines 1059-1066): Remove the `all_workers` re-fetch loop and pass `worker_states` directly to `_failsafe_stale_worker_check`. The current code fetches raw workers from DB at lines 1060-1064 just to pass to failsafe — this is no longer needed.

**IMPORTANT: Keep `_efficient_zombie_check` call at the end unchanged.**
  Depends on: T1
  Executor notes: Rewrote `_failsafe_stale_worker_check` to accept `List[DerivedWorkerState]`, skip `ws.is_terminal`, `ws.in_startup_phase`, and `ws.should_terminate`, compute staleness from `ws.heartbeat_age_sec` or `ws.effective_age_sec`, and use `ws.worker_id`/`ws.runpod_id` in the termination loop with raw worker fetches only inside that loop. `_run_periodic_checks` now passes `worker_states` directly. Verification confirms the failsafe block contains zero instances of `metadata.get('startup_phase')`, `datetime.fromisoformat`, `worker['status']`, and `worker.get('last_heartbeat')`; `python -m py_compile gpu_orchestrator/control_loop.py` passed.
  Files changed:
    - gpu_orchestrator/control_loop.py
  Reviewer verdict: Pass. `_failsafe_stale_worker_check()` now accepts `List[DerivedWorkerState]`, reads derived fields only, skips startup and already-terminating workers, and `_run_periodic_checks()` passes `worker_states` directly.
  Evidence files:
    - gpu_orchestrator/control_loop.py

- [x] **T4:** Update stale phase copies in `gpu_orchestrator/control/phases/health.py` and `gpu_orchestrator/control/phases/periodic.py` to match the changes from T2 and T3. These files contain stale copies of the control_loop methods and need to be kept in sync even though they are not wired in yet.

(1) In `phases/health.py` `_health_check_active_workers`: remove the same duplicate stale+queued branch and dead-code no-heartbeat branches removed in T2.
(2) In `phases/periodic.py` `_failsafe_stale_worker_check`: apply the same derived-state rewrite from T3 — accept `List[DerivedWorkerState]`, use `ws.in_startup_phase`, use derived timing fields instead of raw datetime parsing.
(3) In `phases/periodic.py` `_run_periodic_checks`: update the caller to pass `worker_states` directly instead of re-fetching raw workers, matching T3d.
  Depends on: T2, T3
  Executor notes: Updated `gpu_orchestrator/control/phases/health.py` to remove the same duplicate stale-heartbeat queued-task branch and unreachable no-heartbeat branches removed from the canonical `_health_check_active_workers`. Updated `gpu_orchestrator/control/phases/periodic.py` so `_run_periodic_checks` passes `worker_states` directly and `_failsafe_stale_worker_check` accepts `List[DerivedWorkerState]`, skips `ws.is_terminal` / `ws.in_startup_phase` / `ws.should_terminate`, derives age from `ws.heartbeat_age_sec` or `ws.effective_age_sec`, and uses `ws.db_status`/`ws.runpod_id` in termination handling. Verification: `python -m py_compile gpu_orchestrator/control/phases/health.py gpu_orchestrator/control/phases/periodic.py` passed; targeted source checks confirmed the removed health-branch strings are absent and the periodic mixin contains the same derived-state failsafe markers as the canonical control loop. Both files currently appear as untracked in `git status`, so verification relied on direct source inspection rather than `git diff`.
  Files changed:
    - gpu_orchestrator/control/phases/health.py
    - gpu_orchestrator/control/phases/periodic.py
  Reviewer verdict: Pass. The stale phase copies mirror the canonical health and periodic logic, and there is no evidence of accidental mixin wiring beyond keeping the copies in sync.
  Evidence files:
    - gpu_orchestrator/control/phases/health.py
    - gpu_orchestrator/control/phases/periodic.py
    - gpu_orchestrator/control_loop.py

- [x] **T5:** Add tests and run full test suite. Tests go in existing test files under `tests/gpu_orchestrator/`.

**New tests for derive_worker_state (in tests/gpu_orchestrator/control/phases/test_state.py or similar):**
1. `test_in_startup_phase_set_with_heartbeat` — worker with startup_phase='deps_installing' AND has_heartbeat=True → `in_startup_phase=True`
2. `test_in_startup_phase_set_without_heartbeat` — worker with startup_phase='deps_installing' AND no heartbeat → `in_startup_phase=True` (KEY SAFETY CASE: pre-heartbeat workers must be protected)
3. `test_in_startup_phase_false_when_none` — worker with no startup_phase → `in_startup_phase=False`
4. `test_in_startup_phase_false_when_running` — worker with startup_phase='running' (past startup) → `in_startup_phase=False`

**Regression test for failsafe:**
5. `test_failsafe_skips_startup_phase_workers` — worker with `in_startup_phase=True` is NOT included in stale list

**Run full test suite:** `python -m pytest tests/ -v`. If tests fail, read errors, fix the code, and re-run until all pass. Verify grep: no raw `metadata.get('startup_phase')` in `_failsafe_stale_worker_check`, no raw datetime arithmetic in failsafe.
  Depends on: T2, T3, T4
  Executor notes: Added four startup-phase tests in `tests/gpu_orchestrator/control/phases/test_state.py` covering the with-heartbeat, without-heartbeat, no-startup-phase, and post-startup (`running`) cases, and added `test_failsafe_skips_startup_phase_workers` in `tests/gpu_orchestrator/control/phases/test_periodic.py`. Updated `tests/scaling_decision_helpers.py` for the current dataclass/config signatures by adding the new `in_startup_phase` field to `make_worker_state()` and removing the stale `max_task_wait_minutes` config input that caused the first pytest run to fail. Verification: after that helper fix, `python -m pytest tests/ -v` passed with `74 passed, 0 failed`; the canonical `_failsafe_stale_worker_check` block also verified clean for `metadata.get('startup_phase')`, `datetime.fromisoformat`, `worker.get('last_heartbeat')`, and `worker['created_at']` (all absent).
  Files changed:
    - tests/gpu_orchestrator/control/phases/test_state.py
    - tests/gpu_orchestrator/control/phases/test_periodic.py
    - tests/scaling_decision_helpers.py
  Reviewer verdict: Pass. The startup-phase derivation tests and failsafe regression test are present, and the shared helper was updated to match the new `DerivedWorkerState`/`OrchestratorConfig` signatures required for the reported green suite.
  Evidence files:
    - tests/gpu_orchestrator/control/phases/test_state.py
    - tests/gpu_orchestrator/control/phases/test_periodic.py
    - tests/scaling_decision_helpers.py

## Watch Items

- [SAFETY-CRITICAL] in_startup_phase must be set REGARDLESS of heartbeat status. Pre-heartbeat workers (e.g., deps_installing with db_status=active but no heartbeat) fall into the SPAWNING_* path in _determine_lifecycle, which does NOT check startup_phase. The failsafe's startup_phase guard is their ONLY protection. Verify test_in_startup_phase_set_without_heartbeat passes.
- [DEBT] The failsafe still computes age > cutoff locally rather than consuming a precomputed is_failsafe_stale verdict from DerivedWorkerState. This is an accepted tradeoff — do NOT introduce is_failsafe_stale in this plan, but do NOT make the duplication worse either.
- [CONTROL FLOW] After removing the stale+queued duplicate (lines 539-547), verify the `if task_counts.queued > 0:` block still flows correctly into the basic health check path (lines 549-554). The else clause at line 548 must become the primary path.
- [CONTROL FLOW] After removing the no-heartbeat dead code (lines 555-562), verify the idle-timeout-with-no-work path (lines 564-573) is still reachable. It's under `else: # No tasks queued` which is independent of the removed code.
- [SCOPE] Do NOT wire in the control/phases/ mixins. Only update the stale copies to match canonical code. Mixin wiring is a separate follow-up.
- [SCOPE] Do NOT touch _reconcile_orphaned_tasks — it is NOT a competing health path (only handles ERROR/TERMINATED workers).
- [TYPE SAFETY] The _failsafe_stale_worker_check termination loop needs raw worker dicts for db.update_worker_status, db.reset_orphaned_tasks, and runpod.terminate_worker. Fetch raw worker inside the termination loop only for workers that need action, not upfront for all workers.
- [EDGE CASE] In failsafe rewrite, handle age=None gracefully. effective_age_sec should always be set, but heartbeat_age_sec can be None for no-heartbeat workers. The `age = ws.heartbeat_age_sec if ws.has_heartbeat else ws.effective_age_sec` pattern handles this.

## Sense Checks

- **SC1** (T1): Does the new `in_startup_phase` field get set to True for a worker with startup_phase='deps_installing' that has NO heartbeat (i.e., is in SPAWNING_* lifecycle)? This is the key safety property.
  Executor note: Yes. Verified directly: a worker with `startup_phase='deps_installing'` and no heartbeat produced `in_startup_phase=True` while in `spawning_script_running`, confirming the flag is set independently of heartbeat presence.
  Verdict: Confirmed. `in_startup_phase` is computed independently of heartbeat presence in `gpu_orchestrator/worker_state.py`, and `test_in_startup_phase_set_without_heartbeat` covers the pre-heartbeat spawning case explicitly.

- **SC2** (T2): After removing the stale+queued duplicate, is `ws.should_terminate` at line 499 the ONLY branch that terminates workers based on heartbeat staleness with queued tasks? Confirm the remaining queued-tasks path only does _perform_basic_health_check (60s threshold).
  Executor note: Yes. In `_health_check_active_workers`, the stale-heartbeat queued-task termination path was removed, so `ws.should_terminate` is now the only stale-heartbeat kill path for queued work. The remaining `if task_counts.queued > 0:` branch only calls `_perform_basic_health_check(worker)`.
  Verdict: Confirmed. In `gpu_orchestrator/control_loop.py`, `ws.should_terminate` is the only stale-heartbeat verdict path in `_health_check_active_workers()`, and the remaining queued-work branch only invokes `_perform_basic_health_check(worker)`.

- **SC3** (T3): Does `_failsafe_stale_worker_check` contain zero instances of: (a) `metadata.get('startup_phase')`, (b) `datetime.fromisoformat`, (c) `worker['status']`, (d) `worker.get('last_heartbeat')`? All should be replaced by derived state fields.
  Executor note: Yes. Verified on the `_failsafe_stale_worker_check` method body: it contains zero instances of `metadata.get('startup_phase')`, `datetime.fromisoformat`, `worker['status']`, and `worker.get('last_heartbeat')`; all four checks returned `False`.
  Verdict: Confirmed. The canonical `_failsafe_stale_worker_check()` contains none of the forbidden raw-data patterns and uses only derived-state fields plus a raw worker fetch inside the cleanup loop.

- **SC4** (T4): Do the phase copies in health.py and periodic.py match the canonical code in control_loop.py after updates? Diff the key methods to confirm they are in sync.
  Executor note: Yes. The phase copies now match the canonical T2/T3 logic at the key-method level: `health.py` only keeps the queued-task basic health-check path and removes the stale/no-heartbeat branches, while `periodic.py` passes `worker_states` directly and its failsafe uses `ws.in_startup_phase`, `ws.should_terminate`, and derived age fields with no raw startup-phase or datetime parsing. Verified with targeted source checks plus successful `py_compile` on both phase files.
  Verdict: Confirmed. `gpu_orchestrator/control/phases/health.py` and `gpu_orchestrator/control/phases/periodic.py` reflect the same key branch structure as the canonical methods: no duplicate stale/no-heartbeat health branches, and failsafe logic driven by `DerivedWorkerState` fields.

- **SC5** (T5): Do all existing tests pass, and do the new tests cover both the with-heartbeat and without-heartbeat cases for in_startup_phase? Run `python -m pytest tests/ -v` and confirm zero failures.
  Executor note: Yes. The new tests cover both startup-phase cases with heartbeat and without heartbeat, plus the false cases for missing/past-startup values, and the periodic regression verifies startup-phase workers are skipped by the failsafe. `python -m pytest tests/ -v` completed with `74 passed, 0 failed`.
  Verdict: Confirmed. The new tests cover both startup-phase heartbeat states plus the negative cases, and the executor’s reported full-suite result is consistent with the helper/test updates now present in the tree.

## Meta

**Execution guidance:**

**Order:** T1 first (additive, zero-risk field addition). T2 and T3 can be done in parallel since they modify different sections of control_loop.py (T2: lines 484-615, T3: lines 1032-1071 and 1577-1641). T4 after both. T5 last.

**Key judgment call — the unresolved flag:** The gate noted that the failsafe still computes `age > cutoff` locally rather than reading a precomputed verdict. The plan accepts this as an incremental improvement: the failsafe now reads derived fields (heartbeat_age_sec, effective_age_sec, in_startup_phase) instead of raw data, eliminating datetime parsing and raw metadata access. Adding an `is_failsafe_stale` boolean to DerivedWorkerState would couple the pure derivation function to orchestrator config thresholds, which may not be desirable. Do NOT add this field — it's explicitly out of scope.

**Biggest risk:** T2's surgery on _health_check_active_workers. The three removal targets are interleaved with code that must stay. Work carefully: remove the stale+queued block (539-547) first, then the two no-heartbeat dead-code blocks (521-528, 555-562). After each removal, re-read the method to verify control flow integrity. The idle-timeout path (564-573) and basic health check path (549-554) must remain functional.

**T3's DB fetch pattern:** The current _run_periodic_checks fetches raw workers from DB just to pass to failsafe (lines 1060-1064). After rewrite, failsafe accepts DerivedWorkerState directly. But the termination loop inside failsafe still needs raw workers for DB operations — fetch them inside the stale-worker termination loop, not upfront. This avoids unnecessary DB calls for non-stale workers.

**Test approach:** Look at existing test patterns in tests/gpu_orchestrator/control/phases/test_state.py for how DerivedWorkerState is tested. Follow the same helper/fixture patterns. The tests for in_startup_phase should construct minimal worker dicts with startup_phase in metadata and verify the field is set correctly regardless of heartbeat presence.
