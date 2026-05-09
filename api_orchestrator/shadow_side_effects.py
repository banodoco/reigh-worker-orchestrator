"""Shadow-mode side-effect recording for dual-run comparison.

This module is inert unless REIGH_DUAL_RUN_SHADOW is explicitly enabled.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


SHADOW_MODE_ENV = "REIGH_DUAL_RUN_SHADOW"
SHADOW_RECORD_PATH_ENV = "REIGH_DUAL_RUN_SHADOW_RECORD_PATH"

_SHADOW_RECORDS: list[dict[str, Any]] = []


def is_shadow_mode() -> bool:
    return os.getenv(SHADOW_MODE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def clear_shadow_side_effects() -> None:
    _SHADOW_RECORDS.clear()


def get_shadow_side_effects() -> list[dict[str, Any]]:
    return list(_SHADOW_RECORDS)


def _safe_payload(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"file_data", "first_frame_data"}:
            safe[key] = f"<redacted:{len(str(value))} chars>"
        elif isinstance(value, bytes):
            safe[key] = f"<redacted:{len(value)} bytes>"
        else:
            safe[key] = value
    return safe


def record_shadow_side_effect(
    *,
    effect: str,
    operation: str,
    task_id: str | None = None,
    endpoint: str | None = None,
    payload: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record = {
        "effect": effect,
        "operation": operation,
        "status": "blocked",
        "reason": "explicit shadow mode records intended request without calling production API",
    }
    if task_id:
        record["task_id"] = task_id
    if endpoint:
        record["endpoint"] = endpoint
    safe_payload = _safe_payload(payload)
    if safe_payload is not None:
        record["payload"] = safe_payload
    if metadata:
        record["metadata"] = dict(metadata)

    _SHADOW_RECORDS.append(record)

    record_path = os.getenv(SHADOW_RECORD_PATH_ENV)
    if record_path:
        path = Path(record_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    return record


def shadow_public_url(task_id: str, filename: str, *, operation: str) -> str:
    safe_filename = filename.replace("/", "_").replace("\\", "_")
    return f"shadow://{operation}/{task_id}/{safe_filename}"
