"""Direct tests for extracted api_orchestrator handler modules."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import api_orchestrator.handlers.common as common_handlers
import api_orchestrator.handlers.fal as fal_handlers
import api_orchestrator.handlers.image as image_handlers
import api_orchestrator.handlers.wavespeed as wavespeed_handlers


class DummyClient:
    """Placeholder for handler signatures."""


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_get_fal_tracking_reads_step_specific_metadata(monkeypatch: Any) -> None:
    async def fake_get_task_metadata(_task_id: str) -> Dict[str, Any]:
        return {
            "fal_upscale": {
                "fal_request_id": "req-123",
                "fal_endpoint": "fal-ai/seedvr/upscale/image",
                "fal_submitted_at": "2026-02-16T00:00:00+00:00",
            }
        }

    monkeypatch.setattr(common_handlers, "get_task_metadata", fake_get_task_metadata)

    tracking = _run(common_handlers.get_fal_tracking("task-1", step_key="upscale"))

    assert tracking is not None
    assert tracking.request_id == "req-123"
    assert tracking.endpoint == "fal-ai/seedvr/upscale/image"


def test_update_fal_tracking_wraps_payload_for_step(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_update_task_metadata(task_id: str, metadata: Dict[str, Any]) -> bool:
        captured["task_id"] = task_id
        captured["metadata"] = metadata
        return True

    monkeypatch.setattr(common_handlers, "update_task_metadata", fake_update_task_metadata)

    ok = _run(
        common_handlers.update_fal_tracking(
            "task-2",
            {"fal_request_id": "req-x", "fal_endpoint": "fal-ai/test", "fal_submitted_at": "now"},
            step_key="interpolation",
        )
    )

    assert ok is True
    assert captured["task_id"] == "task-2"
    assert "fal_interpolation" in captured["metadata"]


def test_handle_image_inpaint_builds_lora_payload(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_composite(
        _client: Any,
        _task_id: str,
        _image_url: str,
        _mask_url: str,
        filename_prefix: str,
    ) -> str:
        assert filename_prefix == "inpaint_composite"
        return "https://example.com/composite.png"

    async def fake_wavespeed(endpoint: str, wavespeed_params: Dict[str, Any], _client: Any) -> Dict[str, Any]:
        captured["endpoint"] = endpoint
        captured["params"] = wavespeed_params
        return {"output_url": "https://wavespeed.example/out.png"}

    async def fake_process(_client: Any, _task_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        return {**result, "processed": True}

    monkeypatch.setattr(image_handlers, "create_masked_composite_image", fake_composite)
    monkeypatch.setattr(image_handlers, "call_wavespeed_api", fake_wavespeed)
    monkeypatch.setattr(image_handlers, "process_external_url_result", fake_process)

    result = _run(
        image_handlers.handle_image_inpaint(
            {"id": "task-image", "task_type": "image_inpaint"},
            {
                "image_url": "https://example.com/input.png",
                "mask_url": "https://example.com/mask.png",
                "prompt": "replace background with mountains",
                "qwen_edit_model": "qwen-edit-2511",
                "resolution": "1024x1024",
                "seed": 7,
                "loras": [{"url": "https://example.com/lora.safetensors", "strength": 0.8}],
            },
            DummyClient(),
        )
    )

    assert captured["endpoint"] == "wavespeed-ai/qwen-image/edit-2511-lora"
    assert captured["params"]["images"] == ["https://example.com/composite.png"]
    assert len(captured["params"]["loras"]) == 2
    assert result["processed"] is True


def test_handle_qwen_image_uses_model_mapping(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_get_tracking(_task_id: str) -> None:
        return None

    async def fake_resilient(**kwargs: Any) -> Dict[str, Any]:
        captured["endpoint"] = kwargs["endpoint"]
        captured["arguments"] = kwargs["arguments"]
        return {"output_url": "https://fal.example/out.png"}

    async def fake_process(_client: Any, _task_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        return {**result, "normalized": True}

    monkeypatch.setattr(fal_handlers, "build_fal_lora_list", lambda _params: [{"path": "x", "scale": 0.5}])
    monkeypatch.setattr(fal_handlers, "get_fal_tracking", fake_get_tracking)
    monkeypatch.setattr(fal_handlers, "call_fal_api_resilient", fake_resilient)
    monkeypatch.setattr(fal_handlers, "process_external_url_result", fake_process)

    result = _run(
        fal_handlers.handle_qwen_image(
            {"id": "task-fal", "task_type": "qwen_image_2512"},
            {"prompt": "A cat in sunglasses", "resolution": "1024x512"},
            DummyClient(),
        )
    )

    assert captured["endpoint"] == "fal-ai/qwen-image-2512"
    assert captured["arguments"]["image_size"] == {"width": 1024, "height": 512}
    assert captured["arguments"]["loras"] == [{"path": "x", "scale": 0.5}]
    assert result["normalized"] is True


def test_handle_wan_2_2_i2v_uses_base_prompt_fallback(monkeypatch: Any) -> None:
    captured: Dict[str, Any] = {}

    async def fake_wavespeed(endpoint: str, wavespeed_params: Dict[str, Any], _client: Any) -> Dict[str, Any]:
        captured["endpoint"] = endpoint
        captured["params"] = wavespeed_params
        return {"output_url": "https://wavespeed.example/video.mp4"}

    async def fake_process(_client: Any, _task_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        return result

    monkeypatch.setattr(wavespeed_handlers, "call_wavespeed_api", fake_wavespeed)
    monkeypatch.setattr(wavespeed_handlers, "process_external_url_result", fake_process)

    _run(
        wavespeed_handlers.handle_wan_2_2_i2v(
            {"id": "task-video", "task_type": "wan_2_2_i2v"},
            {
                "orchestrator_details": {
                    "input_image_paths_resolved": ["https://example.com/frame-1.png"],
                    "base_prompts_expanded": [],
                    "base_prompt": "camera pans over snowy mountains",
                }
            },
            DummyClient(),
        )
    )

    assert captured["endpoint"] == "wavespeed-ai/wan-2.2/i2v-480p"
    assert captured["params"]["prompt"] == "camera pans over snowy mountains"
    assert captured["params"]["duration"] == 5
    assert captured["params"]["seed"] == -1
