"""Config display command."""

from __future__ import annotations

import os
from typing import Iterable, Tuple

from dotenv import load_dotenv


def _mask_value(env_var: str, value: str) -> str:
    if value and value != "Not set" and "KEY" in env_var:
        return f"{value[:8]}...{value[-4:]}" if len(value) > 12 else "***"
    return value


def _print_env_section(
    title: str,
    entries: Iterable[Tuple[str, str, str]],
    explain: bool,
) -> None:
    print(f"\n\n{title}")
    print("-" * 80)
    for env_var, default, description in entries:
        value = os.getenv(env_var, default)
        display_value = _mask_value(env_var, value)
        print(f"\n{env_var}: {display_value}")
        if explain:
            print(f"  └─ {description}")
        if explain and env_var == "WORKER_GRACE_PERIOD_SEC":
            mins = int(value) / 60
            print(f"  └─ {mins:.1f} minutes after promotion to active")
        if explain and env_var == "ORCHESTRATOR_POLL_SEC":
            print(f"  └─ Workers checked every {value} seconds")


def _print_termination_explanation() -> None:
    grace = int(os.getenv("WORKER_GRACE_PERIOD_SEC", "120"))
    poll = int(os.getenv("ORCHESTRATOR_POLL_SEC", "30"))
    min_gpus = os.getenv("MIN_ACTIVE_GPUS", "2")
    min_time = grace
    max_time = grace + poll

    print("\n\n📋 Worker Termination Logic")
    print("-" * 80)
    print(
        f"""
1. Worker is promoted to 'active' status
2. Grace period starts ({grace} seconds)
3. After grace period, worker is eligible for idle checks
4. Every {poll} seconds, orchestrator checks:
   - Does worker have running tasks? → Keep active
   - No tasks AND above MIN_ACTIVE_GPUS ({min_gpus})? → Terminate
   - No tasks BUT at/below minimum? → Keep active
5. Termination is immediate once marked

Note: If tasks are queued, at least 1 worker is kept regardless of MIN_ACTIVE_GPUS
"""
    )
    print(f"⏱️  Idle Termination Time Range: {min_time / 60:.1f} - {max_time / 60:.1f} minutes")
    print("   (Depends on when orchestrator cycle runs after grace period expires)")


def run(client, options: dict) -> None:
    """Handle 'debug.py config' command."""
    _ = client
    load_dotenv()
    explain = options.get("explain", False)

    print("=" * 80)
    print("⚙️  SYSTEM CONFIGURATION")
    print("=" * 80)

    _print_env_section(
        "📊 Timing & Scaling Configuration",
        [
            ("ORCHESTRATOR_POLL_SEC", "30", "How often orchestrator checks workers"),
            ("WORKER_GRACE_PERIOD_SEC", "120", "Grace period before idle check"),
            ("GPU_IDLE_TIMEOUT_SEC", "300", "Heartbeat timeout for active workers"),
            ("GPU_OVERCAPACITY_IDLE_TIMEOUT_SEC", "30", "Idle timeout when over-capacity"),
            ("MIN_ACTIVE_GPUS", "2", "Minimum workers to keep running"),
            ("MAX_ACTIVE_GPUS", "10", "Maximum workers allowed"),
            ("TASKS_PER_GPU_THRESHOLD", "3", "Tasks per GPU before scaling up"),
        ],
        explain,
    )
    _print_env_section(
        "☁️  RunPod Configuration",
        [
            ("RUNPOD_API_KEY", "Not set", "API key for RunPod"),
            ("RUNPOD_TEMPLATE_ID", "Not set", "Default template for GPU workers"),
            ("RUNPOD_NETWORK_VOLUME_ID", "Not set", "Network volume for shared storage"),
        ],
        explain,
    )
    _print_env_section(
        "🗄️  Database Configuration",
        [
            ("SUPABASE_URL", "Not set", "Supabase project URL"),
            ("SUPABASE_SERVICE_ROLE_KEY", "Not set", "Service role key (admin access)"),
        ],
        explain,
    )

    if explain:
        _print_termination_explanation()

    print("\n" + "=" * 80)
