"""Runpod GPU Worker Orchestrator"""

from typing import TYPE_CHECKING

__version__ = "0.1.0"

if TYPE_CHECKING:
    from . import main as _main_entrypoint  # noqa: F401
