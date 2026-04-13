"""Task handler registry mapping task_type to handler callables."""

from __future__ import annotations

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
}

SUPPORTED_TASK_TYPES = list(TASK_HANDLERS.keys())

__all__ = ["TASK_HANDLERS", "SUPPORTED_TASK_TYPES"]
