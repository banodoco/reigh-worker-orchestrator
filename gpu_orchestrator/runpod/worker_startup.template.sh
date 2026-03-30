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
PRIMARY_DIR="$WORKSPACE_DIR/Headless-Wan2GP"
FALLBACK_DIR="$WORKSPACE_DIR/Reigh-Worker"

if [ -d "$PRIMARY_DIR" ]; then
    WORKDIR="$PRIMARY_DIR"
    echo "✅ Using worker directory: $WORKDIR"
elif [ -d "$FALLBACK_DIR" ]; then
    WORKDIR="$FALLBACK_DIR"
    echo "⚠️  Headless-Wan2GP not found; using fallback worker directory: $WORKDIR"
else
    echo "📦 Neither Headless-Wan2GP nor Reigh-Worker found. Cloning Headless-Wan2GP into $PRIMARY_DIR..."
    cd "$WORKSPACE_DIR" || exit 1
    git clone https://github.com/peteromallet/Headless-Wan2GP || exit 1
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
echo "Installing system dependencies (python3.10-venv ffmpeg git curl wget)..."
# Use explicit logs for postmortem; command output also goes to $LOG_FILE via exec/tee.
apt_retry "apt-get update" 300 bash -lc "apt-get -o Dpkg::Use-Pty=0 -o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 update > '$APT_UPDATE_LOG' 2>&1"
apt_retry "apt-get install" 600 bash -lc "apt-get -o Dpkg::Use-Pty=0 -o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 install -y python3.10-venv ffmpeg git curl wget > '$APT_INSTALL_LOG' 2>&1"
echo "✅ System dependencies installed"

# Check if venv exists, if not create it and install dependencies
if [ ! -d "venv" ] || [ ! -f "venv/bin/activate" ]; then
    echo "⚠️  Virtual environment missing or incomplete, building..."

    echo "Creating virtual environment..."
    python3.10 -m venv venv || exit 1

    echo "Activating venv and installing PyTorch..."
    source venv/bin/activate || exit 1
    pip install --no-cache-dir torch==2.6.0 torchvision torchaudio -f https://download.pytorch.org/whl/cu124 || exit 1

    if [ -f Wan2GP/requirements.txt ]; then
        echo "Installing Wan2GP requirements..."
        pip install --no-cache-dir -r Wan2GP/requirements.txt || exit 1
    fi

    echo "Installing worker requirements..."
    pip install --no-cache-dir -r requirements.txt || exit 1

    echo "✅ Virtual environment build complete"
else
    echo "✅ Virtual environment exists, activating..."
    source venv/bin/activate || exit 1

    # Check if dependencies are installed by testing for a key package
    if ! python -c "import torch, dotenv" 2>/dev/null; then
        echo "⚠️  Dependencies missing in venv, installing..."

        echo "Installing PyTorch..."
        pip install --no-cache-dir torch==2.6.0 torchvision torchaudio -f https://download.pytorch.org/whl/cu124 || exit 1

        if [ -f Wan2GP/requirements.txt ]; then
            echo "Installing Wan2GP requirements..."
            pip install --no-cache-dir -r Wan2GP/requirements.txt || exit 1
        fi

        echo "Installing worker requirements..."
        pip install --no-cache-dir -r requirements.txt || exit 1

        echo "✅ Dependencies installed"
    else
        echo "✅ Dependencies already installed"
    fi
fi

echo "✅ Entering worker directory: $WORKDIR"

# Change to worker directory
cd "$WORKDIR"

echo "✅ Now in directory: $(pwd)" >> "$LOG_FILE" 2>&1
echo "✅ Directory contents:" >> "$LOG_FILE" 2>&1
ls -la >> "$LOG_FILE" 2>&1

echo "Worker ID: $WORKER_ID" >> "$LOG_FILE" 2>&1

# Try git pull (but don't fail if it times out)
echo "=== GIT PULL ===" >> "$LOG_FILE" 2>&1

# Capture commit before pull
BEFORE_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo "Before commit: $BEFORE_COMMIT" >> "$LOG_FILE" 2>&1

# Perform pull with timeout and record exit status
# Use || true to prevent set -e from exiting on failure (divergent branches, conflicts, etc.)
timeout 30 git pull --ff-only origin main >> "$LOG_FILE" 2>&1 || {
    GIT_PULL_EXIT=$?
    echo "Git pull failed (exit $GIT_PULL_EXIT), trying git reset --hard to sync with remote..." >> "$LOG_FILE" 2>&1
    # Reset to remote state to handle divergent branches
    git fetch origin main >> "$LOG_FILE" 2>&1 || true
    git reset --hard origin/main >> "$LOG_FILE" 2>&1 || {
        echo "Git reset also failed, continuing with existing code" >> "$LOG_FILE" 2>&1
    }
}

# Capture commit after pull to detect if code actually changed
AFTER_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
echo "After commit:  $AFTER_COMMIT" >> "$LOG_FILE" 2>&1

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

# Activate virtual environment
echo "=== ACTIVATING VIRTUAL ENV ===" >> $LOG_FILE 2>&1
source venv/bin/activate
echo "Virtual env activated: $VIRTUAL_ENV" >> $LOG_FILE 2>&1
echo "Python path: $(which python)" >> $LOG_FILE 2>&1
echo "Python version: $(python --version)" >> $LOG_FILE 2>&1

# Update Python dependencies only if requirements.txt changed (hash-based check)
echo "=== DEPENDENCY UPDATE (hash-based) ===" >> $LOG_FILE 2>&1

# Compute current hash of requirements files
MAIN_REQS_HASH=$(md5sum requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "none")
SUB_REQS_HASH=$(md5sum Wan2GP/requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "none")
CURRENT_HASH="${MAIN_REQS_HASH}_${SUB_REQS_HASH}"

# Load cached hash (stored in venv to persist across restarts)
CACHED_HASH=$(cat venv/.requirements_hash 2>/dev/null || echo "")

echo "Current requirements hash: $CURRENT_HASH" >> $LOG_FILE 2>&1
echo "Cached requirements hash: $CACHED_HASH" >> $LOG_FILE 2>&1

if [ "$CURRENT_HASH" != "$CACHED_HASH" ]; then
    echo "Requirements changed! Installing new/changed Python deps..." >> $LOG_FILE 2>&1
    python -m pip install -r requirements.txt >> $LOG_FILE 2>&1 || echo "WARNING: pip install failed" >> $LOG_FILE 2>&1

    # Also install subfolder requirements if present
    if [ -f Wan2GP/requirements.txt ]; then
        echo "Installing new/changed deps from Wan2GP/requirements.txt" >> $LOG_FILE 2>&1
        python -m pip install -r Wan2GP/requirements.txt >> $LOG_FILE 2>&1 || echo "WARNING: subfolder pip install failed" >> $LOG_FILE 2>&1
    fi

    # Save new hash
    echo "$CURRENT_HASH" > venv/.requirements_hash
    echo "✅ Dependencies updated, hash saved" >> $LOG_FILE 2>&1
else
    echo "✅ Requirements unchanged, skipping pip install" >> $LOG_FILE 2>&1
fi

# Install all known missing packages from Headless-Wan2GP requirements.txt
echo "=== INSTALLING CRITICAL PACKAGES ===" >> $LOG_FILE 2>&1
python -m pip install --quiet \
    mmgp==3.6.10 \
    GitPython \
    smplfitter \
    s3tokenizer \
    conformer \
    >> $LOG_FILE 2>&1 || echo "WARNING: some critical package installs failed" >> $LOG_FILE 2>&1

# Validate all critical imports before starting worker
echo "=== VALIDATING IMPORTS ===" >> $LOG_FILE 2>&1
python << 'VALIDATE_EOF' >> $LOG_FILE 2>&1
import sys
failed = []
packages = [
    ('mmgp', 'mmgp'),
    ('mmgp.fp8_quanto_bridge', 'mmgp'),
    ('git', 'GitPython'),
    ('smplfitter', 'smplfitter'),
    ('s3tokenizer', 's3tokenizer'),
    ('conformer', 'conformer'),
]
for mod, pkg in packages:
    try:
        __import__(mod)
        print(f"OK: {mod}")
    except ImportError as e:
        print(f"MISSING: {mod} (pip install {pkg})")
        failed.append(pkg)

if failed:
    print(f"Missing packages: {', '.join(failed)}")
    print("Attempting to install...")
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet'] + list(set(failed)))
    # Re-check
    still_missing = []
    for mod, pkg in packages:
        try:
            __import__(mod)
        except ImportError:
            still_missing.append(pkg)
    if still_missing:
        print(f"FATAL: Still missing after install: {', '.join(still_missing)}")
        sys.exit(1)
    print("All packages installed successfully on retry")
else:
    print("All critical imports validated")
VALIDATE_EOF

if [ $? -ne 0 ]; then
    echo "❌ Import validation failed!" >> $LOG_FILE 2>&1
    exit 1
fi

# Verify worker.py exists
echo "=== CHECKING FILES ===" >> $LOG_FILE 2>&1
ls -la worker.py >> $LOG_FILE 2>&1

# Final pre-flight checks before starting worker
echo "=== PRE-FLIGHT CHECKS ===" >> $LOG_FILE 2>&1
echo "✅ Virtual env: $VIRTUAL_ENV" >> $LOG_FILE 2>&1
echo "✅ Python: $(which python) ($(python --version 2>&1))" >> $LOG_FILE 2>&1

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
echo "=== STARTING MAIN WORKER ===" >> $LOG_FILE 2>&1
PRELOAD_FLAG="__PRELOAD_FLAG__"
WORKER_CMD="python worker.py --supabase-url $SUPABASE_URL --supabase-access-token $SUPABASE_SERVICE_ROLE_KEY --worker $WORKER_ID --debug $PRELOAD_FLAG --wgp-profile 1"
echo "Command: $WORKER_CMD" >> $LOG_FILE 2>&1
echo "Preload model: __PRELOAD_MODE__" >> $LOG_FILE 2>&1
echo "Starting at: $(date)" >> $LOG_FILE 2>&1

# Start worker in background with comprehensive logging
nohup $WORKER_CMD >> $LOG_FILE 2>&1 &
WORKER_PID=$!

echo "✅ Worker process started with PID: $WORKER_PID at $(date)" >> $LOG_FILE 2>&1

# Give the worker a moment to start and check if it's still running
sleep 2
if kill -0 $WORKER_PID 2>/dev/null; then
    echo "✅ Worker process $WORKER_PID is still running after 2 seconds" >> $LOG_FILE 2>&1
else
    echo "❌ ERROR: Worker process $WORKER_PID died immediately!" >> $LOG_FILE 2>&1
    echo "Exit status was: $?" >> $LOG_FILE 2>&1
fi

echo "=========================================" >> $LOG_FILE 2>&1
echo "🏁 STARTUP SCRIPT COMPLETED SUCCESSFULLY" >> $LOG_FILE 2>&1
echo "=========================================" >> $LOG_FILE 2>&1
