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
