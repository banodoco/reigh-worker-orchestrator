"""Tests for shared logging helper utilities."""

from __future__ import annotations

import logging

from orchestrator_common.logging_helpers import configure_third_party_loggers


def test_configure_third_party_loggers_sets_expected_levels() -> None:
    httpx_logger = logging.getLogger("httpx")
    requests_logger = logging.getLogger("requests")
    orch_logger = logging.getLogger("test.orchestrator")

    httpx_logger.setLevel(logging.NOTSET)
    requests_logger.setLevel(logging.NOTSET)
    orch_logger.setLevel(logging.NOTSET)

    configure_third_party_loggers(["test.orchestrator"])

    assert httpx_logger.level == logging.WARNING
    assert requests_logger.level == logging.WARNING
    assert orch_logger.level == logging.INFO


def test_configure_third_party_loggers_does_not_override_explicit_level() -> None:
    orch_logger = logging.getLogger("test.orchestrator.explicit")
    orch_logger.setLevel(logging.ERROR)

    configure_third_party_loggers(["test.orchestrator.explicit"])

    assert orch_logger.level == logging.ERROR
