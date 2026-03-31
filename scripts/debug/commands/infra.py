"""Infrastructure analysis command - RAM tier stats, failure rates, etc."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple


def _is_heartbeat_failure(error_reason: str) -> bool:
    lowered = error_reason.lower()
    return "heartbeat" in lowered or "stuck" in lowered


def _collect_worker_stats(
    workers: List[Dict[str, Any]],
) -> Tuple[Dict[Any, Dict[str, Any]], Dict[str, Dict[str, int]]]:
    ram_stats: Dict[Any, Dict[str, Any]] = {}
    storage_stats: Dict[str, Dict[str, int]] = {}

    for worker in workers:
        meta = worker.get("metadata", {}) or {}
        ram = meta.get("ram_tier")
        storage = meta.get("storage_volume", "Unknown")
        error_reason = meta.get("error_reason", "")
        is_failure = worker.get("status") in ["error", "terminated"] and _is_heartbeat_failure(error_reason)

        if ram:
            if ram not in ram_stats:
                ram_stats[ram] = {"total": 0, "failed": 0, "failure_reasons": {}}
            ram_stats[ram]["total"] += 1
            if is_failure:
                ram_stats[ram]["failed"] += 1
                reason_key = error_reason[:50] if error_reason else "Unknown"
                ram_stats[ram]["failure_reasons"][reason_key] = ram_stats[ram]["failure_reasons"].get(reason_key, 0) + 1

        if storage not in storage_stats:
            storage_stats[storage] = {"total": 0, "failed": 0}
        storage_stats[storage]["total"] += 1
        if is_failure:
            storage_stats[storage]["failed"] += 1

    return ram_stats, storage_stats


def _print_ram_analysis(ram_stats: Dict[Any, Dict[str, Any]]) -> None:
    print("\n" + "-" * 80)
    print("📊 FAILURE RATE BY RAM TIER")
    print("-" * 80)
    print(f"{'RAM Tier':<12} {'Workers':<10} {'Failures':<10} {'Fail Rate':<12} {'Status'}")
    print("-" * 80)

    for ram in sorted(ram_stats.keys(), reverse=True):
        stats = ram_stats[ram]
        fail_rate = (stats["failed"] / stats["total"] * 100) if stats["total"] > 0 else 0
        if fail_rate < 5:
            status = "✅ Good"
        elif fail_rate < 15:
            status = "⚠️  Warning"
        else:
            status = "❌ High failure"
        print(f"{ram}GB{'':<8} {stats['total']:<10} {stats['failed']:<10} {fail_rate:>6.1f}%{'':<5} {status}")

    print("\n" + "-" * 80)
    print("🔍 TOP FAILURE REASONS BY RAM TIER")
    print("-" * 80)
    for ram in sorted(ram_stats.keys(), reverse=True):
        reasons = ram_stats[ram]["failure_reasons"]
        if not reasons:
            continue
        print(f"\n{ram}GB RAM:")
        for reason, count in sorted(reasons.items(), key=lambda item: -item[1])[:3]:
            print(f"  {count}x {reason}")


def _print_storage_analysis(storage_stats: Dict[str, Dict[str, int]]) -> None:
    print("\n" + "-" * 80)
    print("📍 FAILURE RATE BY STORAGE VOLUME")
    print("-" * 80)
    print(f"{'Storage':<15} {'Workers':<10} {'Failures':<10} {'Fail Rate'}")
    print("-" * 80)
    for storage in sorted(storage_stats.keys()):
        stats = storage_stats[storage]
        fail_rate = (stats["failed"] / stats["total"] * 100) if stats["total"] > 0 else 0
        print(f"{storage:<15} {stats['total']:<10} {stats['failed']:<10} {fail_rate:>6.1f}%")


def _print_recommendations(ram_stats: Dict[Any, Dict[str, Any]]) -> None:
    print("\n" + "-" * 80)
    print("💡 RECOMMENDATIONS")
    print("-" * 80)

    if not ram_stats:
        print("• No RAM-tier data available in the selected time window")
        return

    best_ram = min(
        ram_stats.keys(),
        key=lambda ram: (ram_stats[ram]["failed"] / ram_stats[ram]["total"]) if ram_stats[ram]["total"] > 0 else 0,
    )
    worst_ram = max(
        ram_stats.keys(),
        key=lambda ram: (ram_stats[ram]["failed"] / ram_stats[ram]["total"]) if ram_stats[ram]["total"] > 5 else 0,
    )
    best_rate = (ram_stats[best_ram]["failed"] / ram_stats[best_ram]["total"] * 100) if ram_stats[best_ram]["total"] > 0 else 0
    worst_rate = (ram_stats[worst_ram]["failed"] / ram_stats[worst_ram]["total"] * 100) if ram_stats[worst_ram]["total"] > 0 else 0

    if worst_rate > best_rate * 2 and ram_stats[worst_ram]["total"] > 5:
        print(f"• Consider deprioritizing {worst_ram}GB machines ({worst_rate:.1f}% failure rate)")
        print(f"  vs {best_ram}GB machines ({best_rate:.1f}% failure rate)")
        return

    print("• RAM tier failure rates are relatively balanced")


def run(client, options: dict) -> None:
    """Handle 'debug.py infra' command."""
    try:
        days = options.get("days", 3)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = client.db.supabase.table("workers").select(
            "id,status,created_at,last_heartbeat,metadata"
        ).gte("created_at", cutoff).execute()
        workers = result.data or []

        print("=" * 80)
        print(f"🖥️  INFRASTRUCTURE ANALYSIS (last {days} days)")
        print("=" * 80)
        print(f"\nTotal workers analyzed: {len(workers)}")

        ram_stats, storage_stats = _collect_worker_stats(workers)
        _print_ram_analysis(ram_stats)
        _print_storage_analysis(storage_stats)
        _print_recommendations(ram_stats)
        print("\n" + "=" * 80)
    except Exception as exc:
        print(f"❌ Error analyzing infrastructure: {exc}")
        if options.get("debug"):
            import traceback

            traceback.print_exc()
