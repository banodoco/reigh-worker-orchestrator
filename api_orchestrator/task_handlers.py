"""Task handler registry mapping task_type to handler callables."""

from __future__ import annotations

from .handlers.banodoco import (
    BANODOCO_POOL,
    BANODOCO_POOL_TASK_TYPES,
    handle_banodoco_render_timeline,
    handle_banodoco_timeline_generate,
)
from .handlers.fal import handle_flux_klein_edit, handle_image_upscale, handle_qwen_image, handle_video_enhance, handle_z_image_turbo_i2i
from .handlers.image import handle_annotated_image_edit, handle_image_inpaint
from .handlers.wavespeed import (
    handle_animate_character,
    handle_qwen_image_edit,
    handle_qwen_image_style,
    handle_wan_2_2_i2v,
    handle_wan_2_2_t2i,
)

# Task handler registry mapping task types to handler functions.
TASK_HANDLERS = {
    "qwen_image_edit": handle_qwen_image_edit,
    "qwen_image_style": handle_qwen_image_style,
    "wan_2_2_t2i": handle_wan_2_2_t2i,
    "animate_character": handle_animate_character,
    "wan_2_2_i2v": handle_wan_2_2_i2v,
    "image_inpaint": handle_image_inpaint,
    "annotated_image_edit": handle_annotated_image_edit,
    "image-upscale": handle_image_upscale,
    "qwen_image": handle_qwen_image,
    "qwen_image_2512": handle_qwen_image,
    "z_image_turbo": handle_qwen_image,
    "z_image_turbo_i2i": handle_z_image_turbo_i2i,
    "video_enhance": handle_video_enhance,
    "flux_klein_edit": handle_flux_klein_edit,
    # Sprint 7 + Sprint 8 (SD-034 + SD-035): banodoco worker-pool tasks.
    # The handlers in this registry are the API-worker fallbacks (each
    # raises worker_unavailable); the real execution path is the pinned
    # Railway `banodoco-worker` service polling the queue with both task
    # types in its claim payload. See `handlers/banodoco.py`.
    "banodoco_timeline_generate": handle_banodoco_timeline_generate,
    "banodoco_render_timeline": handle_banodoco_render_timeline,
}

SUPPORTED_TASK_TYPES = list(TASK_HANDLERS.keys())

# Worker-pool taxonomy. Maps each task_type to the pool that should claim
# it. Tasks not listed here default to the API pool (existing behavior).
TASK_TYPE_TO_POOL: dict[str, str] = {
    task_type: BANODOCO_POOL for task_type in BANODOCO_POOL_TASK_TYPES
}

__all__ = ["TASK_HANDLERS", "SUPPORTED_TASK_TYPES", "TASK_TYPE_TO_POOL"]
