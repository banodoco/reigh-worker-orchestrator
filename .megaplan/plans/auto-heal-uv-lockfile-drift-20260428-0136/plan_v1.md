# Implementation Plan: Auto-heal uv lockfile drift in worker startup

## Overview
Production has confirmed that when `reigh-worker`'s `uv.lock` is stale relative to `pyproject.toml`, every newly-spawned pod aborts on `uv sync --locked` and never reaches `deps_verified`. The fix lives entirely inside the `else` branch of the sentinel-skip block in `gpu_orchestrator/runpod/worker_startup.template.sh` (lines 374–379): capture sync output, detect the lockfile-drift marker, and on detection regenerate the lock in-pod, run an unlocked `uv sync`, and write the sentinel using the post-heal inputs hash. The fast-path (sentinel match) and the "real failure" path stay unchanged.

Repo shape:
- Template: `gpu_orchestrator/runpod/worker_startup.template.sh` (single file, plain bash; loaded as a string by `gpu_orchestrator/runpod/startup_script.py:9`).
- Tests: `tests/gpu_orchestrator/runpod/test_startup_script.py` (renders the script and asserts substrings/ordering).
- Existing sentinel logic: lines 342–380. `EXPECTED_INPUTS_HASH` is computed from `uv --version`, python version, extras, and sha256 of `uv.lock` + `pyproject.toml`. After auto-heal, `uv.lock` content changes, so we MUST recompute the inputs hash before writing the sentinel.

Constraints that matter:
- Script runs under `set -e` (line 2) — we must locally disable error-exit around the sync command so we can branch on its exit code, then re-enable it.
- Bash-3 compatible style, matches existing template idioms.
- Existing test `test_rendered_startup_script_gates_uv_sync_on_inputs_sentinel` asserts `'"$UV_BIN" sync --locked'` appears, that `rm -f "$SYNC_SENTINEL"` precedes the sync, and that `printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"` appears after — the new structure must preserve all three.
- Marker pattern: brief specifies `lockfile.*needs to be updated` OR `lockfile is out of date`. The actual `uv` message we observed is "The lockfile at `uv.lock` needs to be updated, but `--locked` was provided." So `needs to be updated` (case-insensitive grep, anchored on `lockfile`) is a safe match.

## Main Phase

### Step 1: Replace the `else` branch with capture + branch logic (`gpu_orchestrator/runpod/worker_startup.template.sh`)
**Scope:** Small
1. **Replace** lines 374–379 with a block that:
   - Creates a temp file: `SYNC_OUTPUT="$(mktemp)"`.
   - Removes the stale sentinel: `rm -f "$SYNC_SENTINEL" 2>/dev/null || true`.
   - Runs the locked sync with output captured AND tee'd to the log, with `set -e` locally disabled so we can branch on rc:
     ```sh
     set +e
     "$UV_BIN" sync --locked --python 3.10 --extra cuda124 > "$SYNC_OUTPUT" 2>&1
     SYNC_RC=$?
     set -e
     cat "$SYNC_OUTPUT" >> "$LOG_FILE"
     ```
   - On `SYNC_RC == 0`: write sentinel + log success (preserves today's success line verbatim so existing test ordering still passes).
   - On `SYNC_RC != 0`: grep the captured output for the lockfile-drift marker:
     ```sh
     if grep -qiE 'lockfile.*needs to be updated' "$SYNC_OUTPUT"; then
         echo "⚠️  Lockfile drift detected on origin/main; regenerating lockfile in-pod and re-syncing (visit reigh-worker CI to fix upstream)" >> "$LOG_FILE" 2>&1
         "$UV_BIN" lock --python 3.10 >> "$LOG_FILE" 2>&1
         "$UV_BIN" sync --python 3.10 --extra cuda124 >> "$LOG_FILE" 2>&1
         # Recompute hash because uv.lock content has changed
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
         echo "❌ uv sync --locked failed and output did not match lockfile-drift marker; aborting" >> "$LOG_FILE" 2>&1
         rm -f "$SYNC_OUTPUT"
         exit "$SYNC_RC"
     fi
     ```
   - Clean up temp file at the end of the success and auto-heal paths: `rm -f "$SYNC_OUTPUT"`.
2. **Note** that since `uv lock` and the recovery `uv sync` invocations append directly to `$LOG_FILE` and `set -e` is restored, any failure inside auto-heal hard-fails the script — preserving the brief's "real lock-resolution problems must hard-fail" requirement.
3. **Verify** by re-reading the template: ordering is still `rm -f sentinel` → `uv sync --locked` → `printf ... > "$SYNC_SENTINEL"`, so existing ordering assertion holds.

### Step 2: Add a focused auto-heal test (`tests/gpu_orchestrator/runpod/test_startup_script.py`)
**Scope:** Small
1. **Add** `test_rendered_startup_script_auto_heals_lockfile_drift` that renders the script and asserts:
   - The drift marker grep is present: `grep -qiE 'lockfile.*needs to be updated'` (or whatever exact pattern was committed).
   - The auto-heal warning line is present: substring `⚠️  Lockfile drift detected`.
   - Both `"$UV_BIN" lock --python 3.10` and `"$UV_BIN" sync --python 3.10 --extra cuda124` (no `--locked`) appear in the script.
   - Ordering inside the auto-heal branch: `uv lock` index < recovery `uv sync` index < the post-heal `printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"` index — i.e. sentinel write comes only after recovery sync.
   - The post-heal success log line `✅ uv sync complete via auto-heal` is present.
   - `set +e` and a matching `set -e` appear so error-exit is locally disabled around the sync rc capture.
2. **Confirm** the existing `test_rendered_startup_script_gates_uv_sync_on_inputs_sentinel` still passes unchanged. The new block keeps `'"$UV_BIN" sync --locked'`, the leading `rm -f "$SYNC_SENTINEL"`, and the original `printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"` (now inside the success arm), so its three index lookups and ordering assertion remain valid.

### Step 3: Run targeted then full test suite
**Scope:** Small
1. **Run** the targeted file first: `pytest tests/gpu_orchestrator/runpod/test_startup_script.py -x`.
2. **Run** broader suite if anything else loads the template: `pytest tests/gpu_orchestrator/ -x`.

## Execution Order
1. Edit the template (Step 1) — produces the rendered script the new test will lock down.
2. Add the test (Step 2) — pins behavior.
3. Run tests (Step 3).

## Validation Order
1. Targeted file: `pytest tests/gpu_orchestrator/runpod/test_startup_script.py`.
2. Broader: `pytest tests/gpu_orchestrator/`.
3. Manual visual diff of the rendered template's `else` block — confirm bash quoting and that `set -e` is restored before the auto-heal branch invocations (so a failure in `uv lock` or recovery `uv sync` aborts the script).
