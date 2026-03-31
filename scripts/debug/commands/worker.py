"""Worker investigation command."""

from __future__ import annotations

from scripts.debug.client import DebugClient
from scripts.debug.formatters import Formatter


def _print_disk_check_result(worker_id: str, result: dict) -> None:
    print("=" * 80)
    print(f"💾 DISK SPACE CHECK: {worker_id}")
    print("=" * 80)
    if result.get("available"):
        print(f"\n✅ SSH successful (RunPod: {result.get('runpod_id')})\n")
        print(result.get("disk_info", "No output"))
        issues = result.get("issues", [])
        if issues:
            print("\n" + "=" * 40)
            print("⚠️  ISSUES DETECTED:")
            for issue in issues:
                print(f"   {issue}")
        else:
            print("\n✅ No disk space issues detected")
    else:
        print(f"\n❌ Cannot check disk space: {result.get('error')}")
        print("\n💡 Worker may be terminated or SSH unavailable")
    print()


def _print_logging_check_result(worker_id: str, result: dict) -> None:
    print("=" * 80)
    print(f"🔍 WORKER LOGGING CHECK: {worker_id}")
    print("=" * 80)
    if result["is_logging"]:
        print(f"\n✅ Worker IS logging! ({result['log_count']} recent logs)")
        print("\nMost recent logs:")
        print("-" * 80)
        for log in result["recent_logs"]:
            timestamp = log["timestamp"][-12:-4]
            level = log["log_level"]
            message = log["message"][:70]
            print(f"[{level:7}] {timestamp} | {message}")
    else:
        print("\n❌ Worker is NOT logging yet")
        print("   This means worker.py has not started or crashed during initialization")
        print("\n💡 Next steps:")
        print(f"   1. Check worker status: debug.py worker {worker_id}")
        print(f"   2. Check startup logs: debug.py worker {worker_id} --startup")
        print("   3. Wait if worker is < 10 minutes old (might be installing dependencies)")
    print()


def _print_startup_logs(worker_id: str, info) -> None:
    print("=" * 80)
    print(f"🚀 WORKER STARTUP LOGS: {worker_id}")
    print("=" * 80)
    print(f"\nFound {len(info.logs)} startup-related log entries\n")

    if not info.logs:
        print("⚠️  No startup logs found")
        print("   Worker may have been created before logging was implemented")
        print("   or is still being provisioned")
        return

    for log in info.logs[:100]:
        timestamp = log["timestamp"][11:19]
        level = log["log_level"]
        message = log["message"]
        level_symbol = {
            "ERROR": "❌",
            "WARNING": "⚠️",
            "INFO": "ℹ️",
            "DEBUG": "🔍",
        }.get(level, "  ")
        print(f"[{timestamp}] {level_symbol} {message}")

    all_messages = " ".join(log["message"] for log in info.logs)
    if "ModuleNotFoundError" in all_messages:
        print("\n⚠️  ISSUE DETECTED: Missing Python module")
        print("   Worker crashed due to missing dependencies")
    elif "died immediately" in all_messages:
        print("\n❌ ISSUE DETECTED: Worker process died immediately")
    elif "still running" in all_messages:
        print("\n✅ Worker process started successfully")


def run(client: DebugClient, worker_id: str, options: dict) -> None:
    """Handle 'debug.py worker <id>' command."""
    try:
        if options.get("check_disk", False):
            _print_disk_check_result(worker_id, client.check_worker_disk_space(worker_id))
            return

        if options.get("check_logging", False):
            _print_logging_check_result(worker_id, client.check_worker_logging(worker_id))
            return

        hours = options.get("hours", 24)
        startup = options.get("startup", False)
        info = client.get_worker_info(worker_id, hours=hours, startup=startup)

        format_type = options.get("format", "text")
        if startup and format_type == "text":
            _print_startup_logs(worker_id, info)
            return

        logs_only = options.get("logs_only", False)
        print(Formatter.format_worker(info, format_type, logs_only))
    except Exception as exc:
        print(f"❌ Error investigating worker: {exc}")
        if options.get("debug"):
            import traceback

            traceback.print_exc()
