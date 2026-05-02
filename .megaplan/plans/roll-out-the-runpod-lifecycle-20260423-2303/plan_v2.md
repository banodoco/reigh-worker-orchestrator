# Implementation Plan: Roll out `runpod_lifecycle` across the Banodoco workspace

## Overview

Three repos, one end-to-end migration:

1. **`/Users/peteromalley/Documents/reigh-workspace/runpod-lifecycle`** — new package, no commits; ship `v0.1.0` to public `banodoco/runpod-lifecycle`.
2. **`/Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator`** — clean `main`; build an orchestrator-local adapter at `gpu_orchestrator/worker_spawner.py` over the async `runpod_lifecycle` API, swap consumers, delete `gpu_orchestrator/runpod/{api,client,lifecycle,storage,ssh}.py` (keep `startup_script.py`), land on `runpod-lifecycle-migration` branch.
3. **`/Users/peteromalley/Documents/reigh-workspace/reigh-worker`** — dirty tree; expand call-site set (includes `scripts/live_test/__init__.py`, `ssh_bootstrap.py`, `terminate_guard.py`, `tests/test_primitives.py`) because reigh-worker cross-imports `gpu_orchestrator.runpod.*`, which will stop existing after Phase 2.

Five critique-driven corrections to the prior draft:

- **Async boundary (FLAG-001).** `runpod_lifecycle.launch`, `terminate`, `list_pods`, `find_orphans` are `async def`. Today's `self.runpod.*` call sites in `control_loop.py` are sync-shaped, but the surrounding `control_loop` already `await`s other coroutines (e.g. `db.update_worker_status` is `AsyncMock` in tests), so an event loop IS running. The adapter therefore exposes **async** methods; every `self.runpod.X(...)` in `control_loop.py` gains `await`; top-level scripts call via `asyncio.run(adapter.X(...))`.
- **Script surface (FLAG-002).** Several scripts call client-only methods (`generate_worker_id`, `spawn_worker`, `execute_command_on_worker`, `_get_storage_volume_id`, `check_storage_health`, `_expand_network_volume`). They CANNOT migrate to bare `runpod_lifecycle` — they need the adapter. Scripts import `from gpu_orchestrator.worker_spawner import create_worker_spawner` and use its methods. Only scripts that today use raw module-level helpers (`get_network_volumes`, `create_pod_and_wait`, `get_pod_ssh_details`, `SSHClient`) migrate to direct `runpod_lifecycle.*` imports.
- **Supabase ownership (FLAG-003).** Adapter is a pure translation layer. It does NOT call `create_worker_record` or `update_worker_status`. `control_loop.py` already brackets `self.runpod.spawn_worker(...)` with DB writes — that flow stays unchanged.
- **reigh-worker cross-imports (FLAG-004).** Beyond the brief's 5 files, update: `reigh-worker/scripts/live_test/__init__.py` (lazy imports of `gpu_orchestrator.runpod.{lifecycle,ssh,api}`), `scripts/live_test/ssh_bootstrap.py`, `scripts/live_test/terminate_guard.py`, `scripts/live_test/tests/test_primitives.py` (monkeypatches `gpu_orchestrator.runpod.client` — retarget to `runpod_lifecycle` or the local `live_test_pkg` re-export).
- **Dependency files (FLAG-005).** Orchestrator has no root `pyproject.toml`; deps live in `gpu_orchestrator/requirements.txt`. Add the git dep there and install via `pip install -r gpu_orchestrator/requirements.txt`. reigh-worker has `pyproject.toml` but no `[dev]` extra; install with `pip install -e .` + whatever test-dep file the repo actually uses. Do NOT run `pip install -e .[dev]` on either repo.

Also preserved from the prior pass: keep `tests/gpu_orchestrator/runpod/test_startup_script.py` (tests the surviving module); keep `startup_script.py` in-place (minimal-diff); enforce no secrets / no `.venv` in any commit; never push to `main` on consumer repos.

## Phase 1: Publish `runpod-lifecycle` v0.1.0

### Step 1: Pre-flight the package (`/Users/peteromalley/Documents/reigh-workspace/runpod-lifecycle`)
**Scope:** Small
1. **Read** `src/runpod_lifecycle/__init__.py` and enumerate exports. Confirm presence of (or shim-target) `RunPodConfig`, `launch`, `terminate`, `list_pods`, `find_orphans`, `get_network_volumes`, and submodules `api` (with `create_pod`, `get_pod_status`, `get_pod_ssh_details`) and `ssh` (SSH helper). Record which are `async def`.
2. **Read** `pyproject.toml` to confirm name `runpod-lifecycle`, version `0.1.0`, dev-extra covers `pytest`/`pytest-asyncio`/`pytest-mock`.
3. **Ensure** `.gitignore` excludes `.venv/`, `.pytest_cache/`, `__pycache__/`, `*.pyc`, `.env*`, `*.egg-info/`. Create if missing.
4. **Run** `.venv/bin/python -m pytest -q`; ≥50 passed required as gate.

### Step 2: Initial commit + publish (`runpod-lifecycle/`)
**Scope:** Small
1. **`git init -b main`** if not already.
2. **Stage** source only (`src/`, `tests/`, `pyproject.toml`, `README.md`, `.gitignore`, `LICENSE` if present). Verify with `git status` + `git diff --cached --stat`; NO `.venv/`, `.pytest_cache/`, `.env*`.
3. **Commit** `Initial commit: runpod_lifecycle v0.1.0`.
4. **Publish** `gh repo create banodoco/runpod-lifecycle --public --source=. --push --description 'Reusable async RunPod lifecycle, discovery, and CLI for the Banodoco stack.'`.
5. **Tag** `git tag v0.1.0 && git push origin v0.1.0`.
6. **Verify** `gh repo view banodoco/runpod-lifecycle --json url,isPrivate,description` — public, description matches, tag visible.

## Phase 2: Migrate `reigh-worker-orchestrator`

### Step 3: Branch + add dependency (`gpu_orchestrator/requirements.txt`)
**Scope:** Small
1. **Branch** `git checkout -b runpod-lifecycle-migration` from clean `main`.
2. **Add** `runpod-lifecycle @ git+https://github.com/banodoco/runpod-lifecycle@v0.1.0` to `gpu_orchestrator/requirements.txt` (the actual deps file the Dockerfile/README install from). Do NOT assume a root `pyproject.toml`.
3. **Install** into the orchestrator's venv: `pip install -r gpu_orchestrator/requirements.txt`. Verify: `python -c "import runpod_lifecycle; print(runpod_lifecycle.__version__)"`.

### Step 4: Audit the async/sync boundary (`gpu_orchestrator/control_loop.py`, `scripts/*.py`)
**Scope:** Small
1. **Read** `control_loop.py` at the 23 observed `self.runpod.*` call sites (`:386, :448, :465, :1126, :1205, :1301, :1306, :1336, :1342, :1480, :1502, :1517, :1541, :1544, :1557, :1598, :1600, :1651`). Determine which enclosing functions are `async def` (expected: most, since `db.update_worker_status` is awaited).
2. **Confirm** scripts in `scripts/` are sync entry points (top-level `if __name__ == '__main__'` without an existing loop). They'll wrap adapter coroutines with `asyncio.run(...)`.
3. **Record** the audit result in a comment at the top of the new adapter so the async contract is explicit.

### Step 5: Build the `WorkerSpawnerAdapter` (`gpu_orchestrator/worker_spawner.py` — new file)
**Scope:** Large
1. **Create** `gpu_orchestrator/worker_spawner.py`. All public methods are `async def` (matching the underlying `runpod_lifecycle` surface):
   - `async spawn_worker(worker_id, worker_env=None) -> Optional[Dict]` — build startup script via `gpu_orchestrator.runpod.startup_script`, pick GPU/RAM tier with fallback, pick storage volume with fallback, `await runpod_lifecycle.launch(...)`. **Returns pod info only. Does NOT touch Supabase** — caller owns DB writes.
   - `async start_worker_process(runpod_id, worker_id, has_pending_tasks=False) -> bool`.
   - `async check_and_initialize_worker(worker_id, runpod_id) -> Dict`.
   - `async terminate_worker(runpod_id) -> bool` — thin `await runpod_lifecycle.terminate(runpod_id, api_key)`. No DB writes.
   - `async get_pod_status(runpod_id) -> Dict` — `await runpod_lifecycle.api.get_pod_status(runpod_id, api_key)`.
   - `async execute_command_on_worker(runpod_id, cmd, ...) -> SSHResult` — used by scripts + start/check methods; wraps `runpod_lifecycle.ssh` or the package's SSH helper.
   - `generate_worker_id() -> str` — sync (pure string op; orchestrator concern; copy verbatim from today's `RunpodClient`).
   - `async check_storage_health(storage_name) -> Dict`, `async get_storage_volume_id(storage_name) -> Optional[str]`, `async expand_network_volume(volume_id, new_size) -> bool` — publicly-named (drop the leading `_` since they're now the stable adapter surface). If `runpod_lifecycle.storage` exports equivalents, delegate; else inline using `runpod_lifecycle.get_network_volumes` + direct RunPod SDK as a fallback.
2. **Import** `gpu_orchestrator.runpod.startup_script` (the preserved module) for startup-script building. Do not reimplement.
3. **Constructor** takes `(config, db, runpod_config: RunPodConfig)`. No env reads inside the class. `db` is stored only because some read paths may need it (metadata lookups); it MUST NOT be used for write/insert in any adapter method.
4. **Factory** `create_worker_spawner(config, db) -> WorkerSpawnerAdapter` that calls `RunPodConfig.from_env()` — drop-in for `create_runpod_client()`.
5. **Cap** the file at ~400 lines. No opportunistic refactors.

### Step 6: Swap consumer scripts (`scripts/*.py`, `scripts/debug/**`)
**Scope:** Medium
1. **Scripts that need full client behavior** (`spawn_worker`, `generate_worker_id`, `execute_command_on_worker`, storage privates) — migrate to the adapter, not bare `runpod_lifecycle`:
   - `scripts/spawn_gpu.py:16` — `from gpu_orchestrator.worker_spawner import create_worker_spawner`; `asyncio.run(spawner.spawn_worker(...))`.
   - `scripts/ssh_to_worker.py:14` — same import; `asyncio.run(spawner.execute_command_on_worker(...))`.
   - `scripts/shutdown_all_workers.py:27` — same import; wrap terminate loop in `asyncio.run(...)`.
   - `scripts/terminate_single_worker.py:21` — same.
   - `scripts/test_runpod.py:15` — same.
   - `scripts/spot_instance_lifetime_test.py:25,109` — adapter for spawn/terminate; direct `runpod_lifecycle.get_network_volumes` for volume listing (line 109).
   - `scripts/debug/commands/storage.py:151` — adapter for `get_storage_volume_id` / `check_storage_health` / `expand_network_volume`; direct `runpod_lifecycle.get_network_volumes` where applicable.
   - `scripts/debug/services/workers.py:10` — adapter for `execute_command_on_worker` and any client-like operations.
2. **Scripts with only module-level helpers** — migrate directly to `runpod_lifecycle`:
   - `scripts/uv_e2e_test.py:16-18` — `from runpod_lifecycle.api import create_pod, get_pod_ssh_details` and `from runpod_lifecycle.ssh import SSHClient` (or equivalent per Step 1 audit). Wrap async calls with `asyncio.run(...)`.
   - `scripts/uv_lock_benchmark.py:22-24` — same.
3. **`scripts/debug/commands/runpod.py:14`** — if it uses raw `runpod` SDK for listing/orphan-finding, migrate to `runpod_lifecycle.list_pods` / `find_orphans` for parity with reigh-worker. Otherwise leave.
4. **Call mapping** (unchanged from brief): `create_runpod_client()` → `create_worker_spawner(config, db)` OR `RunPodConfig.from_env()` depending on needs; `terminate_worker(pod_id)` → `await runpod_lifecycle.terminate(pod_id, api_key)` or `await spawner.terminate_worker(pod_id)`; `get_pod_status(pod_id)` → `await runpod_lifecycle.api.get_pod_status(pod_id, api_key)`; `create_pod_and_wait` → `await runpod_lifecycle.api.create_pod`; `get_pod_ssh_details` → `await runpod_lifecycle.api.get_pod_ssh_details`; `get_network_volumes` → `await runpod_lifecycle.get_network_volumes`.
5. **Syntax gate**: `python -m py_compile` each edited file.

### Step 7: Wire the adapter into `control_loop.py` (`gpu_orchestrator/control_loop.py`)
**Scope:** Medium
1. **Rewrite** `control_loop.py:44` `self.runpod = create_runpod_client()` → `self.runpod = create_worker_spawner(self.config, self.db)`.
2. **Add `await`** to all 23 call sites to match the new async adapter surface. `generate_worker_id()` remains sync (no `await`).
3. **Leave** the Supabase write flow alone: `create_worker_record` before `spawn_worker`, `update_worker_status` after — those stay in `control_loop`, not in the adapter.
4. **Preserve** RAM-tier fallback, storage-volume fallback, startup-script injection — these remain structurally identical inside `spawn_worker` on the adapter. No opportunistic refactoring.
5. **Grep** the repo for `from gpu_orchestrator.runpod import` and `from gpu_orchestrator.runpod.` — update every hit except `startup_script`.

### Step 8: Delete obsolete code + fix tests (`gpu_orchestrator/runpod/`, `tests/`)
**Scope:** Medium
1. **Delete** `gpu_orchestrator/runpod/{api,client,lifecycle,storage,ssh}.py`. **Keep** `startup_script.py`. Trim `gpu_orchestrator/runpod/__init__.py` to re-export only from `startup_script`.
2. **Delete** obsolete tests: `tests/test_runpod_decomposition.py` (168 lines), `tests/test_direct_runpod_lifecycle.py`, `tests/test_direct_runpod_storage.py`, `tests/gpu_orchestrator/runpod/test_{lifecycle,storage,api,client}.py`. **KEEP** `tests/gpu_orchestrator/runpod/test_startup_script.py` (89 real lines, tests the surviving module).
3. **Update** `tests/gpu_orchestrator/control/phases/test_lifecycle.py:34,48`: `host.runpod = Mock(spec=WorkerSpawnerAdapter)`; mocked coroutines need `AsyncMock` for the async methods (`spawn_worker`, `terminate_worker`, `get_pod_status`, etc.). Keep `generate_worker_id` as plain `Mock` (sync).
4. **Grep** for residual `create_runpod_client` / `RunpodClient` references in the repo — expect zero outside deletions.

### Step 9: Verify the orchestrator (`tests/`)
**Scope:** Small
1. **Run** `pytest -q tests/` — must be green. Fix consumer code, not the package.
2. **Commit** and `git push -u origin runpod-lifecycle-migration`. Do NOT open PR.

### Step 10 (contingent): Package re-export + v0.1.1 (`runpod-lifecycle/src/runpod_lifecycle/__init__.py`)
**Scope:** Small
1. **If** Step 1/5/6 surfaced a missing export (e.g., no `SSHClient`, no storage helpers), choose per-symbol: (a) re-export in the package, bump `pyproject.toml` to `0.1.1`, commit, tag `v0.1.1`, push; update both consumer deps to `@v0.1.1`; OR (b) write a consumer-local shim in `gpu_orchestrator/worker_spawner.py` (preferred for orchestrator-only idioms like storage-mixin internals).
2. **Do not** add unrelated functionality.

## Phase 3: Migrate `reigh-worker`

### Step 11: Migration branch + dep, preserve dirty tree (`reigh-worker/`)
**Scope:** Small
1. **Record** `git status --short` — the dirty-file set. If any overlaps with the migration target set (step 12 below), STOP and surface to human.
2. **If zero overlap**: `git stash push -m "pre-migration-unrelated-changes"`, `git checkout -b runpod-lifecycle-migration`, `git stash pop`. Otherwise leave stashed, branch from main, apply migration, restore stash at the end.
3. **Add** `runpod-lifecycle @ git+https://github.com/banodoco/runpod-lifecycle@v0.1.0` to `pyproject.toml` under project dependencies. Do NOT add a `[dev]` extra unless one already exists.
4. **Install**: `pip install -e .` + `pip install -r` whatever existing test-dep file the repo uses (confirm before running). Verify: `python -c "import runpod_lifecycle"`.

### Step 12: Rewrite reigh-worker call sites
**Scope:** Medium
1. **Brief-listed files:**
   - `scripts/live_test/config.py:73` — `RunpodClient` construction → `RunPodConfig.from_env()` (with adapter imports if the surrounding code needs client-level behavior).
   - `scripts/live_test/variant_fresh.py`, `scripts/live_test/variant_update.py` — low-level calls → `runpod_lifecycle.api.*`, awaited from `asyncio.run(...)` at entry points.
   - `debug/commands/storage.py:18` — `create_runpod_client` → `RunPodConfig.from_env()` + `runpod_lifecycle.get_network_volumes(...)` (awaited).
   - `debug/commands/runpod.py:14,30` — raw `runpod` SDK → `runpod_lifecycle.list_pods` / `find_orphans` (awaited).
2. **Critique-surfaced files (FLAG-004):**
   - `scripts/live_test/__init__.py` — lazy imports of `gpu_orchestrator.runpod.{lifecycle,ssh,api}` will break after Phase 2 deletions. Rewrite to import from `runpod_lifecycle` directly (or inline the shim the module was lazily reaching for).
   - `scripts/live_test/ssh_bootstrap.py` — replace any `gpu_orchestrator.runpod.ssh` imports with `runpod_lifecycle.ssh` equivalents.
   - `scripts/live_test/terminate_guard.py` — replace `gpu_orchestrator.runpod.*` usage with `runpod_lifecycle.terminate` / `api.get_pod_status` (awaited).
   - `scripts/live_test/tests/test_primitives.py` — retarget monkeypatches: `monkeypatch.setattr("gpu_orchestrator.runpod.client", ...)` → monkeypatch the new path (likely `runpod_lifecycle.lifecycle` or the local `live_test_pkg` shim). `live_test_pkg.terminate_pod` monkeypatch stays if that symbol still exists; otherwise update to the new attribute.
3. **Py-compile** each edited file.
4. **Grep** reigh-worker for `gpu_orchestrator.runpod` — expect zero hits after this step.

### Step 13: Verify reigh-worker
**Scope:** Small
1. **Run** `pytest -q` in reigh-worker. Green required. Fix consumer code on failure.
2. **Commit** and `git push -u origin runpod-lifecycle-migration`. No PR. Restore stashed unrelated changes if applicable; verify `git status --short` matches the pre-migration dirty set.

## Phase 4: End-to-end verification

### Step 14: Cross-repo smoke (`runpod-lifecycle/`, `reigh-worker-orchestrator/`, `reigh-worker/`)
**Scope:** Small
1. **Re-run** `cd runpod-lifecycle && .venv/bin/python -m pytest -q` (≥50 passed).
2. **Re-run** `pytest -q tests/` in reigh-worker-orchestrator.
3. **Re-run** `pytest -q` in reigh-worker.
4. **Grep** each consumer repo for `gpu_orchestrator.runpod.api`, `.client`, `.lifecycle`, `.storage`, `.ssh` — expect zero (only `.startup_script` permitted).
5. **Grep** each consumer repo for `create_runpod_client` and `RunpodClient` — expect zero.
6. **Verify** `gh repo view banodoco/runpod-lifecycle` shows `v0.1.0` (and `v0.1.1` if Step 10 fired); confirm the orchestrator's `requirements.txt` pins the intended tag.

## Execution Order

1. Phase 1 must complete before Phase 2 or 3 can install the dep.
2. Phase 2 is serial: Step 3 → Step 4 (audit) → Step 5 (adapter) → Step 6 (scripts) and Step 7 (control_loop) in parallel → Step 8 (delete + tests) → Step 9 (verify). Step 10 interleaves if package gaps surface.
3. Phase 3 starts only after Phase 2 verify is green, so any `v0.1.1` bump has already landed.
4. Phase 4 is the final gate.

## Validation Order

1. `python -m py_compile` each edited file as it changes (instant import-typo catch).
2. Package's own `pytest -q` as Phase-1 gate.
3. Orchestrator `pytest -q tests/` after Step 8 wiring+deletion complete.
4. reigh-worker `pytest -q` after Step 12.
5. Cross-repo grep + `gh repo view` in Step 14.
