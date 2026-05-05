#!/usr/bin/env python3
"""
Throwaway script: spin up a cheap RunPod pod, generate uv.lock for reigh-worker,
benchmark cold-cache uv sync, copy uv.lock back, and terminate the pod.

Usage:
    cd reigh-worker-orchestrator
    python scripts/uv_lock_benchmark.py

Requires: RUNPOD_API_KEY in .env or environment.
"""

import os
import sys
import time
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import runpod
from runpod_lifecycle import terminate
from runpod_lifecycle.api import create_pod as create_pod_and_wait, get_pod_ssh_details
from runpod_lifecycle.ssh import SSHClient

API_KEY = os.environ["RUNPOD_API_KEY"]
SSH_PUBLIC_KEY = os.getenv("RUNPOD_SSH_PUBLIC_KEY", "")
SSH_PRIVATE_KEY = os.getenv("RUNPOD_SSH_PRIVATE_KEY", "")
SSH_PRIVATE_KEY_PATH = os.getenv("RUNPOD_SSH_PRIVATE_KEY_PATH")

# Same image the production pods use
IMAGE = "runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04"
# Cheapest available community GPU — we only need it for pip/uv resolve, not training
GPU_TYPE = "NVIDIA GeForce RTX 3090"
WORKER_REPO = "https://github.com/banodoco/Reigh-Worker.git"
DEFAULT_RUNPOD_DISK_GB = 200
UV_LOCK_DEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "reigh-worker",
    "uv.lock",
)

def wait_for_ssh(pod_id: str, max_wait: int = 300) -> dict:
    """Poll until SSH details are available."""
    print(f"Waiting for pod {pod_id} to be ready (up to {max_wait}s)...")
    start = time.time()
    while time.time() - start < max_wait:
        details = get_pod_ssh_details(pod_id, API_KEY)
        if details and details.get("ip") and details.get("port"):
            print(f"  SSH ready: {details['ip']}:{details['port']}")
            return details
        time.sleep(10)
    raise TimeoutError(f"Pod {pod_id} did not become SSH-ready within {max_wait}s")


def make_ssh_client(ssh_details: dict) -> SSHClient:
    """Build an SSHClient from pod SSH details, connect, and return."""
    ip = ssh_details["ip"]
    port = int(ssh_details["port"])

    kwargs = {"hostname": ip, "port": port, "username": "root"}
    if SSH_PRIVATE_KEY_PATH and os.path.isfile(SSH_PRIVATE_KEY_PATH):
        kwargs["private_key_path"] = SSH_PRIVATE_KEY_PATH
    elif SSH_PRIVATE_KEY:
        kwargs["private_key_content"] = SSH_PRIVATE_KEY
    else:
        raise RuntimeError("No SSH private key available (set RUNPOD_SSH_PRIVATE_KEY or RUNPOD_SSH_PRIVATE_KEY_PATH)")

    client = SSHClient(**kwargs)
    client.connect()
    print(f"  SSH connected to {ip}:{port}")
    return client


def run_cmd(ssh: SSHClient, cmd: str, timeout: int = 600) -> str:
    """Run a command via SSH, print output, return stdout."""
    print(f"\n>>> {cmd}")
    exit_code, stdout, stderr = ssh.execute_command(cmd, timeout=timeout)
    if stdout.strip():
        print(stdout.strip())
    if stderr.strip():
        print(f"[stderr] {stderr.strip()}")
    if exit_code != 0:
        raise RuntimeError(f"Command exited {exit_code}: {cmd}")
    return stdout


def main():
    pod_id = None
    try:
        # 1. Create pod
        print("=" * 60)
        print("STEP 1: Creating throwaway RunPod pod")
        print("=" * 60)
        result = create_pod_and_wait(
            api_key=API_KEY,
            gpu_type_id=GPU_TYPE,
            image_name=IMAGE,
            name="uv-lock-benchmark",
            disk_in_gb=DEFAULT_RUNPOD_DISK_GB,
            container_disk_in_gb=DEFAULT_RUNPOD_DISK_GB,
            min_vcpu_count=4,
            min_memory_in_gb=16,
            public_key_string=SSH_PUBLIC_KEY or None,
            env_vars={"PUBLIC_KEY": SSH_PUBLIC_KEY} if SSH_PUBLIC_KEY else None,
        )
        if not result:
            print("Failed to create pod on SECURE. Trying COMMUNITY cloud...")
            runpod.api_key = API_KEY
            pod = runpod.create_pod(
                name="uv-lock-benchmark",
                image_name=IMAGE,
                gpu_type_id=GPU_TYPE,
                gpu_count=1,
                cloud_type="COMMUNITY",
                volume_in_gb=DEFAULT_RUNPOD_DISK_GB,
                container_disk_in_gb=DEFAULT_RUNPOD_DISK_GB,
                min_vcpu_count=4,
                min_memory_in_gb=16,
                ports="22/tcp",
                env={"PUBLIC_KEY": SSH_PUBLIC_KEY} if SSH_PUBLIC_KEY else {},
            )
            # Extract pod_id from various response shapes
            if isinstance(pod, dict):
                if "data" in pod:
                    pod_data = pod["data"].get("podFindAndDeployOnDemand", {})
                    pod_id = pod_data.get("id")
                elif "id" in pod:
                    pod_id = pod["id"]
            if not pod_id:
                print(f"Failed to create community pod: {pod}")
                sys.exit(1)
        else:
            pod_id = result.get("id") or result.get("pod_id")

        print(f"Pod created: {pod_id}")

        # 2. Wait for SSH
        print("\n" + "=" * 60)
        print("STEP 2: Waiting for SSH")
        print("=" * 60)
        ssh_details = wait_for_ssh(pod_id)
        # Give sshd a moment to fully initialize
        time.sleep(5)
        ssh = make_ssh_client(ssh_details)

        # 3. Setup: install uv, clone repo
        print("\n" + "=" * 60)
        print("STEP 3: Installing uv and cloning reigh-worker")
        print("=" * 60)
        run_cmd(ssh, "apt-get update -qq && apt-get install -y -qq python3.10-venv python3.10-dev git curl > /dev/null 2>&1 && echo 'apt done'", timeout=120)
        run_cmd(ssh, "curl -LsSf https://astral.sh/uv/install.sh | sh", timeout=60)
        run_cmd(ssh, "export PATH=\"$HOME/.local/bin:$PATH\" && uv --version")
        run_cmd(ssh, f"cd /tmp && git clone --depth 1 {WORKER_REPO} reigh-worker", timeout=120)

        # 4. Generate uv.lock
        print("\n" + "=" * 60)
        print("STEP 4: Generating uv.lock")
        print("=" * 60)
        run_cmd(ssh, "export PATH=\"$HOME/.local/bin:$PATH\" && cd /tmp/reigh-worker && uv lock --python 3.10", timeout=300)

        # 4b. Copy uv.lock immediately after generation (before sync can fail)
        print("\nCopying uv.lock before sync...")
        lock_content = run_cmd(ssh, "cat /tmp/reigh-worker/uv.lock", timeout=30)
        with open(UV_LOCK_DEST, "w") as f:
            f.write(lock_content)
        print(f"Wrote {len(lock_content)} bytes to {UV_LOCK_DEST}")

        # 5. Benchmark cold-cache uv sync (cuda124)
        print("\n" + "=" * 60)
        print("STEP 5: Benchmarking cold-cache uv sync --extra cuda124")
        print("=" * 60)
        t0 = time.time()
        run_cmd(ssh, "export PATH=\"$HOME/.local/bin:$PATH\" && cd /tmp/reigh-worker && uv sync --locked --python 3.10 --extra cuda124", timeout=1800)
        cuda124_time = time.time() - t0
        print(f"\n*** cuda124 cold-cache sync: {cuda124_time:.1f}s ***")

        # 6. Benchmark cold-cache uv sync (cuda128) — need fresh venv
        print("\n" + "=" * 60)
        print("STEP 6: Benchmarking cold-cache uv sync --extra cuda128")
        print("=" * 60)
        run_cmd(ssh, "cd /tmp/reigh-worker && rm -rf .venv")
        t0 = time.time()
        run_cmd(ssh, "export PATH=\"$HOME/.local/bin:$PATH\" && cd /tmp/reigh-worker && uv sync --locked --python 3.10 --extra cuda128", timeout=1800)
        cuda128_time = time.time() - t0
        print(f"\n*** cuda128 cold-cache sync: {cuda128_time:.1f}s ***")

        # 7. Copy uv.lock back
        print("\n" + "=" * 60)
        print("STEP 7: Copying uv.lock to local repo")
        print("=" * 60)
        lock_content = run_cmd(ssh, "cat /tmp/reigh-worker/uv.lock", timeout=30)
        with open(UV_LOCK_DEST, "w") as f:
            f.write(lock_content)
        print(f"Wrote {len(lock_content)} bytes to {UV_LOCK_DEST}")

        # 8. Ground truth import validation
        print("\n" + "=" * 60)
        print("STEP 8: Ground truth import validation")
        print("=" * 60)
        run_cmd(ssh, 'export PATH="$HOME/.local/bin:$PATH" && cd /tmp/reigh-worker && uv run --python 3.10 python -c "import supabase, realtime, fastapi, pydantic; print(f\'pydantic={pydantic.__version__}, supabase={supabase.__version__}, realtime={realtime.__version__}\')"')
        run_cmd(ssh, 'export PATH="$HOME/.local/bin:$PATH" && cd /tmp/reigh-worker && uv run --python 3.10 python -c "from source.runtime.entrypoints.worker import main; print(\'worker entrypoint imports OK\')"', timeout=120)
        run_cmd(ssh, "export PATH=\"$HOME/.local/bin:$PATH\" && cd /tmp/reigh-worker && uv pip list --format columns 2>/dev/null | grep -iE 'pydantic|supabase|realtime|torch'")

        # Summary
        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        print(f"  cuda124 cold-cache sync: {cuda124_time:.1f}s ({cuda124_time/60:.1f}m)")
        print(f"  cuda128 cold-cache sync: {cuda128_time:.1f}s ({cuda128_time/60:.1f}m)")
        print(f"  uv.lock written to: {UV_LOCK_DEST}")
        if max(cuda124_time, cuda128_time) > 1200:  # 20 min
            print(f"\n  ⚠️  p95 > 20 min — consider bumping max_startup_phase_sec from 1800 to 2700")
        else:
            print(f"\n  ✅ Well within 30-min startup cap — no timeout change needed")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 9. Terminate pod
        if pod_id:
            print(f"\nTerminating pod {pod_id}...")
            try:
                runpod.api_key = API_KEY
                asyncio.run(terminate(pod_id, API_KEY))
                print(f"Pod {pod_id} terminated.")
            except Exception as e:
                print(f"⚠️  Failed to terminate pod {pod_id}: {e}")
                print(f"    Manually terminate at https://www.runpod.io/console/pods")


if __name__ == "__main__":
    main()
