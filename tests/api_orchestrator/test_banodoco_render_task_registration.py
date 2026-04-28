"""Sprint 8 (SD-034 + SD-035): banodoco_render_timeline registration."""

from __future__ import annotations

from api_orchestrator.handlers.banodoco import (
    BANODOCO_POOL,
    BANODOCO_POOL_TASK_TYPES,
    handle_banodoco_render_timeline,
)
from api_orchestrator.task_handlers import (
    SUPPORTED_TASK_TYPES,
    TASK_HANDLERS,
    TASK_TYPE_TO_POOL,
)


def test_banodoco_render_timeline_handler_registered() -> None:
    assert "banodoco_render_timeline" in TASK_HANDLERS
    assert TASK_HANDLERS["banodoco_render_timeline"] is handle_banodoco_render_timeline


def test_banodoco_render_timeline_in_supported_types() -> None:
    assert "banodoco_render_timeline" in SUPPORTED_TASK_TYPES


def test_banodoco_render_timeline_routed_to_banodoco_pool() -> None:
    assert TASK_TYPE_TO_POOL["banodoco_render_timeline"] == BANODOCO_POOL


def test_banodoco_pool_taxonomy_includes_render_after_sprint_8() -> None:
    # Render shares the pool with generate. Same image, different entry.
    assert "banodoco_render_timeline" in BANODOCO_POOL_TASK_TYPES
    assert "banodoco_timeline_generate" in BANODOCO_POOL_TASK_TYPES


def test_render_does_not_leak_into_api_pool_defaults() -> None:
    # Sanity: the existing API-pool task types must not have been
    # accidentally tagged for banodoco during the Sprint 8 wiring.
    api_pool_types = {
        "qwen_image_edit",
        "qwen_image_style",
        "wan_2_2_t2i",
        "image_inpaint",
        "image-upscale",
        "video_enhance",
    }
    for task_type in api_pool_types:
        assert task_type not in TASK_TYPE_TO_POOL, (
            f"{task_type} should default to API pool, not be tagged for banodoco"
        )
