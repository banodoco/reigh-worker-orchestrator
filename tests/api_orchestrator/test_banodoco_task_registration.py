"""Sprint 7 (SD-034 + SD-035): banodoco_timeline_generate registration."""

from __future__ import annotations

from api_orchestrator.handlers.banodoco import (
    BANODOCO_POOL,
    BANODOCO_POOL_TASK_TYPES,
    handle_banodoco_timeline_generate,
)
from api_orchestrator.task_handlers import (
    SUPPORTED_TASK_TYPES,
    TASK_HANDLERS,
    TASK_TYPE_TO_POOL,
)


def test_banodoco_timeline_generate_handler_registered() -> None:
    assert "banodoco_timeline_generate" in TASK_HANDLERS
    assert TASK_HANDLERS["banodoco_timeline_generate"] is handle_banodoco_timeline_generate


def test_banodoco_timeline_generate_in_supported_types() -> None:
    assert "banodoco_timeline_generate" in SUPPORTED_TASK_TYPES


def test_banodoco_pool_task_taxonomy_routes_generate_only_in_sprint_7() -> None:
    # Sprint 8 will append `banodoco_render_timeline`. Until then the pool
    # exposes exactly one type — guards against premature registration.
    assert BANODOCO_POOL_TASK_TYPES == {"banodoco_timeline_generate"}


def test_task_type_to_pool_routes_banodoco_generate_to_banodoco_pool() -> None:
    assert TASK_TYPE_TO_POOL["banodoco_timeline_generate"] == BANODOCO_POOL


def test_existing_task_types_default_to_api_pool() -> None:
    # API-pool task types must NOT appear in the banodoco taxonomy.
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
