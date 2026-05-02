# Implementation Plan: Roll out `runpod_lifecycle` across the Banodoco workspace

## Overview

Three repos, one end-to-end migration:

1. **`/Users/peteromalley/Documents/reigh-workspace/runpod-lifecycle`** — brand-new package, no commits yet, ship `v0.1.0` to a new public GitHub repo `banodoco/runpod-lifecycle`.
2. **`/Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator`** — clean main. Swap ~11 consumer scripts to import from the new package, replace the in-tree `RunpodClient` with a slim local adapter (`gpu_orchestrator/worker_spawner.py`) that calls `runpod_lifecycle.launch()` and owns Supabase row writes + RAM-tier / storage-volume / startup-script fallbacks, delete `gpu_orchestrator/runpod/` (except `startup_script.py`) and the now-obsolete tests, land on a migration branch (never main).
3. **`/Users/peteromalley/Documents/reigh-workspace/reigh-worker`** — dirty working tree with unrelated changes (must not be touched). Add the git dep, update 5 known call sites on a migration branch.

The package's public API surface is load-bearing: every consumer maps only to symbols exported from `src/runpod_lifecycle/__init__.py`. Unexported needs are either re-exported (bump to `v0.1.1`, retag, force consumers to update the dep) or shimmed locally — the brief's explicit instruction.

Observed extras beyond the brief's list (surfaced by inventory of `control_loop.py`):

- **Storage-mixin private calls** at `control_loop.py:1480` (`_get_storage_volume_id`), `:1502` (`check_storage_health`), `:1517` (`_expand_network_volume`) — not in the brief's enumeration. Storage fallback is called out as "must keep working," so these need routes: either through the new package's storage helpers, or through adapter methods that wrap them. See Q3.
- **`generate_worker_id`** at `control_loop.py:1301` — orchestrator-local concern, belongs on the adapter.
- **`scripts/uv_e2e_test.py` / `scripts/uv_lock_benchmark.py`** import from `gpu_orchestrator.runpod.ssh` (`SSHClient`) as well as `.api`. The brief only mentions api mappings; confirm `SSHClient` or equivalent is exported from `runpod_lifecycle` (it's listed in the package's modules), else shim or re-export.
- **`scripts/debug/commands/storage.py`** actually imports at **line 151**, not line 18 as the brief states. The brief's line `18` appears to refer to `reigh-worker/debug/commands/storage.py` (separate repo). Assumption: two different files; use the correct line per repo.
- **`tests/gpu_orchestrator/runpod/test_startup_script.py`** (89 real lines) tests `startup_script.py`, which STAYS in the orchestrator. This test must be kept, not deleted. The brief globs `tests/gpu_orchestrator/runpod/test_*.py` — that glob would sweep this up; exclude it explicitly.
- **`control_loop.py:44`** initializes `self.runpod = create_runpod_client()`. This becomes `self.runpod = WorkerSpawnerAdapter(...)` (or equivalent). Every `self.runpod.*` call site keeps working against the adapter surface.

## Phase 1: Publish `runpod-lifecycle` v0.1.0

### Step 1: Pre-flight the package (`/Users/peteromalley/Documents/reigh-workspace/runpod-lifecycle`)
**Scope:** Small
1. **Read** `src/runpod_lifecycle/__init__.py` and confirm the actual exports match what consumers will need: `RunPodConfig`, `launch`, `terminate`, `list_pods`, `find_orphans`, `get_network_volumes`, `api` submodule (with `create_pod`, `get_pod_status`, `get_pod_ssh_details`), and an SSH helper. Any gap becomes a `v0.1.1` task (Step 9) or a consumer shim.
2. **Read** `pyproject.toml` to confirm package name is `runpod-lifecycle`, version `0.1.0`, and deps are declarable via `pip install -e .[dev]` (dev extra must include `pytest`, `pytest-asyncio`, `pytest-mock` given what tests/ uses).
3. **Read** `.gitignore` (or create it) to ensure `.venv/`, `.pytest_cache/`, `__pycache__/`, `*.pyc`, `.env`, `*.egg-info/` are excluded. The current tree has a fully populated `.venv` that must NOT be committed.
4. **Run** `.venv/bin/python -m pytest -q` from the package root; expect ≥50 passed. Fail the phase if it's red — fix before publishing.

### Step 2: Initial commit + publish (`runpod-lifecycle/`)
**Scope:** Small
1. **Init** git if not already (`git init -b main`).
2. **Stage** only source (`src/`, `tests/`, `pyproject.toml`, `README.md`, `.gitignore`, `LICENSE` if present). Explicitly exclude `.venv/`, `.pytest_cache/`, `__pycache__/`, any `.env*`.
3. **Verify** staged tree with `git status` and `git diff --cached --stat` before committing. No secrets, no venv.
4. **Commit** with a single initial message (e.g., `Initial commit: runpod_lifecycle v0.1.0`).
5. **Create + push** the GitHub repo in one shot: `gh repo create banodoco/runpod-lifecycle --public --source=. --push --description 'Reusable async RunPod lifecycle, discovery, and CLI for the Banodoco stack.'`.
6. **Tag** `v0.1.0` and push the tag: `git tag v0.1.0 && git push origin v0.1.0`.
7. **Verify** browsability: `gh repo view banodoco/runpod-lifecycle --json url,isPrivate,description` — assert public + description matches.

## Phase 2: Migrate `reigh-worker-orchestrator`

### Step 3: Create migration branch + add dependency (`reigh-worker-orchestrator/`)
**Scope:** Small
1. **Branch** from main: `git checkout -b runpod-lifecycle-migration`.
2. **Determine** dep file: if `pyproject.toml` exists, add `runpod-lifecycle @ git+https://github.com/banodoco/runpod-lifecycle@v0.1.0` under project dependencies; else add to `requirements.txt`. (Inventory shows only `requirements-dev.txt` — confirm main deps location in Q2.)
3. **Install** into the orchestrator's venv: `pip install -e .[dev]` (or the equivalent for its setup). `python -c "import runpod_lifecycle; print(runpod_lifecycle.__version__)"` must succeed before any code changes.

### Step 4: Trivial consumer swaps (`scripts/*.py`, `scripts/debug/**`)
**Scope:** Medium
1. **Rewrite imports** in each file, mapping per the brief:
   - `scripts/spawn_gpu.py:16`, `scripts/ssh_to_worker.py:14`, `scripts/shutdown_all_workers.py:27`, `scripts/terminate_single_worker.py:21`, `scripts/test_runpod.py:15`, `scripts/spot_instance_lifetime_test.py:25,109`, `scripts/debug/commands/storage.py:151`, `scripts/debug/services/workers.py:10` — replace `from gpu_orchestrator.runpod import create_runpod_client` with `from runpod_lifecycle import RunPodConfig`, and rewrite the call `create_runpod_client()` → code that constructs the new package's client (e.g., `RunPodConfig.from_env()` + appropriate `launch`/`terminate`/etc. calls depending on what each script does).
   - `scripts/uv_e2e_test.py:16-18` and `scripts/uv_lock_benchmark.py:22-24` — replace `from gpu_orchestrator.runpod.api import create_pod_and_wait, get_pod_ssh_details` and `from gpu_orchestrator.runpod.ssh import SSHClient` with `from runpod_lifecycle.api import create_pod, get_pod_ssh_details` and `from runpod_lifecycle.ssh import SSHClient` (or whatever the package exports — verify in Step 1).
2. **Map call signatures** per the brief:
   - `create_runpod_client()` → construct `RunPodConfig.from_env()` (plus whatever client instance the script needs; it may just want direct module functions with `api_key=config.api_key`).
   - `terminate_worker(pod_id)` → `runpod_lifecycle.terminate(pod_id, api_key)`.
   - `get_pod_status(pod_id)` → `runpod_lifecycle.api.get_pod_status(pod_id, api_key)`.
   - `create_pod_and_wait(...)` → `runpod_lifecycle.api.create_pod(...)`.
   - `get_pod_ssh_details(...)` → `runpod_lifecycle.api.get_pod_ssh_details(...)`.
   - `get_network_volumes(...)` → `runpod_lifecycle.get_network_volumes(...)`.
3. **Leave** `scripts/debug/commands/runpod.py:14,30` alone for now (direct `import runpod`) — per the brief, the parallel file in `reigh-worker/` is what the brief targets for `list_pods` / `find_orphans`; confirm whether the orchestrator's copy should be modernized too (Q5).
4. **Syntax-check** each edited file: `python -m py_compile scripts/spawn_gpu.py ...` — cheap pre-test gate.

### Step 5: Build the `WorkerSpawnerAdapter` (`gpu_orchestrator/worker_spawner.py` — new file)
**Scope:** Large
1. **Create** `gpu_orchestrator/worker_spawner.py` exposing a class with the surface `control_loop.py` uses today via `self.runpod.*`:
   - `spawn_worker(worker_id, worker_env=None) -> Optional[Dict]` — builds startup script via `gpu_orchestrator.runpod.startup_script` (stays in-tree), picks GPU type / RAM tier with fallback logic, picks storage volume with fallback, calls `runpod_lifecycle.launch(...)`, writes the Supabase row via `DatabaseClient`. Preserves RAM-tier fallback, storage-volume fallback, startup-script injection — tested live per constraints.
   - `start_worker_process(runpod_id, worker_id, has_pending_tasks=False) -> bool` — SSH into pod and kick the worker; uses `runpod_lifecycle.ssh` (or shim).
   - `check_and_initialize_worker(worker_id, runpod_id) -> Dict` — SSH-based health probe.
   - `terminate_worker(runpod_id) -> bool` — thin wrapper over `runpod_lifecycle.terminate(runpod_id, api_key)` + Supabase row update.
   - `get_pod_status(runpod_id)` — thin wrapper over `runpod_lifecycle.api.get_pod_status`.
   - `generate_worker_id()` — move verbatim from today's `RunpodClient` (orchestrator concern).
   - **Storage**: `_get_storage_volume_id(storage_name)`, `check_storage_health(...)`, `_expand_network_volume(volume_id, new_size)` — wrap the package's storage helpers if exported; else inline the logic using `runpod_lifecycle.get_network_volumes` and the RunPod SDK directly. See Q3.
2. **Re-use** `gpu_orchestrator/runpod/startup_script.py` (stays in-tree — the brief is explicit) by importing from it.
3. **Construct** with `config`, `db: DatabaseClient`, `runpod_config: RunPodConfig` injected; no env reads inside the class.
4. **Add** a factory `create_worker_spawner(config, db) -> WorkerSpawnerAdapter` that builds `RunPodConfig.from_env()` internally, as a drop-in replacement for `create_runpod_client()`.

### Step 6: Wire the adapter into `control_loop.py` (`gpu_orchestrator/control_loop.py`)
**Scope:** Medium
1. **Rewrite** `control_loop.py:44` (or wherever `self.runpod = create_runpod_client()` lives) to `self.runpod = create_worker_spawner(self.config, self.db)`.
2. **Verify** every `self.runpod.*` call site still resolves against the new adapter. The 23 call sites from inventory — `control_loop.py:386, 448, 465, 1126, 1205, 1301, 1306, 1336, 1342, 1480, 1502, 1517, 1541, 1544, 1557, 1598, 1600, 1651` — must each land on an adapter method or be rewritten to call `runpod_lifecycle.*` directly if that's cleaner.
3. **Preserve** RAM-tier fallback / storage-volume fallback / startup-script injection paths — these are only tested live, so keep their structure identical when moving into the adapter. Do NOT refactor opportunistically.

### Step 7: Delete obsolete code + fix tests (`gpu_orchestrator/runpod/`, `tests/`)
**Scope:** Medium
1. **Delete** `gpu_orchestrator/runpod/api.py`, `client.py`, `lifecycle.py`, `storage.py`, `ssh.py`. **Keep** `startup_script.py`. Update `gpu_orchestrator/runpod/__init__.py` to only re-export from `startup_script`, or move `startup_script.py` up to `gpu_orchestrator/startup_script.py` and delete the `runpod/` directory entirely (cleaner — preferred if nothing else imports `gpu_orchestrator.runpod`).
2. **Delete** obsolete tests: `tests/test_runpod_decomposition.py` (168 lines), `tests/test_direct_runpod_lifecycle.py` (5 lines), `tests/test_direct_runpod_storage.py` (5 lines), `tests/gpu_orchestrator/runpod/test_lifecycle.py`, `tests/gpu_orchestrator/runpod/test_storage.py`, `tests/gpu_orchestrator/runpod/test_api.py`, `tests/gpu_orchestrator/runpod/test_client.py`.
3. **KEEP** `tests/gpu_orchestrator/runpod/test_startup_script.py` (89 real lines testing the module that stays in-tree). The brief's blanket `tests/gpu_orchestrator/runpod/test_*.py` glob would sweep this — do not.
4. **Update** `tests/gpu_orchestrator/control/phases/test_lifecycle.py:34,48`: the `Host.runpod = Mock()` fixture now mocks the adapter surface. Any `host.runpod.terminate_worker.assert_not_called()`-style assertions should keep working since the adapter preserves method names; verify that's true and add `spec=WorkerSpawnerAdapter` to the Mock for safety.
5. **Grep** the whole repo for residual `from gpu_orchestrator.runpod` imports (excluding `startup_script`) and fix.

### Step 8: Verify the orchestrator (`tests/`)
**Scope:** Small
1. **Run** `pytest -q tests/` — must be green. Constraint from brief: **fix consumer code, not the package**, if anything breaks.
2. **Commit** and push branch (not main): `git push -u origin runpod-lifecycle-migration`. Do NOT open PR automatically — let the human.

### Step 9 (contingent): Package re-export + v0.1.1 if needed (`runpod-lifecycle/src/runpod_lifecycle/__init__.py`)
**Scope:** Small
1. **If** any consumer needs a symbol that isn't exported (Step 1 surfaced gaps, or Steps 4/5 hit an import-error), decide per-symbol: (a) re-export from `__init__.py`, bump to `0.1.1` in `pyproject.toml`, commit, tag `v0.1.1`, push; update orchestrator + reigh-worker deps to `@v0.1.1`; OR (b) write a consumer-local shim (preferred for orchestrator-only idioms like storage mixin internals).
2. **Do not** add unrelated functionality to the package — constraint from brief.

## Phase 3: Migrate `reigh-worker`

### Step 10: Migration branch + dep, preserve dirty tree (`reigh-worker/`)
**Scope:** Small
1. **Inspect** `git status --short` and record the dirty files. **Do not** touch any file in that list.
2. **Stash** the dirty changes: `git stash push -m "pre-migration-unrelated-changes"`. Create branch from clean main: `git checkout -b runpod-lifecycle-migration`. Then `git stash pop` IF the dirty files don't overlap with migration targets; else leave stashed and restore at the end. (Prefer: branch from main first, then confirm zero overlap between dirty file set and migration target set before popping.)
3. **Add** `runpod-lifecycle @ git+https://github.com/banodoco/runpod-lifecycle@v0.1.0` to the appropriate dep file (confirm pyproject vs requirements — Q4). Install, sanity-import.

### Step 11: Rewrite reigh-worker call sites
**Scope:** Small
1. **`scripts/live_test/config.py:73`** — replace the `RunpodClient` construction with `RunPodConfig.from_env()` plus whatever helper calls the surrounding code needs.
2. **`scripts/live_test/variant_fresh.py`, `scripts/live_test/variant_update.py`** — rewrite low-level API calls to `runpod_lifecycle.api.*`.
3. **`debug/commands/storage.py:18`** — `create_runpod_client` → `RunPodConfig.from_env()` + `runpod_lifecycle.get_network_volumes(...)`.
4. **`debug/commands/runpod.py:14,30`** — replace raw `runpod` SDK calls with `runpod_lifecycle.list_pods(...)` / `runpod_lifecycle.find_orphans(...)`.
5. **Py-compile** every edited file.

### Step 12: Verify reigh-worker
**Scope:** Small
1. **Run** `pytest -q` in reigh-worker. Must be green. Fix consumer code on failure.
2. **Commit** and push the migration branch. Do NOT push to main. Restore stashed unrelated changes if applicable.

## Phase 4: End-to-end verification

### Step 13: Cross-repo smoke (`runpod-lifecycle/`, `reigh-worker-orchestrator/`, `reigh-worker/`)
**Scope:** Small
1. **Re-run** the package's own test suite after consumers are migrated: `cd runpod-lifecycle && .venv/bin/python -m pytest -q` (≥50 passed).
2. **Re-run** `pytest -q tests/` in reigh-worker-orchestrator.
3. **Re-run** `pytest -q` in reigh-worker.
4. **Grep** each consumer repo for `gpu_orchestrator.runpod.api`, `gpu_orchestrator.runpod.client`, `gpu_orchestrator.runpod.lifecycle`, `gpu_orchestrator.runpod.storage`, `gpu_orchestrator.runpod.ssh` — expect zero hits (only `startup_script` permitted).
5. **Grep** for any leftover `create_runpod_client` / `RunpodClient` references in scripts — expect zero hits in consumers.
6. **Verify** on GitHub: `gh repo view banodoco/runpod-lifecycle` returns the public repo with `v0.1.0` tag visible; the orchestrator's dep resolves to that tag.

## Execution Order

1. Phase 1 (publish package) must complete before Phase 2 or 3 can install the dep.
2. Within Phase 2: Step 3 → Step 4 (trivial swaps) → Step 5 (adapter) → Step 6 (wire) → Step 7 (delete + tests) → Step 8 (verify). Steps 4 and 5 can proceed in parallel once Step 3 is done, but Step 6 blocks on both. If Step 1 surfaces API gaps, Step 9 (v0.1.1) interleaves before Step 4 can complete.
3. Phase 3 can run in parallel with Phase 2 once Phase 1 is done, but is cheap to defer until Phase 2 is green (so any v0.1.1 bump has already happened).
4. Phase 4 is final gate.

## Validation Order

1. Cheapest first: `python -m py_compile` each edited file as you go (catches silly import typos in seconds).
2. Package's own tests after every API-surface change in runpod-lifecycle (Steps 1, 9).
3. Orchestrator test suite after Step 7 (bulk rewire done).
4. reigh-worker test suite after Step 11.
5. Cross-repo grep + `gh repo view` gate in Step 13.
