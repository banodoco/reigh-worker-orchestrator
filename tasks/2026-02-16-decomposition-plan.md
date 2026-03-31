# 2026-02-16 Structural Decomposition Plan

## Objective

Reduce the remaining structural debt by decomposing oversized/over-complex modules while preserving runtime behavior and deployment workflows.

Current structural state from `desloppify`:

- 23 open `structural` findings
- highest risk file: `gpu_orchestrator/runpod_client.py` (T4, complexity 111)
- highest-density area: `scripts/debug` (29 open items across detectors)

## Success Criteria

- `desloppify show structural --status open` drops from 23 to <= 8
- `desloppify show smells --status open` decreases in the decomposed files
- no regression in current smoke tests and existing unit tests
- each decomposed module has a clear single responsibility and a stable public entrypoint

## Workstream Order

1. `gpu_orchestrator/runpod_client.py` (T4 first)
2. `gpu_orchestrator/control_loop.py`
3. `api_orchestrator/task_handlers.py`
4. `scripts/debug/*` package decomposition
5. logging/database handler consolidation
6. remaining medium files (`fal_utils`, `worker_state`, `storage_utils`, dashboards, tests)

## Workstream 1: `gpu_orchestrator/runpod_client.py`

Target split:

- `gpu_orchestrator/runpod/api.py` (RunPod HTTP/SDK primitives)
- `gpu_orchestrator/runpod/ssh.py` (SSH key loading, command execution)
- `gpu_orchestrator/runpod/storage.py` (network volume health/expansion)
- `gpu_orchestrator/runpod/lifecycle.py` (spawn/terminate/reconcile flows)
- keep `gpu_orchestrator/runpod_client.py` as backward-compatible facade

Exit checks:

- facade file <= 300 LOC
- complexity score < 45
- existing call sites unchanged

## Workstream 2: `gpu_orchestrator/control_loop.py`

Target split:

- `gpu_orchestrator/control/phases/*.py` for cycle phases (claiming, scaling, cleanup, diagnostics)
- `gpu_orchestrator/control/diagnostics.py` for worker diagnostics and redaction
- `gpu_orchestrator/control/decision_io.py` for decision rendering/logging
- keep `OrchestratorControlLoop` as coordinator only

Exit checks:

- main loop module <= 500 LOC
- long methods (>50 LOC) reduced to <= 3

## Workstream 3: `api_orchestrator/task_handlers.py`

Target split:

- `api_orchestrator/handlers/wavespeed.py`
- `api_orchestrator/handlers/fal.py`
- `api_orchestrator/handlers/video.py`
- `api_orchestrator/handlers/image.py`
- central dispatch map in `api_orchestrator/task_handlers.py`

Exit checks:

- each handler module bounded by task family
- reduced dict-key and smell findings in handler path

## Workstream 4: `scripts/debug` package

Target split:

- `scripts/debug/services/` for data-fetch/log-query clients
- `scripts/debug/presenters/` for formatting/output layers
- `scripts/debug/commands/` retain only thin CLI adapters
- keep `scripts/debug_cli.py` as single entrypoint

Exit checks:

- `scripts/debug/client.py` and `scripts/debug/formatters.py` each <= 300 LOC
- command modules mostly orchestration, not business logic

## Workstream 5: Logging + DB Handler Deduplication

Targets:

- unify duplicated logic in:
  - `api_orchestrator/database_log_handler.py`
  - `gpu_orchestrator/database_log_handler.py`
- move shared implementation to `common` module, keep thin wrappers

Exit checks:

- duplicate detector count decreases in logging handler methods
- no change to existing logger initialization API

## Workstream 6: Remaining Medium Modules

Decompose in this order:

1. `api_orchestrator/fal_utils.py`
2. `gpu_orchestrator/worker_state.py`
3. `api_orchestrator/storage_utils.py`
4. `scripts/view_logs_dashboard.py`
5. `scripts/shutdown_all_workers.py`
6. `tests/test_scaling_decision.py` (split fixture/builders vs scenario tests)

## Execution Template Per File

1. Capture baseline:
   - `desloppify show <file> --status open --code`
2. Extract one cohesive concern at a time.
3. Keep a compatibility facade during migration.
4. Run validation:
   - `ruff check .`
   - targeted `pytest` for touched areas
   - `desloppify scan --path .`
5. Resolve/verify only after scan confirms reduction.

## Suggested Batch Plan

- Batch A (high impact): Workstreams 1-3
- Batch B (maintainability): Workstreams 4-5
- Batch C (cleanup): Workstream 6

Run `desloppify scan --path .` after each batch and record score deltas in this file.

## Execution Log

### 2026-02-16 - Workstream 1 (RunPod package extraction)

Completed:

- extracted `gpu_orchestrator/runpod_client.py` into a decomposed package:
  - `gpu_orchestrator/runpod/api.py`
  - `gpu_orchestrator/runpod/ssh.py`
  - `gpu_orchestrator/runpod/storage.py`
  - `gpu_orchestrator/runpod/lifecycle.py`
  - `gpu_orchestrator/runpod/startup_script.py`
  - `gpu_orchestrator/runpod/worker_startup.template.sh`
  - `gpu_orchestrator/runpod/client.py`
- converted `gpu_orchestrator/runpod_client.py` into a backward-compatible facade export module.
- added focused tests in `tests/test_runpod_decomposition.py`.

Score deltas:

- baseline before extraction (previous scan): strict `96.8/100`
- after extraction and initial scan: strict `94.3/100`
- after targeted runpod tests: strict `94.8/100`
- after module-mapped direct coverage tests: strict `94.5/100`

Current Workstream 1 status:

- structural risk in legacy `gpu_orchestrator/runpod_client.py` is removed (facade now small/stable).
- follow-up hardening still needed to recover score regression from new-module smell/review findings.

### 2026-02-16 - Workstream 2 (Control loop decomposition)

Completed:

- decomposed `gpu_orchestrator/control_loop.py` into a coordinator + control package:
  - `gpu_orchestrator/control/decision_io.py`
  - `gpu_orchestrator/control/diagnostics.py`
  - `gpu_orchestrator/control/phases/state.py`
  - `gpu_orchestrator/control/phases/lifecycle.py`
  - `gpu_orchestrator/control/phases/health.py`
  - `gpu_orchestrator/control/phases/scaling_decision.py`
  - `gpu_orchestrator/control/phases/worker_capacity.py`
  - `gpu_orchestrator/control/phases/periodic.py`
- reduced `gpu_orchestrator/control_loop.py` to orchestration coordinator responsibilities.
- added direct module-mapped tests for control and runpod decomposition coverage:
  - `tests/gpu_orchestrator/control/*`
  - `tests/test_direct_control_health.py`
  - `tests/test_direct_runpod_storage.py`
  - `tests/test_direct_runpod_lifecycle.py`

Score deltas:

- before Workstream 2 start: strict `92.9/100`
- after first pass extraction: strict `92.5/100`
- after second-pass split and direct coverage tests: strict `95.2/100`
- after review-resolution pass: strict `95.3/100`

Current Workstream 2 status:

- control loop is structurally decomposed with phase boundaries and diagnostics/decision I/O separated.
- remaining control debt is now concentrated in smaller modules (`single_use`, `smells`) rather than a monolithic coordinator.

### 2026-02-16 - Workstreams 3/4/5/6 continuation (score recovery + debug decomposition)

Completed:

- workstream 3 hardening:
  - resolved `dict_keys` drift/phantom reads in `api_orchestrator/handlers/wavespeed.py`.
  - normalized task reset payload construction in `gpu_orchestrator/database.py` to remove schema-drift key warning.
  - added direct handler coverage tests in `tests/api_orchestrator/test_handlers_direct.py`.
- workstream 4 decomposition:
  - split debug formatting layer into presenter modules:
    - `scripts/debug/presenters/task_presenter.py`
    - `scripts/debug/presenters/worker_presenter.py`
    - `scripts/debug/presenters/summary_presenter.py`
    - `scripts/debug/presenters/system_presenter.py`
  - kept `scripts/debug/formatters.py` as stable facade API.
  - split `scripts/debug/client.py` into service-backed flows:
    - `scripts/debug/services/tasks.py`
    - `scripts/debug/services/workers.py`
    - `scripts/debug/services/system.py`
  - decomposed high-complexity debug command modules into helper-oriented structure:
    - `scripts/debug/commands/config.py`
    - `scripts/debug/commands/infra.py`
    - `scripts/debug/commands/runpod.py`
    - `scripts/debug/commands/storage.py`
    - `scripts/debug/commands/worker.py`
- workstream 5 dedup/facade cleanup:
  - converted compatibility wrappers to thin compatibility subclasses:
    - `api_orchestrator/database_log_handler.py`
    - `gpu_orchestrator/database_log_handler.py`
  - removed barrel re-export facades from:
    - `api_orchestrator/handlers/__init__.py`
    - `orchestrator_common/__init__.py`
  - switched `api_orchestrator/task_handlers.py` to direct module imports (no barrel dependency).
- workstream 6 targeted decomposition:
  - split `tests/test_scaling_decision.py` into:
    - `tests/scaling_decision_helpers.py`
    - `tests/test_scaling_decision_spawn.py`
    - `tests/test_scaling_decision_limits.py`

Test/verification:

- `ruff check` clean on all changed modules.
- full test suite passing: `71 passed, 1 warning`.
- `desloppify scan --path .` repeated after each batch.

Score deltas (strict):

- baseline at start of this continuation: `93.5/100`
- after dict-key + direct-coverage fixes: `96.3/100`
- after debug command/client/presenter decomposition and cleanup passes: `96.7/100`

Current status snapshot:

- `dict_keys`, `test_coverage`, `single_use`, and `facade` are now fully clean.
- open `structural` findings reduced to `18` (from `26` at start of this continuation).
- remaining structural debt is concentrated in large production modules (`fal_utils`, `worker_state`, `storage_utils`, `database`, runpod internals) and a few large operational scripts.
