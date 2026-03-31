"""Shared logging helper utilities."""

from __future__ import annotations

import logging
from typing import Iterable


def configure_third_party_loggers(orchestrator_loggers: Iterable[str]) -> None:
    """Reduce noise from third-party loggers and normalize orchestrator logger levels."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)

    logging.getLogger("supabase").setLevel(logging.WARNING)
    logging.getLogger("postgrest").setLevel(logging.WARNING)

    for logger_name in orchestrator_loggers:
        logger = logging.getLogger(logger_name)
        if logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)


__all__ = ["configure_third_party_loggers"]
