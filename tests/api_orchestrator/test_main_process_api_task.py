"""Behavioral tests for api_orchestrator.main.process_api_task."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import pytest

from api_orchestrator import main as api_main


class DummyClient:
    """Minimal placeholder for handler signature compatibility."""


def _run(coro: Any) -> Any:
    """Run coroutine in a fresh event loop for plain pytest tests."""
    return asyncio.run(coro)


def test_process_api_task_parses_json_params_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, Any] = {}

    async def handler(task: Dict[str, Any], params: Dict[str, Any], _client: Any) -> Dict[str, Any]:
        captured["task"] = task
        captured["params"] = params
        return {"ok": True, "value": params.get("value")}

    monkeypatch.setitem(api_main.TASK_HANDLERS, "unit_task", handler)

    result = _run(
        api_main.process_api_task(
            {
                "task_type": "unit_task",
                "params": json.dumps({"value": 42}),
            },
            DummyClient(),
        )
    )

    assert result == {"ok": True, "value": 42}
    assert captured["params"]["value"] == 42


def test_process_api_task_raises_value_error_on_invalid_json() -> None:
    with pytest.raises(ValueError) as exc_info:
        _run(
            api_main.process_api_task(
                {
                    "task_type": "qwen_image_edit",
                    "params": "{not valid json",
                },
                DummyClient(),
            )
        )

    assert "Invalid JSON in params field" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


def test_process_api_task_raises_value_error_for_unknown_task() -> None:
    with pytest.raises(ValueError) as exc_info:
        _run(
            api_main.process_api_task(
                {
                    "task_type": "definitely_not_supported",
                    "params": {},
                },
                DummyClient(),
            )
        )

    assert "Unsupported task type" in str(exc_info.value)


def test_process_api_task_applies_legacy_wavespeed_override(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Any] = {}

    async def qwen_handler(task: Dict[str, Any], params: Dict[str, Any], _client: Any) -> Dict[str, Any]:
        seen["task_type"] = task.get("task_type")
        seen["params"] = params
        return {"handled": "qwen_image_edit"}

    monkeypatch.setitem(api_main.TASK_HANDLERS, "qwen_image_edit", qwen_handler)

    result = _run(
        api_main.process_api_task(
            {
                "task_type": "legacy_task",
                "params": {"api_type": "wavespeed", "prompt": "hello"},
            },
            DummyClient(),
        )
    )

    assert result == {"handled": "qwen_image_edit"}
    assert seen["params"]["api_type"] == "wavespeed"
