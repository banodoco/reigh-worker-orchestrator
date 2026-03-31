"""
Task management utilities for the API orchestrator.
Handles task claiming, completion, status updates, and task counting.
"""

import os
import asyncio
import logging
from typing import Any, Dict, Optional

import httpx
from supabase import create_client

logger = logging.getLogger(__name__)
_MISSING_ENV_LOGGED_ATTR = "_task_utils_missing_env_logged"


def _get_supabase_edge_urls() -> Dict[str, str]:
    """Get Supabase edge function URLs for task operations."""
    base_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    return {
        "claim": f"{base_url}/functions/v1/claim-next-task" if base_url else "",
        "complete": f"{base_url}/functions/v1/complete_task" if base_url else "",
        "fail": f"{base_url}/functions/v1/mark-task-failed" if base_url else "",
    }


def _auth_headers() -> Dict[str, str]:
    """Get authentication headers for Supabase requests."""
    token = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ACCESS_TOKEN")
    return {
        "Authorization": f"Bearer {token}" if token else "",
        "Content-Type": "application/json",
    }


def _has_logged_missing_env() -> bool:
    return bool(getattr(logger, _MISSING_ENV_LOGGED_ATTR, False))


def _set_logged_missing_env() -> None:
    setattr(logger, _MISSING_ENV_LOGGED_ATTR, True)


async def count_tasks(client: httpx.AsyncClient, run_type: Optional[str]) -> Dict[str, Any]:
    """Count tasks using the new task-counts endpoint."""
    try:
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        task_counts_url = f"{supabase_url}/functions/v1/task-counts"
        
        logger.debug(f"[TASK_COUNT] Counting tasks with run_type: {run_type}")
        logger.debug(f"[TASK_COUNT] URL: {task_counts_url}")
        
        if not supabase_url:
            logger.error("[TASK_COUNT] SUPABASE_URL not configured")
            return {"queued_plus_active": 0, "raw": None}
        
        headers = _auth_headers()
        if not headers["Authorization"]:
            logger.error("[TASK_COUNT] Missing required request context")
            return {"queued_plus_active": 0, "raw": None}

        payload: Dict[str, Any] = {"include_active": False}
        if run_type:
            payload["run_type"] = run_type

        logger.debug(f"[TASK_COUNT] Sending request with payload: {payload}")

        resp = await client.post(task_counts_url, headers=headers, json=payload, timeout=15)
        
        logger.debug(f"[TASK_COUNT] Response status: {resp.status_code}")
        
        if resp.status_code != 200:
            logger.warning(f"[TASK_COUNT] Task counts failed with status {resp.status_code}")
            logger.error(f"[TASK_COUNT] Error response: {resp.text}")
            return {"queued_plus_active": 0, "raw": None}
            
        data = resp.json()
        logger.debug(f"[TASK_COUNT] Raw response data: {data}")
        
        # Extract the counts from the new endpoint format
        # Assuming it returns the same structure as the old endpoint
        if "totals" in data:
            totals = data["totals"]
            # For API workers, only consider queued tasks as claimable
            available_count = int(totals.get("queued_only", totals.get("queued_plus_active", 0)))
            logger.debug(f"[TASK_COUNT] Using totals.queued_only: {available_count}")
        else:
            # Fallback: look for common count fields
            available_count = int(data.get("queued_plus_active", data.get("available_tasks", 0)))
            logger.debug(f"[TASK_COUNT] Using fallback count: {available_count}")
        
        logger.info(f"[TASK_COUNT] Available tasks: {available_count}")
        return {"queued_plus_active": available_count, "raw": data}
        
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.error(f"Count tasks failed: {exc}")
        return {"queued_plus_active": 0, "raw": None}


async def _recover_phantom_claim(
    worker_id: str,
    known_active_ids: set[str],
) -> Optional[Dict[str, Any]]:
    """After a claim timeout, check the DB for a task we may have claimed.

    The claim RPC is atomic — if it assigned a task to us, that task is now
    In Progress with our worker_id.  We query for in-progress tasks on this
    worker and return any that aren't already being tracked.
    """
    try:
        import os
        from supabase import create_client

        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return None

        sb = create_client(url, key)
        result = (
            sb.table("tasks")
            .select("id, task_type, params, project_id")
            .eq("status", "In Progress")
            .eq("worker_id", worker_id)
            .order("generation_started_at", desc=True)
            .limit(10)
            .execute()
        )

        if not result.data:
            return None

        for task in result.data:
            if task["id"] not in known_active_ids:
                return {
                    "task_id": task["id"],
                    "task_type": task.get("task_type"),
                    "params": task.get("params"),
                    "project_id": task.get("project_id"),
                }

        return None
    except Exception as exc:
        logger.warning(f"[TASK_CLAIM] Phantom claim recovery query failed: {exc}")
        return None


async def claim_next_task(
    client: httpx.AsyncClient,
    worker_id: str,
    run_type: Optional[str],
    known_active_ids: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Claim the next available task for processing.

    On timeout, queries the DB to recover a phantom claim (claim that
    succeeded server-side but whose response was lost).  *known_active_ids*
    lets the recovery query skip tasks we're already processing.
    """
    urls = _get_supabase_edge_urls()
    headers = _auth_headers()

    if not urls["claim"] or not headers["Authorization"]:
        if not _has_logged_missing_env():
            logger.error("[TASK_CLAIM] Missing endpoint configuration or request context; cannot claim tasks")
            _set_logged_missing_env()
        await asyncio.sleep(1)
        return None

    payload: Dict[str, Any] = {"worker_id": worker_id}
    if run_type:
        payload["run_type"] = run_type

    try:
        resp = await client.post(urls["claim"], headers=headers, json=payload, timeout=30)

        if resp.status_code == 204:
            return None

        resp.raise_for_status()
        data = resp.json()
        logger.info(f"[TASK_CLAIM] Worker {worker_id}: Successfully claimed task: {data.get('task_id')} ({data.get('task_type')})")
        return data

    except httpx.TimeoutException:
        # Timeout is ambiguous: the claim may have succeeded server-side.
        # Check the DB to find out.
        logger.warning(f"[TASK_CLAIM] Worker {worker_id}: Claim timed out — checking for phantom claim")
        recovered = await _recover_phantom_claim(worker_id, known_active_ids or set())
        if recovered:
            logger.warning(
                f"[TASK_CLAIM] Worker {worker_id}: Recovered phantom claim: "
                f"{recovered.get('task_id')} ({recovered.get('task_type')})"
            )
        return recovered

    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.error(f"[TASK_CLAIM] Worker {worker_id}: Claim failed: {exc}")
        if isinstance(exc, httpx.HTTPStatusError):
            logger.error(f"[TASK_CLAIM] Response body: {exc.response.text}")
        await asyncio.sleep(0.5)
        return None


async def mark_complete_via_edge_function(client: httpx.AsyncClient, task_id: str, output_location: str = None) -> bool:
    """Mark task complete via edge function.

    Uses complete_task when no output_location is provided (file upload handled separately),
    or update-task-status when we only have an output_location URL (e.g. fallback to external URL
    after upload failure). The complete_task endpoint requires file_data or storage_path, so
    passing just an output_location would fail with 400.
    """
    try:
        headers = _auth_headers()

        if not headers["Authorization"]:
            logger.error("Missing request context for mark-task-complete")
            return False

        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")

        if output_location:
            # Use update-task-status for URL-only completion (complete_task requires file data)
            edge_url = f"{supabase_url}/functions/v1/update-task-status" if supabase_url else ""
            payload = {
                "task_id": task_id,
                "status": "Complete",
                "output_location": output_location
            }
        else:
            urls = _get_supabase_edge_urls()
            edge_url = urls["complete"]
            payload = {"task_id": task_id}

        if not edge_url:
            logger.error("Missing edge function URL configuration")
            return False

        logger.info(f"Attempting to mark task {task_id} complete via {edge_url}")
        logger.debug(f"Payload: {payload}")

        resp = await client.post(edge_url, json=payload, headers=headers, timeout=30)

        # Log response details for debugging
        logger.info(f"Mark complete response: status={resp.status_code}, headers={dict(resp.headers)}")
        if resp.status_code != 200:
            response_text = resp.text
            logger.error(f"Mark complete failed with status {resp.status_code}: {response_text}")

        resp.raise_for_status()

        # Log response body for successful requests too
        try:
            response_json = resp.json()
            logger.info(f"Task {task_id} marked complete successfully: {response_json}")
        except ValueError:
            logger.info(f"Task {task_id} marked complete (no JSON response)")

        return True

    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.error(f"Failed to mark task {task_id} complete: {exc}")
        return False


async def mark_failed_via_edge_function(client: httpx.AsyncClient, task_id: str, error_message: str) -> bool:
    """Mark task failed via update-task-status edge function"""
    try:
        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        edge_url = (
            os.getenv("SUPABASE_EDGE_UPDATE_TASK_URL")
            or (f"{supabase_url}/functions/v1/update-task-status" if supabase_url else None)
        )
        
        if not edge_url:
            logger.error("No update-task-status edge URL configured")
            return False
            
        headers = {
            "Content-Type": "application/json", 
            "Authorization": f"Bearer {os.getenv('SUPABASE_ACCESS_TOKEN') or os.getenv('SUPABASE_SERVICE_ROLE_KEY')}"
        }
        
        payload = {
            "task_id": task_id,
            "status": "Failed",
            "output_location": error_message[:500]  # Truncate long error messages
        }
        
        logger.info(f"Attempting to mark task {task_id} failed via {edge_url}")
        logger.debug(f"Payload: {payload}")
        
        resp = await client.post(edge_url, json=payload, headers=headers, timeout=30)
        
        # Log response details for debugging
        logger.info(f"Mark failed response: status={resp.status_code}, headers={dict(resp.headers)}")
        if resp.status_code != 200:
            response_text = resp.text
            logger.error(f"Mark failed failed with status {resp.status_code}: {response_text}")
            
        resp.raise_for_status()
        
        # Log response body for successful requests too
        try:
            response_json = resp.json()
            logger.info(f"Task {task_id} marked failed successfully: {response_json}")
        except ValueError:
            logger.info(f"Task {task_id} marked failed (no JSON response)")
            
        return True
        
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        logger.error(f"Failed to mark task {task_id} as failed: {exc}")
        return False


async def mark_complete(client: httpx.AsyncClient, task_id: str, result: Dict[str, Any]) -> bool:
    """Mark task complete - wrapper for backward compatibility"""
    output_location = result.get("output_location")
    success = await mark_complete_via_edge_function(client, task_id, output_location)
    if not success:
        logger.error(f"CRITICAL: Failed to mark task {task_id} as complete in database - task may remain stuck!")
        logger.error(f"Task result that failed to save: {result}")
    return success


async def mark_failed(client: httpx.AsyncClient, task_id: str, error_message: str) -> bool:
    """Mark task failed - wrapper for backward compatibility"""
    success = await mark_failed_via_edge_function(client, task_id, error_message)
    if not success:
        logger.error(f"CRITICAL: Failed to mark task {task_id} as failed in database - task may remain stuck!")
        logger.error(f"Error message that failed to save: {error_message}")
    return success


async def update_task_metadata(task_id: str, metadata_updates: Dict[str, Any]) -> bool:
    """
    Update task metadata (result_data field) to store tracking information.
    
    This is used to store fal.ai request IDs and other tracking data
    that allows recovery if the orchestrator crashes mid-job.
    
    Args:
        task_id: The task ID to update
        metadata_updates: Dictionary of key-value pairs to merge into result_data
        
    Returns:
        True if successful, False otherwise
    """
    try:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY for metadata update")
            return False
        
        client = create_client(supabase_url, supabase_key)
        
        # First, get the current result_data to merge with
        result = client.table('tasks').select('result_data').eq('id', task_id).execute()
        
        if not result.data:
            logger.error(f"Task {task_id} not found for metadata update")
            return False
        
        current_data = result.data[0].get('result_data') or {}
        
        # Merge the new metadata
        updated_data = {**current_data, **metadata_updates}
        
        # Update the task
        update_result = client.table('tasks').update({
            'result_data': updated_data
        }).eq('id', task_id).execute()
        
        if update_result.data:
            logger.info(f"Updated task {task_id} metadata with: {list(metadata_updates.keys())}")
            return True
        else:
            logger.error(f"Failed to update task {task_id} metadata - no data returned")
            return False
            
    except Exception as exc:
        logger.exception(f"Failed to update task {task_id} metadata: {exc}")
        return False


async def get_task_metadata(task_id: str) -> Optional[Dict[str, Any]]:
    """
    Get task metadata (result_data field) for recovery purposes.
    
    Args:
        task_id: The task ID to query
        
    Returns:
        The result_data dict if found, None otherwise
    """
    try:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            logger.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY for metadata query")
            return None
        
        client = create_client(supabase_url, supabase_key)
        
        result = client.table('tasks').select('result_data').eq('id', task_id).execute()
        
        if result.data:
            return result.data[0].get('result_data') or {}
        else:
            logger.warning(f"Task {task_id} not found for metadata query")
            return None
            
    except Exception as exc:
        logger.error(f"Failed to get task {task_id} metadata: {exc}")
        return None
