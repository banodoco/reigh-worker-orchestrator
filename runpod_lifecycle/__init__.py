"""Local shim for the sibling ``runpod-lifecycle`` checkout.

This repo cannot rely on the package being installed in CI/local development
yet, so imports resolve to the adjacent source tree when available.
"""

from __future__ import annotations

from pathlib import Path

_SOURCE_ROOT = (
    Path(__file__).resolve().parents[2] / "runpod-lifecycle" / "src" / "runpod_lifecycle"
)
_SOURCE_INIT = _SOURCE_ROOT / "__init__.py"

if not _SOURCE_INIT.exists():
    raise ModuleNotFoundError(
        "runpod_lifecycle is not installed and sibling source tree was not found at "
        f"{_SOURCE_ROOT}"
    )

__file__ = str(_SOURCE_INIT)
__path__ = [str(_SOURCE_ROOT)]

if __spec__ is not None:
    __spec__.submodule_search_locations = __path__

exec(compile(_SOURCE_INIT.read_text(), __file__, "exec"), globals(), globals())
