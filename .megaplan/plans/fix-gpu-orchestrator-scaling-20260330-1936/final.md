# Execution Checklist

- [x] **T1:** Phase 1 bug fixes: Apply all 5 code fixes across both control_loop.py and phase mixins simultaneously.

**Step 1 — Remove idle-timeout termination from health check:**
- `control_loop.py:539-548`: Remove the `else` block that calls `_mark_worker_error` for idle timeout (the block starting with `if await self._is_worker_idle_with_timeout(worker, self.config.gpu_idle_timeout_sec):` through `continue`). Keep the preceding `if task_counts.queued > 0:` block and all code after line 548.
- `control/phases/health.py:126-137`: Remove the matching block (`if await self._is_worker_idle_with_timeout(...)` through `return True`). Keep `return False` at the end.

**Step 2 — Fix desired_floor in scaling decision:**
- `worker_state.py:461`: Change `desired_floor = max(config.min_active_gpus, busy_count + config.machines_to_keep_idle)` → `desired_floor = max(config.min_active_gpus, busy_count + max(config.machines_to_keep_idle, 1))`
- `worker_state.py:488`: Change `buffer_based = busy_count + config.machines_to_keep_idle` → `buffer_based = busy_count + max(config.machines_to_keep_idle, 1)`

**Step 3 — Fix early_desired in early termination path:**
- `control_loop.py:346`: Change `early_desired = max(config.min_active_gpus, task_based, config.machines_to_keep_idle)` → `early_desired = max(config.min_active_gpus, task_based, max(config.machines_to_keep_idle, 1))`
- `lifecycle.py:45` (exact line: `early_desired = max(config.min_active_gpus, task_based, config.machines_to_keep_idle)`): Apply identical change.

**Step 4 — Fix spawn failures poisoning circuit breaker:**
- `control_loop.py:1336` (the line `await self.db.mark_worker_error(worker_id, 'Failed to spawn Runpod instance')`): Replace with `await self.db.update_worker_status(worker_id, 'terminated', {'termination_reason': 'spawn_failed', 'terminated_at': datetime.now(timezone.utc).isoformat()})`
- `worker_capacity.py:253` (the line `await self.db.mark_worker_error(worker_id, "Failed to spawn Runpod instance")`): Apply identical change.

**Step 5 — Fix scale-down buffer in execute_scaling:**
- `control_loop.py:941`: Change `max_by_buffer = max(0, decision.idle_count - config.machines_to_keep_idle)` → `max_by_buffer = max(0, decision.idle_count - max(config.machines_to_keep_idle, 1))`
- `worker_capacity.py:45` (the matching `max_by_buffer` line): Apply identical change.

**FLAG-003 extras (from gate warnings):**
- `scaling_decision.py:70`: Change `buffer_based = busy_count + config.machines_to_keep_idle` → `buffer_based = busy_count + max(config.machines_to_keep_idle, 1)` (legacy mixin)
- `control_loop.py:833`: Change `buffer_based = busy_count + config.machines_to_keep_idle` → `buffer_based = busy_count + max(config.machines_to_keep_idle, 1)` (legacy method in control_loop.py)

**Step 10 partial — Add TODO comment to legacy path:**
- At the top of `_calculate_scaling_decision_legacy` in `control_loop.py`, add: `# TODO: Remove legacy scaling path (DECISION-003)`
  Executor notes: Applied the requested Phase 1 fixes in both the legacy control_loop.py path and the corresponding phase mixins: removed PATH A idle-timeout termination, enforced max(config.machines_to_keep_idle, 1) in desired_floor/buffer/early_desired/scale-down buffer calculations, changed spawn-failure handling from error to terminated with termination_reason=spawn_failed, and added the DECISION-003 TODO comment to control_loop.py legacy scaling. Verification: greps show scaling/termination logic now uses max(..., 1) everywhere relevant, health.py no longer contains the idle-timeout termination block, and spawn failures no longer call mark_worker_error.
  Files changed:
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/control_loop.py
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/worker_state.py
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/control/phases/health.py
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/control/phases/lifecycle.py
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/control/phases/worker_capacity.py
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/control/phases/scaling_decision.py
  Reviewer verdict: Pass. The Phase 1 fixes are present in the main loop and mirrored mixins: idle-timeout termination was removed from health, the minimum warm-buffer floor is enforced consistently, the legacy path keeps the floor fix, and spawn failures now land as terminated/spawn_failed in both _spawn_worker implementations.
  Evidence files:
    - gpu_orchestrator/control_loop.py
    - gpu_orchestrator/worker_state.py
    - gpu_orchestrator/control/phases/health.py
    - gpu_orchestrator/control/phases/lifecycle.py
    - gpu_orchestrator/control/phases/scaling_decision.py
    - gpu_orchestrator/control/phases/worker_capacity.py

- [x] **T2:** Phase 3: Guard PATH C failsafe against healthy idle workers.

**In `control_loop.py` `_failsafe_stale_worker_check` method (around line 1558-1562):** Inside the first `for ws in worker_states:` loop, after the existing `if ws.should_terminate: continue` check, add:
```python
# Idle healthy workers are PATH B's responsibility (scaling execution)
if ws.is_active and not ws.has_active_task and ws.heartbeat_is_recent:
    continue
```

**In `periodic.py` `_failsafe_stale_worker_check` method (around line 188-192):** Apply the identical guard after `if ws.should_terminate: continue`:
```python
# Idle healthy workers are PATH B's responsibility (scaling execution)
if ws.is_active and not ws.has_active_task and ws.heartbeat_is_recent:
    continue
```

This ensures PATH C (failsafe) only handles stuck/stale workers, not healthy idle workers that PATH B should manage.
  Executor notes: Added the healthy-idle worker guard to both failsafe implementations so PATH C skips recent-heartbeat idle workers and leaves them to PATH B scaling execution. Verification: both control_loop.py and periodic.py now continue on ws.is_active and not ws.has_active_task and ws.heartbeat_is_recent, while stale detection still uses spawning_timeout_sec for spawning workers and gpu_idle_timeout_sec for genuinely stale/lost-heartbeat workers.
  Files changed:
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/control_loop.py
    - /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator/gpu_orchestrator/control/phases/periodic.py
  Reviewer verdict: Pass. The healthy-idle guard is present in both failsafe implementations and preserves PATH B ownership for capacity-based idle termination.
  Evidence files:
    - gpu_orchestrator/control_loop.py
    - gpu_orchestrator/control/phases/periodic.py

- [x] **T3:** Update existing tests and add new test cases.

**Update existing tests:**
- `tests/test_scaling_decision_spawn.py` TestBufferMaintenance: With `machines_to_keep_idle=0` (the default in helpers), the buffer is now `max(0,1)=1`. Update assertions if tests use `machines_to_keep_idle=0` and expect buffer=0 behavior. Check `test_buffer_spawn_when_no_spawning_in_progress` (line 101) — it has 1 busy + 0 idle, buffer was `1+0=1`, now `1+max(0,1)=2`, so `spawn_reason_floor` may change.
- `tests/test_scaling_decision_limits.py` TestScaleDown: `test_no_scale_down_with_active_tasks` (line 90) has 2 idle + 1 busy, `desired` was `max(0, 0, 1+0)=1`, now `max(0, 0, 1+1)=2`, so `workers_to_terminate` changes from 1 to 0 or 1. Verify and update.
- Check `tests/scaling_decision_helpers.py` default for `machines_to_keep_idle` — it's 0, which is what makes these tests sensitive to the buffer fix.

**Add new test cases:**
1. In `tests/test_scaling_decision_spawn.py`, add `test_idle_worker_not_killed_when_only_warm_capacity`: 2 busy + 1 idle, `machines_to_keep_idle=0` → `workers_to_terminate=0`.
2. In `tests/test_scaling_decision_spawn.py`, add `test_overcapacity_respects_buffer_minimum`: 2 busy + 3 idle, `machines_to_keep_idle=0` → `workers_to_terminate=2` (keeps 1 idle).
3. In `tests/gpu_orchestrator/control/phases/test_lifecycle.py`, add `test_early_desired_respects_buffer_minimum`: unit test that early termination path uses `max(machines_to_keep_idle, 1)`.
4. Create `tests/test_circuit_breaker.py` with `test_spawn_failure_does_not_poison_failure_rate`: mock `db.get_workers` to return workers with `terminated` status and `spawn_failed` reason, call `_check_worker_failure_rate()`, verify it returns `True` (rate OK).
  Depends on: T1
  Executor notes: Updated the scaling-decision tests to match the enforced warm-capacity floor, added the two required machines_to_keep_idle=0 scale-down/buffer cases, added a lifecycle mixin test proving early termination keeps the minimum warm buffer, and added a circuit-breaker test covering terminated spawn_failed workers. Verification: python -m pytest tests/test_scaling_decision_spawn.py tests/test_scaling_decision_limits.py tests/gpu_orchestrator/control/phases/test_lifecycle.py tests/test_circuit_breaker.py -v passed with 23 tests green.
  Files changed:
    - tests/test_scaling_decision_spawn.py
    - tests/test_scaling_decision_limits.py
    - tests/gpu_orchestrator/control/phases/test_lifecycle.py
    - tests/test_circuit_breaker.py
  Reviewer verdict: Pass. The requested tests were added or updated, and they cover the warm-buffer, overcapacity, early-termination, and circuit-breaker scenarios at the intended layers. The helper default discrepancy noted in the audit is advisory only because the sensitive tests explicitly set machines_to_keep_idle=0 where needed.
  Evidence files:
    - tests/test_scaling_decision_spawn.py
    - tests/test_scaling_decision_limits.py
    - tests/gpu_orchestrator/control/phases/test_lifecycle.py
    - tests/test_circuit_breaker.py
    - tests/scaling_decision_helpers.py

- [x] **T4:** Run full test suite and verify all changes.

1. Run `python -m pytest tests/test_scaling_decision_spawn.py tests/test_scaling_decision_limits.py -v`
2. Run `python -m pytest tests/gpu_orchestrator/ -v`
3. Run `python -m pytest tests/test_circuit_breaker.py -v`
4. Run `python -m pytest tests/ -v` (full suite)
5. If any test fails, read the error, fix the code, and re-run until all pass.

**Verification greps (run after tests pass):**
- Grep for `_mark_worker_error` in health.py and control_loop.py health check section — confirm no idle-timeout callers remain.
- Grep for `machines_to_keep_idle` across all source files — confirm all uses apply `max(..., 1)` buffer (except config definition and logging).
- Grep for `mark_worker_error.*spawn` — confirm spawn failures no longer use `mark_worker_error`.
- Verify `use_new_scaling_logic` defaults to `true` in config.py (already confirmed at line 148).
  Depends on: T1, T2, T3
  Executor notes: Ran the full required verification sequence successfully with `TMPDIR=/tmp`: 20/20 passed for `tests/test_scaling_decision_spawn.py` plus `tests/test_scaling_decision_limits.py`, 21/21 passed for `tests/gpu_orchestrator/`, 1/1 passed for `tests/test_circuit_breaker.py`, and 78/78 passed for `python -m pytest tests/ -v`. Strengthened verification confirms no idle-timeout termination callers remain in the health-check path, all relevant scaling/termination uses of `machines_to_keep_idle` apply `max(config.machines_to_keep_idle, 1)`, there are no `final_status = error` assignments in either `_spawn_worker` implementation, both RunPod `status == error` branches map to `final_status = terminated` with `termination_reason=spawn_failed`, and `use_new_scaling_logic` defaults to `true` in `config.py`.
  Reviewer verdict: Pass. This was a verification-only task, so the lack of files_changed is consistent. The strengthened grep set matches the current tree, including the corrected spawn-failure mapping, and the recorded pytest runs are reasonable to accept despite this sandbox not being able to rerun them.
  Evidence files:
    - gpu_orchestrator/control_loop.py
    - gpu_orchestrator/control/phases/worker_capacity.py
    - gpu_orchestrator/config.py
    - finalize.json
    - execution_audit.json

## Watch Items

- DEBT: control-decomposition — two additional duplicated paths (legacy scaling in scaling_decision.py:70 and failsafe in periodic.py:175-229) must also get the fixes. T1 and T2 include these explicitly.
- DEBT: failsafe-health-verdict — the failsafe still computes age > cutoff locally rather than consuming a precomputed verdict from DerivedWorkerState. Do not make this worse.
- SCOPE CREEP: The gate flagged scope creep. Stick to the plan steps. Do not refactor the phase mixin architecture or remove legacy paths beyond adding a TODO comment (DECISION-003).
- DUAL LOCATIONS: Every fix must land in BOTH control_loop.py AND the corresponding phase mixin. If you fix one and miss the other, the bug persists depending on which code path runs.
- TEST SENSITIVITY: The test helper make_config() defaults machines_to_keep_idle=0. The buffer fix changes behavior for all tests using this default. Expect cascading assertion updates.
- CIRCUIT BREAKER SEMANTICS: Changing spawn failures from error→terminated changes what _check_worker_failure_rate counts. Verify the failure rate method only counts 'error' status workers, not 'terminated' with spawn_failed reason.
- datetime import: The spawn failure fix in control_loop.py needs datetime and timezone imported. Verify these are already imported at the top of the file before using them in the replacement code.
- DECISION-001: Use max(machines_to_keep_idle, 1) in code, not a config change. Do not add new config parameters.
- DECISION-002: Health check (PATH A) must not terminate idle workers. Scaling execution (PATH B) is the sole owner.
- DECISION-003: Do not remove the legacy scaling path. Only add a TODO comment.

## Sense Checks

- **SC1** (T1): After T1, does grepping for `machines_to_keep_idle` in source files (excluding config definition, logging, and tests) show `max(config.machines_to_keep_idle, 1)` everywhere? Are there exactly 0 bare `config.machines_to_keep_idle` uses in scaling/termination logic?
  Executor note: Satisfied. In scaling/termination logic, desired_floor, buffer_based, early_desired, and max_by_buffer now use max(config.machines_to_keep_idle, 1) in worker_state.py, control_loop.py, lifecycle.py, scaling_decision.py, and worker_capacity.py. Remaining bare machines_to_keep_idle references are logging-only.
  Verdict: Confirmed. Current code inspection shows max(config.machines_to_keep_idle, 1) everywhere relevant in scaling/termination logic, with remaining bare references limited to config/logging paths.

- **SC2** (T1): After T1, does grepping for `mark_worker_error` in health.py confirm the idle-timeout block is fully removed? Does the health check still handle: (a) basic health check when tasks queued, (b) heartbeat validation for busy workers, (c) stuck task detection?
  Executor note: Satisfied. health.py no longer contains the idle-timeout mark_worker_error block. The health path still performs queued-task basic health checks, busy-worker heartbeat validation, and stuck-task detection.
  Verdict: Confirmed. The idle-timeout mark_worker_error block is removed, while queued-work health checks, busy-worker heartbeat handling, and stuck-task detection remain intact.

- **SC3** (T2): After T2, does the failsafe in both control_loop.py and periodic.py skip workers where `ws.is_active and not ws.has_active_task and ws.heartbeat_is_recent`? Does it still catch: spawning-timeout workers, lost-heartbeat workers, and genuinely stale workers?
  Executor note: Satisfied. Both failsafe implementations now skip ws.is_active and not ws.has_active_task and ws.heartbeat_is_recent, while still flagging spawning-timeout workers and workers whose heartbeat/effective age exceeds the configured cutoff.
  Verdict: Confirmed. Both failsafe implementations skip healthy idle workers with recent heartbeats and still process spawning-timeout and stale/lost-heartbeat cases.

- **SC4** (T3): Do the new tests cover all 4 scenarios: (a) idle worker preserved as warm capacity with machines_to_keep_idle=0, (b) overcapacity respects buffer minimum, (c) early termination respects buffer minimum, (d) spawn failures don't affect circuit breaker failure rate?
  Executor note: Satisfied. The updated/new tests cover all four requested scenarios: preserving warm idle capacity with machines_to_keep_idle=0, overcapacity scale-down while still keeping one idle worker, early termination honoring max(machines_to_keep_idle, 1), and terminated spawn_failed workers not poisoning the failure-rate circuit breaker.
  Verdict: Confirmed. The new and updated tests cover the four scenarios called out in the plan at the intended layers.

- **SC5** (T4): Does `python -m pytest tests/ -v` pass with 0 failures? Do the verification greps confirm: no idle-timeout callers in health check, all machines_to_keep_idle uses have max(...,1), and no mark_worker_error for spawn failures?
  Executor note: Satisfied. `TMPDIR=/tmp python -m pytest tests/ -v` passed with 78 tests and 0 failures. Strengthened verification confirms no idle-timeout termination callers remain in the health-check path, all relevant scaling/termination uses of `machines_to_keep_idle` apply `max(..., 1)`, and both `_spawn_worker` implementations map RunPod `status == error` to `final_status = terminated` with `termination_reason=spawn_failed` rather than any error-status worker update.
  Verdict: Confirmed. The current code satisfies the grep-based checks, and the executor-reported full-suite pass is consistent with the inspected implementation even though this sandbox cannot rerun pytest without writable temp storage.

## Meta

**Key execution guidance:**

1. **Read before editing.** The plan references line numbers from the approved plan, but files have local uncommitted changes. The actual line numbers from the Explore agent are: control_loop.py health check idle block at ~539-548, early_desired at ~346, legacy buffer at ~833, execute_scaling max_by_buffer at ~941, spawn failure at ~1336, failsafe at ~1546. Phase mixins: health.py:126-137, lifecycle.py:45, worker_capacity.py:45 and :253, scaling_decision.py:70, periodic.py:188.

2. **Order of edits matters.** Edit control_loop.py LAST (or be careful with line shifts) since it's the largest file with 5 separate edit locations. Alternatively, edit bottom-up (highest line numbers first) to avoid line shift issues.

3. **The test helper `make_config()` defaults `machines_to_keep_idle=0`.** This means EVERY existing test that doesn't explicitly set machines_to_keep_idle will see different buffer behavior after the fix. The most impacted tests: TestBufferMaintenance and TestScaleDown. Read the full test carefully and trace through the math with the new formula before updating assertions.

4. **The spawn failure fix needs careful datetime handling.** In control_loop.py, `datetime` and `timezone` are likely already imported (used extensively). In worker_capacity.py, verify the import exists. The replacement code is: `await self.db.update_worker_status(worker_id, 'terminated', {'termination_reason': 'spawn_failed', 'terminated_at': datetime.now(timezone.utc).isoformat()})`.

5. **Circuit breaker test (test_circuit_breaker.py) needs to test `_check_worker_failure_rate()` which is a method on the control loop class.** You'll need to instantiate or mock the control loop. Look at how existing tests in `tests/gpu_orchestrator/` set up the control host for patterns to follow.

6. **Do not expand scope.** The gate explicitly warned about scope creep. Do not refactor phase mixin architecture, do not remove legacy paths, do not add new config parameters. The only "extra" work beyond the plan is applying fixes to scaling_decision.py:70 and periodic.py:175-229 per FLAG-003 gate warning — these are already included in T1 and T2.
