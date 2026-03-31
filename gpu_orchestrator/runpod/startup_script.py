"""Template-backed worker startup script builders."""

from __future__ import annotations

import shlex
from pathlib import Path
from textwrap import dedent

_TEMPLATE_PATH = Path(__file__).with_name("worker_startup.template.sh")
_TEMPLATE_BODY = _TEMPLATE_PATH.read_text(encoding="utf-8")

_WORKDIR_DISCOVERY_SNIPPET = dedent(
    """
    WORKSPACE_DIR="/workspace"
    PRIMARY_DIR="$WORKSPACE_DIR/Headless-Wan2GP"
    FALLBACK_DIR="$WORKSPACE_DIR/Reigh-Worker"
    if [ -d "$PRIMARY_DIR" ]; then
        WORKDIR="$PRIMARY_DIR"
    elif [ -d "$FALLBACK_DIR" ]; then
        WORKDIR="$FALLBACK_DIR"
    else
        WORKDIR="$PRIMARY_DIR"
    fi
    """
).strip()


def _escape_for_double_quotes(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("$", "\\$").replace("`", "\\`")
    return escaped


def render_startup_script(
    worker_id: str,
    supabase_url: str,
    supabase_anon_key: str,
    supabase_service_key: str,
    replicate_api_token: str,
    max_task_wait_minutes: int,
    has_pending_tasks: bool,
) -> str:
    preload_flag = "" if has_pending_tasks else "--preload-model wan_2_2_i2v_lightning_baseline_2_2_2"
    preload_mode = "NO (tasks pending)" if has_pending_tasks else "YES (no tasks pending)"

    replacements = {
        "__WORKER_ID__": _escape_for_double_quotes(worker_id),
        "__SUPABASE_URL__": _escape_for_double_quotes(supabase_url),
        "__SUPABASE_ANON_KEY__": _escape_for_double_quotes(supabase_anon_key),
        "__SUPABASE_SERVICE_KEY__": _escape_for_double_quotes(supabase_service_key),
        "__REPLICATE_API_TOKEN__": _escape_for_double_quotes(replicate_api_token),
        "__MAX_TASK_WAIT_MINUTES__": _escape_for_double_quotes(str(max_task_wait_minutes)),
        "__PRELOAD_FLAG__": _escape_for_double_quotes(preload_flag),
        "__PRELOAD_MODE__": _escape_for_double_quotes(preload_mode),
    }

    script = _TEMPLATE_BODY
    for placeholder, value in replacements.items():
        script = script.replace(placeholder, value)

    return script


def build_create_script_command(script_path: str, script_content: str) -> str:
    quoted_path = shlex.quote(script_path)
    return f"cat > {quoted_path} << 'SCRIPT_EOF'\n{script_content}\nSCRIPT_EOF"


def build_launch_command(script_path: str, worker_id: str) -> str:
    quoted_script = shlex.quote(script_path)
    return dedent(
        f"""
        {_WORKDIR_DISCOVERY_SNIPPET}
        mkdir -p "$WORKDIR/logs"
        chmod +x {quoted_script}
        nohup {quoted_script} > "$WORKDIR/logs/{worker_id}_startup.log" 2>&1 &
        echo $!
        """
    ).strip()


def build_log_retrieval_command(worker_id: str) -> str:
    return dedent(
        f"""
        {_WORKDIR_DISCOVERY_SNIPPET}
        if [ -f "$WORKDIR/logs/gpu_{worker_id}.log" ]; then
            echo "=== WORKER STARTUP LOG ==="
            tail -100 "$WORKDIR/logs/gpu_{worker_id}.log"
        fi
        if [ -f "$WORKDIR/logs/{worker_id}_startup.log" ]; then
            echo "=== INITIALIZATION LOG ==="
            tail -200 "$WORKDIR/logs/{worker_id}_startup.log"
        fi
        """
    ).strip()


def build_startup_status_check_command(worker_id: str) -> str:
    return dedent(
        f"""
        echo "=== DISK SPACE ==="
        df -h / /tmp /var 2>/dev/null | head -10
        echo ""
        {_WORKDIR_DISCOVERY_SNIPPET}
        if [ -f "$WORKDIR/logs/{worker_id}_startup.log" ]; then
            echo "=== STARTUP LOG ERRORS ==="
            tail -50 "$WORKDIR/logs/{worker_id}_startup.log" | grep -i "error\\|fail\\|exception\\|no space" || echo "No errors found in recent logs"
        else
            echo "=== STARTUP LOG ==="
            echo "No startup log file yet"
        fi
        """
    ).strip()


__all__ = [
    "build_create_script_command",
    "build_launch_command",
    "build_log_retrieval_command",
    "build_startup_status_check_command",
    "render_startup_script",
]
