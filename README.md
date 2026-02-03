# Headless WGP Orchestrator

Manages GPU workers on RunPod and API-based tasks for video generation.

## Services

- **gpu_orchestrator/** - Spawns, monitors, and terminates RunPod GPU workers based on task demand
- **api_orchestrator/** - Handles API-based tasks (fal.ai, Wavespeed, image processing)

## Deploy

```bash
./deploy_to_railway.sh          # Deploy both
./deploy_to_railway.sh --gpu    # GPU orchestrator only
./deploy_to_railway.sh --api    # API orchestrator only
```

## Debug

```bash
python scripts/debug.py task <task_id>      # Investigate a task
python scripts/debug.py worker <worker_id>  # Check worker status
python scripts/debug.py health              # System overview
python scripts/debug.py runpod              # Find orphaned pods
```

## Local Dev

```bash
cp env.example .env  # Fill in credentials
pip install -r api_orchestrator/requirements.txt
pip install -r gpu_orchestrator/requirements.txt
python -m gpu_orchestrator.main continuous
```
