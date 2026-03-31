"""API orchestrator-specific database log handler compatibility class."""

from __future__ import annotations

from orchestrator_common.database_log_handler import DatabaseLogHandler as SharedDatabaseLogHandler


class DatabaseLogHandler(SharedDatabaseLogHandler):
    """Backward-compatible API wrapper over the shared log handler."""


__all__ = ["DatabaseLogHandler"]
