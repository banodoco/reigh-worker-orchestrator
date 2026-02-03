"""Image dimension and manipulation utilities for the API orchestrator."""

import logging
from io import BytesIO
from typing import Optional

import httpx
from PIL import Image

logger = logging.getLogger(__name__)


def normalize_resolution(resolution: str, min_dimension: int = 512, max_dimension: int = 1200) -> Optional[str]:
    """
    Normalize a resolution string to fit within min/max dimension constraints.

    Args:
        resolution: Resolution string like "400x225" or "1920*1080"
        min_dimension: Minimum size for the shortest side (default 512)
        max_dimension: Maximum size for the longest side (default 1200)

    Returns:
        Normalized resolution string like "512*288" or None if parsing failed
    """
    if not resolution:
        return None

    parts = resolution.replace("*", "x").split("x")
    if len(parts) != 2:
        logger.warning(f"Invalid resolution format '{resolution}', expected WIDTHxHEIGHT")
        return None

    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError:
        logger.warning(f"Could not parse resolution '{resolution}' as integers")
        return None

    original_width, original_height = width, height

    # First, scale UP if below minimum (use shortest side)
    shortest_side = min(width, height)
    if shortest_side < min_dimension:
        ratio = min_dimension / shortest_side
        width = int(width * ratio)
        height = int(height * ratio)
        logger.info(f"Scaled up resolution from {original_width}x{original_height} to {width}x{height} (min {min_dimension}px)")

    # Then, scale DOWN if above maximum (use longest side)
    longest_side = max(width, height)
    if longest_side > max_dimension:
        ratio = max_dimension / longest_side
        width = int(width * ratio)
        height = int(height * ratio)
        logger.info(f"Capped resolution to {width}x{height} (max {max_dimension}px)")

    return f"{width}*{height}"


async def get_image_dimensions(client: httpx.AsyncClient, image_url: str) -> tuple[int, int] | None:
    """
    Fetch image dimensions from a URL by downloading just enough to read the header.

    Args:
        client: httpx client for downloading
        image_url: URL of the image

    Returns:
        Tuple of (width, height) or None if failed
    """
    try:
        # Stream just enough bytes to get image header (usually < 64KB is enough)
        async with client.stream("GET", image_url, timeout=30.0) as response:
            response.raise_for_status()
            chunks = []
            bytes_read = 0
            max_bytes = 64 * 1024  # 64KB should be enough for any image header

            async for chunk in response.aiter_bytes(chunk_size=8192):
                chunks.append(chunk)
                bytes_read += len(chunk)
                if bytes_read >= max_bytes:
                    break

            data = b"".join(chunks)
            img = Image.open(BytesIO(data))
            width, height = img.size
            logger.info(f"Got image dimensions from URL: {width}x{height}")
            return width, height

    except Exception as e:
        logger.warning(f"Failed to get image dimensions from {image_url}: {e}")
        return None


def scale_dimensions_to_megapixels(
    width: int,
    height: int,
    target_megapixels: float = 1.0,
    round_to: int = 8
) -> dict[str, int]:
    """
    Scale dimensions to approximately target megapixels while preserving aspect ratio.

    Args:
        width: Original width
        height: Original height
        target_megapixels: Target total pixels in millions (default 1.0 = 1024x1024)
        round_to: Round dimensions to nearest multiple of this (default 8 for model compatibility)

    Returns:
        Dict with {"width": scaled_width, "height": scaled_height}
    """
    import math

    current_pixels = width * height
    target_pixels = target_megapixels * 1_000_000

    # Calculate scale factor
    scale = math.sqrt(target_pixels / current_pixels)

    # Apply scale and round to nearest multiple
    new_width = round(width * scale / round_to) * round_to
    new_height = round(height * scale / round_to) * round_to

    # Ensure minimum dimensions
    new_width = max(new_width, round_to)
    new_height = max(new_height, round_to)

    actual_pixels = new_width * new_height
    logger.info(f"Scaled {width}x{height} ({current_pixels/1e6:.2f}MP) -> {new_width}x{new_height} ({actual_pixels/1e6:.2f}MP)")

    return {"width": new_width, "height": new_height}


async def create_masked_composite_image(
    client: httpx.AsyncClient,
    task_id: str,
    image_url: str,
    mask_url: str,
    filename_prefix: str = "composite"
) -> str:
    """
    Download image and mask, create green-overlay composite, upload to Supabase.
    Returns the public URL of the uploaded composite image.

    Args:
        client: httpx client for downloading
        task_id: task ID for upload naming
        image_url: URL of the original image
        mask_url: URL of the mask (white = areas to edit)
        filename_prefix: prefix for uploaded filename

    Returns:
        Public URL of the uploaded composite image
    """
    from .storage_utils import upload_to_supabase_storage_only

    try:
        # Download the original image
        logger.info("Downloading original image...")
        image_response = await client.get(image_url)
        image_response.raise_for_status()
        image = Image.open(BytesIO(image_response.content)).convert("RGB")
        logger.info(f"Image downloaded: {image.size[0]}x{image.size[1]}")

        # Download the mask
        logger.info("Downloading mask...")
        mask_response = await client.get(mask_url)
        mask_response.raise_for_status()
        mask = Image.open(BytesIO(mask_response.content)).convert("L")  # Convert to grayscale
        logger.info(f"Mask downloaded: {mask.size[0]}x{mask.size[1]}")

        # Resize image to a reasonable size if it's too large
        # Keep max 1200px on the widest side while maintaining aspect ratio
        max_dimension = 1200
        if image.size[0] > max_dimension or image.size[1] > max_dimension:
            # Calculate new size maintaining aspect ratio
            ratio = min(max_dimension / image.size[0], max_dimension / image.size[1])
            new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
            logger.info(f"Resizing image from {image.size[0]}x{image.size[1]} to {new_size[0]}x{new_size[1]}")
            image = image.resize(new_size, Image.Resampling.LANCZOS)

        # Resize mask to match image dimensions if needed
        if mask.size != image.size:
            logger.info(f"Resizing mask from {mask.size[0]}x{mask.size[1]} to {image.size[0]}x{image.size[1]}")
            mask = mask.resize(image.size, Image.Resampling.LANCZOS)
            # Apply binary threshold to eliminate gray values from interpolation
            # This ensures crisp black/white boundaries and prevents graininess
            mask = mask.point(lambda x: 255 if x > 127 else 0)
            logger.info("Applied binary threshold to mask after resizing")

        # Create a pure green overlay where the mask is white
        # Create a green image of the same size
        green_overlay = Image.new("RGB", image.size, (0, 255, 0))

        # Composite: where mask is white (255), use green; where black (0), use original image
        # Image.composite uses the mask as an alpha channel
        composite = Image.composite(green_overlay, image, mask)

        logger.info("Created composite image with green mask overlay")

        # Upload composite image to Supabase storage
        # Use JPEG format with quality setting to reduce file size
        composite_bytes = BytesIO()
        composite.save(composite_bytes, format='JPEG', quality=95, optimize=True)
        composite_bytes.seek(0)

        file_size_mb = len(composite_bytes.getvalue()) / (1024 * 1024)
        logger.info(f"Composite image size: {file_size_mb:.2f}MB")

        # Upload composite WITHOUT marking task complete (we need to call Wavespeed API first)
        composite_url = await upload_to_supabase_storage_only(
            client,
            task_id,
            composite_bytes.getvalue(),
            filename=f"{filename_prefix}_{task_id}.jpg"
        )
        logger.info(f"Uploaded composite image to: {composite_url}")

        return composite_url

    except Exception as e:
        logger.error(f"Failed to process images for masked composite: {e}")
        raise Exception(f"Image processing failed: {str(e)}")
