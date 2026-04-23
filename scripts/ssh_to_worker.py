#!/usr/bin/env python3
"""
SSH into a RunPod worker to check its status directly.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv

from gpu_orchestrator.config import OrchestratorConfig
from gpu_orchestrator.worker_spawner import create_worker_spawner

async def ssh_to_worker(runpod_id: str):
    load_dotenv()

    runpod_client = create_worker_spawner(OrchestratorConfig.from_env(), None)

    print(f"Connecting to RunPod worker: {runpod_id}")

    # Check running processes
    result = await runpod_client.execute_command_on_worker(
        runpod_id,
        "ps aux | grep -E '(python|worker)' | grep -v grep",
    )

    if result and result[0] == 0:
        print("Running Python processes:")
        print(result[1])
    else:
        print(f"Failed to get process list: {result}")

    # Check worker log file - try both possible log file names
    result = await runpod_client.execute_command_on_worker(
        runpod_id,
        f"ls -la /workspace/Headless-Wan2GP/logs/*{runpod_id}* 2>/dev/null || ls -la /workspace/Headless-Wan2GP/logs/gpu-* 2>/dev/null | head -5 || ls -la /workspace/Reigh-Worker/logs/*{runpod_id}* 2>/dev/null || ls -la /workspace/Reigh-Worker/logs/gpu-* 2>/dev/null | head -5",
    )

    if result and result[0] == 0:
        print("\nWorker log file (last 20 lines):")
        print(result[1])
    else:
        print(f"Failed to read worker log: {result}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python3 scripts/ssh_to_worker.py RUNPOD_ID')
        sys.exit(1)

    runpod_id = sys.argv[1]
    asyncio.run(ssh_to_worker(runpod_id))
