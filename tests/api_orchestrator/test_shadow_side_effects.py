from __future__ import annotations

import asyncio
from typing import Any

import pytest

from api_orchestrator import storage_utils, task_utils
from api_orchestrator.shadow_side_effects import clear_shadow_side_effects, get_shadow_side_effects


class _ForbiddenClient:
    async def post(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("shadow mode must not call client.post")

    async def put(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("shadow mode must not call client.put")


class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    text = "{}"

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"ok": True}


class _RecordingClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> _Response:
        self.posts.append({"url": url, **kwargs})
        return _Response()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_shadow(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_shadow_side_effects()
    monkeypatch.delenv("REIGH_DUAL_RUN_SHADOW", raising=False)
    monkeypatch.delenv("REIGH_DUAL_RUN_SHADOW_RECORD_PATH", raising=False)


def test_url_only_completion_is_recorded_and_blocked_in_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REIGH_DUAL_RUN_SHADOW", "1")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")

    ok = _run(
        task_utils.mark_complete_via_edge_function(
            _ForbiddenClient(),
            "task-1",
            output_location="https://cdn.example/out.mp4",
        )
    )

    assert ok is True
    records = get_shadow_side_effects()
    assert len(records) == 1
    assert records[0]["operation"] == "update-task-status-url-complete"
    assert records[0]["endpoint"].endswith("/functions/v1/update-task-status")
    assert records[0]["payload"]["output_location"] == "https://cdn.example/out.mp4"


def test_normal_completion_behavior_still_posts_outside_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    client = _RecordingClient()

    ok = _run(task_utils.mark_complete_via_edge_function(client, "task-1", output_location=None))

    assert ok is True
    assert len(client.posts) == 1
    assert client.posts[0]["url"].endswith("/functions/v1/complete_task")
    assert client.posts[0]["json"] == {"task_id": "task-1"}
    assert get_shadow_side_effects() == []


def test_direct_complete_task_upload_is_recorded_and_blocked_in_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REIGH_DUAL_RUN_SHADOW", "true")

    url = _run(
        storage_utils._upload_direct_base64(
            _ForbiddenClient(),
            "task-2",
            b"small-file",
            "out.png",
            None,
            {"Authorization": "Bearer token"},
            "https://example.supabase.co/functions/v1/complete_task",
        )
    )

    assert url == "shadow://complete-task-direct-base64/task-2/out.png"
    records = get_shadow_side_effects()
    assert [record["operation"] for record in records] == ["complete_task-direct-base64"]
    assert records[0]["payload"]["file_data"].startswith("<redacted:")


def test_presigned_upload_chain_is_recorded_and_blocked_in_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REIGH_DUAL_RUN_SHADOW", "yes")

    url = _run(
        storage_utils._upload_presigned_url(
            _ForbiddenClient(),
            "task-3",
            b"large-file",
            "movie.mp4",
            "video/mp4",
            "thumbnail-bytes",
            {"Authorization": "Bearer token"},
            "https://example.supabase.co/functions/v1/generate-upload-url",
            "https://example.supabase.co/functions/v1/complete_task",
        )
    )

    assert url == "shadow://presigned-upload/task-3/movie.mp4"
    assert [record["operation"] for record in get_shadow_side_effects()] == [
        "generate-upload-url",
        "storage-upload",
        "storage-upload-thumbnail",
        "complete_task-post-upload",
    ]


def test_storage_only_upload_is_recorded_and_blocked_in_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REIGH_DUAL_RUN_SHADOW", "on")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")

    url = _run(
        storage_utils.upload_to_supabase_storage_only(
            _ForbiddenClient(),
            "task-4",
            b"intermediate",
            "latent.pt",
        )
    )

    assert url == "shadow://storage-upload-only/task-4/latent.pt"
    records = get_shadow_side_effects()
    assert len(records) == 1
    assert records[0]["operation"] == "storage-upload-only"
    assert records[0]["metadata"]["byte_count"] == len(b"intermediate")
