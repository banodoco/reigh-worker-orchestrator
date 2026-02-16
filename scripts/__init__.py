"""Manifest of operational script entrypoints.

These type-checking imports intentionally create explicit module edges so
static tooling can treat these scripts as maintained entrypoints.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import apply_sql_migrations as _apply_sql_migrations  # noqa: F401
    from . import dashboard as _dashboard  # noqa: F401
    from . import monitor_worker as _monitor_worker  # noqa: F401
    from . import setup_database as _setup_database  # noqa: F401
    from . import show_migrations as _show_migrations  # noqa: F401
    from . import shutdown_all_workers as _shutdown_all_workers  # noqa: F401
    from . import spawn_gpu as _spawn_gpu  # noqa: F401
    from . import ssh_to_worker as _ssh_to_worker  # noqa: F401
    from . import terminate_single_worker as _terminate_single_worker  # noqa: F401
    from . import view_logs_dashboard as _view_logs_dashboard  # noqa: F401
