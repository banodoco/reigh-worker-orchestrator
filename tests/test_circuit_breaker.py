"""Circuit-breaker tests for worker failure-rate calculation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from gpu_orchestrator.control_loop import OrchestratorControlLoop
from tests.scaling_decision_helpers import make_config


def test_spawn_failure_does_not_poison_failure_rate():
    now = datetime.now(timezone.utc)
    workers = [
        {
            "id": f"worker-{index}",
            "status": "terminated",
            "termination_reason": "spawn_failed",
            "updated_at": (now - timedelta(minutes=1)).isoformat(),
        }
        for index in range(5)
    ]

    loop = OrchestratorControlLoop.__new__(OrchestratorControlLoop)
    loop.config = make_config(
        max_worker_failure_rate=0.0,
        min_workers_for_rate_check=5,
        failure_window_minutes=5,
    )
    loop.db = SimpleNamespace(get_workers=AsyncMock(return_value=workers))

    result = asyncio.run(loop._check_worker_failure_rate())

    assert result is True
    loop.db.get_workers.assert_awaited_once_with(
        ["spawning", "active", "terminating", "error", "terminated"]
    )
