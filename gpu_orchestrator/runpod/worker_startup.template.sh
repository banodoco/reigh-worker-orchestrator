#!/bin/bash
set -e  # Exit on any error

# Set environment variables
export WORKER_ID="__WORKER_ID__"
export SUPABASE_URL="__SUPABASE_URL__"
export SUPABASE_ANON_KEY="__SUPABASE_ANON_KEY__"
export SUPABASE_SERVICE_ROLE_KEY="__SUPABASE_SERVICE_KEY__"
export SUPABASE_SERVICE_KEY="__SUPABASE_SERVICE_KEY__"
export REPLICATE_API_TOKEN="__REPLICATE_API_TOKEN__"
export MAX_TASK_WAIT_MINUTES="__MAX_TASK_WAIT_MINUTES__"

# Non-interactive apt
export DEBIAN_FRONTEND=noninteractive

# uv: skip failed hardlink attempts on RunPod volumes, cache on persistent storage
export UV_LINK_MODE=copy
export UV_CACHE_DIR=/workspace/.uv-cache

# ------------------------------------------------------------
# EARLY LOGGING (must exist before any apt/network operations)
# ------------------------------------------------------------
# Write all stdout/stderr to a single per-worker log from the very beginning.
LOG_FILE="/tmp/worker_startup___WORKER_ID__.log"
APT_UPDATE_LOG="/tmp/apt-update-__WORKER_ID__.log"
APT_INSTALL_LOG="/tmp/apt-install-__WORKER_ID__.log"
mkdir -p /tmp
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "========================================="
echo "🚀 WORKER STARTUP SCRIPT EXECUTION BEGIN"
echo "========================================="
echo "Worker ID: $WORKER_ID"
echo "Timestamp: $(date -Iseconds)"
echo "Initial PWD: $(pwd)"
echo "USER: $(whoami)"
echo "Kernel: $(uname -a | head -c 200)"
echo "Log file: $LOG_FILE"
echo

# Error handling with useful tails
trap 'echo "❌ SCRIPT FAILED at line $LINENO with exit code $? at $(date -Iseconds)"; \
      echo "--- tail $APT_UPDATE_LOG"; tail -200 "$APT_UPDATE_LOG" 2>/dev/null || true; \
      echo "--- tail $APT_INSTALL_LOG"; tail -200 "$APT_INSTALL_LOG" 2>/dev/null || true; \
      exit 1' ERR

# Signal startup phase to orchestrator via Supabase REST.
# Reads current metadata, merges in startup_phase, writes back.
# The orchestrator reads metadata.startup_phase to distinguish
# "still setting up" from "ready but not claiming".
update_worker_phase_strict() {
    local phase="$1"
    local key="$SUPABASE_SERVICE_ROLE_KEY"
    local current_body_file patch_body_file merged payload_status current_status

    if [ -z "$key" ]; then
        echo "❌ Cannot record startup phase '$phase': SUPABASE_SERVICE_ROLE_KEY is missing"
        return 1
    fi

    current_body_file=$(mktemp) || return 1
    patch_body_file=$(mktemp) || {
        rm -f "$current_body_file"
        return 1
    }

    if ! current_status=$(curl -sS -m 5 \
        --output "$current_body_file" \
        --write-out '%{http_code}' \
        "${SUPABASE_URL}/rest/v1/workers?id=eq.${WORKER_ID}&select=metadata" \
        -H "Authorization: Bearer $key" \
        -H "apikey: $key" 2>/dev/null); then
        echo "❌ Failed to fetch worker metadata for startup phase '$phase'"
        rm -f "$current_body_file" "$patch_body_file"
        return 1
    fi

    if [ "$current_status" != "200" ]; then
        echo "❌ Metadata fetch for startup phase '$phase' returned HTTP $current_status"
        cat "$current_body_file" 2>/dev/null || true
        rm -f "$current_body_file" "$patch_body_file"
        return 1
    fi

    if ! merged=$(python3 -c "
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as handle:
    rows = json.load(handle)
meta = rows[0].get('metadata', {}) if rows else {}
meta['startup_phase'] = sys.argv[2]
print(json.dumps({'metadata': meta}))
" "$current_body_file" "$phase" 2>/dev/null); then
        echo "❌ Failed to merge startup phase '$phase' into worker metadata"
        rm -f "$current_body_file" "$patch_body_file"
        return 1
    fi

    printf '%s' "$merged" > "$patch_body_file"
    if ! payload_status=$(curl -sS -m 5 -X PATCH \
        --output /dev/null \
        --write-out '%{http_code}' \
        "${SUPABASE_URL}/rest/v1/workers?id=eq.${WORKER_ID}" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $key" \
        -H "apikey: $key" \
        -H "Prefer: return=minimal" \
        --data-binary "@$patch_body_file" 2>/dev/null); then
        echo "❌ Failed to PATCH startup phase '$phase'"
        rm -f "$current_body_file" "$patch_body_file"
        return 1
    fi

    rm -f "$current_body_file" "$patch_body_file"

    if [ "$payload_status" != "204" ]; then
        echo "❌ Startup phase '$phase' PATCH returned HTTP $payload_status"
        return 1
    fi

    echo "📡 Phase: $phase"
}

update_worker_phase() {
    local phase="$1"
    if ! update_worker_phase_strict "$phase"; then
        echo "⚠️  Failed to record startup phase '$phase'; continuing"
    fi
    return 0
}

record_initial_deps_installing_or_exit() {
    local attempt
    for attempt in 1 2 3; do
        if update_worker_phase_strict "deps_installing"; then
            return 0
        fi
        echo "⚠️  deps_installing PATCH failed (attempt $attempt/3)"
        if [ "$attempt" -lt 3 ]; then
            sleep 2
        fi
    done
    echo "❌ Unable to record initial deps_installing startup phase after 3 attempts; aborting startup"
    exit 1
}

# Apt helper with timeouts/retries (prevents indefinite hangs)
apt_retry() {
    local name="$1"
    local timeout_sec="$2"
    shift 2
    local attempt rc
    for attempt in 1 2 3; do
        echo "📦 $name (attempt $attempt/3, timeout ${timeout_sec}s): $*"
        rm -f "$APT_UPDATE_LOG" "$APT_INSTALL_LOG" 2>/dev/null || true
        if timeout "$timeout_sec" "$@" ; then
            echo "✅ $name succeeded"
            return 0
        fi
        rc=$?
        echo "⚠️  $name failed (rc=$rc)"
        # Show recent output if we captured any
        tail -80 "$APT_UPDATE_LOG" 2>/dev/null || true
        tail -80 "$APT_INSTALL_LOG" 2>/dev/null || true
        sleep $((attempt * 5))
    done
    echo "❌ $name failed after 3 attempts"
    return 1
}

# Pick worker repo directory (supports both legacy + renamed repo)
WORKSPACE_DIR="/workspace"
PRIMARY_DIR="$WORKSPACE_DIR/Reigh-Worker"
FALLBACK_DIR="$WORKSPACE_DIR/Headless-Wan2GP"

if [ -d "$PRIMARY_DIR" ]; then
    WORKDIR="$PRIMARY_DIR"
    echo "✅ Using worker directory: $WORKDIR"
elif [ -d "$FALLBACK_DIR" ]; then
    WORKDIR="$FALLBACK_DIR"
    echo "⚠️  Reigh-Worker not found; using fallback worker directory: $WORKDIR"
else
    echo "📦 Neither Reigh-Worker nor Headless-Wan2GP found. Cloning Reigh-Worker into $PRIMARY_DIR..."
    cd "$WORKSPACE_DIR" || exit 1
    git clone https://github.com/banodoco/Reigh-Worker.git "$PRIMARY_DIR" || exit 1
    WORKDIR="$PRIMARY_DIR"
fi

# Ensure a stable log location in the repo (symlink to /tmp early log)
LOG_DIR="$WORKDIR/logs"
mkdir -p "$LOG_DIR"
ln -sf "$LOG_FILE" "$LOG_DIR/__WORKER_ID__.log" || true
echo "🔗 Log symlink: $LOG_DIR/__WORKER_ID__.log -> $LOG_FILE"

# Start Jupyter Lab immediately so it's available while worker initializes
echo "🚀 Starting Jupyter Lab on port 8888 (root: /workspace)..."
cd /workspace
nohup jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --ServerApp.token='' --ServerApp.password='' --ServerApp.root_dir=/workspace > /var/log/jupyter.log 2>&1 &
JUPYTER_PID=$!
echo "✅ Jupyter Lab started (PID: $JUPYTER_PID)"

cd "$WORKDIR" || exit 1

# Ensure core system dependencies exist (needed for venv + video processing)
echo "Installing system dependencies (python3.10-venv python3.10-dev ffmpeg git curl wget)..."
# Use explicit logs for postmortem; command output also goes to $LOG_FILE via exec/tee.
apt_retry "apt-get update" 300 bash -lc "apt-get -o Dpkg::Use-Pty=0 -o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update > '$APT_UPDATE_LOG' 2>&1"
apt_retry "apt-get install" 600 bash -lc "apt-get -o Dpkg::Use-Pty=0 -o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 install -y python3.10-venv python3.10-dev ffmpeg git curl wget > '$APT_INSTALL_LOG' 2>&1"
echo "✅ System dependencies installed"

ensure_uv_installed() {
    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
        echo "✅ uv already available at $UV_BIN"
        return 0
    fi

    echo "📦 Installing uv via Astral installer..."
    curl -LsSf https://astral.sh/uv/install.sh | sh || return 1
    export PATH="$HOME/.local/bin:$PATH"

    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
        echo "✅ uv installed at $UV_BIN"
        return 0
    fi

    if [ -x "$HOME/.local/bin/uv" ]; then
        UV_BIN="$HOME/.local/bin/uv"
        echo "✅ uv installed at $UV_BIN"
        return 0
    fi

    echo "❌ uv installation failed"
    return 1
}

echo "✅ Entering worker directory: $WORKDIR"

# Change to worker directory
cd "$WORKDIR"

echo "✅ Now in directory: $(pwd)" >> "$LOG_FILE" 2>&1
echo "✅ Directory contents:" >> "$LOG_FILE" 2>&1
ls -la >> "$LOG_FILE" 2>&1

echo "Worker ID: $WORKER_ID" >> "$LOG_FILE" 2>&1

# Force checkout to origin/main regardless of current branch/state.
# Persistent volumes may have stale feature branches that can't fast-forward.
echo "=== GIT SYNC ===" >> "$LOG_FILE" 2>&1

BEFORE_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
BEFORE_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
echo "Before: $BEFORE_COMMIT ($BEFORE_BRANCH)" >> "$LOG_FILE" 2>&1

timeout 30 git fetch origin main >> "$LOG_FILE" 2>&1 || {
    echo "Git fetch failed (exit $?); continuing with existing checkout" >> "$LOG_FILE" 2>&1
}
git checkout main >> "$LOG_FILE" 2>&1 || true
git reset --hard origin/main >> "$LOG_FILE" 2>&1 || {
    echo "Git reset failed (exit $?); continuing with existing checkout" >> "$LOG_FILE" 2>&1
}

echo "=== GIT SUBMODULE SYNC ===" >> "$LOG_FILE" 2>&1

# Persistent volumes can carry a stale Wan2GP/ from before the submodule
# migration. `git submodule update` does an internal `git clone`, which refuses
# to clone into a non-empty directory — bricking the spawn. Reconcile first.
if [ -f .gitmodules ] && grep -q 'path = Wan2GP' .gitmodules; then
    if [ -d Wan2GP ] && ! git submodule status Wan2GP >/dev/null 2>&1; then
        echo "Stale non-submodule Wan2GP/ on persistent volume; removing for clean clone" >> "$LOG_FILE" 2>&1
        rm -rf Wan2GP
    fi
fi

SUBMODULE_UPDATE_OK=1
timeout 300 git submodule update --init --recursive >> "$LOG_FILE" 2>&1 || SUBMODULE_UPDATE_OK=0

if [ -f .gitmodules ] && grep -q 'path = Wan2GP' .gitmodules; then
    if [ "$SUBMODULE_UPDATE_OK" != "1" ] || [ ! -d Wan2GP ] || [ -z "$(ls -A Wan2GP 2>/dev/null)" ]; then
        echo "❌ Wan2GP submodule is missing or empty after submodule update; refusing to start worker" >> "$LOG_FILE" 2>&1
        echo "❌ Wan2GP submodule is missing or empty after submodule update; refusing to start worker" >&2
        exit 1
    fi
    echo "✅ Wan2GP submodule populated" >> "$LOG_FILE" 2>&1
else
    echo "ℹ️  No Wan2GP submodule declared in this tree; skipping verification" >> "$LOG_FILE" 2>&1
fi

AFTER_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo "After:  $AFTER_COMMIT (main)" >> "$LOG_FILE" 2>&1

# Verify critical dependencies
echo "=== VERIFYING DEPENDENCIES ===" >> $LOG_FILE 2>&1
if command -v ffmpeg >/dev/null 2>&1; then
    echo "✅ FFmpeg found: $(which ffmpeg)" >> $LOG_FILE 2>&1
    echo "✅ FFmpeg version: $(ffmpeg -version 2>&1 | head -1)" >> $LOG_FILE 2>&1
else
    echo "❌ ERROR: FFmpeg not found! Worker cannot process videos" >> $LOG_FILE 2>&1
fi

if command -v git >/dev/null 2>&1; then
    echo "✅ Git found: $(which git)" >> $LOG_FILE 2>&1
else
    echo "❌ WARNING: Git not found!" >> $LOG_FILE 2>&1
fi

if command -v python3.10 >/dev/null 2>&1; then
    echo "✅ Python 3.10 found: $(which python3.10)" >> $LOG_FILE 2>&1
else
    echo "❌ WARNING: Python 3.10 not found!" >> $LOG_FILE 2>&1
fi

# Install or find uv, then migrate and sync on every startup
record_initial_deps_installing_or_exit
echo "=== UV BOOTSTRAP ===" >> "$LOG_FILE" 2>&1
ensure_uv_installed || exit 1
export PATH="$HOME/.local/bin:$PATH"
echo "uv path: $UV_BIN" >> "$LOG_FILE" 2>&1
echo "uv version: $("$UV_BIN" --version)" >> "$LOG_FILE" 2>&1

echo "=== LEGACY ENV MIGRATION ===" >> "$LOG_FILE" 2>&1
if [ ! -f ".uv-migrated" ]; then
    UV_MIGRATION_TS="$(date +%Y%m%d%H%M%S)"
    if [ -d "venv" ]; then
        echo "Backing up legacy venv -> venv.pre-uv-$UV_MIGRATION_TS" >> "$LOG_FILE" 2>&1
        mv "venv" "venv.pre-uv-$UV_MIGRATION_TS"
    fi
    if [ -d ".venv" ]; then
        echo "Backing up legacy .venv -> .venv.pre-uv-$UV_MIGRATION_TS" >> "$LOG_FILE" 2>&1
        mv ".venv" ".venv.pre-uv-$UV_MIGRATION_TS"
    fi
else
    echo "✅ .uv-migrated already present; skipping legacy env backup" >> "$LOG_FILE" 2>&1
fi

echo "=== DEPENDENCY SYNC (uv) ===" >> "$LOG_FILE" 2>&1
"$UV_BIN" sync --locked --python 3.10 --extra cuda124 >> "$LOG_FILE" 2>&1
touch .uv-migrated
echo "✅ uv sync complete; sentinel refreshed" >> "$LOG_FILE" 2>&1

update_worker_phase "deps_verified"

echo "=== VALIDATING IMPORTS ===" >> "$LOG_FILE" 2>&1
"$UV_BIN" run --python 3.10 --extra cuda124 python - << 'VALIDATE_EOF' >> "$LOG_FILE" 2>&1
import importlib

packages = [
    "torch",
    "dotenv",
    "fastapi",
]

for mod in packages:
    importlib.import_module(mod)
    print(f"OK: {mod}")
VALIDATE_EOF

# Verify worker.py exists
echo "=== CHECKING FILES ===" >> $LOG_FILE 2>&1
ls -la worker.py >> $LOG_FILE 2>&1

# Final pre-flight checks before starting worker
echo "=== PRE-FLIGHT CHECKS ===" >> $LOG_FILE 2>&1
echo "✅ uv: $UV_BIN" >> $LOG_FILE 2>&1
echo "✅ Python: $("$UV_BIN" run --python 3.10 --extra cuda124 python --version 2>&1)" >> $LOG_FILE 2>&1

if [ -f worker.py ]; then
    echo "✅ worker.py exists ($(wc -l < worker.py) lines)" >> $LOG_FILE 2>&1
else
    echo "❌ ERROR: worker.py not found!" >> $LOG_FILE 2>&1
    exit 1
fi

echo "✅ Checking environment variables..." >> $LOG_FILE 2>&1
echo "WORKER_ID: $WORKER_ID" >> $LOG_FILE 2>&1
echo "SUPABASE_URL: ${SUPABASE_URL:0:30}..." >> $LOG_FILE 2>&1
echo "SUPABASE_ANON_KEY: ${SUPABASE_ANON_KEY:0:20}..." >> $LOG_FILE 2>&1
echo "SUPABASE_SERVICE_ROLE_KEY: ${SUPABASE_SERVICE_ROLE_KEY:0:20}..." >> $LOG_FILE 2>&1
echo "MAX_TASK_WAIT_MINUTES: $MAX_TASK_WAIT_MINUTES" >> $LOG_FILE 2>&1

# Start the actual worker process
update_worker_phase "worker_starting"
echo "=== STARTING MAIN WORKER ===" >> $LOG_FILE 2>&1
PRELOAD_FLAG="__PRELOAD_FLAG__"
WORKER_CMD=(
    "$UV_BIN" run --python 3.10 --extra cuda124 python worker.py
    --supabase-url "$SUPABASE_URL"
    --supabase-access-token "$SUPABASE_SERVICE_ROLE_KEY"
    --worker "$WORKER_ID"
    --wgp-profile 1
)
if [ "${WORKER_DEBUG:-false}" = "true" ]; then
    WORKER_CMD+=(--debug)
fi
if [ -n "$PRELOAD_FLAG" ]; then
    WORKER_CMD+=($PRELOAD_FLAG)
fi
printf 'Command:' >> "$LOG_FILE" 2>&1
printf ' %q' "${WORKER_CMD[@]}" >> "$LOG_FILE" 2>&1
echo >> "$LOG_FILE" 2>&1
echo "Preload model: __PRELOAD_MODE__" >> $LOG_FILE 2>&1
echo "Starting at: $(date)" >> $LOG_FILE 2>&1

# Start worker in background with comprehensive logging
nohup "${WORKER_CMD[@]}" >> "$LOG_FILE" 2>&1 &
WORKER_PID=$!

echo "✅ Worker process started with PID: $WORKER_PID at $(date)" >> $LOG_FILE 2>&1

# Give the worker a moment to start and check if it's still running
sleep 2
if kill -0 $WORKER_PID 2>/dev/null; then
    echo "✅ Worker process $WORKER_PID is still running after 2 seconds" >> $LOG_FILE 2>&1
    update_worker_phase "ready"
else
    echo "❌ ERROR: Worker process $WORKER_PID died immediately!" >> $LOG_FILE 2>&1
    echo "Exit status was: $?" >> $LOG_FILE 2>&1
fi

echo "=========================================" >> $LOG_FILE 2>&1
echo "🏁 STARTUP SCRIPT COMPLETED SUCCESSFULLY" >> $LOG_FILE 2>&1
echo "=========================================" >> $LOG_FILE 2>&1
