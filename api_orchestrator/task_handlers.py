"""Individual task handler functions for each task type."""

import asyncio
import base64
import logging
import os
import random
import tempfile
from typing import Any, Dict, Optional

import httpx

from .fal_utils import (
    call_fal_api_resilient,
    build_fal_lora_list,
    FalRequestTracking,
    FalRetryConfig,
)
from .image_utils import (
    normalize_resolution,
    get_image_dimensions,
    scale_dimensions_to_megapixels,
    create_masked_composite_image,
)
from .storage_utils import process_external_url_result, upload_to_supabase_storage
from .task_utils import update_task_metadata, get_task_metadata
from .wavespeed_utils import call_wavespeed_api
from .video_utils import (
    download_video_to_temp,
    remove_last_frame_from_video,
    join_videos,
    extract_first_frame_bytes,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Helper for resilient fal.ai calls
# =============================================================================

async def _get_fal_tracking(task_id: str, step_key: Optional[str] = None) -> Optional[FalRequestTracking]:
    """
    Get existing fal.ai tracking data from task metadata.
    
    Args:
        task_id: The task ID
        step_key: Optional key for multi-step tasks (e.g., "interpolation", "upscale")
        
    Returns:
        FalRequestTracking if found, None otherwise
    """
    try:
        metadata = await get_task_metadata(task_id)
        if not metadata:
            return None
        
        # For multi-step tasks, look in step-specific tracking
        if step_key and f'fal_{step_key}' in metadata:
            step_data = metadata[f'fal_{step_key}']
            return FalRequestTracking.from_dict(step_data)
        
        # Otherwise look for top-level tracking
        return FalRequestTracking.from_dict(metadata)
        
    except Exception as e:
        logger.warning(f"Could not get fal.ai tracking for task {task_id}: {e}")
        return None


async def _update_fal_tracking(task_id: str, tracking_data: Dict[str, Any], step_key: Optional[str] = None) -> bool:
    """
    Update task metadata with fal.ai tracking data.
    
    Args:
        task_id: The task ID
        tracking_data: The tracking data dict
        step_key: Optional key for multi-step tasks
        
    Returns:
        True if successful
    """
    if step_key:
        # Wrap in step-specific key for multi-step tasks
        tracking_data = {f'fal_{step_key}': tracking_data}
    
    return await update_task_metadata(task_id, tracking_data)


async def handle_qwen_image_edit(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle qwen_image_edit task type via Wavespeed API."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "qwen_image_edit")

    # Determine which model/endpoint to use based on qwen_edit_model parameter
    qwen_edit_model = params.get("qwen_edit_model", "qwen-edit")  # default to current behavior

    # Check if we have LoRAs to determine if we need LoRA endpoint variant
    loras = params.get("loras", [])
    has_loras = bool(loras)

    # Map model names to endpoints
    # 2511 and 2509 (edit-plus) use "images" array format
    # Default edit-lora uses "image" string format
    if qwen_edit_model == "qwen-edit-2511":
        endpoint_path = "wavespeed-ai/qwen-image/edit-2511-lora" if has_loras else "wavespeed-ai/qwen-image/edit-2511"
        use_images_array = True
    elif qwen_edit_model == "qwen-edit-2509":
        endpoint_path = "wavespeed-ai/qwen-image/edit-plus-lora" if has_loras else "wavespeed-ai/qwen-image/edit-plus"
        use_images_array = True
    else:  # "qwen-edit" or default
        endpoint_path = "wavespeed-ai/qwen-image/edit-lora"
        use_images_array = False

    logger.info(f"Using qwen_edit_model: {qwen_edit_model} -> endpoint: {endpoint_path}")

    # Build parameters based on API format
    image_url = params.get("image", "")

    if use_images_array:
        # 2511/2509 APIs use "images" array format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "images": [image_url] if image_url else [],
            "output_format": params.get("output_format", "jpeg"),
            "prompt": params.get("prompt", ""),
            "seed": params.get("seed", -1),
        }

        # Add size parameter if resolution is provided
        resolution = params.get("resolution", "")
        if resolution:
            normalized_size = normalize_resolution(resolution)
            if normalized_size:
                wavespeed_params["size"] = normalized_size
                logger.info(f"Using resolution/size: {normalized_size}")

        # Add LoRAs if using lora endpoint variant
        if has_loras:
            wavespeed_params["loras"] = []
            for lora in loras:
                if isinstance(lora, dict):
                    lora_url = lora.get("url") or lora.get("path", "")
                    lora_strength = lora.get("strength", lora.get("scale", 1.0))
                    if lora_url:
                        wavespeed_params["loras"].append({
                            "path": lora_url,
                            "scale": float(lora_strength)
                        })
                        logger.info(f"Added LoRA: {lora_url} with strength {lora_strength}")
            logger.info(f"Processing with {len(wavespeed_params['loras'])} LoRAs")
        else:
            logger.info("Processing without LoRAs")
    else:
        # Default edit-lora API uses "image" string format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "image": image_url,  # API expects "image" as string (not array)
            "output_format": params.get("output_format", "jpeg"),
            "prompt": params.get("prompt", ""),
            "seed": params.get("seed", -1),
            "loras": []
        }

        # Add size parameter if resolution is provided
        resolution = params.get("resolution", "")
        if resolution:
            normalized_size = normalize_resolution(resolution)
            if normalized_size:
                wavespeed_params["size"] = normalized_size
                logger.info(f"Using resolution/size: {normalized_size}")

        # Map loras - support both {"url": ..., "strength": ...} and {"path": ..., "scale": ...} formats
        for lora in loras:
            if isinstance(lora, dict):
                # Support both "url"/"strength" and "path"/"scale" formats
                lora_url = lora.get("url") or lora.get("path", "")
                lora_strength = lora.get("strength", lora.get("scale", 1.0))

                if lora_url:
                    wavespeed_params["loras"].append({
                        "path": lora_url,
                        "scale": float(lora_strength)
                    })
                    logger.info(f"Added LoRA: {lora_url} with strength {lora_strength}")

        if loras:
            logger.info(f"Processing with {len(wavespeed_params['loras'])} LoRAs")
        else:
            logger.info("Processing without LoRAs")

    result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

    # Process external URL with automatic screenshot extraction for videos
    result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result


async def handle_qwen_image_style(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle qwen_image_style task type via Wavespeed API."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "qwen_image_style")

    logger.info(f"Processing {task_type} task via Wavespeed API")

    # Determine which model/endpoint to use based on qwen_edit_model parameter
    # Style transfer always uses LoRA endpoints since it needs style/subject/scene LoRAs
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

    # Build the prompt with style and subject modifications
    original_prompt = params.get("prompt", "")
    modified_prompt = original_prompt

    # Get style, subject, and scene parameters
    style_strength = params.get("style_reference_strength", 0.0)
    subject_strength = params.get("subject_strength", 0.0)
    scene_strength = params.get("scene_reference_strength", 0.0)
    subject_description = params.get("subject_description", "")
    in_this_scene = params.get("in_this_scene", False)

    # Build prompt modifications
    prompt_parts = []
    has_style_prefix = False

    # Add style prefix if style_strength > 0
    if style_strength > 0.0:
        prompt_parts.append("In the style of this image,")
        has_style_prefix = True

    # Add subject prefix if subject_strength > 0
    if subject_strength > 0.0 and subject_description:
        # Use lowercase 'make' if style prefix is already present
        make_word = "make" if has_style_prefix else "Make"
        if in_this_scene:
            prompt_parts.append(f"{make_word} an image of this {subject_description} in this scene:")
        else:
            prompt_parts.append(f"{make_word} an image of this {subject_description}:")

    # Combine prompt parts with original prompt
    if prompt_parts:
        modified_prompt = " ".join(prompt_parts) + " " + original_prompt
        logger.info(f"Modified prompt from '{original_prompt}' to '{modified_prompt}'")

    # Determine which reference image to use (they should be the same)
    reference_image = params.get("style_reference_image") or params.get("subject_reference_image", "")

    logger.info(f"Using reference image: {reference_image}")

    # Build LoRA list for style transfer
    style_loras = []

    # Add LoRA configuration for style transfer
    # Use a default style transfer LoRA if style_reference_strength is provided
    if style_strength > 0.0:
        # Default style transfer LoRA path - can be overridden via params
        default_lora_path = "https://huggingface.co/peteromallet/ad_motion_loras/resolve/main/style_transfer_qwen_edit_2_000011250.safetensors"
        lora_path = params.get("style_lora_path", default_lora_path)

        style_loras.append({
            "path": lora_path,
            "scale": float(style_strength)
        })
        logger.info(f"Added style transfer LoRA: {lora_path} with strength {style_strength}")

    # Add subject LoRA if subject_strength > 0
    if subject_strength > 0.0:
        # Add subject LoRA
        subject_lora_path = "https://huggingface.co/peteromallet/mystery_models/resolve/main/in_subject_qwen_edit_2_000006750.safetensors"
        style_loras.append({
            "path": subject_lora_path,
            "scale": float(subject_strength)
        })
        logger.info(f"Added subject LoRA: {subject_lora_path} with strength {subject_strength}")

    # Add scene LoRA if scene_strength > 0
    if scene_strength > 0.0:
        # Add scene LoRA for "in the same scene" transformations
        scene_lora_path = "https://huggingface.co/peteromallet/ad_motion_loras/resolve/main/in_scene_different_perspective_000019000.safetensors"
        style_loras.append({
            "path": scene_lora_path,
            "scale": float(scene_strength)
        })
        logger.info(f"Added scene LoRA: {scene_lora_path} with strength {scene_strength}")

    # Add any additional LoRAs from params
    additional_loras = params.get("loras", [])
    if additional_loras:
        for lora in additional_loras:
            if isinstance(lora, dict) and "path" in lora and "scale" in lora:
                style_loras.append({
                    "path": lora["path"],
                    "scale": float(lora["scale"])
                })
        logger.info(f"Added {len(additional_loras)} additional LoRAs")

    # Build parameters based on API format
    if use_images_array:
        # 2511/2509 APIs use "images" array format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "images": [reference_image] if reference_image else [],
            "output_format": params.get("output_format", "jpeg"),
            "prompt": modified_prompt,
            "seed": params.get("seed", -1),
            "loras": style_loras
        }
    else:
        # Default edit-lora API uses "image" string format
        wavespeed_params = {
            "enable_base64_output": params.get("enable_base64_output", False),
            "enable_sync_mode": params.get("enable_sync_mode", False),
            "output_format": params.get("output_format", "jpeg"),
            "prompt": modified_prompt,
            "seed": params.get("seed", -1),
            "image": reference_image,
            "model_id": params.get("model_id", "wavespeed-ai/qwen-image/edit-lora"),
            "loras": style_loras
        }

    # Add size parameter if resolution is provided
    resolution = params.get("resolution", "")
    if resolution:
        normalized_size = normalize_resolution(resolution)
        if normalized_size:
            wavespeed_params["size"] = normalized_size
            logger.info(f"Using resolution/size: {normalized_size}")

    result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

    # Process external URL with automatic screenshot extraction for videos
    result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result


async def handle_wan_2_2_t2i(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle wan_2_2_t2i task type via Wavespeed API."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "wan_2_2_t2i")

    # Wavespeed AI WAN 2.2 Text-to-Image with LoRA
    endpoint_path = "wavespeed-ai/wan-2.2/text-to-image-lora"
    logger.info(f"Calling Wavespeed API endpoint: {endpoint_path}")

    # Extract orchestrator details or use top-level params
    orchestrator_details = params.get("orchestrator_details", {})
    effective_params = {**params, **orchestrator_details}
    effective_params.setdefault("character_image_url", "")
    effective_params.setdefault("mode", "animate")
    effective_params.setdefault("prompt", "")
    effective_params.setdefault("resolution", "480p")
    effective_params.setdefault("seed", -1)
    effective_params.setdefault("motion_video_url", "")

    # Map parameters to Wavespeed API format
    wavespeed_params = {
        "enable_base64_output": False,
        "enable_sync_mode": False,
        "output_format": "jpeg",
        "prompt": effective_params.get("prompt", ""),
        "seed": effective_params.get("seed", -1),
        "size": effective_params.get("resolution", "256*256").replace("x", "*"),
        "high_noise_loras": [],
        "low_noise_loras": [],
        "loras": []
    }

    # Extract and format LoRAs from additional_loras
    additional_loras = effective_params.get("additional_loras", {})
    if additional_loras:
        for lora_path, scale in additional_loras.items():
            wavespeed_params["loras"].append({
                "path": lora_path,
                "scale": float(scale)
            })
        logger.info(f"Added {len(additional_loras)} LoRAs to request")

    result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

    # Process external URL with automatic screenshot extraction for videos
    result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result


async def handle_animate_character(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle animate_character task type via Wavespeed API."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "animate_character")

    # Wavespeed AI WAN 2.2 Character Animation
    endpoint_path = "wavespeed-ai/wan-2.2/animate"
    logger.info(f"Processing {task_type} task via Wavespeed API endpoint: {endpoint_path}")

    # Extract orchestrator details or use top-level params
    orchestrator_details = params.get("orchestrator_details", {})
    effective_params = {**params, **orchestrator_details}
    effective_params.setdefault("additional_loras", {})
    effective_params.setdefault("input_image_paths_resolved", [])
    effective_params.setdefault("base_prompts_expanded", [])
    effective_params.setdefault("negative_prompts_expanded", [])
    effective_params.setdefault("base_prompt", "")
    effective_params.setdefault("duration", 5)
    effective_params.setdefault("seed_base", -1)

    # Map parameters to Wavespeed API format for character animation
    wavespeed_params = {
        "image": effective_params.get("character_image_url", ""),
        "mode": effective_params.get("mode", "animate"),
        "prompt": effective_params.get("prompt", ""),
        "resolution": effective_params.get("resolution", "480p"),
        "seed": effective_params.get("seed", -1),
        "video": effective_params.get("motion_video_url", "")
    }

    logger.info(f"Character animation params: image={wavespeed_params['image'][:50]}..., "
               f"video={wavespeed_params['video'][:50]}..., "
               f"mode={wavespeed_params['mode']}, "
               f"resolution={wavespeed_params['resolution']}, "
               f"seed={wavespeed_params['seed']}")

    result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

    # Process external URL with automatic screenshot extraction for videos
    result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result


async def handle_wan_2_2_i2v(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle wan_2_2_i2v task type via Wavespeed API."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "wan_2_2_i2v")

    logger.info(f"Processing {task_type} task via Wavespeed API")

    # Extract orchestrator details or use top-level params
    orchestrator_details = params.get("orchestrator_details", {})
    effective_params = {**params, **orchestrator_details}

    # Check if we have LoRAs to determine which endpoint to use
    additional_loras = effective_params.get("additional_loras", {})
    has_loras = bool(additional_loras)

    # Get input images and prompts
    input_images = effective_params.get("input_image_paths_resolved", [])
    base_prompts = effective_params.get("base_prompts_expanded", [])
    negative_prompts = effective_params.get("negative_prompts_expanded", [])

    # Fall back to base_prompt (singular) if base_prompts_expanded is empty or not provided
    if not base_prompts or (len(base_prompts) == 1 and not base_prompts[0]):
        base_prompt_singular = effective_params.get("base_prompt", "")
        if base_prompt_singular:
            base_prompts = [base_prompt_singular]
            logger.info(f"Using base_prompt fallback: '{base_prompt_singular}'")

    # If we have more than 2 images, generate pairwise transitions and join
    if len(input_images) > 2:
        logger.info(f"Processing {len(input_images)} images as pairwise transitions")
        video_segments: list[str] = []
        num_segments = max(0, len(input_images) - 1)

        try:
            # Build per-segment videos for each consecutive image pair
            for i in range(num_segments):
                image_url = input_images[i]
                next_image_url = input_images[i + 1]
                logger.info(f"Processing segment {i+1}/{num_segments}: {image_url} -> {next_image_url}")

                # Per-segment prompts
                prompt = base_prompts[i] if i < len(base_prompts) else (base_prompts[0] if base_prompts else "")
                negative_prompt = negative_prompts[i] if i < len(negative_prompts) else (negative_prompts[0] if negative_prompts else "")

                if has_loras:
                    endpoint_path = "wavespeed-ai/wan-2.2/i2v-480p-lora"
                    wavespeed_params = {
                        "duration": effective_params.get("duration", 5),
                        "high_noise_loras": [],
                        "image": image_url,
                        "last_image": next_image_url,
                        "loras": [],
                        "low_noise_loras": [],
                        "negative_prompt": negative_prompt,
                        "prompt": prompt,
                        "seed": effective_params.get("seed_base", -1) + i
                    }
                    for lora_path, scale in additional_loras.items():
                        wavespeed_params["loras"].append({"path": lora_path, "scale": float(scale)})
                else:
                    endpoint_path = "wavespeed-ai/wan-2.2/i2v-480p"
                    wavespeed_params = {
                        "seed": effective_params.get("seed_base", -1) + i,
                        "image": image_url,
                        "last_image": next_image_url,
                        "prompt": prompt,
                        "duration": effective_params.get("duration", 5),
                        "negative_prompt": negative_prompt,
                    }

                # Call Wavespeed for this segment
                segment_result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

                # Extract video URL directly from Wavespeed result
                video_url = None
                if isinstance(segment_result, dict):
                    if 'output_url' in segment_result:
                        video_url = segment_result['output_url']
                    elif 'url' in segment_result:
                        video_url = segment_result['url']
                    elif 'video_url' in segment_result:
                        video_url = segment_result['video_url']
                    elif 'outputs' in segment_result and segment_result['outputs']:
                        video_url = segment_result['outputs'][0]

                if not video_url:
                    raise Exception(f"No video URL found in segment {i+1} result")

                # Download to temp file
                temp_video_path = await download_video_to_temp(client, video_url)
                if not temp_video_path:
                    raise Exception(f"Failed to download video for segment {i+1}")

                video_segments.append(temp_video_path)
                logger.info(f"Segment {i+1}/{num_segments} ready: {temp_video_path}")

            # Remove last frame from all but the final segment and join
            if len(video_segments) > 1:
                processed_segments: list[str] = []
                for i, video_path in enumerate(video_segments[:-1]):
                    processed_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
                    processed_file.close()
                    if remove_last_frame_from_video(video_path, processed_file.name):
                        processed_segments.append(processed_file.name)
                        logger.info(f"Removed last frame from segment {i+1}")
                    else:
                        processed_segments.append(video_path)
                        logger.warning(f"Could not remove last frame from segment {i+1}; using original")
                processed_segments.append(video_segments[-1])

                final_video = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
                final_video.close()
                if not join_videos(processed_segments, final_video.name):
                    raise Exception("Failed to join video segments")
                logger.info(f"Joined {len(processed_segments)} segments -> {final_video.name}")

                # Upload final video to Supabase with first frame screenshot
                with open(final_video.name, 'rb') as f:
                    file_bytes = f.read()
                screenshot_bytes = extract_first_frame_bytes(file_bytes)
                first_frame_b64 = base64.b64encode(screenshot_bytes).decode('utf-8') if screenshot_bytes else None
                public_url = await upload_to_supabase_storage(
                    client,
                    task_id,
                    file_bytes,
                    filename=f"joined_{task_id}.mp4",
                    first_frame_data=first_frame_b64
                )

                # Cleanup temp files
                for temp_path in video_segments + processed_segments:
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
                try:
                    os.unlink(final_video.name)
                except Exception:
                    pass

                result = {
                    "output_location": public_url,
                    "output_url": public_url,
                    "segments_processed": num_segments,
                    "message": f"Successfully processed {num_segments} transitions into joined video",
                    "_task_completed_by_upload": True
                }
            else:
                # Degenerate case; return the only segment after uploading
                only_path = video_segments[0]
                with open(only_path, 'rb') as f:
                    file_bytes = f.read()
                screenshot_bytes = extract_first_frame_bytes(file_bytes)
                first_frame_b64 = base64.b64encode(screenshot_bytes).decode('utf-8') if screenshot_bytes else None
                public_url = await upload_to_supabase_storage(
                    client,
                    task_id,
                    file_bytes,
                    filename=f"segment_{task_id}_0.mp4",
                    first_frame_data=first_frame_b64
                )
                try:
                    os.unlink(only_path)
                except Exception:
                    pass
                result = {
                    "output_location": public_url,
                    "output_url": public_url,
                    "segments_processed": 1,
                    "message": "Single transition processed",
                    "_task_completed_by_upload": True
                }

        except Exception as e:
            # Clean up any temporary files on error
            for temp_path in list(locals().get('video_segments', [])):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
            raise e

    else:
        # Original logic for 1-2 images
        if has_loras:
            # Use LoRA endpoint: wan-2.2/i2v-480p-lora
            endpoint_path = "wavespeed-ai/wan-2.2/i2v-480p-lora"
            logger.info(f"Using LoRA endpoint: {endpoint_path}")

            # Map parameters to Wavespeed API format for LoRA endpoint
            wavespeed_params = {
                "duration": effective_params.get("duration", 5),
                "high_noise_loras": [],
                "image": input_images[0] if input_images else "",
                "last_image": "",
                "loras": [],
                "low_noise_loras": [],
                "negative_prompt": negative_prompts[0] if negative_prompts else "",
                "prompt": base_prompts[0] if base_prompts else "",
                "seed": effective_params.get("seed_base", -1)
            }

            # Add last_image if we have multiple input images
            if len(input_images) > 1:
                wavespeed_params["last_image"] = input_images[1]
                logger.info(f"Using first image: {input_images[0]}")
                logger.info(f"Using last image: {input_images[1]}")
            else:
                logger.info(f"Using single image: {input_images[0] if input_images else 'None'}")

            # Add LoRAs from additional_loras
            for lora_path, scale in additional_loras.items():
                wavespeed_params["loras"].append({
                    "path": lora_path,
                    "scale": float(scale)
                })
            logger.info(f"Added {len(additional_loras)} LoRAs to i2v request")

        else:
            # Use non-LoRA endpoint: wan-2.2/i2v-480p
            endpoint_path = "wavespeed-ai/wan-2.2/i2v-480p"
            logger.info(f"Using non-LoRA endpoint: {endpoint_path}")

            # Map parameters to Wavespeed API format for non-LoRA endpoint
            wavespeed_params = {
                "seed": effective_params.get("seed_base", -1),
                "image": input_images[0] if input_images else "",
                "prompt": base_prompts[0] if base_prompts else "",
                "duration": effective_params.get("duration", 5),
                "negative_prompt": negative_prompts[0] if negative_prompts else "",
                "model_id": "wavespeed-ai/wan-2.2/i2v-480p"
            }

            # Set last_image to second input image if available
            if len(input_images) > 1:
                wavespeed_params["last_image"] = input_images[1]
                logger.info(f"Using first image: {input_images[0]}")
                logger.info(f"Using last image (second input): {input_images[1]}")
            else:
                logger.info(f"Using single image: {input_images[0] if input_images else 'None'}")

        result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

        # Process external URL with automatic screenshot extraction for videos
        result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result


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

    logger.info(f"Calling inpainting API with composite image and LoRA")

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

    logger.info(f"Calling annotated edit API with composite image and scene annotation LoRAs")

    result = await call_wavespeed_api(endpoint_path, wavespeed_params, client)

    # Process external URL with automatic screenshot extraction for videos
    result = await process_external_url_result(client, task_id, result)

    logger.info(f"Processed {task_type} task via Wavespeed API")
    return result


async def handle_image_upscale(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle image-upscale task type via fal.ai API with resilient request tracking."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "image-upscale")

    logger.info(f"Processing {task_type} task via fal.ai API (resilient mode)")

    # Extract parameters - support both "image_url" and "image" parameter names
    image_url = params.get("image_url") or params.get("image", "")
    # Support both "upscale_factor" and "scale_factor" parameter names
    upscale_factor = params.get("upscale_factor") or params.get("scale_factor", 2)
    # Noise scale for the generation process (default 0.1)
    noise_scale = params.get("noise_scale", 0.1)
    # Output format - normalize "jpeg" to "jpg" for fal API
    output_format = params.get("output_format", "jpg")
    if output_format == "jpeg":
        output_format = "jpg"
    # Optional seed for reproducibility
    seed = params.get("seed")

    if not image_url:
        logger.error(f"Missing image parameter. Available params: {list(params.keys())}")
        raise Exception("image_url or image parameter is required for image-upscale task")

    logger.info(f"Upscaling image: {image_url} with factor: {upscale_factor}, noise_scale: {noise_scale}")

    try:
        endpoint = "fal-ai/seedvr/upscale/image"
        fal_args = {
            "image_url": image_url,
            "upscale_factor": upscale_factor,
            "noise_scale": noise_scale,
            "output_format": output_format,
        }
        if seed is not None:
            fal_args["seed"] = seed
        
        # Check for existing tracking from previous attempt
        existing_tracking = await _get_fal_tracking(task_id)
        if existing_tracking:
            logger.info(f"Found existing request: {existing_tracking.request_id}")
        
        # Create update function
        async def update_metadata(tid: str, data: Dict[str, Any]):
            return await update_task_metadata(tid, data)
        
        result = await call_fal_api_resilient(
            endpoint=endpoint,
            arguments=fal_args,
            client=client,
            task_id=task_id,
            update_task_metadata=update_metadata,
            existing_tracking=existing_tracking,
        )

        # Process external URL with automatic download and upload to Supabase
        result = await process_external_url_result(client, task_id, result)

        logger.info(f"Processed {task_type} task via fal.ai API")
        return result

    except Exception as e:
        logger.error(f"fal.ai API call failed: {e}")
        raise Exception(f"fal.ai upscale failed: {str(e)}")


async def handle_qwen_image(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle qwen_image, qwen_image_2512, and z_image_turbo task types via fal.ai API with resilient tracking."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "qwen_image")

    # Maps task_type to fal.ai endpoint base path
    FALAI_IMAGE_MODELS = {
        "qwen_image": "fal-ai/qwen-image",
        "qwen_image_2512": "fal-ai/qwen-image-2512",
        "z_image_turbo": "fal-ai/z-image/turbo",
    }

    endpoint = FALAI_IMAGE_MODELS[task_type]
    logger.info(f"Processing {task_type} task via fal.ai API (resilient mode)")
    logger.info(f"Using endpoint: {endpoint}")

    # Build LoRA list from params (same format as qwen_image_style)
    # Supports: loras=[{"path": ..., "scale": ...}] or additional_loras={path: scale}
    loras = build_fal_lora_list(params)
    if loras:
        logger.info(f"Including {len(loras)} LoRAs in request")

    # Build arguments - following similar naming to qwen_image_style
    fal_args = {
        "prompt": params.get("prompt", ""),
        "seed": params.get("seed", -1),
    }

    # Add size/resolution if provided
    # fal.ai accepts either preset strings or {"width": x, "height": y}
    resolution = params.get("resolution", "")
    if resolution:
        # Parse resolution string like "512x512" or "1920*1080"
        parts = resolution.replace("*", "x").split("x")
        if len(parts) == 2:
            try:
                width = int(parts[0])
                height = int(parts[1])
                fal_args["image_size"] = {"width": width, "height": height}
                logger.info(f"Using resolution: {width}x{height}")
            except ValueError:
                logger.warning(f"Could not parse resolution '{resolution}', using default")

    # Add negative prompt if provided
    negative_prompt = params.get("negative_prompt", "")
    if negative_prompt:
        fal_args["negative_prompt"] = negative_prompt

    # Add LoRAs to args if provided
    if loras:
        fal_args["loras"] = loras

    try:
        # Check for existing tracking from previous attempt
        existing_tracking = await _get_fal_tracking(task_id)
        if existing_tracking:
            logger.info(f"Found existing request: {existing_tracking.request_id}")
        
        # Create update function
        async def update_metadata(tid: str, data: Dict[str, Any]):
            return await update_task_metadata(tid, data)
        
        result = await call_fal_api_resilient(
            endpoint=endpoint,
            arguments=fal_args,
            client=client,
            task_id=task_id,
            update_task_metadata=update_metadata,
            existing_tracking=existing_tracking,
        )
        result = await process_external_url_result(client, task_id, result)
        logger.info(f"Processed {task_type} task via fal.ai API")
        return result
    except Exception as e:
        logger.error(f"fal.ai API call failed: {e}")
        raise Exception(f"fal.ai {task_type} failed: {str(e)}")


async def handle_z_image_turbo_i2i(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle z_image_turbo_i2i task type via fal.ai API with resilient tracking."""
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "z_image_turbo_i2i")

    logger.info(f"Processing {task_type} task via fal.ai API (resilient mode)")

    # Build LoRA list from params
    loras = build_fal_lora_list(params)

    # Select endpoint based on whether LoRAs are provided
    if loras:
        endpoint = "fal-ai/z-image/turbo/image-to-image/lora"
        logger.info(f"Using LoRA endpoint with {len(loras)} LoRAs")
    else:
        endpoint = "fal-ai/z-image/turbo/image-to-image"
        logger.info(f"Using standard endpoint (no LoRAs)")

    # Get required image_url
    image_url = params.get("image_url") or params.get("image")
    if not image_url:
        raise Exception("image_url or image parameter is required for z_image_turbo_i2i task")

    # Determine output image_size
    # If explicit image_size is provided (not "auto"), use it directly
    # Otherwise, fetch input dimensions and scale to target megapixels
    explicit_image_size = params.get("image_size")
    if explicit_image_size and explicit_image_size != "auto":
        # User provided explicit size - use as-is
        image_size = explicit_image_size
        logger.info(f"Using explicit image_size: {image_size}")
    else:
        # Auto-scale to target megapixels (default ~1MP = 1024x1024 equivalent)
        target_mp = params.get("target_megapixels", 1.0)
        dimensions = await get_image_dimensions(client, image_url)

        if dimensions:
            width, height = dimensions
            image_size = scale_dimensions_to_megapixels(width, height, target_megapixels=target_mp)
            logger.info(f"Auto-scaled to {image_size['width']}x{image_size['height']} (~{target_mp}MP)")
        else:
            # Fallback to "auto" if we couldn't get dimensions
            image_size = "auto"
            logger.warning(f"Could not get input dimensions, falling back to image_size='auto'")

    # Build arguments
    fal_args = {
        "prompt": params.get("prompt", ""),
        "image_url": image_url,
        "strength": params.get("strength", 0.6),
        "image_size": image_size,
        "num_inference_steps": params.get("num_inference_steps", 8),
        "num_images": params.get("num_images", 1),
        "enable_safety_checker": params.get("enable_safety_checker", True),
        "output_format": params.get("output_format", "png"),
        "acceleration": params.get("acceleration", "none" if loras else "high"),
    }

    # Add LoRAs for LoRA endpoint calls.
    if loras:
        fal_args["loras"] = loras

    # Add optional seed if provided
    seed = params.get("seed")
    if seed is not None:
        fal_args["seed"] = seed

    # Add negative prompt if provided
    negative_prompt = params.get("negative_prompt", "")
    if negative_prompt:
        fal_args["negative_prompt"] = negative_prompt

    logger.info(f"Z-Image Turbo i2i: image={image_url}, strength={fal_args['strength']}, steps={fal_args['num_inference_steps']}, size={image_size}")

    try:
        # Check for existing tracking from previous attempt
        existing_tracking = await _get_fal_tracking(task_id)
        if existing_tracking:
            logger.info(f"Found existing request: {existing_tracking.request_id}")
        
        # Create update function
        async def update_metadata(tid: str, data: Dict[str, Any]):
            return await update_task_metadata(tid, data)
        
        result = await call_fal_api_resilient(
            endpoint=endpoint,
            arguments=fal_args,
            client=client,
            task_id=task_id,
            update_task_metadata=update_metadata,
            existing_tracking=existing_tracking,
        )
        result = await process_external_url_result(client, task_id, result)
        logger.info(f"Processed {task_type} task via fal.ai API")
        return result
    except Exception as e:
        logger.error(f"fal.ai API call failed: {e}")
        raise Exception(f"fal.ai {task_type} failed: {str(e)}")


async def handle_video_enhance(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient
) -> Dict[str, Any]:
    """Handle video_enhance task type via fal.ai API with resilient request tracking.
    
    Supports two enhancement operations that can run independently or chained:
    - Interpolation: Frame interpolation using Google FILM (fal-ai/film/video)
    - Upscale: Video upscaling using FlashVSR (fal-ai/flashvsr/upscale/video)
    
    When both are enabled, interpolation runs first, then upscale on the result.
    
    This handler uses resilient fal.ai calls that:
    - Track request IDs in task metadata for recovery
    - Check for existing completed jobs before resubmitting
    - Use extended retries for 503 "Runner disconnected" errors
    
    Parameters:
        Common:
            - video_url: URL of the video to enhance (required)
            - enable_interpolation: Enable frame interpolation (default: false)
            - enable_upscale: Enable video upscaling (default: false)
        
        Interpolation (nested in "interpolation" object):
            - num_frames: 1-4, frames added between each pair (default: 1)
            - use_calculated_fps: Maintain same playback speed (default: true)
            - video_quality: "low"/"medium"/"high"/"maximum" (default: "high")
            - fps: Output FPS if use_calculated_fps is false (default: 8)
            - use_scene_detection: Split video into scenes before interpolation (optional)
            - loop: Loop final frame back to first (optional)
            - video_write_mode: "fast"/"balanced"/"small" (default: "balanced")
        
        Upscale (nested in "upscale" object):
            - upscale_factor: 1.5-4x scaling factor (default: 2)
            - color_fix: Apply color correction (default: true)
            - output_quality: "low"/"medium"/"high"/"maximum" (default: "high")
            - acceleration: "regular"/"high"/"full" - faster = longer videos supported (default: "regular")
            - quality: 0-100 tile blending quality (default: 70)
            - preserve_audio: Copy original audio to output (optional)
            - output_format: "X264 (.mp4)"/"VP9 (.webm)"/"PRORES4444 (.mov)"/"GIF (.gif)"
            - output_write_mode: "fast"/"balanced"/"small" (default: "balanced")
            - seed: Random seed for reproducibility (optional)
    """
    task_id = task.get("task_id") or task.get("id")
    task_type = task.get("task_type", "video_enhance")

    logger.info(f"Processing {task_type} task via fal.ai API (resilient mode)")

    # Get required video_url
    video_url = params.get("video_url") or params.get("video")
    if not video_url:
        raise Exception("video_url or video parameter is required for video_enhance task")

    # Check which operations are enabled
    enable_interpolation = params.get("enable_interpolation", False)
    enable_upscale = params.get("enable_upscale", False)

    if not enable_interpolation and not enable_upscale:
        raise Exception("At least one of enable_interpolation or enable_upscale must be true")

    # Get nested parameter objects
    interpolation_params = params.get("interpolation", {})
    upscale_params = params.get("upscale", {})

    current_video_url = video_url
    result = None
    operations_performed = []
    
    # Configuration for video processing - longer timeouts since videos take time
    video_config = FalRetryConfig(
        poll_timeout_sec=900.0,  # 15 minutes for video processing
        poll_progress_log_interval_sec=30.0,  # Log every 30s
        fetch_max_attempts=25,  # More attempts for result fetch
        fetch_base_delay_sec=5.0,
        fetch_max_delay_sec=60.0,
    )

    try:
        # Step 1: Interpolation (if enabled) - runs first to add frames
        if enable_interpolation:
            endpoint = "fal-ai/film/video"
            logger.info(f"Step 1: Running FILM interpolation on {current_video_url}")

            fal_args = {
                "video_url": current_video_url,
                "num_frames": interpolation_params.get("num_frames", 1),
                "use_calculated_fps": interpolation_params.get("use_calculated_fps", True),
                "video_quality": interpolation_params.get("video_quality", "high"),
            }

            # Optional FILM parameters
            if "fps" in interpolation_params:
                fal_args["fps"] = interpolation_params["fps"]
            if "use_scene_detection" in interpolation_params:
                fal_args["use_scene_detection"] = interpolation_params["use_scene_detection"]
            if "loop" in interpolation_params:
                fal_args["loop"] = interpolation_params["loop"]
            if "video_write_mode" in interpolation_params:
                fal_args["video_write_mode"] = interpolation_params["video_write_mode"]

            logger.info(f"FILM interpolation: num_frames={fal_args['num_frames']}, "
                       f"use_calculated_fps={fal_args['use_calculated_fps']}, "
                       f"video_quality={fal_args['video_quality']}")

            # Check for existing tracking from previous attempt
            existing_tracking = await _get_fal_tracking(task_id, step_key="interpolation")
            if existing_tracking:
                logger.info(f"Found existing interpolation request: {existing_tracking.request_id}")
            
            # Create step-specific update function
            async def update_interpolation_metadata(tid: str, data: Dict[str, Any]):
                return await _update_fal_tracking(tid, data, step_key="interpolation")
            
            result = await call_fal_api_resilient(
                endpoint=endpoint,
                arguments=fal_args,
                client=client,
                task_id=task_id,
                update_task_metadata=update_interpolation_metadata,
                existing_tracking=existing_tracking,
                config=video_config,
            )
            operations_performed.append("interpolation")

            # If upscale is also enabled, use the interpolated video as input
            if enable_upscale and result.get("output_url"):
                current_video_url = result["output_url"]
                logger.info(f"Interpolation complete, chaining to upscale with: {current_video_url}")
                
                # Store the intermediate URL in metadata for recovery
                await update_task_metadata(task_id, {
                    'interpolation_output_url': current_video_url,
                    'interpolation_completed_at': asyncio.get_event_loop().time(),
                })

        # Step 2: Upscale (if enabled) - runs on original or interpolated video
        if enable_upscale:
            endpoint = "fal-ai/flashvsr/upscale/video"
            
            # Check if we have an interpolation result URL from a previous run
            if enable_interpolation and not result:
                # Try to recover interpolation output from metadata
                metadata = await get_task_metadata(task_id)
                if metadata and metadata.get('interpolation_output_url'):
                    current_video_url = metadata['interpolation_output_url']
                    logger.info(f"Recovered interpolation output URL from metadata: {current_video_url}")
                    operations_performed.append("interpolation")  # Mark as done
            
            logger.info(f"Step 2: Running FlashVSR upscale on {current_video_url}")

            fal_args = {
                "video_url": current_video_url,
                "upscale_factor": upscale_params.get("upscale_factor", 2),
                "color_fix": upscale_params.get("color_fix", True),
                "output_quality": upscale_params.get("output_quality", "high"),
            }

            # Optional FlashVSR parameters
            if "acceleration" in upscale_params:
                fal_args["acceleration"] = upscale_params["acceleration"]  # regular/high/full
            if "quality" in upscale_params:
                fal_args["quality"] = upscale_params["quality"]  # 0-100 tile blending
            if "preserve_audio" in upscale_params:
                fal_args["preserve_audio"] = upscale_params["preserve_audio"]
            if "output_format" in upscale_params:
                fal_args["output_format"] = upscale_params["output_format"]
            if "output_write_mode" in upscale_params:
                fal_args["output_write_mode"] = upscale_params["output_write_mode"]
            if "seed" in upscale_params:
                fal_args["seed"] = upscale_params["seed"]

            logger.info(f"FlashVSR upscale: factor={fal_args['upscale_factor']}, "
                       f"color_fix={fal_args['color_fix']}, output_quality={fal_args['output_quality']}")

            # Check for existing tracking from previous attempt
            existing_tracking = await _get_fal_tracking(task_id, step_key="upscale")
            if existing_tracking:
                logger.info(f"Found existing upscale request: {existing_tracking.request_id}")
            
            # Create step-specific update function
            async def update_upscale_metadata(tid: str, data: Dict[str, Any]):
                return await _update_fal_tracking(tid, data, step_key="upscale")
            
            result = await call_fal_api_resilient(
                endpoint=endpoint,
                arguments=fal_args,
                client=client,
                task_id=task_id,
                update_task_metadata=update_upscale_metadata,
                existing_tracking=existing_tracking,
                config=video_config,
            )
            operations_performed.append("upscale")

        # Process final result - download and upload to Supabase
        result = await process_external_url_result(client, task_id, result)
        result["operations_performed"] = operations_performed
        
        logger.info(f"Processed {task_type} task via fal.ai API: {', '.join(operations_performed)}")
        return result

    except Exception as e:
        logger.error(f"fal.ai API call failed during {operations_performed or ['setup']}: {e}")
        raise Exception(f"fal.ai {task_type} failed during {operations_performed or ['setup']}: {str(e)}")


# Task handler registry mapping task types to handler functions
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
}

# List of supported task types for error messages
SUPPORTED_TASK_TYPES = list(TASK_HANDLERS.keys())
