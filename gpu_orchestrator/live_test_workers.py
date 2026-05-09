"""Distinguishing live-test workers from production workers.

The orchestrator treats live-test workers as opaque to *capacity control* —
they don't count toward production scaling decisions, and the production
phases (spawn handling, health checks) skip them, because their lifecycle is
owned by an out-of-band harness on its own schedule.

They are NOT opaque to *cleanup*. The failsafe stale check and zombie
reaper operate on every worker the orchestrator can see. Crashes leak GPUs
regardless of who originally owned the pod, so the orchestrator is the
single authority on liveness; ownership tags only affect accounting.
"""

from __future__ import annotations

from typing import Any, Mapping


def is_excluded_from_capacity_control(worker: Mapping[str, Any]) -> bool:
    """Return True when a worker should be ignored by production capacity math.

    These workers are still subject to failsafe and zombie reaping — see
    ``partition_capacity_workers`` for how the control loop applies the
    distinction.
    """
    metadata = worker.get("metadata")
    if not isinstance(metadata, Mapping):
        return False

    return bool(
        metadata.get("live_test_variant")
        or metadata.get("live_test_run_id")
        or metadata.get("live_test") is True
    )


def partition_capacity_workers(
    workers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split workers into ``(production, excluded)`` lists.

    Production workers participate in scaling, spawn handling, and active
    health checks. Excluded workers are visible only to cleanup paths
    (failsafe stale check, zombie reaper).
    """
    production: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for worker in workers:
        (excluded if is_excluded_from_capacity_control(worker) else production).append(worker)
    return production, excluded
