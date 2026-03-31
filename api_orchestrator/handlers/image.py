"""Image-processing task handlers."""

from __future__ import annotations

import logging
import random
from typing import Any, Dict

import httpx

from api_orchestrator.image_utils import create_masked_composite_image, normalize_resolution
from api_orchestrator.storage_utils import process_external_url_result
from api_orchestrator.wavespeed_utils import call_wavespeed_api

logger = logging.getLogger(__name__)

async def handle_image_inpaint(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle image_inpaint task type via Wavespeed API."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "image_inpaint")

    logger.info(f"Processing {task_type} task via Wavespeed API")

    # Extract parameters
    image_url = params.get("image_url") or params.get("image", "")
    mask_url = params.get("mask_url", "")
    prompt = params.get("prompt", "")

    if not image_url:
        raise Exception("image_url parameter is required for image_inpaint task")
    if not mask_url:
        raise Exception("mask_url parameter is required for image_inpaint task")

    logger.info(f"Inpainting image: {image_url}")
    logger.info(f"Using mask: {mask_url}")
    logger.info(f"Prompt: {prompt}")

    # Create masked composite image
    composite_url = await create_masked_composite_image(
        client, task_id, image_url, mask_url, filename_prefix="inpaint_composite"
    )

    # Generate random seed if not provided
    seed = params.get("seed")
    if seed is None or seed == -1:
        seed = random.randint(0, 2**31 - 1)
        logger.info(f"Generated random seed: {seed}")

    # Determine which model/endpoint to use based on qwen_edit_model parameter
    # Inpaint always uses LoRA endpoints since it needs the inpainting LoRA
    qwen_edit_model = params.get("qwen_edit_model", "qwen-edit")  # default to current behavior

    # Map model names to endpoints
    # 2511 and 2509 (edit-plus) use "images" array format
    # Default edit-lora uses "image" string format
    if qwen_edit_model == "qwen-edit-2511":
        endpoint_path = "wavespeed-ai/qwen-image/edit-2511-lora"
        use_images_array = True
    elif qwen_edit_model == "qwen-edit-2509":
        endpoint_path = "wavespeed-ai/qwen-image/edit-plus-lora"
        use_images_array = True
    else:  # "qwen-edit" or default
        endpoint_path = "wavespeed-ai/qwen-image/edit-lora"
        use_images_array = False

    logger.info(f"Using qwen_edit_model: {qwen_edit_model} -> endpoint: {endpoint_path}")

    # Build the inpainting LoRA list
    inpaint_loras = [
        {
            "path": "https://huggingface.co/ostris/qwen_image_edit_inpainting/resolve/main/qwen_image_edit_inpainting.safetensors",
            "scale": 1.0
        }
    ]

    # Add any additional LoRAs from params
    additional_loras = params.get("loras", [])
    if additional_loras:
        for lora in additional_loras:
            if isinstance(lora, dict):
                # Support both "url"/"strength" and "path"/"scale" formats
                lora_url = lora.get("url") or lora.get("path", "")
                lora_strength = lora.get("strength", lora.get("scale", 1.0))

                if lora_url:
                    inpaint_loras.append({
                        "path": lora_url,
                        "scale": float(lora_strength)
                    })
                    logger.info(f"Added additional inpaint LoRA: {lora_url} with strength {lora_strength}")
        logger.info(f"Added {len(additional_loras)} additional LoRAs to inpaint request")

    # Build parameters based on API format
    if use_images_array:
        # 2511/2509 APIs use "images" array format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "images": [composite_url],
            "output_format": params.get("output_format", "jpeg"),
            "prompt": prompt,
            "seed": seed,
            "loras": inpaint_loras
        }
    else:
        # Default edit-lora API uses "image" string format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "output_format": params.get("output_format", "jpeg"),
            "prompt": prompt,
            "seed": seed,
            "image": composite_url,  # Use the composite image with green mask
            "loras": inpaint_loras
        }

    # Add size parameter if resolution is provided
    resolution = params.get("resolution", "")
    if resolution:
        normalized_size = normalize_resolution(resolution)
        if normalized_size:
            wavespeed_params["size"] = normalized_size
            logger.info(f"Using resolution/size: {normalized_size}")

    logger.info("Calling inpainting API with composite image and LoRA")

    result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

    # Process external URL with automatic screenshot extraction for videos
    result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result


async def handle_annotated_image_edit(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle annotated_image_edit task type via Wavespeed API."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "annotated_image_edit")

    logger.info(f"Processing {task_type} task via Wavespeed API")

    # Extract parameters
    image_url = params.get("image_url") or params.get("image", "")
    mask_url = params.get("mask_url", "")
    prompt = params.get("prompt", "")

    if not image_url:
        raise Exception("image_url parameter is required for annotated_image_edit task")
    if not mask_url:
        raise Exception("mask_url parameter is required for annotated_image_edit task")

    logger.info(f"Annotated image editing: {image_url}")
    logger.info(f"Using mask: {mask_url}")
    logger.info(f"Prompt: {prompt}")

    # Create masked composite image
    composite_url = await create_masked_composite_image(
        client, task_id, image_url, mask_url, filename_prefix="annotated_edit_composite"
    )

    # Generate random seed if not provided
    seed = params.get("seed")
    if seed is None or seed == -1:
        seed = random.randint(0, 2**31 - 1)
        logger.info(f"Generated random seed: {seed}")

    # Determine which model/endpoint to use based on qwen_edit_model parameter
    # Annotated edit always uses LoRA endpoints since it needs the annotation LoRA
    qwen_edit_model = params.get("qwen_edit_model", "qwen-edit")  # default to current behavior

    # Map model names to endpoints
    # 2511 and 2509 (edit-plus) use "images" array format
    # Default edit-lora uses "image" string format
    if qwen_edit_model == "qwen-edit-2511":
        endpoint_path = "wavespeed-ai/qwen-image/edit-2511-lora"
        use_images_array = True
    elif qwen_edit_model == "qwen-edit-2509":
        endpoint_path = "wavespeed-ai/qwen-image/edit-plus-lora"
        use_images_array = True
    else:  # "qwen-edit" or default
        endpoint_path = "wavespeed-ai/qwen-image/edit-lora"
        use_images_array = False

    logger.info(f"Using qwen_edit_model: {qwen_edit_model} -> endpoint: {endpoint_path}")

    # Build the annotation LoRA list
    annotation_loras = [
        {
            "path": "https://huggingface.co/peteromallet/random_junk/resolve/main/in_scene_pure_squares_flipped_450_lr_000006700.safetensors",
            "scale": 1.0
        }
    ]

    # Add any additional LoRAs from params
    additional_loras = params.get("loras", [])
    if additional_loras:
        for lora in additional_loras:
            if isinstance(lora, dict):
                # Support both "url"/"strength" and "path"/"scale" formats
                lora_url = lora.get("url") or lora.get("path", "")
                lora_strength = lora.get("strength", lora.get("scale", 1.0))

                if lora_url:
                    annotation_loras.append({
                        "path": lora_url,
                        "scale": float(lora_strength)
                    })
                    logger.info(f"Added additional annotated edit LoRA: {lora_url} with strength {lora_strength}")
        logger.info(f"Added {len(additional_loras)} additional LoRAs to annotated edit request")

    # Build parameters based on API format
    if use_images_array:
        # 2511/2509 APIs use "images" array format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "images": [composite_url],
            "output_format": params.get("output_format", "jpeg"),
            "prompt": prompt,
            "seed": seed,
            "loras": annotation_loras
        }
    else:
        # Default edit-lora API uses "image" string format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "output_format": params.get("output_format", "jpeg"),
            "prompt": prompt,
            "seed": seed,
            "image": composite_url,  # Use the composite image with green mask
            "loras": annotation_loras
        }

    # Add size parameter if resolution is provided
    resolution = params.get("resolution", "")
    if resolution:
        normalized_size = normalize_resolution(resolution)
        if normalized_size:
            wavespeed_params["size"] = normalized_size
            logger.info(f"Using resolution/size: {normalized_size}")

    logger.info("Calling annotated edit API with composite image and scene annotation LoRAs")

    result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

    # Process external URL with automatic screenshot extraction for videos
    result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result



__all__ = [
    "handle_image_inpaint",
    "handle_annotated_image_edit",
]
