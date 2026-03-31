"""Worker-focused debug output formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from scripts.debug.models import WorkerInfo


def _append_overview(lines: List[str], worker: Dict[str, Any], now: datetime) -> None:
    lines.append("\n🏷️  Overview")
    lines.append(f"   Status: {worker.get('status', 'Unknown')}")

    created_at = worker.get("created_at")
    if created_at:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age_minutes = (now - created).total_seconds() / 60
            lines.append(f"   Created: {created_at} (age: {age_minutes:.1f}m)")
        except (AttributeError, TypeError, ValueError):
            lines.append(f"   Created: {created_at}")

    last_hb = worker.get("last_heartbeat")
    if not last_hb:
        lines.append("   Last Heartbeat: None")
        return

    try:
        hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
        hb_age = (now - hb_time).total_seconds()
        if hb_age < 60:
            status = "✅ HEALTHY"
        elif hb_age < 300:
            status = "⚠️  WARNING"
        else:
            status = "❌ STALE"
        lines.append(f"   Last Heartbeat: {last_hb} ({hb_age:.0f}s ago) {status}")
    except (AttributeError, TypeError, ValueError):
        lines.append(f"   Last Heartbeat: {last_hb}")


def _append_metadata(lines: List[str], worker: Dict[str, Any]) -> Dict[str, Any]:
    metadata = worker.get("metadata", {})
    lines.append(f"   RunPod ID: {metadata.get('runpod_id', 'N/A')}")
    lines.append(f"   RAM Tier: {metadata.get('ram_tier', 'Unknown')}GB")
    return metadata


def _append_diagnostics(lines: List[str], worker: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    diagnostics = metadata.get("diagnostics", {})
    if diagnostics:
        lines.append("\n🔬 Pre-Termination Diagnostics")
        _append_vram(lines, diagnostics)
        _append_running_tasks(lines, diagnostics)
        _append_pod_status(lines, diagnostics)
        if diagnostics.get("collection_success"):
            lines.append("   ✅ Diagnostics collected successfully")
        elif "collection_error" in diagnostics:
            lines.append(f"   ❌ Collection error: {diagnostics['collection_error']}")
        return

    if worker.get("status") in ["error", "terminated"]:
        lines.append("\n🔬 Pre-Termination Diagnostics")
        lines.append("   ⚠️  No diagnostics collected")
        lines.append("   (Worker may have failed before collection was implemented)")


def _append_vram(lines: List[str], diagnostics: Dict[str, Any]) -> None:
    if "vram_total_mb" not in diagnostics:
        return
    vram_used = diagnostics.get("vram_used_mb", 0)
    vram_total = diagnostics.get("vram_total_mb", 0)
    vram_percent = diagnostics.get("vram_usage_percent", 0)
    lines.append(f"   VRAM: {vram_used}/{vram_total} MB ({vram_percent:.1f}%)")
    vram_ts = diagnostics.get("vram_timestamp", "")
    if not vram_ts:
        return
    if isinstance(vram_ts, (int, float)):
        try:
            vram_dt = datetime.fromtimestamp(float(vram_ts), tz=timezone.utc)
            lines.append(f"   VRAM Timestamp: {vram_dt.isoformat(timespec='seconds')}")
        except (TypeError, ValueError):
            lines.append(f"   VRAM Timestamp: {vram_ts}")
        return
    lines.append(f"   VRAM Timestamp: {str(vram_ts)[:19]}")


def _append_running_tasks(lines: List[str], diagnostics: Dict[str, Any]) -> None:
    running_tasks = diagnostics.get("running_tasks", [])
    running_count = diagnostics.get("running_tasks_count", 0)
    if running_count <= 0:
        lines.append("   Running Tasks: 0")
        return
    lines.append(f"   Running Tasks: {running_count}")
    for i, task in enumerate(running_tasks[:3], 1):
        task_id = str(task.get("id", "unknown"))[:8]
        task_type = task.get("task_type", "unknown")
        age = task.get("age_seconds", 0)
        lines.append(f"     {i}. {task_id}... ({task_type}) - {age:.0f}s old")


def _append_pod_status(lines: List[str], diagnostics: Dict[str, Any]) -> None:
    pod_status = diagnostics.get("pod_status", {})
    if not pod_status:
        return
    desired = pod_status.get("desired_status", "N/A")
    actual = pod_status.get("actual_status", "N/A")
    uptime = pod_status.get("uptime_seconds", 0)
    cost = pod_status.get("cost_per_hr", 0)
    lines.append(f"   Pod Status: {desired} / {actual}")
    lines.append(f"   Pod Uptime: {uptime}s (${cost:.3f}/hr)")


def _append_timeline(lines: List[str], logs: List[Dict[str, Any]]) -> None:
    if not logs:
        lines.append("\n📜 Event Timeline")
        lines.append("   No logs found for this worker")
        return

    lines.append("\n📜 Event Timeline (from system_logs)")
    lines.append(f"   Found {len(logs)} log entries")
    lines.append("")

    for log in logs[:50]:
        timestamp = log["timestamp"][11:19] if len(log["timestamp"]) >= 19 else log["timestamp"]
        level = log["log_level"]
        message = log["message"][:80]
        level_symbol = {
            "ERROR": "❌",
            "WARNING": "⚠️",
            "INFO": "ℹ️",
            "DEBUG": "🔍",
        }.get(level, "  ")
        lines.append(f"   [{timestamp}] {level_symbol} [{level:8}] {message}")

    if len(logs) > 50:
        lines.append(f"\n   ... and {len(logs) - 50} more log entries")


def _append_recent_tasks(lines: List[str], tasks: List[Dict[str, Any]]) -> None:
    if not tasks:
        lines.append("\n📋 Tasks Processed")
        lines.append("   No tasks assigned")
        return
    lines.append("\n📋 Tasks Processed (Recent 20)")
    for task in tasks[:20]:
        task_id = task["id"][:8]
        task_type = task.get("task_type", "unknown")[:25]
        status = task.get("status", "unknown")
        lines.append(f"   {task_id}... | {task_type:25} | {status}")


def _append_issues(lines: List[str], logs: List[Dict[str, Any]]) -> None:
    error_logs = [log for log in logs if log["log_level"] == "ERROR"]
    if not error_logs:
        return
    lines.append("\n❌ Issues Detected")
    lines.append(f"   Error logs: {len(error_logs)}")
    for err in error_logs[:3]:
        lines.append(f"   - {err['message'][:80]}")


def format_worker_text(info: WorkerInfo) -> str:
    """Format worker info as human-readable text."""
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(f"👷 WORKER: {info.worker_id}")
    lines.append("=" * 80)

    if not info.state:
        lines.append("\n❌ Worker not found in database")
        return "\n".join(lines)

    worker = info.state
    now = datetime.now(timezone.utc)
    _append_overview(lines, worker, now)
    metadata = _append_metadata(lines, worker)
    _append_diagnostics(lines, worker, metadata)
    _append_timeline(lines, info.logs)
    _append_recent_tasks(lines, info.tasks)
    _append_issues(lines, info.logs)

    lines.append("\n💡 Related Resources")
    lines.append(f"   All logs: python scripts/query_logs.py --worker {info.worker_id}")
    if info.tasks:
        lines.append(f"   Tasks: python scripts/debug.py tasks --worker {info.worker_id}")
    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def format_worker_logs_only_text(info: WorkerInfo) -> str:
    """Format only worker logs timeline."""
    lines: List[str] = []
    lines.append(f"📜 Event Timeline for Worker: {info.worker_id}")
    lines.append("=" * 80)
    if not info.logs:
        lines.append("No logs found")
        return "\n".join(lines)

    for log in info.logs:
        timestamp = log["timestamp"][11:19] if len(log["timestamp"]) >= 19 else log["timestamp"]
        level = log["log_level"]
        message = log["message"]
        lines.append(f"[{timestamp}] [{level:8}] {message}")
    return "\n".join(lines)


__all__ = ["format_worker_logs_only_text", "format_worker_text"]
