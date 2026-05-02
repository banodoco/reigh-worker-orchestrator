# Implementation Plan: Sentinel-Skip for `uv sync` in Worker Startup

## Overview
Today the persistent-volume worker startup runs `uv sync --locked --python 3.10 --extra cuda124` unconditionally on every boot (≈90 s, 383 packages re-resolved). The `.venv` is preserved across pod lifetimes, so when the inputs that drive venv state are unchanged we can skip the resolve entirely.

We will:
1. Compute a sha256 hash of the *semantic* inputs that determine venv state — `uv.lock` contents, **`pyproject.toml` contents**, the `--python` value, the `--extra` flags, and the `uv` binary's `--version` output.
2. Compare it against the value recorded in `.venv/.sync-inputs` (the sentinel — inside `.venv/` so wiping `.venv` self-heals).
3. If they match exactly: log a clear skip line and proceed.
4. Otherwise: clear any prior sentinel, run today's `uv sync --locked …` exactly as before, then write the new hash to the sentinel **only on success** (`set -e` already aborts on a failed sync, so the sentinel write is unreachable on failure).

**Why include `pyproject.toml` (FLAG-001):** today the `--locked` flag at line 343 is the worker-layer backstop that rejects a stale lockfile when `pyproject.toml` and `uv.lock` disagree. If we skip the sync, we also skip that check. By making `pyproject.toml` a hash input, *any* `pyproject.toml` edit forces the sync path to run — at which point `--locked` still gets to do its job. This costs one extra `sha256sum` per boot (microseconds) and closes the only gap that would let pyproject/lock drift slip through silently.

The downstream import-validation block (line ~349) and the `update_worker_phase "deps_verified"` transition (line ~347) stay outside the if/else — they always run. The submodule reconcile block at lines 270–293 is **not** touched (already shipped in 134e3ec).

The single change is in `gpu_orchestrator/runpod/worker_startup.template.sh` between lines 342 and 345, plus a few new assertions in `tests/gpu_orchestrator/runpod/test_startup_script.py`.

### Landmine awareness
- The hash MUST cover all five inputs (`uv.lock`, `pyproject.toml`, python version, extras, uv binary version). Missing any one means a config drift goes silently undetected.
- Comparison is on the **literal file contents** of the sentinel vs the freshly computed hash — not on exit codes. This is the lesson from the recent `git submodule status` bug (commit 134e3ec, where exit 0 was returned even on the broken state).
- The sentinel write is gated by a successful `uv sync` exit. With `set -e` at the top of the script, a non-zero `uv sync` exit aborts the script before the write — no stale sentinel can result.
- First-time provisioning: no `.venv` ⇒ no `.venv/.sync-inputs` ⇒ skip path naturally not taken.
- `--locked` stays on the actual `uv sync` invocation when it runs (lockfile-drift backstop preserved). Adding `pyproject.toml` to the hash guarantees that every meaningful `pyproject.toml` edit routes through the sync path so `--locked` actually evaluates it.

## Main Phase

### Step 1: Confirm exact insertion point and surrounding invariants (`gpu_orchestrator/runpod/worker_startup.template.sh`)
**Scope:** Small
1. **Confirm** that the only change is between lines 342 ("=== DEPENDENCY SYNC (uv) ===") and 345 ("✅ uv sync complete; sentinel refreshed"); line 347 (`update_worker_phase "deps_verified"`) and line 349 ("=== VALIDATING IMPORTS ===") block remain unchanged (`gpu_orchestrator/runpod/worker_startup.template.sh:342`).
2. **Confirm** that `set -e` at line 2 will cause a failing `uv sync` to exit before any sentinel write — meaning we do not need explicit `if … ; then write sentinel` plumbing.
3. **Confirm** that the script's working directory at this point is the repo root (the `cd "$WORKDIR"` at line 241), where `uv.lock`, `pyproject.toml`, and `.venv/` all live.

### Step 2: Replace the dependency-sync block with the sentinel-skip logic (`gpu_orchestrator/runpod/worker_startup.template.sh`)
**Scope:** Small

Replace lines 342–345 with the block below. The actual `"$UV_BIN" sync --locked --python 3.10 --extra cuda124` command and the `touch .uv-migrated` call remain in place; everything else is added scaffolding.

```bash
echo "=== DEPENDENCY SYNC (uv) ===" >> "$LOG_FILE" 2>&1

# Inputs that fully determine .venv state. If any of these change, the venv
# must be re-synced. We hash the *contents* of uv.lock and pyproject.toml
# (not their mtimes) plus the literal CLI inputs and the uv binary's own
# version. pyproject.toml is hashed so that any edit to it routes through
# the sync path — where `uv sync --locked` is still the backstop that
# catches lockfile drift. Comparison is on the file contents of the
# sentinel — never on an exit code — because proxy checks like
# `git submodule status … >/dev/null` can return 0 on broken states
# (see commit 134e3ec).
SYNC_SENTINEL=".venv/.sync-inputs"
UV_VERSION_STR="$("$UV_BIN" --version 2>/dev/null || echo unknown)"
EXPECTED_INPUTS_HASH=$(
    {
        echo "uv-version: $UV_VERSION_STR"
        echo "python: 3.10"
        echo "extras: cuda124"
        if [ -f uv.lock ]; then
            printf 'uv-lock-sha256: '
            sha256sum uv.lock | awk '{print $1}'
        else
            echo "uv-lock-sha256: MISSING"
        fi
        if [ -f pyproject.toml ]; then
            printf 'pyproject-sha256: '
            sha256sum pyproject.toml | awk '{print $1}'
        else
            echo "pyproject-sha256: MISSING"
        fi
    } | sha256sum | awk '{print $1}'
)

SYNC_SKIPPED=0
if [ -d .venv ] && [ -f "$SYNC_SENTINEL" ]; then
    RECORDED_HASH="$(cat "$SYNC_SENTINEL" 2>/dev/null | tr -d '[:space:]')"
    if [ -n "$RECORDED_HASH" ] && [ "$RECORDED_HASH" = "$EXPECTED_INPUTS_HASH" ]; then
        SYNC_SKIPPED=1
    fi
fi

if [ "$SYNC_SKIPPED" = "1" ]; then
    echo "⏭️  Skipping uv sync: $SYNC_SENTINEL matches current inputs (hash=$EXPECTED_INPUTS_HASH; uv=$UV_VERSION_STR)" >> "$LOG_FILE" 2>&1
else
    # Remove any prior sentinel so an interrupted sync cannot leave a stale
    # "all good" marker behind. set -e at script top guarantees we exit on a
    # failed sync before the sentinel write below runs.
    rm -f "$SYNC_SENTINEL" 2>/dev/null || true
    "$UV_BIN" sync --locked --python 3.10 --extra cuda124 >> "$LOG_FILE" 2>&1
    printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"
    echo "✅ uv sync complete; $SYNC_SENTINEL refreshed (hash=$EXPECTED_INPUTS_HASH; uv=$UV_VERSION_STR)" >> "$LOG_FILE" 2>&1
fi
touch .uv-migrated
```

Notes on choices:
1. **`sha256sum` over `md5sum`** — collisions matter here (a silent miss means a stale venv); sha256sum is in coreutils on the RunPod Ubuntu image.
2. **`tr -d '[:space:]'`** — strips the trailing newline from the recorded hash before string compare; without it the comparison would always mismatch.
3. **`.venv/.sync-inputs`** — under `.venv/` so `rm -rf .venv` self-heals (per the brief).
4. **`echo "<key>: MISSING"` for absent files** — degrades to a deterministic value if `uv.lock` or `pyproject.toml` is missing (which would be a packaging bug); the subsequent `uv sync` will then surface the real error. We never want a missing input to silently produce the same hash twice.
5. **`pyproject.toml` is hashed** (FLAG-001 fix) — any edit to it forces the sync path to run, so `uv sync --locked` retains its role as the lockfile-drift backstop.
6. **`update_worker_phase "deps_verified"` (line 347 today) is left unchanged** — it sits *after* the `fi`, so the orchestrator phase signal fires on both the skip path and the sync path.

### Step 3: Update startup-script unit tests (`tests/gpu_orchestrator/runpod/test_startup_script.py`)
**Scope:** Small

The existing `test_rendered_startup_script_bootstraps_uv_and_runs_locked_sync` (line 59) asserts:
- `'"$UV_BIN" sync --locked --python 3.10 --extra cuda124' in script` — **still passes** (the literal sync command is preserved).
- `'touch .uv-migrated' in script` — **still passes** (we keep the `touch`).
- `'update_worker_phase "deps_verified"' in script` — **still passes** (untouched).

Add a new test alongside it that pins the sentinel-skip contract, e.g. `test_rendered_startup_script_gates_uv_sync_on_inputs_sentinel`:

```python
def test_rendered_startup_script_gates_uv_sync_on_inputs_sentinel():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-2",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=False,
    )

    # Sentinel path is inside .venv so wiping .venv self-heals.
    assert 'SYNC_SENTINEL=".venv/.sync-inputs"' in script

    # All five inputs must contribute to the hash:
    # uv version, python, extras, uv.lock contents, pyproject.toml contents.
    assert '"$UV_BIN" --version' in script
    assert "python: 3.10" in script
    assert "extras: cuda124" in script
    assert "sha256sum uv.lock" in script
    assert "sha256sum pyproject.toml" in script

    # Skip path logs clearly and does NOT run uv sync.
    skip_idx = script.index("⏭️  Skipping uv sync")
    sync_idx = script.index('"$UV_BIN" sync --locked')
    sentinel_write_idx = script.index('printf \'%s\\n\' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"')

    # Sentinel is removed before sync (no stale marker on interrupted sync)
    # and (re)written only after the sync command — i.e. on success, since
    # the script runs under `set -e`.
    rm_idx = script.index('rm -f "$SYNC_SENTINEL"')
    assert rm_idx < sync_idx < sentinel_write_idx
    assert skip_idx < sync_idx  # skip branch printed in the if-arm above the sync command

    # deps_verified phase still fires regardless of skip vs. sync (i.e. it sits
    # after the closing `fi`, not inside either branch).
    deps_verified_idx = script.index('update_worker_phase "deps_verified"')
    fi_indices = [i for i in range(len(script)) if script.startswith("\nfi\n", i)]
    assert any(sync_idx < fi < deps_verified_idx for fi in fi_indices)
```

(Last assertion is best-effort structural — if it proves brittle, drop it and rely on the existing phase-order test at line 9.)

### Step 4: Validate (`tests/`, shell parse)
**Scope:** Small
1. **Shell-parse** the rendered script to catch syntax errors before any runtime test:
   ```bash
   python -c "from gpu_orchestrator.runpod import startup_script; \
     import sys; \
     sys.stdout.write(startup_script.render_startup_script( \
       worker_id='w', supabase_url='u', supabase_anon_key='a', \
       supabase_service_key='s', replicate_api_token='r', \
       max_task_wait_minutes=1, has_pending_tasks=False))" \
     | bash -n
   ```
2. **Run** the focused unit tests:
   ```bash
   pytest tests/gpu_orchestrator/runpod/test_startup_script.py -q
   ```
3. **Run** the broader gpu_orchestrator suite to confirm no collateral breakage:
   ```bash
   pytest tests/gpu_orchestrator -q
   ```

## Execution Order
1. Step 1 (confirm landmarks) — pure read.
2. Step 2 (template edit) — the actual change.
3. Step 3 (test additions) — pin the new contract.
4. Step 4 (validation) — parse + run.

## Validation Order
1. `bash -n` on the rendered script (cheapest; catches typos).
2. `pytest tests/gpu_orchestrator/runpod/test_startup_script.py` (focused).
3. `pytest tests/gpu_orchestrator` (broader regression sweep).
4. **Manual / out-of-band (info only):** on the next real worker boot, confirm `/tmp/worker_startup_*.log` shows the `⏭️  Skipping uv sync` line on a warm boot with no lock/pyproject change, and the `✅ uv sync complete; .venv/.sync-inputs refreshed` line on a cold boot or after editing `uv.lock` or `pyproject.toml`.
