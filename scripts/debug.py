#!/usr/bin/env python3
"""Compatibility wrapper for the unified debug CLI."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.debug_cli import main
if __name__ == "__main__":
    main()
