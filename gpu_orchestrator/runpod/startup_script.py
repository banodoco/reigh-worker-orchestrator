"""Template-backed worker startup script builders."""

from __future__ import annotations

import shlex
import json
import os
from pathlib import Path
from textwrap import dedent

_TEMPLATE_PATH = Path(__file__).with_name("worker_startup.template.sh")
_TEMPLATE_BODY = _TEMPLATE_PATH.read_text(encoding="utf-8")

_WORKDIR_DISCOVERY_SNIPPET = dedent(
    """
    WORKSPACE_DIR="/workspace"
    PRIMARY_DIR="$WORKSPACE_DIR/Reigh-Worker"
    FALLBACK_DIR="$WORKSPACE_DIR/Headless-Wan2GP"
    if [ -d "$PRIMARY_DIR" ]; then
        WORKDIR="$PRIMARY_DIR"
    elif [ -d "$FALLBACK_DIR" ]; then
        WORKDIR="$FALLBACK_DIR"
    else
        WORKDIR="$PRIMARY_DIR"
    fi
    """
).strip()

STARTUP_PHASE_SEQUENCE = ("deps_installing", "deps_verified", "worker_starting", "ready")
STRICT_STARTUP_PHASE = STARTUP_PHASE_SEQUENCE[0]
STRICT_STARTUP_PHASE_ATTEMPTS = 3
DEFAULT_WARM_CACHE_MODEL_BY_ROUTE = {
    ("wgp", "1"): "wan_2_2_i2v_lightning_baseline_2_2_2",
}


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
    worker_backend: str = "wgp",
    worker_profile: str = "1",
    worker_pool: str = "gpu-wgp-production",
    selector_namespace: str = "production",
    selector_version: str | None = None,
    worker_contract_version: int = 1,
    worker_run_id: str | None = None,
) -> str:
    warm_cache = _resolve_startup_warm_cache(
        has_pending_tasks=has_pending_tasks,
        worker_backend=worker_backend,
        worker_profile=worker_profile,
    )
    preload_flag = f"--preload-model {shlex.quote(warm_cache['preload_model'])}" if warm_cache["preload_model"] else ""
    preload_mode = warm_cache["mode"]

    replacements = {
        "__WORKER_ID__": _escape_for_double_quotes(worker_id),
        "__SUPABASE_URL__": _escape_for_double_quotes(supabase_url),
        "__SUPABASE_ANON_KEY__": _escape_for_double_quotes(supabase_anon_key),
        "__SUPABASE_SERVICE_KEY__": _escape_for_double_quotes(supabase_service_key),
        "__REPLICATE_API_TOKEN__": _escape_for_double_quotes(replicate_api_token),
        "__MAX_TASK_WAIT_MINUTES__": _escape_for_double_quotes(str(max_task_wait_minutes)),
        "__WORKER_BACKEND__": _escape_for_double_quotes(worker_backend),
        "__WORKER_PROFILE__": _escape_for_double_quotes(worker_profile),
        "__WORKER_POOL__": _escape_for_double_quotes(worker_pool),
        "__SELECTOR_NAMESPACE__": _escape_for_double_quotes(selector_namespace),
        "__SELECTOR_VERSION__": _escape_for_double_quotes(selector_version or ""),
        "__WORKER_CONTRACT_VERSION__": _escape_for_double_quotes(str(worker_contract_version)),
        "__WORKER_RUN_ID__": _escape_for_double_quotes(worker_run_id or ""),
        "__WARM_CACHE_PRELOAD_MODEL__": _escape_for_double_quotes(warm_cache["preload_model"]),
        "__WARM_CACHE_SOURCE__": _escape_for_double_quotes(warm_cache["source"]),
        "__WARM_CACHE_SKIP_REASON__": _escape_for_double_quotes(warm_cache["skip_reason"]),
        "__PRELOAD_FLAG__": _escape_for_double_quotes(preload_flag),
        "__PRELOAD_MODE__": _escape_for_double_quotes(preload_mode),
    }

    script = _TEMPLATE_BODY
    for placeholder, value in replacements.items():
        script = script.replace(placeholder, value)

    return script


def _resolve_startup_warm_cache(
    *,
    has_pending_tasks: bool,
    worker_backend: str,
    worker_profile: str,
) -> dict[str, str]:
    if has_pending_tasks:
        return {
            "preload_model": "",
            "source": "pending_task_guard",
            "skip_reason": "pending_tasks",
            "mode": "NO (tasks pending)",
        }
    direct_env = os.environ.get("REIGH_WARM_CACHE_PRELOAD_MODEL", "").strip()
    if direct_env:
        return {"preload_model": direct_env, "source": "env", "skip_reason": "", "mode": "YES (env)"}

    manifest_model = _warm_cache_manifest_model(worker_backend=worker_backend, worker_profile=worker_profile)
    if manifest_model:
        return {"preload_model": manifest_model, "source": "manifest", "skip_reason": "", "mode": "YES (manifest)"}

    default_model = DEFAULT_WARM_CACHE_MODEL_BY_ROUTE.get((worker_backend.lower(), str(worker_profile)), "")
    if default_model:
        return {"preload_model": default_model, "source": "default", "skip_reason": "", "mode": "YES (default)"}
    return {"preload_model": "", "source": "default", "skip_reason": "no_matching_model", "mode": "NO (no matching warm-cache model)"}


def _warm_cache_manifest_model(*, worker_backend: str, worker_profile: str) -> str:
    inline = os.environ.get("REIGH_WARM_CACHE_CONFIG", "").strip()
    data = None
    if inline:
        try:
            data = json.loads(inline)
        except json.JSONDecodeError:
            data = None
    manifest_path = os.environ.get("REIGH_WARM_CACHE_MANIFEST", "").strip()
    if data is None and manifest_path:
        try:
            data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if not isinstance(data, dict):
        return ""
    routes = data.get("routes")
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, dict):
                continue
            if str(route.get("backend", "")).lower() != worker_backend.lower():
                continue
            if str(route.get("profile", "")) != str(worker_profile):
                continue
            return str(route.get("preload_model") or "").strip()
    by_backend = data.get(worker_backend)
    if isinstance(by_backend, dict):
        return str(by_backend.get(str(worker_profile)) or by_backend.get("default") or "").strip()
    return ""


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
    "STARTUP_PHASE_SEQUENCE",
    "STRICT_STARTUP_PHASE",
    "STRICT_STARTUP_PHASE_ATTEMPTS",
    "build_create_script_command",
    "build_launch_command",
    "build_log_retrieval_command",
    "build_startup_status_check_command",
    "render_startup_script",
]
