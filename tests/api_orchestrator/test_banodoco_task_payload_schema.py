"""Sprint 7 (SD-034): payload validation for banodoco_timeline_generate."""

from __future__ import annotations

import uuid

import pytest

from api_orchestrator.handlers.banodoco import BanodocoTimelineGeneratePayload


def _valid_params() -> dict:
    return {
        "intent": "extend the hype reel by 15 seconds",
        "brief_inputs": {"transcript": "...", "sources": []},
        "theme_id": "2rp",
        "expected_version": 7,
        "scope": "insert",
        "user_jwt": "eyJhbGciOiJSUzI1NiIs...payload.signature",
        "project_id": str(uuid.uuid4()),
        "timeline_id": str(uuid.uuid4()),
        "correlation_id": str(uuid.uuid4()),
    }


def test_payload_accepts_full_valid_input() -> None:
    payload = BanodocoTimelineGeneratePayload.from_params(_valid_params())
    assert payload.scope == "insert"
    assert payload.expected_version == 7
    assert payload.theme_id == "2rp"
    assert payload.current_timeline is None


def test_payload_accepts_optional_current_timeline_object() -> None:
    params = _valid_params()
    params["current_timeline"] = {"clips": [], "tracks": []}
    payload = BanodocoTimelineGeneratePayload.from_params(params)
    assert payload.current_timeline == {"clips": [], "tracks": []}


def test_payload_rejects_missing_correlation_id() -> None:
    params = _valid_params()
    del params["correlation_id"]
    with pytest.raises(ValueError) as exc:
        BanodocoTimelineGeneratePayload.from_params(params)
    assert "correlation_id" in str(exc.value)


def test_payload_rejects_missing_expected_version() -> None:
    params = _valid_params()
    del params["expected_version"]
    with pytest.raises(ValueError):
        BanodocoTimelineGeneratePayload.from_params(params)


def test_payload_rejects_missing_user_jwt() -> None:
    params = _valid_params()
    del params["user_jwt"]
    with pytest.raises(ValueError) as exc:
        BanodocoTimelineGeneratePayload.from_params(params)
    assert "user_jwt" in str(exc.value)


def test_payload_rejects_invalid_scope() -> None:
    params = _valid_params()
    params["scope"] = "wholesale_replace"
    with pytest.raises(ValueError) as exc:
        BanodocoTimelineGeneratePayload.from_params(params)
    assert "scope" in str(exc.value).lower()


def test_payload_rejects_negative_expected_version() -> None:
    params = _valid_params()
    params["expected_version"] = -1
    with pytest.raises(ValueError):
        BanodocoTimelineGeneratePayload.from_params(params)


def test_payload_rejects_non_int_expected_version() -> None:
    params = _valid_params()
    params["expected_version"] = "7"
    with pytest.raises(ValueError):
        BanodocoTimelineGeneratePayload.from_params(params)


def test_payload_rejects_non_dict_brief_inputs() -> None:
    params = _valid_params()
    params["brief_inputs"] = "transcript text"
    with pytest.raises(ValueError):
        BanodocoTimelineGeneratePayload.from_params(params)


def test_payload_rejects_non_uuid_correlation_id() -> None:
    params = _valid_params()
    params["correlation_id"] = "not-a-uuid"
    with pytest.raises(ValueError) as exc:
        BanodocoTimelineGeneratePayload.from_params(params)
    assert "UUID" in str(exc.value)


def test_payload_rejects_non_dict_current_timeline() -> None:
    params = _valid_params()
    params["current_timeline"] = "config-as-string"
    with pytest.raises(ValueError):
        BanodocoTimelineGeneratePayload.from_params(params)


def test_payload_to_dict_round_trips_required_and_optional() -> None:
    params = _valid_params()
    params["current_timeline"] = {"clips": [{"id": "c1"}]}
    payload = BanodocoTimelineGeneratePayload.from_params(params)
    body = payload.to_dict()
    for key in (
        "intent",
        "brief_inputs",
        "theme_id",
        "expected_version",
        "scope",
        "user_jwt",
        "project_id",
        "timeline_id",
        "correlation_id",
        "current_timeline",
    ):
        assert key in body, f"to_dict() should include {key}"
