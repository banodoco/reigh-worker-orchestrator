"""Task-focused debug output formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from scripts.debug.models import TaskInfo


def _extract_lora_urls(params: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    if not params:
        return urls

    phase_config = params.get("phase_config", {})
    if phase_config and isinstance(phase_config, dict):
        for phase in phase_config.get("phases", []):
            if not isinstance(phase, dict):
                continue
            for lora in phase.get("loras", []):
                if isinstance(lora, dict) and "url" in lora:
                    url = lora["url"]
                    if url not in urls:
                        urls.append(url)

    additional_loras = params.get("additional_loras", {})
    if additional_loras and isinstance(additional_loras, dict):
        for url in additional_loras.keys():
            if url not in urls:
                urls.append(url)

    return urls


def _append_timing(lines: List[str], task: Dict[str, Any]) -> None:
    lines.append("\n⏱️  Timing")
    created_at = task.get("created_at")
    started_at = task.get("generation_started_at")
    processed_at = task.get("generation_processed_at")

    if not created_at:
        return

    lines.append(f"   Created: {created_at}")
    if not started_at:
        lines.append("   ⚠️  Never started")
        return

    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        queue_seconds = (started - created).total_seconds()
        lines.append(f"   Started: {started_at} (queue: {queue_seconds:.1f}s)")
    except (AttributeError, TypeError, ValueError) as exc:
        lines.append(f"   Error parsing timestamps: {exc}")
        return

    if processed_at:
        try:
            processed = datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
            processing_seconds = (processed - started).total_seconds()
            total_seconds = (processed - created).total_seconds()
            lines.append(f"   Processed: {processed_at} (processing: {processing_seconds:.1f}s)")
            lines.append(f"   Total: {total_seconds:.1f}s")
        except (AttributeError, TypeError, ValueError) as exc:
            lines.append(f"   Error parsing processed timestamp: {exc}")
        return

    running_seconds = (datetime.now(timezone.utc) - started).total_seconds()
    lines.append(f"   ⚠️  Never processed (running: {running_seconds:.1f}s)")


def _append_timeline(lines: List[str], logs: List[Dict[str, Any]]) -> None:
    if not logs:
        lines.append("\n📜 Event Timeline")
        lines.append("   No logs found for this task")
        return

    lines.append("\n📜 Event Timeline (from system_logs)")
    lines.append(f"   Found {len(logs)} log entries")
    lines.append("")

    for log in logs[:50]:
        timestamp = log["timestamp"][11:19] if len(log["timestamp"]) >= 19 else log["timestamp"]
        level = log["log_level"]
        source = log.get("source_id", "unknown")[:20]
        message = log["message"][:100]
        level_symbol = {
            "ERROR": "❌",
            "WARNING": "⚠️",
            "INFO": "ℹ️",
            "DEBUG": "🔍",
            "CRITICAL": "🔥",
        }.get(level, "  ")
        lines.append(f"   [{timestamp}] {level_symbol} [{level:8}] [{source:20}] {message}")

    if len(logs) > 50:
        lines.append(f"\n   ... and {len(logs) - 50} more log entries")


def _append_params(lines: List[str], params: Any) -> None:
    if not isinstance(params, dict) or not params:
        return
    lines.append("\n📝 Parameters")
    for key, value in list(params.items())[:10]:
        value_str = str(value)
        if len(value_str) > 100:
            value_str = value_str[:100] + "..."
        lines.append(f"   {key}: {value_str}")
    if len(params) > 10:
        lines.append(f"   ... and {len(params) - 10} more parameters")


def _append_error_context(lines: List[str], logs: List[Dict[str, Any]]) -> None:
    error_logs = [log for log in logs if log["log_level"] in ["ERROR", "WARNING"]]
    if not error_logs:
        return
    lines.append("\n🔍 Error Context (all ERROR/WARNING logs):")
    for err in error_logs[-5:]:
        timestamp = err["timestamp"][11:19]
        level = err["log_level"]
        msg = err["message"][:120]
        lines.append(f"   [{timestamp}] {level}: {msg}")


def _append_error_analysis(
    lines: List[str],
    task: Dict[str, Any],
    logs: List[Dict[str, Any]],
) -> None:
    error_msg = task.get("error_message")
    output_location = task.get("output_location")
    params = task.get("params")

    if error_msg:
        lines.append("\n❌ Error Analysis")
        lines.append(f"   Error: {error_msg}")
        error_logs = [log for log in logs if log["log_level"] == "ERROR"]
        if error_logs:
            lines.append(f"   Last error log: {error_logs[-1]['message'][:100]}")
        return

    if not output_location:
        return

    if "failed" not in output_location.lower():
        lines.append("\n✅ Success")
        lines.append(f"   Output: {output_location}")
        return

    lines.append("\n❌ Error Analysis")
    lines.append(f"   Output (contains error): {output_location}")
    if output_location.endswith(": ") or output_location.endswith(":"):
        lines.append("   ⚠️  ERROR MESSAGE APPEARS INCOMPLETE - missing filename or details")

    if "safetensors" in output_location.lower() or "lora" in output_location.lower():
        lines.append("\n📎 LoRA URLs in Configuration:")
        lora_urls = _extract_lora_urls(params if isinstance(params, dict) else {})
        if lora_urls:
            for i, url in enumerate(lora_urls, 1):
                lines.append(f"   {i}. {url}")
        else:
            lines.append("   No LoRAs found in phase_config")

    _append_error_context(lines, logs)


def format_task_text(info: TaskInfo) -> str:
    """Format task info as human-readable text."""
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(f"📋 TASK: {info.task_id}")
    lines.append("=" * 80)

    if not info.state:
        lines.append("\n❌ Task not found in database")
        return "\n".join(lines)

    task = info.state
    lines.append("\n🏷️  Overview")
    lines.append(f"   Status: {task.get('status', 'Unknown')}")
    lines.append(f"   Type: {task.get('task_type', 'Unknown')}")
    lines.append(f"   Worker: {task.get('worker_id', 'None')}")
    lines.append(f"   Attempts: {task.get('attempts', 0)}")

    _append_timing(lines, task)
    _append_timeline(lines, info.logs)
    _append_params(lines, task.get("params"))
    _append_error_analysis(lines, task, info.logs)

    lines.append("\n💡 Related Resources")
    if task.get("worker_id"):
        lines.append(f"   Worker: python scripts/debug.py worker {task['worker_id']}")
    lines.append(f"   All logs: python scripts/query_logs.py --task {info.task_id}")
    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def format_task_logs_only_text(info: TaskInfo) -> str:
    """Format only the task logs timeline."""
    lines: List[str] = []
    lines.append(f"📜 Event Timeline for Task: {info.task_id}")
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


__all__ = ["format_task_logs_only_text", "format_task_text"]
