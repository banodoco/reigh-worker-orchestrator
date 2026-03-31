"""fal.ai-backed task handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

import httpx

from api_orchestrator.fal_utils import FalRetryConfig, build_fal_lora_list, call_fal_api_resilient
from api_orchestrator.image_utils import get_image_dimensions, scale_dimensions_to_megapixels
from api_orchestrator.storage_utils import process_external_url_result
from api_orchestrator.task_utils import get_task_metadata, update_task_metadata

from .common import get_fal_tracking, update_fal_tracking

logger = logging.getLogger(__name__)

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
        existing_tracking = await get_fal_tracking(task_id)
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
        existing_tracking = await get_fal_tracking(task_id)
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
        logger.info("Using standard endpoint (no LoRAs)")

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
            logger.warning("Could not get input dimensions, falling back to image_size='auto'")

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
        existing_tracking = await get_fal_tracking(task_id)
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
            existing_tracking = await get_fal_tracking(task_id, step_key="interpolation")
            if existing_tracking:
                logger.info(f"Found existing interpolation request: {existing_tracking.request_id}")
            
            # Create step-specific update function
            async def update_interpolation_metadata(tid: str, data: Dict[str, Any]):
                return await update_fal_tracking(tid, data, step_key="interpolation")
            
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
            existing_tracking = await get_fal_tracking(task_id, step_key="upscale")
            if existing_tracking:
                logger.info(f"Found existing upscale request: {existing_tracking.request_id}")
            
            # Create step-specific update function
            async def update_upscale_metadata(tid: str, data: Dict[str, Any]):
                return await update_fal_tracking(tid, data, step_key="upscale")
            
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


__all__ = [
    "handle_image_upscale",
    "handle_qwen_image",
    "handle_z_image_turbo_i2i",
    "handle_video_enhance",
]
