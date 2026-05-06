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
export REIGH_BACKEND="__WORKER_BACKEND__"
export WORKER_BACKEND="$REIGH_BACKEND"
export REIGH_WORKER_PROFILE="__WORKER_PROFILE__"
export WGP_PROFILE="$REIGH_WORKER_PROFILE"
export REIGH_WORKER_POOL="__WORKER_POOL__"
export WORKER_POOL="$REIGH_WORKER_POOL"
export REIGH_SELECTOR_NAMESPACE="__SELECTOR_NAMESPACE__"
export ROUTE_SELECTOR_NAMESPACE="$REIGH_SELECTOR_NAMESPACE"
export REIGH_SELECTOR_VERSION="__SELECTOR_VERSION__"
export ROUTE_SELECTOR_VERSION="$REIGH_SELECTOR_VERSION"
export REIGH_WORKER_CONTRACT_VERSION="__WORKER_CONTRACT_VERSION__"
export REIGH_WORKER_RUN_ID="__WORKER_RUN_ID__"
export WORKER_RUN_ID="$REIGH_WORKER_RUN_ID"
export REIGH_WARM_CACHE_PRELOAD_MODEL="__WARM_CACHE_PRELOAD_MODEL__"
export REIGH_WARM_CACHE_SOURCE="__WARM_CACHE_SOURCE__"
export REIGH_WARM_CACHE_SKIP_REASON="__WARM_CACHE_SKIP_REASON__"
if [ "$REIGH_BACKEND" = "vibecomfy" ] && [[ "$REIGH_WORKER_PROFILE" =~ ^[0-9]+$ ]]; then
    export VIBECOMFY_MEMORY_PROFILE="$REIGH_WORKER_PROFILE"
fi

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
echo "Route contract: backend=$REIGH_BACKEND profile=$REIGH_WORKER_PROFILE pool=$REIGH_WORKER_POOL selector=$REIGH_SELECTOR_NAMESPACE/${REIGH_SELECTOR_VERSION:-default} contract=$REIGH_WORKER_CONTRACT_VERSION run=${REIGH_WORKER_RUN_ID:-none}"
echo "Timestamp: $(date -Iseconds)"
echo "Initial PWD: $(pwd)"
echo "USER: $(whoami)"
echo "Kernel: $(uname -a | head -c 200)"
echo "Log file: $LOG_FILE"
echo

echo "=== EARLY DISK DIAGNOSTICS ==="
df -h /
df -h /workspace || true
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

wait_worker_preflight_ready_or_exit() {
    local worker_pid="$1"
    local timeout_sec="${REIGH_PREFLIGHT_READY_TIMEOUT_SEC:-900}"
    local started_at now elapsed body_file http_status preflight_status
    started_at=$(date +%s)
    body_file=$(mktemp) || exit 1

    echo "⏳ Waiting for worker preflight readiness (timeout ${timeout_sec}s)" >> "$LOG_FILE" 2>&1
    while true; do
        if ! kill -0 "$worker_pid" 2>/dev/null; then
            echo "❌ ERROR: Worker process $worker_pid exited before preflight passed" >> "$LOG_FILE" 2>&1
            rm -f "$body_file"
            exit 1
        fi

        http_status=$(curl -sS -m 5 \
            --output "$body_file" \
            --write-out '%{http_code}' \
            "${SUPABASE_URL}/rest/v1/workers?id=eq.${WORKER_ID}&select=metadata" \
            -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
            -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" 2>/dev/null || true)

        if [ "$http_status" = "200" ]; then
            preflight_status=$(python3 -c "
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as handle:
    rows = json.load(handle)
meta = rows[0].get('metadata', {}) if rows else {}
print(meta.get('preflight_status') or '')
" "$body_file" 2>/dev/null || true)
            if [ "$preflight_status" = "passed" ]; then
                echo "✅ Worker preflight passed; startup can mark ready" >> "$LOG_FILE" 2>&1
                rm -f "$body_file"
                return 0
            fi
            if [ "$preflight_status" = "failed" ]; then
                echo "❌ Worker preflight failed; refusing to mark ready" >> "$LOG_FILE" 2>&1
                cat "$body_file" >> "$LOG_FILE" 2>&1 || true
                rm -f "$body_file"
                exit 1
            fi
        else
            echo "⚠️  Preflight metadata fetch returned HTTP $http_status" >> "$LOG_FILE" 2>&1
        fi

        now=$(date +%s)
        elapsed=$((now - started_at))
        if [ "$elapsed" -ge "$timeout_sec" ]; then
            echo "❌ Timed out waiting for worker preflight after ${elapsed}s" >> "$LOG_FILE" 2>&1
            rm -f "$body_file"
            exit 1
        fi
        sleep 5
    done
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
    # An initialized submodule always has Wan2GP/.git (gitdir pointer file).
    # If the directory exists but lacks .git, it's stale leftover content from
    # the pre-submodule vendored era — `git submodule status` still returns 0
    # in that case (just with a `-` prefix), so checking exit code is wrong.
    if [ -d Wan2GP ] && [ ! -e Wan2GP/.git ]; then
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
SYNC_SENTINEL=".venv/.sync-inputs"
UV_VERSION_STR="$("$UV_BIN" --version 2>/dev/null || echo unknown)"
if [ -f uv.lock ]; then
    UV_LOCK_SHA256="$(sha256sum uv.lock | awk '{print $1}')"
else
    UV_LOCK_SHA256="MISSING"
fi
if [ -f pyproject.toml ]; then
    PYPROJECT_SHA256="$(sha256sum pyproject.toml | awk '{print $1}')"
else
    PYPROJECT_SHA256="MISSING"
fi
EXPECTED_INPUTS_HASH="$(
    {
        printf 'uv-version: %s\n' "$UV_VERSION_STR"
        printf 'python: 3.10\n'
        printf 'extras: cuda124\n'
        printf 'uv-lock-sha256: %s\n' "$UV_LOCK_SHA256"
        printf 'pyproject-sha256: %s\n' "$PYPROJECT_SHA256"
    } | sha256sum | awk '{print $1}'
)"
SYNC_SKIPPED=0
if [ -d .venv ] && [ -f "$SYNC_SENTINEL" ]; then
    RECORDED_INPUTS_HASH="$(cat "$SYNC_SENTINEL" 2>/dev/null | tr -d '[:space:]')"
    if [ "$RECORDED_INPUTS_HASH" = "$EXPECTED_INPUTS_HASH" ]; then
        SYNC_SKIPPED=1
    fi
fi

if [ "$SYNC_SKIPPED" = "1" ]; then
    echo "⏭️  Skipping uv sync: $SYNC_SENTINEL matches current inputs (hash=$EXPECTED_INPUTS_HASH; uv=$UV_VERSION_STR)" >> "$LOG_FILE" 2>&1
else
    rm -f "$SYNC_SENTINEL" 2>/dev/null || true
    SYNC_OUTPUT="$(mktemp)"
    # Wrap in `if` so the ERR trap does not fire on a non-zero rc — bash's
    # ERR trap ignores `set +e` but is suppressed inside a conditional.
    if "$UV_BIN" sync --locked --python 3.10 --extra cuda124 > "$SYNC_OUTPUT" 2>&1; then
        SYNC_RC=0
    else
        SYNC_RC=$?
    fi
    cat "$SYNC_OUTPUT" >> "$LOG_FILE" 2>&1
    if [ "$SYNC_RC" -eq 0 ]; then
        printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"
        echo "✅ uv sync complete; $SYNC_SENTINEL refreshed (hash=$EXPECTED_INPUTS_HASH; uv=$UV_VERSION_STR)" >> "$LOG_FILE" 2>&1
        rm -f "$SYNC_OUTPUT"
    elif grep -qiE 'lockfile.*needs to be updated' "$SYNC_OUTPUT"; then
        echo "⚠️  Lockfile drift detected on origin/main; regenerating lockfile in-pod and re-syncing (visit reigh-worker CI to fix upstream)" >> "$LOG_FILE" 2>&1
        rm -f "$SYNC_OUTPUT"
        "$UV_BIN" lock --python 3.10 >> "$LOG_FILE" 2>&1
        "$UV_BIN" sync --python 3.10 --extra cuda124 >> "$LOG_FILE" 2>&1
        UV_LOCK_SHA256="$(sha256sum uv.lock | awk '{print $1}')"
        EXPECTED_INPUTS_HASH="$(
            {
                printf 'uv-version: %s\n' "$UV_VERSION_STR"
                printf 'python: 3.10\n'
                printf 'extras: cuda124\n'
                printf 'uv-lock-sha256: %s\n' "$UV_LOCK_SHA256"
                printf 'pyproject-sha256: %s\n' "$PYPROJECT_SHA256"
            } | sha256sum | awk '{print $1}'
        )"
        printf '%s\n' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"
        echo "✅ uv sync complete via auto-heal; sentinel refreshed (post-heal hash=$EXPECTED_INPUTS_HASH; uv=$UV_VERSION_STR)" >> "$LOG_FILE" 2>&1
    else
        echo "❌ uv sync --locked failed (rc=$SYNC_RC) and output did not match lockfile-drift marker; aborting" >> "$LOG_FILE" 2>&1
        rm -f "$SYNC_OUTPUT"
        exit "$SYNC_RC"
    fi
fi
touch .uv-migrated

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

wait_worker_preflight_ready_or_exit "$WORKER_PID"
update_worker_phase "ready"

echo "=========================================" >> $LOG_FILE 2>&1
echo "🏁 STARTUP SCRIPT COMPLETED SUCCESSFULLY" >> $LOG_FILE 2>&1
echo "=========================================" >> $LOG_FILE 2>&1
