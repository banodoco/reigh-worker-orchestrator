# Sprint 0C First Boot Measurement

Status: `PENDING_FIRST_BOOT_NETWORK_BLOCKED`

This runbook records the launch paths and exact measurements for the first dual-stack RunPod boot. Do not fill pod IDs, disk values, or timing values from memory; record only data from a live pod or leave the field as `PENDING`.

## Disk Default Change

Owner: Peter O'Malley
Change date: 2026-05-06
Status: `LANDED`

The default RunPod pod disk and container disk changed from 50 GB to 200 GB in the shared lifecycle config and orchestrator launch defaults. Intentional WGP-only 50 GB launches remain available through explicit environment or CLI overrides:

```bash
RUNPOD_DISK_SIZE_GB=50 RUNPOD_CONTAINER_DISK_GB=50 ...
VIBECOMFY_RUNPOD_DISK_SIZE_GB=50 VIBECOMFY_RUNPOD_CONTAINER_DISK_GB=50 ...
```

## Launch Paths

### Orchestrator Path

Use the worker orchestrator path when the measurement should exercise worker spawning, startup-script upload, startup disk diagnostics, and worker status metadata.

```bash
cd /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator
python scripts/spawn_gpu.py spawn
```

The orchestrator path uses `gpu_orchestrator.worker_spawner.create_worker_spawner()`, which builds `RunPodConfig.from_env()` with 200 GB env-absent disk defaults. The startup script is rendered from:

```text
gpu_orchestrator/runpod/worker_startup.template.sh
```

Early startup logs must include:

```bash
df -h /
df -h /workspace || true
```

### VibeComfy Path

Use the VibeComfy path when the measurement should exercise the shared `runpod-lifecycle` launch machinery and VibeComfy validation without duplicating pod lifecycle logic in the worker.

Cheap smoke:

```bash
cd /Users/peteromalley/Documents/reigh-workspace/vibecomfy
python scripts/runpod_validate.py
```

Model-backed matrix:

```bash
cd /Users/peteromalley/Documents/reigh-workspace/vibecomfy
python scripts/runpod_model_matrix.py
```

The VibeComfy path delegates disk defaults to `RunPodConfig.from_env()` unless `VIBECOMFY_RUNPOD_DISK_SIZE_GB` or `VIBECOMFY_RUNPOD_CONTAINER_DISK_GB` is set.

## Measurement Commands

Run these on the live pod through SSH or the existing launcher command execution path. Capture stdout in the measurement record.

Disk summary:

```bash
date -Iseconds
df -h /
df -h /workspace || true
df -BG /
df -BG /workspace || true
```

Largest top-level directories:

```bash
du -xBG --max-depth=1 / 2>/dev/null | sort -n | tail -30
du -BG --max-depth=2 /workspace 2>/dev/null | sort -n | tail -40 || true
du -BG --max-depth=2 /workspace/Reigh-Worker 2>/dev/null | sort -n | tail -40 || true
du -BG --max-depth=2 /workspace/Headless-Wan2GP 2>/dev/null | sort -n | tail -40 || true
du -BG --max-depth=2 /root/vibecomfy 2>/dev/null | sort -n | tail -40 || true
```

GPU and RAM:

```bash
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader
free -h
cat /proc/meminfo | sed -n '1,5p'
```

Image, Python, package, and model staging facts:

```bash
cat /etc/os-release | sed -n '1,8p'
python3 --version
which uv || true
uv --version || true
find /workspace -maxdepth 4 -type f \( -name '*.safetensors' -o -name '*.ckpt' -o -name '*.pt' -o -name '*.gguf' \) -printf '%s\t%p\n' 2>/dev/null | sort -n | tail -80 || true
find /root/vibecomfy -maxdepth 5 -type f \( -name '*.safetensors' -o -name '*.ckpt' -o -name '*.pt' -o -name '*.gguf' \) -printf '%s\t%p\n' 2>/dev/null | sort -n | tail -80 || true
```

Startup timing from orchestrator metadata/logs:

```bash
grep -E 'WORKER STARTUP SCRIPT EXECUTION BEGIN|Phase:|System dependencies installed|uv sync|Starting worker|SCRIPT FAILED' /tmp/worker_startup_*.log 2>/dev/null || true
ls -lh /tmp/worker_startup_*.log 2>/dev/null || true
```

Cleanup:

```bash
cd /Users/peteromalley/Documents/reigh-workspace/reigh-worker-orchestrator
python scripts/spawn_gpu.py terminate --pod-id <pod-id>
```

or, for VibeComfy launched pods, use the `manual_cleanup_command=` printed by `scripts/runpod_runner.py`.

## Recording Template

Measurement status: `VIBECOMFY_SMOKE_VERIFIED`
Measurement owner: Peter O'Malley
Target measurement date: 2026-05-06

First boot attempt:

- Attempted at: 2026-05-05T23:31:34Z
- Attempted launch path: VibeComfy validation path, using shared `runpod-lifecycle` config
- Intended disk override for attempt: `RUNPOD_DISK_SIZE_GB=200`, `RUNPOD_CONTAINER_DISK_GB=200`
- Credential check: `RUNPOD_API_KEY` present in `runpod-lifecycle/.env`; `RunPodConfig.from_env()` loaded the API key
- Local config check: `gpu_type=NVIDIA GeForce RTX 4090`, `storage_name=Peter`, `disk_size_gb=200`, `container_disk_gb=200`
- Launch result: `PASS`
- Worker-environment blocker: Codex execute sandbox could not resolve `api.runpod.io`, but the main shell resolved and used the RunPod API successfully.
- Cleanup required: pod `tfsd5it42jaowa` was terminated by `scripts/runpod_runner.py`; `terminated=true`.

Pod identity:

- Launch path: `vibecomfy/scripts/runpod_validate.py`
- Pod ID: `tfsd5it42jaowa`
- Worker ID: `PENDING`
- RunPod name: `vibecomfy-*`
- RunPod image: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
- Network volume name/id: `Peter` / `m6ccu1lodp`
- GPU type: `NVIDIA GeForce RTX 4090`
- GPU memory total: `PENDING`
- RAM total: `PENDING`

Disk:

- Root disk total: `PENDING`
- Root disk used: `PENDING`
- Root disk free: `PENDING`
- Workspace disk total: `PENDING`
- Workspace disk used: `PENDING`
- Workspace disk free: `PENDING`
- Largest root directories: `PENDING`
- Largest workspace directories: `PENDING`

Model and asset staging:

- VibeComfy template count used: 51
- Asset manifest model count used: 28
- Local model hashes available before launch: 0
- Remote-only hash status: `pending_first_download`
- Model files staged on pod: none required for `smoke/empty_image_red`
- Largest model files: none required for `smoke/empty_image_red`
- Custom-node lock audit used: `vibecomfy/CUSTOM_NODES_AUDIT.md`

VibeComfy smoke result:

- Artifact directory: `vibecomfy/out/runpod_artifacts/20260505T233347Z`
- Report: `vibecomfy/out/runpod_artifacts/20260505T233347Z/report.md`
- Manifest: `vibecomfy/out/runpod_artifacts/20260505T233347Z/manifest.json`
- Remote root: `/workspace/vibecomfy`
- Ready template: `smoke/empty_image_red`
- Result row: `ready_template_empty_image_red	ok	9	1	505`
- Output: `output/vibecomfy_ready_smoke_red_00001_.png`
- Output dimensions: `64x64`
- Output SHA256: `62c93efe501c1db1d7558b1eda059b0369c3b3b21d3038b1ea337d930e00336d`
- Warning: watchdog reported `/system_stats` stopped responding, but `stop_reason=completed`, exit code was 0, and output media exists.

Timing:

- Launch command started at: `2026-05-05T23:31:34Z`
- RunPod pod created at: `2026-05-05T23:31:35Z`
- SSH ready at: `2026-05-05T23:31:45Z`
- Startup script launched at: `2026-05-05T23:32:15Z`
- `deps_installing` recorded at: `PENDING`
- `deps_verified` recorded at: `PENDING`
- Worker process started at: `PENDING`
- First successful heartbeat at: `PENDING`
- First VibeComfy/Comfy output at: `2026-05-05T23:33:47Z`
- Total first boot duration: about 2m13s from launch command to artifact collection

Gate decision:

- Used disk for gate: `PENDING`
- 180 GB gate result: `PENDING_DISK_MEASUREMENT`
- Decision: `KEEP_200GB_FOR_NOW_WITH_SMOKE_VERIFIED`; run a model-backed measurement before raising above 200 GB.
- Follow-up owner/date if over gate: `PENDING`

## 180 GB Gate

After the first dual-stack boot, compare the highest measured used-disk value from `/` and `/workspace` against 180 GB.

- If used disk is `<= 180 GB`, keep 200 GB defaults for Sprint 1.
- If used disk is `> 180 GB`, record the measured value here and raise both pod disk and container disk defaults to 250 GB before Sprint 1.
- If the first boot cannot be performed, leave measurement status as `PENDING` with owner and target date. Do not fabricate pod IDs, timings, or disk usage.
