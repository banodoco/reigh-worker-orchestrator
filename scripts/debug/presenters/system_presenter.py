"""System and orchestrator status output formatting."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from scripts.debug.models import OrchestratorStatus, SystemHealth


def format_health_text(health: SystemHealth) -> str:
    """Format system health."""
    lines: List[str] = []

    lines.append("=" * 80)
    lines.append("🔍 SYSTEM HEALTH CHECK")
    lines.append("=" * 80)
    lines.append(f"Time: {health.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append("")

    lines.append("📊 WORKER STATUS")
    lines.append("-" * 80)
    lines.append(f"Active: {health.workers_active}")
    lines.append(f"Spawning: {health.workers_spawning}")
    lines.append(f"Healthy: {health.workers_healthy}")

    if health.workers_active == 0 and health.workers_spawning == 0:
        lines.append("⚠️  WARNING: No active or spawning workers!")
    elif health.workers_healthy > 0:
        lines.append(f"✅ {health.workers_healthy} healthy workers")

    lines.append("")
    lines.append("📝 TASK STATUS")
    lines.append("-" * 80)
    lines.append(f"Queued: {health.tasks_queued}")
    lines.append(f"In Progress: {health.tasks_in_progress}")

    if health.tasks_queued > 0 and health.workers_active == 0:
        lines.append("⚠️  Tasks queued but no active workers!")

    if health.failure_rate is not None:
        lines.append("")
        lines.append("📈 FAILURE RATE ANALYSIS")
        lines.append("-" * 80)
        lines.append(f"Failure rate: {health.failure_rate:.1%}")
        lines.append(f"Status: {health.failure_rate_status}")

        if health.failure_rate_status == "BLOCKED":
            lines.append("❌ Worker spawning is BLOCKED due to high failure rate")

    if health.recent_errors:
        lines.append("")
        lines.append("❌ RECENT ERRORS (Last Hour)")
        lines.append("-" * 80)
        for error in health.recent_errors[:5]:
            timestamp = error["timestamp"][11:19]
            source = error.get("source_id", "unknown")[:20]
            message = error["message"][:60]
            lines.append(f"[{timestamp}] {source:20} | {message}")

    lines.append("")
    lines.append("=" * 80)
    return "\n".join(lines)


def format_orchestrator_text(status: OrchestratorStatus) -> str:
    """Format orchestrator status."""
    lines: List[str] = []

    lines.append("=" * 80)
    lines.append("🔄 ORCHESTRATOR STATUS")
    lines.append("=" * 80)

    lines.append("\n📊 Status")

    if status.last_activity:
        age_minutes = (datetime.now(timezone.utc) - status.last_activity).total_seconds() / 60
        lines.append(f"   Last Activity: {status.last_activity.strftime('%Y-%m-%d %H:%M:%S')} ({age_minutes:.1f}m ago)")
    else:
        lines.append("   Last Activity: None")

    if status.last_cycle:
        lines.append(f"   Last Cycle: #{status.last_cycle}")

    lines.append(f"   Status: {status.status}")

    if status.status == "HEALTHY":
        lines.append("   ✅ Orchestrator is running normally")
    elif status.status == "WARNING":
        lines.append("   ⚠️  Orchestrator activity is stale")
    elif status.status == "STALE":
        lines.append("   ❌ Orchestrator may have stopped")
    else:
        lines.append("   ❌ No orchestrator logs found")

    if status.recent_cycles:
        lines.append("\n🔄 Recent Cycles")
        for cycle in status.recent_cycles[:10]:
            timestamp = cycle["timestamp"][11:19]
            cycle_num = cycle["cycle_number"]
            lines.append(f"   Cycle #{cycle_num} at {timestamp}")

    if status.recent_logs:
        lines.append("\n📜 Recent Activity (Last 10)")
        for log in status.recent_logs[:10]:
            timestamp = log["timestamp"][11:19]
            level = log["log_level"]
            message = log["message"][:60]
            lines.append(f"   [{timestamp}] [{level:8}] {message}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


__all__ = ["format_health_text", "format_orchestrator_text"]
