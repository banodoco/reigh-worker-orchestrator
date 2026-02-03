"""
fal.ai API wrapper with resilient request tracking and retry logic.

This module provides a robust interface to fal.ai that:
1. Tracks request IDs for recovery on failure/restart
2. Implements extended retries for transient errors (especially 503s)
3. Checks for existing completed jobs before resubmitting
4. Provides detailed progress logging
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

import fal_client
import httpx
from fal_client.client import FalClientHTTPError

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class FalRetryConfig:
    """Configuration for fal.ai retry behavior."""
    
    # Submission retries (if initial submit fails)
    submit_max_attempts: int = 3
    submit_base_delay_sec: float = 1.0
    submit_max_delay_sec: float = 10.0
    
    # Polling configuration
    poll_interval_sec: float = 2.0
    poll_timeout_sec: float = 600.0  # 10 minutes default
    poll_progress_log_interval_sec: float = 30.0  # Log progress every 30s
    poll_max_consecutive_errors: int = 5  # Give up polling after 5 consecutive errors
    
    # Result fetch retries (critical - this is where 503s often occur)
    fetch_max_attempts: int = 20
    fetch_base_delay_sec: float = 5.0
    fetch_max_delay_sec: float = 60.0
    fetch_exponential_base: float = 1.5
    
    # Status check retries (for checking existing jobs)
    status_max_attempts: int = 5
    status_base_delay_sec: float = 2.0


# Default configuration
DEFAULT_CONFIG = FalRetryConfig()


# =============================================================================
# Data Types
# =============================================================================

class FalJobStatus(Enum):
    """Possible states for a fal.ai job."""
    PENDING = "PENDING"           # Job submitted but not yet picked up
    IN_QUEUE = "IN_QUEUE"         # Job is queued
    IN_PROGRESS = "IN_PROGRESS"   # Job is being processed
    COMPLETED = "COMPLETED"       # Job finished successfully
    FAILED = "FAILED"             # Job failed
    UNKNOWN = "UNKNOWN"           # Status couldn't be determined


@dataclass  
class FalRequestTracking:
    """Tracking data stored in task metadata."""
    request_id: str
    endpoint: str
    submitted_at: str
    arguments_hash: Optional[str] = None  # To detect if args changed
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'fal_request_id': self.request_id,
            'fal_endpoint': self.endpoint,
            'fal_submitted_at': self.submitted_at,
            'fal_arguments_hash': self.arguments_hash,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional['FalRequestTracking']:
        """Extract tracking data from task metadata."""
        request_id = data.get('fal_request_id')
        endpoint = data.get('fal_endpoint')
        if not request_id or not endpoint:
            return None
        return cls(
            request_id=request_id,
            endpoint=endpoint,
            submitted_at=data.get('fal_submitted_at', ''),
            arguments_hash=data.get('fal_arguments_hash'),
        )


# =============================================================================
# Helper Functions
# =============================================================================

def _get_fal_key() -> str:
    """Get fal.ai API key from environment."""
    key = os.getenv('FAL_KEY')
    if not key:
        raise ValueError("FAL_KEY environment variable not set")
    return key


def _calculate_backoff(attempt: int, base: float, max_delay: float, exponential_base: float = 2.0) -> float:
    """Calculate exponential backoff delay with jitter."""
    delay = min(base * (exponential_base ** attempt), max_delay)
    # Add jitter (±20%)
    jitter = delay * 0.2 * (random.random() * 2 - 1)
    return delay + jitter


def _hash_arguments(arguments: Dict[str, Any]) -> str:
    """Create a hash of arguments to detect changes."""
    # Sort keys for consistent hashing
    arg_str = json.dumps(arguments, sort_keys=True, default=str)
    return hashlib.md5(arg_str.encode()).hexdigest()[:16]


def _is_retryable_error(error: Exception) -> bool:
    """Check if an error is retryable (transient)."""
    if isinstance(error, FalClientHTTPError):
        # 5xx errors are retryable
        return error.status_code >= 500
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    return False


def _extract_model_id(endpoint: str) -> str:
    """
    Extract model_id from a fal.ai endpoint.
    
    fal.ai requires using only the model_id (first two path segments) for status
    checks and result fetching, NOT the full endpoint with subpaths.
    
    Example: "fal-ai/flashvsr/upscale/video" -> "fal-ai/flashvsr"
    
    See: https://docs.fal.ai/model-endpoints/queue
    """
    parts = endpoint.split('/')
    return '/'.join(parts[:2]) if len(parts) >= 2 else endpoint


# =============================================================================
# Low-Level API Functions
# =============================================================================

async def check_fal_job_status(
    request_id: str,
    endpoint: str,
    config: FalRetryConfig = DEFAULT_CONFIG
) -> Tuple[FalJobStatus, Optional[Dict[str, Any]]]:
    """
    Check the status of an existing fal.ai job.
    
    Returns:
        Tuple of (status, metadata_dict or None)
    
    Note: fal.ai requires using only the model_id for status checks, not the full 
    endpoint with subpaths. See _extract_model_id() for details.
    """
    fal_key = _get_fal_key()
    model_id = _extract_model_id(endpoint)
    status_url = f"https://queue.fal.run/{model_id}/requests/{request_id}/status"
    
    headers = {"Authorization": f"Key {fal_key}"}
    
    for attempt in range(config.status_max_attempts):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(status_url, headers=headers)
                
                # fal.ai returns 200 for COMPLETED, 202 for IN_QUEUE/IN_PROGRESS
                if response.status_code in (200, 202):
                    data = response.json()
                    status_str = data.get('status', 'UNKNOWN').upper()
                    try:
                        status = FalJobStatus(status_str)
                    except ValueError:
                        status = FalJobStatus.UNKNOWN
                    return status, data
                    
                elif response.status_code == 404:
                    logger.warning(f"fal.ai request {request_id} not found (404)")
                    return FalJobStatus.UNKNOWN, None
                    
                elif response.status_code >= 500:
                    if attempt < config.status_max_attempts - 1:
                        delay = _calculate_backoff(attempt, config.status_base_delay_sec, 30.0)
                        logger.warning(f"fal.ai status check got {response.status_code}, retrying in {delay:.1f}s...")
                        await asyncio.sleep(delay)
                        continue
                    return FalJobStatus.UNKNOWN, None
                    
                else:
                    logger.error(f"fal.ai status check failed: {response.status_code} {response.text}")
                    return FalJobStatus.UNKNOWN, None
                    
        except Exception as e:
            if attempt < config.status_max_attempts - 1:
                delay = _calculate_backoff(attempt, config.status_base_delay_sec, 30.0)
                logger.warning(f"fal.ai status check failed ({e}), retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                continue
            logger.error(f"fal.ai status check failed after {config.status_max_attempts} attempts: {e}")
            return FalJobStatus.UNKNOWN, None
    
    return FalJobStatus.UNKNOWN, None


async def fetch_fal_result(
    request_id: str,
    endpoint: str,
    config: FalRetryConfig = DEFAULT_CONFIG
) -> Optional[Dict[str, Any]]:
    """
    Fetch the result of a completed fal.ai job with extended retries.
    
    This is where 503 "Runner disconnected" errors often occur.
    We use aggressive retries because the job may have completed
    but the result endpoint is temporarily unavailable.
    
    Note: fal.ai requires using only the model_id for result fetching, not the full 
    endpoint with subpaths. See _extract_model_id() for details.
    
    Returns:
        Result dict if successful, None if unrecoverable
    """
    fal_key = _get_fal_key()
    model_id = _extract_model_id(endpoint)
    result_url = f"https://queue.fal.run/{model_id}/requests/{request_id}"
    
    headers = {"Authorization": f"Key {fal_key}"}
    
    logger.info(f"Fetching result for fal.ai request {request_id}")
    
    for attempt in range(config.fetch_max_attempts):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(result_url, headers=headers)
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Successfully fetched fal.ai result for {request_id}")
                    return result
                    
                elif response.status_code == 503:
                    # This is the "Runner disconnected" case
                    # Check if fal.ai says we should retry
                    should_retry = response.headers.get('x-fal-needs-retry') == '1'
                    
                    if attempt < config.fetch_max_attempts - 1:
                        delay = _calculate_backoff(
                            attempt, 
                            config.fetch_base_delay_sec, 
                            config.fetch_max_delay_sec,
                            config.fetch_exponential_base
                        )
                        logger.warning(
                            f"fal.ai result fetch got 503 (Runner disconnected), "
                            f"attempt {attempt + 1}/{config.fetch_max_attempts}, "
                            f"x-fal-needs-retry={should_retry}, "
                            f"retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(
                            f"fal.ai result fetch failed after {config.fetch_max_attempts} attempts. "
                            f"Request ID {request_id} may be unrecoverable."
                        )
                        return None
                        
                elif response.status_code >= 500:
                    if attempt < config.fetch_max_attempts - 1:
                        delay = _calculate_backoff(
                            attempt, 
                            config.fetch_base_delay_sec, 
                            config.fetch_max_delay_sec,
                            config.fetch_exponential_base
                        )
                        logger.warning(
                            f"fal.ai result fetch got {response.status_code}, "
                            f"attempt {attempt + 1}/{config.fetch_max_attempts}, "
                            f"retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                        continue
                    return None
                    
                elif response.status_code == 404:
                    logger.error(f"fal.ai request {request_id} not found (404)")
                    return None
                    
                else:
                    logger.error(f"fal.ai result fetch failed: {response.status_code} {response.text}")
                    return None
                    
        except httpx.TimeoutException:
            if attempt < config.fetch_max_attempts - 1:
                delay = _calculate_backoff(attempt, config.fetch_base_delay_sec, config.fetch_max_delay_sec)
                logger.warning(f"fal.ai result fetch timed out, retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                continue
            logger.error(f"fal.ai result fetch timed out after {config.fetch_max_attempts} attempts")
            return None
            
        except Exception as e:
            if attempt < config.fetch_max_attempts - 1 and _is_retryable_error(e):
                delay = _calculate_backoff(attempt, config.fetch_base_delay_sec, config.fetch_max_delay_sec)
                logger.warning(f"fal.ai result fetch failed ({e}), retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                continue
            logger.error(f"fal.ai result fetch failed: {e}")
            return None
    
    return None


async def submit_fal_job(
    endpoint: str,
    arguments: Dict[str, Any],
    config: FalRetryConfig = DEFAULT_CONFIG
) -> str:
    """
    Submit a job to fal.ai and return the request ID.
    
    Raises:
        Exception if submission fails after all retries
    """
    for attempt in range(config.submit_max_attempts):
        try:
            # fal_client.submit is synchronous, run in executor
            loop = asyncio.get_event_loop()
            handle = await loop.run_in_executor(
                None,
                lambda: fal_client.submit(endpoint, arguments=arguments)
            )
            request_id = handle.request_id
            logger.info(f"Submitted job to fal.ai {endpoint}, request_id={request_id}")
            return request_id
            
        except Exception as e:
            if attempt < config.submit_max_attempts - 1 and _is_retryable_error(e):
                delay = _calculate_backoff(attempt, config.submit_base_delay_sec, config.submit_max_delay_sec)
                logger.warning(f"fal.ai job submission failed ({e}), retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
                continue
            logger.error(f"fal.ai job submission failed after {config.submit_max_attempts} attempts: {e}")
            raise


async def poll_fal_job(
    request_id: str,
    endpoint: str,
    config: FalRetryConfig = DEFAULT_CONFIG,
    on_progress: Optional[Callable[[str, float], None]] = None
) -> Tuple[FalJobStatus, Optional[Dict[str, Any]]]:
    """
    Poll a fal.ai job until completion or failure.
    
    Args:
        request_id: The fal.ai request ID
        endpoint: The fal.ai endpoint
        config: Retry configuration
        on_progress: Optional callback for progress updates (status_message, elapsed_seconds)
    
    Returns:
        Tuple of (final_status, status_metadata)
    """
    start_time = time.time()
    last_progress_log = start_time
    consecutive_errors = 0
    last_status = FalJobStatus.PENDING
    
    while True:
        elapsed = time.time() - start_time
        
        # Check timeout
        if elapsed > config.poll_timeout_sec:
            logger.error(f"fal.ai job {request_id} timed out after {elapsed:.0f}s")
            return FalJobStatus.UNKNOWN, {'timeout': True, 'elapsed': elapsed}
        
        # Check status
        status, metadata = await check_fal_job_status(request_id, endpoint, config)
        
        if status == FalJobStatus.UNKNOWN:
            consecutive_errors += 1
            if consecutive_errors >= config.poll_max_consecutive_errors:
                logger.error(f"fal.ai job {request_id} polling failed {consecutive_errors} times, giving up")
                return FalJobStatus.UNKNOWN, {'consecutive_errors': consecutive_errors}
        else:
            consecutive_errors = 0
            last_status = status
        
        # Log progress periodically
        now = time.time()
        if now - last_progress_log >= config.poll_progress_log_interval_sec:
            logger.info(f"fal.ai job {request_id}: status={status.value}, elapsed={elapsed:.0f}s")
            if on_progress:
                on_progress(status.value, elapsed)
            last_progress_log = now
        
        # Check terminal states
        if status == FalJobStatus.COMPLETED:
            logger.info(f"fal.ai job {request_id} completed after {elapsed:.0f}s")
            return status, metadata
            
        if status == FalJobStatus.FAILED:
            logger.error(f"fal.ai job {request_id} failed after {elapsed:.0f}s")
            return status, metadata
        
        # Wait before next poll
        await asyncio.sleep(config.poll_interval_sec)


# =============================================================================
# High-Level API (Main Entry Points)
# =============================================================================

async def call_fal_api_resilient(
    endpoint: str,
    arguments: Dict[str, Any],
    client: httpx.AsyncClient,
    task_id: str,
    update_task_metadata: Callable[[str, Dict[str, Any]], Any],
    existing_tracking: Optional[FalRequestTracking] = None,
    config: FalRetryConfig = DEFAULT_CONFIG,
) -> Dict[str, Any]:
    """
    Resilient fal.ai API call with request tracking and recovery.
    
    This is the main entry point for making fal.ai calls. It:
    1. Checks if there's an existing completed job that can be recovered
    2. Submits a new job if needed, storing the request ID
    3. Polls for completion with progress logging
    4. Fetches the result with extended retries
    
    Args:
        endpoint: fal.ai endpoint (e.g., "fal-ai/film/video")
        arguments: Arguments to pass to fal.ai
        client: httpx client (kept for interface consistency)
        task_id: The task ID for metadata updates
        update_task_metadata: Async function to update task metadata
        existing_tracking: Optional tracking data from previous attempt
        config: Retry configuration
        
    Returns:
        Transformed result dictionary
        
    Raises:
        Exception on unrecoverable failure
    """
    logger.info(f"Calling fal.ai endpoint: {endpoint}")
    logger.info(f"Arguments: {json.dumps(arguments, default=str)[:500]}...")
    
    request_id = None
    active_endpoint = endpoint  # Track which endpoint we're actually using
    args_hash = _hash_arguments(arguments)
    
    # ==========================================================================
    # Phase 1: Check for existing job that can be recovered
    # ==========================================================================
    
    if existing_tracking:
        logger.info(f"Found existing fal.ai request: {existing_tracking.request_id}")
        
        # Warn if endpoint changed (shouldn't happen, but be safe)
        if existing_tracking.endpoint != endpoint:
            logger.warning(
                f"Endpoint changed from {existing_tracking.endpoint} to {endpoint}, "
                f"will use original endpoint for recovery"
            )
        
        # Check if arguments have changed
        if existing_tracking.arguments_hash and existing_tracking.arguments_hash != args_hash:
            logger.warning(
                f"Arguments have changed since previous submission "
                f"(hash {existing_tracking.arguments_hash} -> {args_hash}), "
                f"will submit new job"
            )
        else:
            # Check status of existing job - ALWAYS use the tracked endpoint
            status, metadata = await check_fal_job_status(
                existing_tracking.request_id, 
                existing_tracking.endpoint,
                config
            )
            
            logger.info(f"Existing job {existing_tracking.request_id} status: {status.value}")
            
            if status == FalJobStatus.COMPLETED:
                # Try to fetch the result!
                logger.info(f"Existing job completed, attempting to fetch result...")
                result = await fetch_fal_result(
                    existing_tracking.request_id,
                    existing_tracking.endpoint,
                    config
                )
                
                if result:
                    logger.info(f"Successfully recovered result from existing job {existing_tracking.request_id}")
                    return _transform_fal_result(result)
                else:
                    logger.warning(
                        f"Existing job {existing_tracking.request_id} completed but result "
                        f"could not be fetched, will submit new job"
                    )
                    
            elif status in (FalJobStatus.IN_QUEUE, FalJobStatus.IN_PROGRESS, FalJobStatus.PENDING):
                # Job is still running, continue polling with the ORIGINAL endpoint
                logger.info(f"Existing job {existing_tracking.request_id} still in progress, continuing to poll...")
                request_id = existing_tracking.request_id
                active_endpoint = existing_tracking.endpoint  # Use the tracked endpoint!
                
            elif status == FalJobStatus.FAILED:
                logger.warning(f"Existing job {existing_tracking.request_id} failed, will submit new job")
                
            else:  # UNKNOWN
                logger.warning(
                    f"Could not determine status of existing job {existing_tracking.request_id}, "
                    f"will submit new job"
                )
    
    # ==========================================================================
    # Phase 2: Submit new job if needed
    # ==========================================================================
    
    if not request_id:
        request_id = await submit_fal_job(endpoint, arguments, config)
        active_endpoint = endpoint  # New job uses the passed endpoint
        
        # Store tracking info in task metadata IMMEDIATELY
        tracking = FalRequestTracking(
            request_id=request_id,
            endpoint=endpoint,
            submitted_at=datetime.now(timezone.utc).isoformat(),
            arguments_hash=args_hash,
        )
        
        try:
            await update_task_metadata(task_id, tracking.to_dict())
            logger.info(f"Stored fal.ai request tracking in task metadata: {request_id}")
        except Exception as e:
            # Log but don't fail - the job is already submitted
            logger.error(f"Failed to store fal.ai request tracking: {e}")
    
    # ==========================================================================
    # Phase 3: Poll for completion
    # ==========================================================================
    
    def on_progress(status_msg: str, elapsed: float):
        logger.info(f"fal.ai progress: {status_msg} ({elapsed:.0f}s elapsed)")
    
    status, metadata = await poll_fal_job(
        request_id, 
        active_endpoint,  # Use the active endpoint (may be from existing tracking)
        config,
        on_progress=on_progress
    )
    
    if status == FalJobStatus.FAILED:
        error_msg = metadata.get('error', 'Unknown error') if metadata else 'Unknown error'
        raise Exception(f"fal.ai job failed: {error_msg}")
        
    if status != FalJobStatus.COMPLETED:
        timeout_info = f" (timeout after {metadata.get('elapsed', '?')}s)" if metadata and metadata.get('timeout') else ""
        raise Exception(f"fal.ai job did not complete successfully: {status.value}{timeout_info}")
    
    # ==========================================================================
    # Phase 4: Fetch result with extended retries
    # ==========================================================================
    
    result = await fetch_fal_result(request_id, active_endpoint, config)  # Use active endpoint
    
    if not result:
        # This is the critical failure case - job completed but result not fetchable
        raise Exception(
            f"fal.ai job {request_id} completed but result could not be fetched. "
            f"This may be a fal.ai infrastructure issue. "
            f"Request ID preserved for potential manual recovery."
        )
    
    logger.info(f"fal.ai result: {json.dumps(result, default=str)[:500]}...")
    
    return _transform_fal_result(result)


def _transform_fal_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform fal.ai result format to expected format.
    
    fal.ai typically returns:
        {'image': {'url': '...', 'width': ..., 'height': ...}, ...}
        or {'images': [{'url': '...', ...}, ...], ...}
        or {'video': {'url': '...', ...}, ...}
    
    We transform to: {'output_url': '...', ...}
    """
    if isinstance(result, dict):
        # Handle single image response
        if 'image' in result:
            image_data = result['image']
            if isinstance(image_data, dict) and 'url' in image_data:
                return {
                    'output_url': image_data['url'],
                    'width': image_data.get('width'),
                    'height': image_data.get('height'),
                    'seed': result.get('seed'),
                    'content_type': image_data.get('content_type')
                }

        # Handle multiple images response (take first)
        if 'images' in result and result['images']:
            image_data = result['images'][0]
            if isinstance(image_data, dict) and 'url' in image_data:
                return {
                    'output_url': image_data['url'],
                    'width': image_data.get('width'),
                    'height': image_data.get('height'),
                    'seed': result.get('seed'),
                    'content_type': image_data.get('content_type'),
                    'all_images': [img.get('url') for img in result['images'] if img.get('url')]
                }

        # Handle single video response
        if 'video' in result:
            video_data = result['video']
            if isinstance(video_data, dict) and 'url' in video_data:
                return {
                    'output_url': video_data['url'],
                    'width': video_data.get('width'),
                    'height': video_data.get('height'),
                    'duration': video_data.get('duration'),
                    'fps': video_data.get('fps'),
                    'content_type': video_data.get('content_type')
                }

        # Handle direct URL response
        if 'url' in result:
            return {
                'output_url': result['url'],
                'width': result.get('width'),
                'height': result.get('height'),
                'seed': result.get('seed'),
            }

    # Fallback if format is unexpected
    logger.warning(f"Unexpected fal.ai result format, returning as-is: {result}")
    return result


# =============================================================================
# Legacy API (Backward Compatibility)
# =============================================================================

async def call_fal_api(
    endpoint: str,
    arguments: Dict[str, Any],
    client: httpx.AsyncClient,
) -> Dict[str, Any]:
    """
    Legacy fal.ai API call (backward compatible).
    
    This uses the original blocking subscribe() approach without request tracking.
    Use call_fal_api_resilient() for new code.
    """
    logger.info(f"Calling fal.ai endpoint: {endpoint}")
    logger.info(f"Arguments: {json.dumps(arguments, default=str)[:500]}...")

    def on_queue_update(update):
        if isinstance(update, fal_client.InProgress):
            for log in update.logs:
                logger.info(f"fal.ai: {log['message']}")

    # Run the blocking fal_client.subscribe in a thread pool
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: fal_client.subscribe(
            endpoint,
            arguments=arguments,
            with_logs=True,
            on_queue_update=on_queue_update,
        )
    )

    logger.info(f"fal.ai result: {result}")
    return _transform_fal_result(result)


# =============================================================================
# Utility Functions for LoRA Handling
# =============================================================================

def build_fal_lora_list(params: Dict[str, Any]) -> list:
    """
    Build a list of LoRA configurations from task params.

    Supports multiple input formats:
    - params["loras"]: list of {"path": ..., "scale": ...} or {"url": ..., "strength": ...}
    - params["additional_loras"]: dict of {path: scale, ...}

    Returns:
        List of LoRA configs in fal.ai format: [{"path": ..., "scale": ...}, ...]
    """
    loras = []

    # Handle list format
    loras_list = params.get("loras", [])
    for lora in loras_list:
        if isinstance(lora, dict):
            lora_path = lora.get("path") or lora.get("url", "")
            lora_scale = lora.get("scale", lora.get("strength", 1.0))
            if lora_path:
                loras.append({"path": lora_path, "scale": float(lora_scale)})

    # Handle dict format (additional_loras)
    additional_loras = params.get("additional_loras", {})
    if isinstance(additional_loras, dict):
        for lora_path, scale in additional_loras.items():
            loras.append({"path": lora_path, "scale": float(scale)})

    return loras
