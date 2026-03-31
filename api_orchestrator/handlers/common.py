"""Shared fal request tracking helpers for task handlers."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from api_orchestrator.fal_utils import FalRequestTracking
from api_orchestrator.task_utils import get_task_metadata, update_task_metadata

logger = logging.getLogger(__name__)


async def get_fal_tracking(task_id: str, step_key: Optional[str] = None) -> Optional[FalRequestTracking]:
    """Get existing fal.ai tracking data from task metadata."""
    try:
        metadata = await get_task_metadata(task_id)
        if not metadata:
            return None

        if step_key and f"fal_{step_key}" in metadata:
            step_data = metadata[f"fal_{step_key}"]
            return FalRequestTracking.from_dict(step_data)

        return FalRequestTracking.from_dict(metadata)
    except Exception as exc:
        logger.warning("Could not get fal.ai tracking for task %s: %s", task_id, exc)
        return None


async def update_fal_tracking(task_id: str, tracking_data: Dict[str, Any], step_key: Optional[str] = None) -> bool:
    """Update task metadata with fal.ai tracking data."""
    if step_key:
        tracking_data = {f"fal_{step_key}": tracking_data}
    return await update_task_metadata(task_id, tracking_data)


__all__ = ["get_fal_tracking", "update_fal_tracking"]
