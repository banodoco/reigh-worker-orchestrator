"""Utility to configure logging for the orchestrator.

This centralises logging configuration so all modules share the same
settings and makes it easy to switch between plain-text and JSON logs
that are simple to parse by log aggregation systems.

Environment variables supported
--------------------------------
LOG_FORMAT: "json" (default) or "plain"
LOG_LEVEL:  Python logging level name (default: INFO)
LOG_FILE:   Path to write logs to a rotating file (default: ./orchestrator.log).
            Set to empty string to disable file logging.
ENABLE_DB_LOGGING: "true" to enable database logging (default: false)
DB_LOG_LEVEL: Log level for database handler (default: INFO)
"""

import logging
import os
import socket
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Optional

from orchestrator_common.logging_helpers import configure_third_party_loggers

if TYPE_CHECKING:
    from orchestrator_common.database_log_handler import DatabaseLogHandler

try:
    # `python-json-logger` is lightweight and already whitelisted in requirements.
    from pythonjsonlogger import json as jsonlogger  # type: ignore
except ImportError:  # pragma: no cover – fallback for runtime without dep
    jsonlogger = None  # type: ignore

_DB_LOG_HANDLER_ATTR = f"_{__name__.replace('.', '_')}_db_log_handler"
_ORCHESTRATOR_LOGGERS = [
    "gpu_orchestrator",
    "gpu_orchestrator.control_loop",
    "gpu_orchestrator.database",
    "gpu_orchestrator.runpod_client",
    "__main__",
]


def _set_db_log_handler(handler: Optional['DatabaseLogHandler']) -> None:
    """Persist DB handler on the root logger to avoid mutable module globals."""
    root_logger = logging.getLogger()
    if handler is None:
        if hasattr(root_logger, _DB_LOG_HANDLER_ATTR):
            delattr(root_logger, _DB_LOG_HANDLER_ATTR)
        return
    setattr(root_logger, _DB_LOG_HANDLER_ATTR, handler)


def _get_db_log_handler() -> Optional['DatabaseLogHandler']:
    """Read DB handler from the root logger storage."""
    return getattr(logging.getLogger(), _DB_LOG_HANDLER_ATTR, None)


def setup_logging(db_client=None, source_type: str = "orchestrator_gpu"):
    """Configure root logger for the orchestrator.

    This should be called once as early as possible in the main entry
    point before any other modules configure logging.
    
    Args:
        db_client: Optional DatabaseClient instance for centralized logging
        source_type: Type of source for database logs ('orchestrator_gpu' or 'orchestrator_api')
    
    Returns:
        DatabaseLogHandler instance if database logging is enabled, otherwise None
    """
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    log_format = os.getenv("LOG_FORMAT", "json").lower()

    handlers: list[logging.Handler] = []

    # Stream handler (stdout)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(_build_formatter(log_format))
    handlers.append(stream_handler)

    # File handler with rotation - DEFAULT to creating logs
    log_file = os.getenv("LOG_FILE", "./orchestrator.log")  # Default to current directory
    if log_file:  # Empty string disables file logging
        # Ensure log directory exists
        import pathlib
        log_path = pathlib.Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
        file_handler.setFormatter(_build_formatter(log_format))
        handlers.append(file_handler)

    # Apply configuration atomically via basicConfig (only takes effect once)
    logging.basicConfig(level=log_level, handlers=handlers, force=True)
    
    # Suppress noisy HTTP request logs to focus on health and process information
    configure_third_party_loggers(_ORCHESTRATOR_LOGGERS)
    
    # Add database logging handler if enabled and db_client provided
    enable_db_logging = os.getenv("ENABLE_DB_LOGGING", "false").lower() == "true"
    if enable_db_logging and db_client:
        try:
            # Try relative import first (when run as package)
            try:
                from .database_log_handler import DatabaseLogHandler
            except ImportError:
                # Fall back to absolute import (when run as script)
                from database_log_handler import DatabaseLogHandler
            
            # Generate source ID (use instance ID if set, otherwise hostname)
            source_id = os.getenv(
                "ORCHESTRATOR_INSTANCE_ID",
                f"{source_type}-{socket.gethostname()}"
            )
            
            # Get database log level
            db_log_level_str = os.getenv("DB_LOG_LEVEL", "INFO").upper()
            db_log_level = getattr(logging, db_log_level_str, logging.INFO)
            
            # Create database handler
            db_log_handler = DatabaseLogHandler(
                supabase_client=db_client.supabase,
                source_type=source_type,
                source_id=source_id,
                batch_size=int(os.getenv("DB_LOG_BATCH_SIZE", "50")),
                flush_interval=float(os.getenv("DB_LOG_FLUSH_INTERVAL", "5.0")),
                min_level=db_log_level
            )
            db_log_handler.setFormatter(_build_formatter("json"))
            
            # Add to root logger
            logging.getLogger().addHandler(db_log_handler)
            _set_db_log_handler(db_log_handler)
            
            logger = logging.getLogger(__name__)
            logger.info(f"✅ Database logging enabled: {source_id} -> Supabase")
            
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"⚠️  Failed to enable database logging: {e}")
            _set_db_log_handler(None)
    
    return _get_db_log_handler()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_db_log_handler():
    """Get the current database log handler instance."""
    return _get_db_log_handler()


def set_current_cycle(cycle_number: int):
    """Set current cycle number in database log handler for context tracking."""
    db_log_handler = _get_db_log_handler()
    if db_log_handler:
        db_log_handler.set_current_cycle(cycle_number)


def set_current_worker(worker_id: Optional[str]):
    """Set current worker ID in database log handler for context tracking."""
    db_log_handler = _get_db_log_handler()
    if db_log_handler:
        db_log_handler.set_current_worker(worker_id)


def set_current_task(task_id: Optional[str]):
    """Set current task ID in database log handler for context tracking."""
    db_log_handler = _get_db_log_handler()
    if db_log_handler:
        db_log_handler.set_current_task(task_id)


def get_db_logging_stats():
    """Get statistics from database log handler."""
    db_log_handler = _get_db_log_handler()
    if db_log_handler:
        return db_log_handler.get_stats()
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_formatter(log_format: str) -> logging.Formatter:  # pragma: no cover
    """Return a suitable Formatter instance for *log_format*."""

    if log_format == "json" and jsonlogger is not None:
        # Use JSON formatting – fields are flattened for easy parsing
        return jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    # Fallback to plain-text formatter identical to previous output
    return logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s") 
