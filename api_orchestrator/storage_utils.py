"""
Storage utilities for the API orchestrator.
Handles file downloads, uploads, and storage operations with Supabase.
"""

import os
import logging
import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


async def download_url_content(client: httpx.AsyncClient, url: str) -> bytes:
    """Download content from a URL and return as bytes."""
    try:
        logger.info(f"Downloading content from: {url}")
        response = await client.get(url, timeout=300)
        response.raise_for_status()
        
        content = response.content
        logger.info(f"Downloaded {len(content)} bytes from {url}")
        return content
        
    except Exception as e:
        logger.error(f"Failed to download from {url}: {e}")
        raise


async def upload_to_supabase_storage_only(client: httpx.AsyncClient, task_id: str, file_data: bytes, filename: str) -> str:
    """
    Upload file data to Supabase storage WITHOUT marking task complete.
    Returns the public URL of the uploaded file.
    Used for intermediate files that need to be uploaded but task is not yet complete.
    """
    try:
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        
        if not supabase_url:
            raise Exception("SUPABASE_URL not configured")
            
        auth_token = os.getenv('SUPABASE_ACCESS_TOKEN') or os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        
        # Sanitize filename
        safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-").strip()
        if not safe_filename:
            safe_filename = f"task_{task_id}.bin"
        
        # Guess content type from filename
        content_type = mimetypes.guess_type(safe_filename)[0] or 'application/octet-stream'
        
        file_size_mb = len(file_data) / (1024 * 1024)
        logger.info(f"Uploading {len(file_data)} bytes ({file_size_mb:.2f}MB) to Supabase storage (no task completion) as {safe_filename}")
        
        # Construct storage path
        storage_path = f"task_outputs/{safe_filename}"
        upload_url = f"{supabase_url}/storage/v1/object/image_uploads/{storage_path}"
        
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": content_type,
            "x-upsert": "true"
        }
        
        # Upload directly to storage
        logger.info(f"Uploading directly to storage path: {storage_path}")
        response = await client.post(upload_url, content=file_data, headers=headers, timeout=120)
        
        if response.status_code not in (200, 201):
            error_text = response.text
            logger.error(f"Storage upload failed with status {response.status_code}: {error_text}")
            response.raise_for_status()
        
        # Construct public URL
        public_url = f"{supabase_url}/storage/v1/object/public/image_uploads/{storage_path}"
        
        logger.info(f"Upload successful (no task completion): {public_url}")
        return public_url
        
    except Exception as e:
        logger.error(f"Failed to upload to Supabase storage: {e}")
        raise


async def upload_to_supabase_storage(client: httpx.AsyncClient, task_id: str, file_data: bytes, filename: str, first_frame_data: str = None) -> str:
    """
    Upload file data to Supabase storage.
    Uses optimized path based on file size:
    - Files <2MB: Direct base64 upload to complete_task (fewer round trips)
    - Files >=2MB: Pre-signed URL upload (no base64 overhead, no Edge Function limits)
    Returns the public URL of the uploaded file.
    """
    try:
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        complete_task_url = f"{supabase_url}/functions/v1/complete_task"
        
        if not supabase_url:
            raise Exception("SUPABASE_URL not configured")
            
        auth_token = os.getenv('SUPABASE_ACCESS_TOKEN') or os.getenv('SUPABASE_SERVICE_ROLE_KEY')
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}"
        }
        
        # Sanitize filename
        safe_filename = "".join(c for c in filename if c.isalnum() or c in "._-").strip()
        if not safe_filename:
            # Fallback filename based on task_id and content type
            parsed_url = urlparse(filename) if filename else None
            ext = Path(parsed_url.path).suffix if parsed_url else ".bin"
            safe_filename = f"task_{task_id}{ext}"
        
        # Guess content type from filename
        content_type = mimetypes.guess_type(safe_filename)[0] or 'application/octet-stream'
        
        file_size_mb = len(file_data) / (1024 * 1024)
        logger.info(f"Uploading {len(file_data)} bytes ({file_size_mb:.2f}MB) to Supabase storage as {safe_filename} (type: {content_type})")
        
        # Use optimized path based on file size
        # Small files (<2MB): Use direct base64 upload (fewer round trips, faster for small files)
        # Large files (>=2MB): Use pre-signed URLs (no base64 overhead, no Edge Function limits)
        SIZE_THRESHOLD_MB = 2.0
        
        if file_size_mb < SIZE_THRESHOLD_MB:
            # OLD PATH: Direct base64 upload to complete_task (optimal for small files)
            logger.info(f"Using direct upload path for {file_size_mb:.2f}MB file (< {SIZE_THRESHOLD_MB}MB threshold)")
            return await _upload_direct_base64(client, task_id, file_data, safe_filename, first_frame_data, headers, complete_task_url)
        else:
            # NEW PATH: Pre-signed URL upload (optimal for large files)
            logger.info(f"Using pre-signed URL path for {file_size_mb:.2f}MB file (>= {SIZE_THRESHOLD_MB}MB threshold)")
            generate_upload_url = f"{supabase_url}/functions/v1/generate-upload-url"
            return await _upload_presigned_url(client, task_id, file_data, safe_filename, content_type, first_frame_data, headers, generate_upload_url, complete_task_url)
            
    except Exception as e:
        logger.error(f"Failed to upload to Supabase storage: {e}")
        raise


async def _upload_direct_base64(client: httpx.AsyncClient, task_id: str, file_data: bytes, filename: str, first_frame_data: str, headers: Dict[str, str], complete_task_url: str) -> str:
    """
    Upload file using direct base64 encoding to complete_task Edge Function.
    Optimal for files <2MB (fewer round trips despite base64 overhead).
    """
    # Encode file data as base64
    file_base64 = base64.b64encode(file_data).decode('utf-8')
    
    payload = {
        "task_id": task_id,
        "file_data": file_base64,
        "filename": filename
    }
    
    # Include first frame data if provided
    if first_frame_data:
        payload["first_frame_data"] = first_frame_data
        # Generate screenshot filename based on main filename
        base_name = Path(filename).stem
        payload["first_frame_filename"] = f"{base_name}_screenshot.png"
    
    logger.info(f"Direct upload: {len(file_data)} bytes -> {len(file_base64)} chars base64")
    
    response = await client.post(complete_task_url, headers=headers, json=payload, timeout=120)
    
    if response.status_code != 200:
        response_text = response.text
        logger.error(f"Direct upload failed with status {response.status_code}: {response_text}")
        
    response.raise_for_status()
    
    result = response.json()
    
    # Extract the public URL from the response
    public_url = result.get("public_url") or result.get("output_location")
    
    if not public_url:
        raise Exception(f"No public URL returned from upload: {result}")
        
    logger.info(f"Direct upload successful: {public_url}")
    return public_url


async def _upload_presigned_url(client: httpx.AsyncClient, task_id: str, file_data: bytes, filename: str, content_type: str, first_frame_data: str, headers: Dict[str, str], generate_upload_url: str, complete_task_url: str) -> str:
    """
    Upload file using pre-signed URLs.
    Optimal for files >=2MB (no base64 overhead, bypasses Edge Function limits).
    """
    # Step 1: Generate pre-signed upload URL
    generate_payload = {
        "task_id": task_id,
        "filename": filename,
        "content_type": content_type,
        "generate_thumbnail_url": bool(first_frame_data)  # Request thumbnail URL if we have screenshot
    }
    
    logger.info(f"Requesting pre-signed upload URL for task {task_id}")
    response = await client.post(generate_upload_url, headers=headers, json=generate_payload, timeout=30)
    response.raise_for_status()
    
    upload_urls = response.json()
    main_upload_url = upload_urls.get("upload_url")
    storage_path = upload_urls.get("storage_path")
    thumbnail_upload_url = upload_urls.get("thumbnail_upload_url")
    thumbnail_storage_path = upload_urls.get("thumbnail_storage_path")
    
    if not main_upload_url or not storage_path:
        raise Exception(f"Invalid response from generate-upload-url: {upload_urls}")
    
    logger.info(f"Generated upload URL for storage path: {storage_path}")
    
    # Step 2: Upload main file directly to storage (binary upload, no base64!)
    upload_headers = {
        "Content-Type": content_type,
        "x-upsert": "true"  # Allow overwriting if file exists
    }
    
    logger.info(f"Uploading {len(file_data)} bytes directly to storage...")
    upload_response = await client.put(main_upload_url, content=file_data, headers=upload_headers, timeout=120)
    
    if upload_response.status_code not in (200, 201):
        error_text = upload_response.text
        logger.error(f"Direct storage upload failed with status {upload_response.status_code}: {error_text}")
        upload_response.raise_for_status()
    
    logger.info(f"Successfully uploaded main file to storage")
    
    # Step 3: Upload thumbnail if provided
    if first_frame_data and thumbnail_upload_url and thumbnail_storage_path:
        try:
            # Decode base64 screenshot data
            screenshot_bytes = base64.b64decode(first_frame_data)
            
            thumbnail_headers = {
                "Content-Type": "image/png",
                "x-upsert": "true"
            }
            
            logger.info(f"Uploading {len(screenshot_bytes)} byte thumbnail to storage...")
            thumb_response = await client.put(thumbnail_upload_url, content=screenshot_bytes, headers=thumbnail_headers, timeout=60)
            
            if thumb_response.status_code not in (200, 201):
                logger.warning(f"Thumbnail upload failed with status {thumb_response.status_code}: {thumb_response.text}")
            else:
                logger.info(f"Successfully uploaded thumbnail to {thumbnail_storage_path}")
        except Exception as thumb_error:
            # Don't fail the whole upload if thumbnail fails
            logger.warning(f"Thumbnail upload failed (non-fatal): {thumb_error}")
    
    # Step 4: Mark task complete with storage path
    complete_payload = {
        "task_id": task_id,
        "storage_path": storage_path
    }
    
    # Include thumbnail path if uploaded
    if first_frame_data and thumbnail_storage_path:
        complete_payload["thumbnail_storage_path"] = thumbnail_storage_path
    
    logger.info(f"Marking task {task_id} complete with storage_path: {storage_path}")
    complete_response = await client.post(complete_task_url, headers=headers, json=complete_payload, timeout=30)
    
    if complete_response.status_code != 200:
        error_text = complete_response.text
        logger.error(f"Complete task failed with status {complete_response.status_code}: {error_text}")
        complete_response.raise_for_status()
    
    result = complete_response.json()
    
    # Extract the public URL from the response
    public_url = result.get("public_url") or result.get("output_location")
    
    if not public_url:
        raise Exception(f"No public URL returned from complete_task: {result}")
        
    logger.info(f"Pre-signed URL upload successful: {public_url}")
    return public_url


async def download_and_upload_to_supabase(client: httpx.AsyncClient, task_id: str, external_url: str, extract_screenshot: bool = True) -> Dict[str, str]:
    """
    Download content from external URL and re-upload to Supabase storage.
    If the content is a video and extract_screenshot=True, also extracts and uploads first frame.
    Returns dict with 'url' and optionally 'screenshot_url'.
    """
    from .video_utils import is_video_file, extract_first_frame_bytes
    
    # Video MIME types to check in content-type header
    VIDEO_MIME_TYPES = {'video/mp4', 'video/avi', 'video/quicktime', 'video/x-msvideo', 
                        'video/x-matroska', 'video/webm', 'video/x-m4v', 'video/x-flv', 
                        'video/x-ms-wmv', 'video/mpeg'}
    
    try:
        # Extract filename from URL
        parsed_url = urlparse(external_url)
        filename = Path(parsed_url.path).name
        
        # Download the content and capture content-type
        # 300s timeout: large videos (1080p, 90s+) can be 50-100MB+
        logger.info(f"Downloading content from: {external_url}")
        response = await client.get(external_url, timeout=300)
        response.raise_for_status()
        file_data = response.content
        content_type = response.headers.get('content-type', 'application/octet-stream').split(';')[0].strip().lower()
        logger.info(f"Downloaded {len(file_data)} bytes, content-type: {content_type}")
        
        # If no filename in URL, create one based on task_id and content-type
        if not filename or filename == '/' or '.' not in filename:
            ext = mimetypes.guess_extension(content_type) or '.bin'
            filename = f"task_{task_id}{ext}"
            logger.info(f"Generated filename from content-type: {filename}")
        
        # Check if this is a video file using BOTH content-type and filename extension
        is_video = content_type in VIDEO_MIME_TYPES or is_video_file(filename)
        
        # Extract screenshot if requested and this is a video
        first_frame_data = None
        if extract_screenshot and is_video:
            logger.info(f"Video detected ({len(file_data)} bytes, content-type={content_type}), extracting first frame screenshot for task {task_id}")
            screenshot_bytes = extract_first_frame_bytes(file_data)
            if screenshot_bytes:
                # Convert to base64 for inclusion in main upload (matching original approach)
                first_frame_data = base64.b64encode(screenshot_bytes).decode('utf-8')
                logger.info(f"First frame extracted and will be included in upload")
            else:
                logger.warning(f"Failed to extract first frame from video for task {task_id}")
        
        # Upload to Supabase storage (with first frame data if available)
        supabase_url = await upload_to_supabase_storage(
            client, task_id, file_data, filename, first_frame_data
        )
        
        result = {"url": supabase_url}
        if first_frame_data:
            result["screenshot_included"] = True
        
        logger.info(f"Successfully migrated {external_url} -> {supabase_url}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to download and upload {external_url}: {e}")
        # Return original URL as fallback, but mark it as failed so caller knows
        logger.warning(f"Falling back to original URL: {external_url}")
        return {"url": external_url, "upload_failed": True, "error": str(e)}


async def process_external_url_result(client: httpx.AsyncClient, task_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a task result that contains an external URL, downloading and re-uploading to Supabase.
    Automatically extracts first frame screenshots for video files.
    Updates the result dict in place and returns it.
    """
    if not result.get("output_url"):
        return result
    
    external_url = result["output_url"]
    logger.info(f"Processing external URL for task {task_id}: {external_url}")
    
    try:
        # Download and upload with automatic screenshot extraction
        upload_result = await download_and_upload_to_supabase(client, task_id, external_url)
        
        # Update result with new URLs
        result["output_location"] = upload_result["url"]
        result["original_external_url"] = external_url
        
        # Add screenshot URL if extracted
        if "screenshot_url" in upload_result:
            result["screenshot_url"] = upload_result["screenshot_url"]
        
        # Check if upload actually failed (download_and_upload_to_supabase returns fallback on error)
        if upload_result.get("upload_failed"):
            logger.warning(f"Upload failed for task {task_id}, will need explicit mark_complete: {upload_result.get('error')}")
            result["migration_error"] = upload_result.get("error")
            # Do NOT set _task_completed_by_upload - let the caller handle completion
        else:
            # Mark that task completion was handled by the upload
            result["_task_completed_by_upload"] = True
            logger.info(f"Successfully processed external URL for task {task_id}")
        
    except Exception as e:
        logger.error(f"Failed to process external URL {external_url} for task {task_id}: {e}")
        # Fallback to original external URL
        result["output_location"] = external_url
        result["migration_error"] = str(e)
        logger.warning(f"Using original external URL as fallback: {external_url}")
    
    return result
