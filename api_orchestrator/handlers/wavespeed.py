"""Wavespeed-backed task handlers."""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from typing import Any, Dict

import httpx

from api_orchestrator.image_utils import normalize_resolution
from api_orchestrator.storage_utils import process_external_url_result, upload_to_supabase_storage
from api_orchestrator.video_utils import (
    download_video_to_temp,
    extract_first_frame_bytes,
    join_videos,
    remove_last_frame_from_video,
)
from api_orchestrator.wavespeed_utils import call_wavespeed_api


logger = logging.getLogger(__name__)

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
    effective_params.setdefault("additional_loras", {})
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
    effective_params.setdefault("character_image_url", "")
    effective_params.setdefault("mode", "animate")
    effective_params.setdefault("prompt", "")
    effective_params.setdefault("resolution", "480p")
    effective_params.setdefault("seed", -1)
    effective_params.setdefault("motion_video_url", "")

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
    effective_params.setdefault("additional_loras", {})
    effective_params.setdefault("input_image_paths_resolved", [])
    effective_params.setdefault("base_prompts_expanded", [])
    effective_params.setdefault("negative_prompts_expanded", [])
    effective_params.setdefault("base_prompt", "")
    effective_params.setdefault("duration", 5)
    effective_params.setdefault("seed_base", -1)

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


__all__ = [
    "handle_qwen_image_edit",
    "handle_qwen_image_style",
    "handle_wan_2_2_t2i",
    "handle_animate_character",
    "handle_wan_2_2_i2v",
]
