"""Sprint 7 (SD-034): worker_unavailable surfacing for banodoco pool.

The orchestrator surfaces `worker_unavailable` when no `banodoco`-pool
worker claims a queued task within `BANODOCO_WORKER_UNAVAILABLE_TIMEOUT_SEC`
(default 30s). The agent's task-status poll uses this signal to tell the
user the generation is stuck and to retry.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from api_orchestrator.handlers.banodoco import (
    BANODOCO_POOL,
    DEFAULT_WORKER_UNAVAILABLE_TIMEOUT_SEC,
    handle_banodoco_timeline_generate,
    is_worker_pool_available,
)


def test_pool_available_when_queued_recently() -> None:
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)
    queued_at = now - timedelta(seconds=5)
    assert is_worker_pool_available(BANODOCO_POOL, queued_at, now=now) is True


def test_pool_unavailable_after_default_timeout() -> None:
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)
    queued_at = now - timedelta(seconds=DEFAULT_WORKER_UNAVAILABLE_TIMEOUT_SEC + 1)
    assert is_worker_pool_available(BANODOCO_POOL, queued_at, now=now) is False


def test_pool_timeout_overridable_by_argument() -> None:
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)
    queued_at = now - timedelta(seconds=10)
    assert is_worker_pool_available(BANODOCO_POOL, queued_at, now=now, timeout_sec=5) is False
    assert is_worker_pool_available(BANODOCO_POOL, queued_at, now=now, timeout_sec=120) is True


def test_pool_timeout_picks_up_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BANODOCO_WORKER_UNAVAILABLE_TIMEOUT_SEC", "5")
    now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)
    queued_at = now - timedelta(seconds=10)
    assert is_worker_pool_available(BANODOCO_POOL, queued_at, now=now) is False


def test_api_handler_raises_worker_unavailable_when_invoked() -> None:
    """The API worker must not silently execute banodoco tasks. It surfaces
    `worker_unavailable` so the agent's poll sees the right reason."""
    valid_params = {
        "intent": "extend",
        "brief_inputs": {},
        "theme_id": "2rp",
        "expected_version": 1,
        "scope": "insert",
        "user_jwt": "jwt",
        "project_id": str(uuid.uuid4()),
        "timeline_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
    }

    async def call() -> None:
        await handle_banodoco_timeline_generate(
            {"task_id": "t-1", "task_type": "banodoco_timeline_generate"},
            valid_params,
            client=None,  # type: ignore[arg-type]
        )

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(call())
    assert "worker_unavailable" in str(exc.value)
