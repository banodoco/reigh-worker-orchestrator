"""Summary-level debug output formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from scripts.debug.models import TasksSummary, WorkersSummary


def format_tasks_summary_text(summary: TasksSummary) -> str:
    """Format tasks summary."""
    lines: List[str] = []

    lines.append("=" * 80)
    lines.append("📊 RECENT TASKS ANALYSIS")
    lines.append("=" * 80)

    lines.append("\n📈 Overview")
    lines.append(f"   Total tasks: {summary.total_count}")

    if summary.tasks:
        oldest = summary.tasks[-1].get("created_at", "")
        newest = summary.tasks[0].get("created_at", "")
        lines.append(f"   Time range: {oldest[:19]} to {newest[:19]}")

    lines.append("\n📊 Status Distribution")
    for status, count in sorted(summary.status_distribution.items()):
        percentage = (count / summary.total_count * 100) if summary.total_count > 0 else 0
        lines.append(f"   {status}: {count} ({percentage:.1f}%)")

    if summary.task_type_distribution:
        lines.append("\n🔧 Task Types")
        sorted_types = sorted(summary.task_type_distribution.items(), key=lambda x: x[1], reverse=True)
        for task_type, count in sorted_types[:10]:
            percentage = (count / summary.total_count * 100) if summary.total_count > 0 else 0
            lines.append(f"   {task_type}: {count} ({percentage:.1f}%)")

    timing = summary.timing_stats
    if timing.get("avg_processing_seconds") or timing.get("avg_queue_seconds"):
        lines.append("\n⏱️  Timing Analysis")
        if timing.get("avg_queue_seconds"):
            lines.append(f"   Avg Queue Time: {timing['avg_queue_seconds']:.1f}s")
        if timing.get("avg_processing_seconds"):
            lines.append(f"   Avg Processing Time: {timing['avg_processing_seconds']:.1f}s")
        lines.append(f"   Tasks with timing: {timing['total_with_timing']}")

    if summary.recent_failures:
        lines.append("\n❌ Recent Failures")
        for failure in summary.recent_failures[:5]:
            task_id = str(failure.get("task_id", "unknown"))[:8]
            message = failure.get("message", "Unknown error")[:60]
            lines.append(f"   {task_id}... | {message}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def format_workers_summary_text(summary: WorkersSummary) -> str:
    """Format workers summary."""
    lines: List[str] = []

    lines.append("=" * 80)
    lines.append("👷 WORKERS STATUS")
    lines.append("=" * 80)

    lines.append("\n📊 Summary")
    for status, count in sorted(summary.status_counts.items()):
        lines.append(f"   {status}: {count}")

    lines.append("\n✅ Active Workers Health")
    lines.append(f"   Healthy (HB < 60s): {summary.active_healthy}")
    lines.append(f"   Stale (HB > 60s): {summary.active_stale}")

    active_workers = [w for w in summary.workers if w["status"] == "active"]
    if active_workers:
        lines.append("\n📋 Active Workers")
        for worker in active_workers[:10]:
            worker_id = worker["id"]
            created = worker.get("created_at", "")[:19]
            last_hb = worker.get("last_heartbeat", "never")

            if last_hb != "never":
                try:
                    hb_time = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    hb_age = (now - hb_time).total_seconds()

                    if hb_age < 60:
                        status_symbol = "✅"
                    elif hb_age < 300:
                        status_symbol = "⚠️"
                    else:
                        status_symbol = "❌"

                    hb_str = f"{hb_age:.0f}s ago"
                except (AttributeError, TypeError, ValueError):
                    status_symbol = "❓"
                    hb_str = last_hb[:19]
            else:
                status_symbol = "❓"
                hb_str = "no HB"

            lines.append(f"   {status_symbol} {worker_id} | Created: {created} | HB: {hb_str}")

    if summary.recent_failures:
        lines.append(f"\n❌ Recently Failed ({len(summary.recent_failures)})")
        for failure in summary.recent_failures[:10]:
            worker_id = failure.get("worker_id", "unknown")
            reason = failure.get("error_reason", "Unknown")
            lines.append(f"   {worker_id} | {reason}")

    if summary.failure_rate is not None:
        lines.append("\n📈 Failure Rate Analysis")
        lines.append(f"   Failure rate: {summary.failure_rate:.1%}")
        lines.append("   Threshold: 80.0%")

        if summary.failure_rate > 0.8:
            lines.append("   Status: ❌ BLOCKED - spawning disabled")
        else:
            lines.append("   Status: ✅ OK - spawning allowed")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


__all__ = ["format_tasks_summary_text", "format_workers_summary_text"]
