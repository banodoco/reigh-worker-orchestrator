"""API Orchestrator main entry point.

This module contains the event loop, task validation, and dispatch logic.
Task handlers are implemented in task_handlers.py.
"""

import os
import sys
import asyncio
import json
import logging
from typing import Any, Dict

import httpx
from dotenv import load_dotenv

# Load environment variables from .env at import time so module-level reads work
load_dotenv()

# Configure structured logging before importing internal modules
from .logging_config import setup_logging, set_current_cycle, get_db_logging_stats
from .database import DatabaseClient

# Initial setup without database client
setup_logging()

# Import utility modules
from .task_utils import count_tasks, claim_next_task, mark_complete, mark_failed
from .task_handlers import TASK_HANDLERS, SUPPORTED_TASK_TYPES

CONCURRENCY = int(os.getenv("API_WORKER_CONCURRENCY", "20"))
RUN_TYPE = "api"  # Hardcoded for API workers - they process API tasks
PARENT_POLL_SEC = int(os.getenv("API_PARENT_POLL_SEC", "10"))


logger = logging.getLogger(__name__)


async def process_api_task(task: Dict[str, Any], client: httpx.AsyncClient) -> Dict[str, Any]:
    """Process API tasks by dispatching to the appropriate handler.

    Args:
        task: Task dictionary containing task_type and params
        client: httpx client for making HTTP requests

    Returns:
        Result dictionary from the handler

    Raises:
        Exception: If task type is unsupported or handler fails
    """
    params = task.get("params") or {}
    task_type = task.get("task_type", "unknown")

    # Parse params if it's a JSON string
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse params JSON: {e}")
            raise Exception(f"Invalid JSON in params field: {e}")

    # Check for wavespeed api_type override (legacy support)
    if task_type == "qwen_image_edit" or params.get("api_type") == "wavespeed":
        task_type = "qwen_image_edit"

    # Look up the handler for this task type
    handler = TASK_HANDLERS.get(task_type)

    if handler is None:
        raise Exception(
            f"Unsupported task type: {task_type}. "
            f"Supported types: {', '.join(repr(t) for t in SUPPORTED_TASK_TYPES)}."
        )

    # Dispatch to the handler
    return await handler(task, params, client)


def validate_api_environment():
    """Validate all required environment variables for API orchestrator."""
    logger.info("🔍 [STARTUP] Validating API orchestrator environment...")

    # Required environment variables
    required_vars = {
        'SUPABASE_URL': 'Supabase database URL for task management',
        'SUPABASE_SERVICE_ROLE_KEY': 'Supabase service role key for database access',
        'WAVESPEED_API_KEY': 'Wavespeed API key for processing tasks',
        'FAL_KEY': 'fal.ai API key for image upscaling tasks',
    }

    # Optional but important environment variables
    important_vars = {
        'API_WORKER_ID': 'Unique identifier for this API worker instance',
        'CONCURRENCY': 'Number of concurrent tasks to process',
        'RUN_TYPE': 'Type of tasks to process (cloud/local)',
        'PARENT_POLL_SEC': 'Polling interval for task checking',
        'LOG_LEVEL': 'Logging level (DEBUG/INFO/WARNING/ERROR)',
    }

    # Check required variables
    missing_required = []
    for var, description in required_vars.items():
        value = os.getenv(var)
        if not value:
            missing_required.append(f"  ❌ {var}: {description}")
            logger.error(f"[STARTUP] Missing required environment variable: {var}")
        else:
            # Show partial value for security
            if 'KEY' in var or 'SECRET' in var:
                display_value = f"{value[:10]}..." if len(value) > 10 else "***"
            else:
                display_value = value
            logger.info(f"[STARTUP]   ✅ {var}: {display_value}")

    # Check important variables
    for var, description in important_vars.items():
        value = os.getenv(var)
        if not value:
            logger.warning(f"[STARTUP]   ⚠️  {var}: {description} (using default)")
        else:
            logger.info(f"[STARTUP]   ✅ {var}: {value}")

    # Report results
    if missing_required:
        logger.error("[STARTUP] ❌ CRITICAL: Missing required environment variables:")
        for msg in missing_required:
            logger.error(f"[STARTUP] {msg}")
        logger.error("[STARTUP] 🛑 Cannot start API orchestrator without required configuration!")
        return False

    logger.info("[STARTUP] ✅ Environment validation completed successfully")
    return True


async def main_async() -> None:
    logger.info(f"[STARTUP] API Orchestrator starting...")

    # Validate environment before proceeding
    if not validate_api_environment():
        logger.error("[STARTUP] 🛑 Exiting due to missing required configuration!")
        sys.exit(1)

    # Initialize database client for centralized logging
    db_client = None
    try:
        db_client = DatabaseClient()

        # Re-initialize logging with database client if DB logging is enabled
        from .logging_config import setup_logging as reinit_logging
        reinit_logging(db_client=db_client, source_type="orchestrator_api")

        logger.info("[STARTUP] ✅ Centralized database logging initialized")
    except Exception as e:
        logger.warning(f"[STARTUP] ⚠️  Could not initialize database logging: {e}")
        logger.warning("[STARTUP] Continuing with console logging only...")

    # Register API worker in the database (required for task claiming)
    worker_id = os.getenv("API_WORKER_ID", "api-worker-main")
    if db_client:
        if not db_client.register_worker(worker_id):
            logger.error(f"[STARTUP] ❌ Failed to register worker {worker_id} in database!")
            logger.error("[STARTUP] 🛑 Task claiming will fail due to foreign key constraint")
            sys.exit(1)
        logger.info(f"[STARTUP] ✅ Worker {worker_id} registered in database")
    else:
        logger.warning(f"[STARTUP] ⚠️  No database client - worker {worker_id} not registered")

    # Log additional startup configuration
    logger.info(f"[STARTUP] CONCURRENCY: {CONCURRENCY}")
    logger.info(f"[STARTUP] RUN_TYPE: {RUN_TYPE}")
    logger.info(f"[STARTUP] PARENT_POLL_SEC: {PARENT_POLL_SEC}")
    limits = httpx.Limits(max_connections=max(64, CONCURRENCY * 4), max_keepalive_connections=max(32, CONCURRENCY * 2))
    active_tasks: set[asyncio.Task] = set()

    async def spawn_task(task_payload: Dict[str, Any], client: httpx.AsyncClient):
        task_id = task_payload.get("task_id") or task_payload.get("id")

        # Set task context for logging
        from .logging_config import set_current_task
        set_current_task(str(task_id))

        try:
            logger.info(f"Starting task {task_id}")
            result = await process_api_task(task_payload, client)

            # Check if task completion was already handled by the upload process
            if result.get("_task_completed_by_upload"):
                logger.info(f"Task {task_id} completion already handled by upload process, skipping additional mark_complete call")
            else:
                # Only call mark_complete if the upload process didn't handle it
                success = await mark_complete(client, task_id, result)
                if not success:
                    # If we can't mark the task complete, treat it as a failure to prevent stuck tasks
                    logger.error(f"Task {task_id} processed successfully but failed to save to database")
                    await mark_failed(client, task_id, "Task processed successfully but failed to save completion status to database")
                else:
                    logger.info(f"Task {task_id} completed successfully")
        except Exception as exc:
            logger.error(f"Task {task_id} failed with exception: {exc}")
            logger.error(f"Exception type: {type(exc).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            failed_success = await mark_failed(client, task_id, str(exc))
            if not failed_success:
                logger.error(f"DOUBLE FAILURE: Task {task_id} failed AND could not mark as failed in database!")
        finally:
            # Clear task context
            set_current_task(None)

    async with httpx.AsyncClient(limits=limits, timeout=20.0) as client:
        logger.info(f"[WORKER_LOOP] Starting worker loop for {worker_id} with RUN_TYPE: {RUN_TYPE}, CONCURRENCY: {CONCURRENCY}, POLL_SEC: {PARENT_POLL_SEC}")

        loop_count = 0
        while True:
            loop_count += 1

            # prune finished subtasks
            done = {t for t in active_tasks if t.done()}
            if done:
                logger.debug(f"[WORKER_LOOP] Pruned {len(done)} completed tasks")
                active_tasks -= done

            capacity = max(0, CONCURRENCY - len(active_tasks))

            # Log every 10 loops or when there's activity
            if loop_count % 10 == 1 or capacity > 0 or len(active_tasks) > 0:
                logger.info(f"[WORKER_LOOP] Loop #{loop_count}: Active tasks: {len(active_tasks)}, Capacity: {capacity}")

            # Log database logging stats periodically (every 100 loops)
            if loop_count % 100 == 0:
                db_stats = get_db_logging_stats()
                if db_stats:
                    logger.info(f"[WORKER_LOOP] 📊 Database logging stats: {db_stats}")

            if capacity > 0:
                logger.debug(f"[WORKER_LOOP] Checking for available tasks...")
                count_info = await count_tasks(client, RUN_TYPE)
                available_tasks = int(count_info.get("queued_plus_active") or 0)
                to_claim = min(capacity, available_tasks)

                logger.debug(f"[WORKER_LOOP] Task count result: {count_info}")
                logger.info(f"[WORKER_LOOP] Available tasks: {available_tasks}, Will attempt to claim: {to_claim}")

                if to_claim > 0:
                    logger.info(f"[WORKER_LOOP] Claiming {to_claim} tasks (capacity: {capacity}, available: {available_tasks})")

                    claimed_count = 0
                    for i in range(to_claim):
                        logger.debug(f"[WORKER_LOOP] Attempting to claim task {i+1}/{to_claim}")
                        claimed = await claim_next_task(client, worker_id, RUN_TYPE)
                        if not claimed:
                            logger.warning(f"[WORKER_LOOP] Failed to claim task {i+1}/{to_claim} - no tasks available despite count showing {available_tasks}")
                            break
                        claimed_count += 1
                        logger.info(f"[WORKER_LOOP] Successfully claimed task {i+1}/{to_claim}: {claimed.get('task_id')}")
                        t = asyncio.create_task(spawn_task(claimed, client))
                        active_tasks.add(t)

                    if claimed_count > 0:
                        logger.info(f"[WORKER_LOOP] Spawned {claimed_count} tasks")
                    elif available_tasks > 0:
                        logger.error(f"[WORKER_LOOP] CRITICAL: Could not claim any tasks despite {available_tasks} being available - check task filters and dependencies")
                elif available_tasks == 0:
                    logger.debug(f"[WORKER_LOOP] No tasks available to claim")
                else:
                    logger.debug(f"[WORKER_LOOP] No capacity to claim tasks")

            await asyncio.sleep(PARENT_POLL_SEC)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
