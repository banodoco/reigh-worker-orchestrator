"""Sprint 8 (SD-034): payload validation for banodoco_render_timeline."""

from __future__ import annotations

import uuid

import pytest

from api_orchestrator.handlers.banodoco import BanodocoRenderTimelinePayload


def _valid_params() -> dict:
    return {
        "timeline_id": str(uuid.uuid4()),
        "timeline": {"clips": [], "tracks": [], "theme": "2rp"},
        "assets": {"assets": {}},
        "theme_id": "2rp",
        "output_filename": "hype-reel.mp4",
        "user_jwt": "eyJhbGciOiJSUzI1NiIs...payload.signature",
        "project_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
    }


def test_payload_accepts_full_valid_input() -> None:
    payload = BanodocoRenderTimelinePayload.from_params(_valid_params())
    assert payload.theme_id == "2rp"
    assert payload.output_filename == "hype-reel.mp4"
    assert isinstance(payload.timeline, dict)
    assert isinstance(payload.assets, dict)


def test_payload_rejects_missing_correlation_id() -> None:
    params = _valid_params()
    del params["correlation_id"]
    with pytest.raises(ValueError) as exc:
        BanodocoRenderTimelinePayload.from_params(params)
    assert "correlation_id" in str(exc.value)


def test_payload_rejects_missing_user_jwt() -> None:
    params = _valid_params()
    del params["user_jwt"]
    with pytest.raises(ValueError) as exc:
        BanodocoRenderTimelinePayload.from_params(params)
    assert "user_jwt" in str(exc.value)


def test_payload_rejects_missing_timeline() -> None:
    params = _valid_params()
    del params["timeline"]
    with pytest.raises(ValueError) as exc:
        BanodocoRenderTimelinePayload.from_params(params)
    assert "timeline" in str(exc.value)


def test_payload_rejects_missing_output_filename() -> None:
    params = _valid_params()
    del params["output_filename"]
    with pytest.raises(ValueError) as exc:
        BanodocoRenderTimelinePayload.from_params(params)
    assert "output_filename" in str(exc.value)


def test_payload_rejects_blank_output_filename() -> None:
    params = _valid_params()
    params["output_filename"] = "   "
    with pytest.raises(ValueError):
        BanodocoRenderTimelinePayload.from_params(params)


def test_payload_rejects_non_dict_timeline() -> None:
    params = _valid_params()
    params["timeline"] = "config-as-string"
    with pytest.raises(ValueError):
        BanodocoRenderTimelinePayload.from_params(params)


def test_payload_rejects_non_dict_assets() -> None:
    params = _valid_params()
    params["assets"] = ["not", "a", "dict"]
    with pytest.raises(ValueError):
        BanodocoRenderTimelinePayload.from_params(params)


def test_payload_rejects_non_uuid_correlation_id() -> None:
    params = _valid_params()
    params["correlation_id"] = "not-a-uuid"
    with pytest.raises(ValueError) as exc:
        BanodocoRenderTimelinePayload.from_params(params)
    assert "UUID" in str(exc.value)


def test_payload_rejects_non_uuid_timeline_id() -> None:
    params = _valid_params()
    params["timeline_id"] = "not-a-uuid"
    with pytest.raises(ValueError):
        BanodocoRenderTimelinePayload.from_params(params)


def test_payload_to_dict_round_trips_required_fields() -> None:
    params = _valid_params()
    payload = BanodocoRenderTimelinePayload.from_params(params)
    body = payload.to_dict()
    for key in (
        "timeline_id",
        "timeline",
        "assets",
        "theme_id",
        "output_filename",
        "user_jwt",
        "project_id",
        "correlation_id",
    ):
        assert key in body, f"to_dict() should include {key}"
