"""Helpers for keeping live-test workers out of production control actions."""

from __future__ import annotations

from typing import Any, Mapping


def is_live_test_worker(worker: Mapping[str, Any]) -> bool:
    """Return True when a worker row is owned by the live-test harness."""
    metadata = worker.get("metadata")
    if not isinstance(metadata, Mapping):
        return False

    return bool(
        metadata.get("live_test_variant")
        or metadata.get("live_test_run_id")
        or metadata.get("live_test") is True
    )


def filter_live_test_workers(workers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Workers managed by production orchestration, excluding harness-owned rows."""
    return [worker for worker in workers if not is_live_test_worker(worker)]
