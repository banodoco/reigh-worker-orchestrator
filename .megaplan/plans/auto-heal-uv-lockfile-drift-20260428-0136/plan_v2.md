# Implementation Plan: Auto-heal uv lockfile drift in worker startup

## Overview
Production has confirmed that when `reigh-worker`'s `uv.lock` is stale relative to `pyproject.toml`, every newly-spawned pod aborts on `uv sync --locked` and never reaches `deps_verified`. The fix is contained to the `else` branch of the sentinel-skip block in `gpu_orchestrator/runpod/worker_startup.template.sh` (lines 374–379): capture sync stdout/stderr, branch on exit code, and on a lockfile-drift marker regenerate the lock in-pod, run an unlocked `uv sync`, and write the sentinel using the post-heal inputs hash. The fast-path (sentinel match) and the "real failure" path stay unchanged.

Repo shape:
- Template: `gpu_orchestrator/runpod/worker_startup.template.sh` (plain bash; loaded verbatim by `gpu_orchestrator/runpod/startup_script.py:9`).
- Tests: `tests/gpu_orchestrator/runpod/test_startup_script.py` (renders the script, asserts substrings + ordering).
- Existing sentinel logic: lines 342–380. `EXPECTED_INPUTS_HASH` derives from `uv --version`, python version, extras, sha256 of `uv.lock` + `pyproject.toml`. After auto-heal, `uv.lock` content changes, so we MUST recompute `UV_LOCK_SHA256` and `EXPECTED_INPUTS_HASH` before writing the sentinel.

Constraints:
- Script runs under `set -e` (line 2). We must locally `set +e` around the captured sync invocation so we can branch on its rc, then `set -e` again before the auto-heal commands so any real lock-resolution failure aborts via the existing ERR trap.
- Bash-3 compatible style (no bashisms beyond what's already used).
- Existing test `test_rendered_startup_script_gates_uv_sync_on_inputs_sentinel` uses `script.index(...)` (first match) to assert ordering of `rm -f "$SYNC_SENTINEL"` < `"$UV_BIN" sync --locked` < `printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"`. The new structure keeps the success-arm sentinel write as the FIRST occurrence of that printf, so `index()` still returns the success-arm position and ordering holds.
- Marker pattern: `lockfile.*needs to be updated` (case-insensitive). This matches the observed production message ("The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.") — the only failure message we have evidence for.

## Main Phase

### Step 1: Replace the `else` branch with capture + branch logic (`gpu_orchestrator/runpod/worker_startup.template.sh`)
**Scope:** Small

1. **Replace** lines 374–379 with a block that captures sync output, branches on rc, and self-heals on the drift marker only:
   ```sh
   rm -f "$SYNC_SENTINEL" 2>/dev/null || true
   SYNC_OUTPUT="$(mktemp)"
   set +e
   "$UV_BIN" sync --locked --python 3.10 --extra cuda124 > "$SYNC_OUTPUT" 2>&1
   SYNC_RC=$?
   set -e
   cat "$SYNC_OUTPUT" >> "$LOG_FILE"

   if [ "$SYNC_RC" -eq 0 ]; then
       printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"
       echo "✅ uv sync complete; $SYNC_SENTINEL refreshed (hash=$EXPECTED_INPUTS_HASH; uv=$UV_VERSION_STR)" >> "$LOG_FILE" 2>&1
       rm -f "$SYNC_OUTPUT"
   elif grep -qiE 'lockfile.*needs to be updated' "$SYNC_OUTPUT"; then
       echo "⚠️  Lockfile drift detected on origin/main; regenerating lockfile in-pod and re-syncing (visit reigh-worker CI to fix upstream)" >> "$LOG_FILE" 2>&1
       rm -f "$SYNC_OUTPUT"
       "$UV_BIN" lock --python 3.10 >> "$LOG_FILE" 2>&1
       "$UV_BIN" sync --python 3.10 --extra cuda124 >> "$LOG_FILE" 2>&1
       UV_LOCK_SHA256="$(sha256sum uv.lock | awk '{print $1}')"
       EXPECTED_INPUTS_HASH="$(
           {
               printf 'uv-version: %s\n' "$UV_VERSION_STR"
               printf 'python: 3.10\n'
               printf 'extras: cuda124\n'
               printf 'uv-lock-sha256: %s\n' "$UV_LOCK_SHA256"
               printf 'pyproject-sha256: %s\n' "$PYPROJECT_SHA256"
           } | sha256sum | awk '{print $1}'
       )"
       printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"
       echo "✅ uv sync complete via auto-heal; sentinel refreshed (post-heal hash=$EXPECTED_INPUTS_HASH; uv=$UV_VERSION_STR)" >> "$LOG_FILE" 2>&1
   else
       echo "❌ uv sync --locked failed (rc=$SYNC_RC) and output did not match lockfile-drift marker; aborting" >> "$LOG_FILE" 2>&1
       rm -f "$SYNC_OUTPUT"
       exit "$SYNC_RC"
   fi
   ```
2. **Confirm** key invariants by re-reading the rendered template:
   - `set -e` is restored before `uv lock` and the recovery `uv sync` so either failure aborts via the existing ERR trap.
   - `if [ "$SYNC_RC" -eq 0 ]` arm runs FIRST in document order, so the existing test's `script.index('printf \'%s\\n\' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"')` lookup still resolves to the success-arm position (after `"$UV_BIN" sync --locked`).
   - `EXPECTED_INPUTS_HASH` is recomputed only inside the auto-heal arm — the success arm uses the value computed earlier (lines 355–363), unchanged.
   - `$SYNC_OUTPUT` is removed in all three arms (no leak).

### Step 2: Add a focused auto-heal test (`tests/gpu_orchestrator/runpod/test_startup_script.py`)
**Scope:** Small

1. **Add** `test_rendered_startup_script_auto_heals_lockfile_drift` that renders the script and asserts:
   - The captured-output plumbing exists: `set +e`, `SYNC_RC=$?`, and a matching `set -e` are present.
   - The drift marker grep is present: `grep -qiE 'lockfile.*needs to be updated'`.
   - The auto-heal warning line is present: substring `⚠️  Lockfile drift detected`.
   - Both `"$UV_BIN" lock --python 3.10` and `"$UV_BIN" sync --python 3.10 --extra cuda124` (without `--locked`) appear.
   - `EXPECTED_INPUTS_HASH` is recomputed inside the auto-heal block (assert `UV_LOCK_SHA256="$(sha256sum uv.lock | awk '{print $1}')"` appears AFTER the warning marker).
   - Ordering inside the auto-heal arm: warning index < `uv lock` index < recovery `uv sync` index < the auto-heal `printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"` (use `script.rindex(...)` for the auto-heal sentinel write since the success arm has the same string earlier in the file).
   - The post-heal success log line `✅ uv sync complete via auto-heal` is present.
   - The non-drift abort log line `output did not match lockfile-drift marker` is present.
2. **Confirm** the existing `test_rendered_startup_script_gates_uv_sync_on_inputs_sentinel` still passes unchanged. Reasoning recorded in the test docstring: the new structure keeps `rm -f "$SYNC_SENTINEL"` before the locked sync, keeps the literal `"$UV_BIN" sync --locked` substring, and keeps the success-arm `printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"` as the FIRST occurrence in document order — so the existing `script.index(...)` ordering checks all hold.

### Step 3: Run targeted then broader test suite
**Scope:** Small

1. **Run** the targeted file first: `pytest tests/gpu_orchestrator/runpod/test_startup_script.py -x`.
2. **Run** broader suite if anything else loads the template: `pytest tests/gpu_orchestrator/ -x`.

## Execution Order
1. Edit the template (Step 1) — produces the rendered script the new test will lock down.
2. Add the test (Step 2) — pins behavior including ordering invariants.
3. Run tests (Step 3).

## Validation Order
1. Targeted file: `pytest tests/gpu_orchestrator/runpod/test_startup_script.py`.
2. Broader: `pytest tests/gpu_orchestrator/`.
3. Manual visual diff of the rendered template's `else` block — confirm `set -e` is restored before the auto-heal `uv lock` / `uv sync` (so a real lock-resolution failure aborts), and that `$SYNC_OUTPUT` is cleaned up in all three arms.
