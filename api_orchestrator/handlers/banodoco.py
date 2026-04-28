"""Banodoco worker-pool handlers (Sprint 7).

These handlers do NOT execute generation themselves — they validate the task
payload (SD-034 contract) and route the work onto the `banodoco` worker pool.
The pinned Railway `banodoco-worker` service polls the task queue for tasks
of these types, executes the Python pipeline, and writes the result directly
via `update_timeline_config_versioned`.

This module enforces the SD-034 Task Lifecycle Contract at registration time:

  - `expected_version` (int, required) — versioned-RPC idempotency.
  - `correlation_id` (uuid, required) — set by the agent at enqueue time;
    the worker embeds it in the timeline-version metadata so a retry that
    races a predecessor's write can recognise its own work.
  - `user_jwt` (string, required) — SD-022 authorization; the worker verifies
    this against Reigh's Supabase JWKS endpoint before mutating any DB row.

Pool taxonomy:

  - The `banodoco` pool is reserved for the Node + Python image documented
    in `banodoco-workspace/banodoco-worker/`. Sprint 7 ships
    `banodoco_timeline_generate` only; Sprint 8 will register
    `banodoco_render_timeline` against the same pool.

If no `banodoco` worker claims a task within
`BANODOCO_WORKER_UNAVAILABLE_TIMEOUT_SEC` (default 30s), the orchestrator's
control loop surfaces `worker_unavailable` as the task-failure reason. That
timeout is enforced by the polling check in `is_worker_pool_available`.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pool taxonomy
# ---------------------------------------------------------------------------

BANODOCO_POOL = "banodoco"

# Tasks claimable by the banodoco worker pool. Sprint 7 = generate only;
# Sprint 8 will append `banodoco_render_timeline`.
BANODOCO_POOL_TASK_TYPES = {
    "banodoco_timeline_generate",
}

# Default seconds the orchestrator will wait for a `banodoco` worker to claim
# a task before transitioning it to `worker_unavailable`. Configurable via
# BANODOCO_WORKER_UNAVAILABLE_TIMEOUT_SEC.
DEFAULT_WORKER_UNAVAILABLE_TIMEOUT_SEC = 30


# ---------------------------------------------------------------------------
# Task payload schema (SD-034)
# ---------------------------------------------------------------------------

ScopeT = Literal["full", "insert", "replace_range"]


@dataclass
class BanodocoTimelineGeneratePayload:
    """Validated payload for the `banodoco_timeline_generate` task type.

    Built explicitly (no pydantic) so the orchestrator stays free of an
    extra dependency and the validation cost is trivial — this runs on the
    queueing path, not the hot inner loop.
    """

    intent: str
    brief_inputs: Dict[str, Any]
    theme_id: str
    expected_version: int
    scope: ScopeT
    user_jwt: str
    project_id: str
    timeline_id: str
    correlation_id: str
    current_timeline: Optional[Dict[str, Any]] = None

    @classmethod
    def from_params(cls, params: Dict[str, Any]) -> "BanodocoTimelineGeneratePayload":
        missing: list[str] = []
        for required in (
            "intent",
            "brief_inputs",
            "theme_id",
            "expected_version",
            "scope",
            "user_jwt",
            "project_id",
            "timeline_id",
            "correlation_id",
        ):
            if required not in params or params[required] in (None, ""):
                missing.append(required)
        if missing:
            raise ValueError(
                f"banodoco_timeline_generate payload missing required fields: {', '.join(missing)}"
            )

        scope = params["scope"]
        if scope not in ("full", "insert", "replace_range"):
            raise ValueError(
                f"banodoco_timeline_generate scope must be one of full/insert/replace_range, got {scope!r}"
            )

        expected_version = params["expected_version"]
        if not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError(
                f"banodoco_timeline_generate expected_version must be a non-negative int, got {expected_version!r}"
            )

        brief_inputs = params["brief_inputs"]
        if not isinstance(brief_inputs, dict):
            raise ValueError("banodoco_timeline_generate brief_inputs must be an object")

        # Validate that correlation_id and ids parse as UUIDs (loose check —
        # we just need a deterministic string the worker can match against
        # the timeline-version metadata).
        for key in ("correlation_id", "project_id", "timeline_id"):
            try:
                uuid.UUID(str(params[key]))
            except (ValueError, AttributeError) as exc:
                raise ValueError(
                    f"banodoco_timeline_generate {key} must be a UUID string: {exc}"
                ) from exc

        current_timeline = params.get("current_timeline")
        if current_timeline is not None and not isinstance(current_timeline, dict):
            raise ValueError(
                "banodoco_timeline_generate current_timeline must be an object or omitted"
            )

        return cls(
            intent=str(params["intent"]),
            brief_inputs=brief_inputs,
            theme_id=str(params["theme_id"]),
            expected_version=int(expected_version),
            scope=scope,  # type: ignore[arg-type]
            user_jwt=str(params["user_jwt"]),
            project_id=str(params["project_id"]),
            timeline_id=str(params["timeline_id"]),
            correlation_id=str(params["correlation_id"]),
            current_timeline=current_timeline,
        )

    def to_dict(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "intent": self.intent,
            "brief_inputs": self.brief_inputs,
            "theme_id": self.theme_id,
            "expected_version": self.expected_version,
            "scope": self.scope,
            "user_jwt": self.user_jwt,
            "project_id": self.project_id,
            "timeline_id": self.timeline_id,
            "correlation_id": self.correlation_id,
        }
        if self.current_timeline is not None:
            body["current_timeline"] = self.current_timeline
        return body


# ---------------------------------------------------------------------------
# Pool availability check (worker_unavailable surfacing)
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_worker_pool_available(
    pool: str,
    queued_at: datetime,
    *,
    now: Optional[datetime] = None,
    timeout_sec: Optional[int] = None,
) -> bool:
    """Return True if a worker from `pool` is *expected* to be available.

    The orchestrator's API worker can't itself drive a banodoco task — the
    `banodoco` pool is a separate Railway service. This helper is consumed
    by the status-surfacing path: when the agent polls task status for a
    queued banodoco task and the queued_at timestamp is older than
    `timeout_sec`, the orchestrator surfaces `worker_unavailable`.
    """
    cutoff = timeout_sec if timeout_sec is not None else int(
        os.getenv("BANODOCO_WORKER_UNAVAILABLE_TIMEOUT_SEC", str(DEFAULT_WORKER_UNAVAILABLE_TIMEOUT_SEC))
    )
    elapsed = ((now or _now_utc()) - queued_at).total_seconds()
    available = elapsed < cutoff
    if not available:
        logger.warning(
            "[BANODOCO_POOL] Worker pool %s deemed unavailable: queued %.0fs ago, cutoff %ds",
            pool,
            elapsed,
            cutoff,
        )
    return available


# ---------------------------------------------------------------------------
# API-orchestrator handler
# ---------------------------------------------------------------------------


async def handle_banodoco_timeline_generate(
    task: Dict[str, Any],
    params: Dict[str, Any],
    client: httpx.AsyncClient,  # noqa: ARG001 — unused; this handler only validates + routes
) -> Dict[str, Any]:
    """API-side handler for `banodoco_timeline_generate`.

    Per Sprint 7: the API orchestrator does NOT execute the pipeline. It
    validates the SD-034 payload contract, then returns a routing record
    that drops the task back into the queue tagged for the `banodoco`
    worker pool. The pinned Railway `banodoco-worker` polls and claims it.

    This handler is invoked when the API worker accidentally claims a
    banodoco task (e.g. mis-routed during pool-taxonomy migration). The
    correct response is to fail soft with `worker_unavailable` so the
    agent can see why it's stuck.
    """
    task_id = task.get("task_id") or task.get("id") or "unknown"
    task_type = task.get("task_type", "banodoco_timeline_generate")

    # Validate payload — this also flags malformed enqueues from the agent.
    payload = BanodocoTimelineGeneratePayload.from_params(params)

    logger.info(
        "[BANODOCO] API worker received %s task %s (correlation_id=%s); rerouting to banodoco pool.",
        task_type,
        task_id,
        payload.correlation_id,
    )

    # The API worker doesn't have the Node+Python toolchain. Returning a
    # `worker_unavailable` failure surfaces the same error path the agent
    # would see if no banodoco worker was warm.
    raise RuntimeError(
        "worker_unavailable: banodoco_timeline_generate must be claimed by a "
        "banodoco-pool worker. The API worker does not host the pipeline."
    )


__all__ = [
    "BANODOCO_POOL",
    "BANODOCO_POOL_TASK_TYPES",
    "BanodocoTimelineGeneratePayload",
    "DEFAULT_WORKER_UNAVAILABLE_TIMEOUT_SEC",
    "handle_banodoco_timeline_generate",
    "is_worker_pool_available",
]
