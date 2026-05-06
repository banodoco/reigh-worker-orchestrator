from gpu_orchestrator.runpod import startup_script


def test_startup_script_builders_exist():
    assert callable(startup_script.render_startup_script)
    assert callable(startup_script.build_launch_command)


def test_rendered_startup_script_records_legacy_startup_phases_in_order():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-1",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=False,
    )

    phase_markers = [
        'record_initial_deps_installing_or_exit',
        'update_worker_phase "deps_verified"',
        'update_worker_phase "worker_starting"',
        'update_worker_phase "ready"',
    ]
    phase_positions = [script.index(marker) for marker in phase_markers]

    assert startup_script.STARTUP_PHASE_SEQUENCE == (
        "deps_installing",
        "deps_verified",
        "worker_starting",
        "ready",
    )
    assert phase_positions == sorted(phase_positions)


def test_rendered_startup_script_uses_strict_helper_for_initial_phase_patch():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-1",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=True,
    )

    assert startup_script.STRICT_STARTUP_PHASE == "deps_installing"
    assert startup_script.STRICT_STARTUP_PHASE_ATTEMPTS == 3
    assert "update_worker_phase_strict()" in script
    assert "record_initial_deps_installing_or_exit()" in script
    assert "--write-out '%{http_code}'" in script
    assert "curl --fail" not in script
    assert "for attempt in 1 2 3; do" in script
    assert 'if update_worker_phase_strict "deps_installing"; then' in script
    assert "exit 1" in script


def test_rendered_startup_script_marks_ready_only_after_preflight_passes():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-1",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=True,
    )

    assert "wait_worker_preflight_ready_or_exit()" in script
    assert 'preflight_status" = "passed"' in script
    assert 'preflight_status" = "failed"' in script
    assert "kill -0 \"$worker_pid\"" in script
    assert "is still running after 2 seconds" not in script
    assert script.index('wait_worker_preflight_ready_or_exit "$WORKER_PID"') < script.index('update_worker_phase "ready"')


def test_rendered_startup_script_exports_selected_pool_contract():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-vibe",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=True,
        worker_backend="vibecomfy",
        worker_profile="3",
        worker_pool="gpu-vibecomfy-canary",
        selector_namespace="canary",
        selector_version="42",
        worker_contract_version=2,
        worker_run_id="run-abc",
    )

    assert 'export REIGH_BACKEND="vibecomfy"' in script
    assert 'export WORKER_BACKEND="$REIGH_BACKEND"' in script
    assert 'export REIGH_WORKER_PROFILE="3"' in script
    assert 'export WGP_PROFILE="$REIGH_WORKER_PROFILE"' in script
    assert 'export REIGH_WORKER_POOL="gpu-vibecomfy-canary"' in script
    assert 'export REIGH_SELECTOR_NAMESPACE="canary"' in script
    assert 'export REIGH_SELECTOR_VERSION="42"' in script
    assert 'export REIGH_WORKER_CONTRACT_VERSION="2"' in script
    assert 'export REIGH_WORKER_RUN_ID="run-abc"' in script
    assert 'export VIBECOMFY_MEMORY_PROFILE="$REIGH_WORKER_PROFILE"' in script
    assert "backend=$REIGH_BACKEND profile=$REIGH_WORKER_PROFILE" in script
    assert 'export REIGH_WARM_CACHE_PRELOAD_MODEL=""' in script
    assert 'export REIGH_WARM_CACHE_SKIP_REASON="pending_tasks"' in script


def test_rendered_startup_script_logs_early_disk_diagnostics_before_package_work():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-1",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=True,
    )

    logging_index = script.index('exec > >(tee -a "$LOG_FILE") 2>&1')
    diagnostics_index = script.index("=== EARLY DISK DIAGNOSTICS ===")
    root_df_index = script.index("\ndf -h /\n")
    workspace_df_index = script.index("\ndf -h /workspace || true\n")
    apt_update_index = script.index('apt_retry "apt-get update"')

    assert logging_index < diagnostics_index < root_df_index
    assert root_df_index < workspace_df_index < apt_update_index


def test_rendered_startup_script_bootstraps_uv_and_runs_locked_sync():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-2",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=False,
    )

    assert 'PRIMARY_DIR="$WORKSPACE_DIR/Reigh-Worker"' in script
    assert 'git clone https://github.com/banodoco/Reigh-Worker.git "$PRIMARY_DIR"' in script
    assert "ensure_uv_installed()" in script
    assert "curl -LsSf https://astral.sh/uv/install.sh | sh" in script
    assert 'if [ ! -f ".uv-migrated" ]; then' in script
    assert 'mv "venv" "venv.pre-uv-$UV_MIGRATION_TS"' in script
    assert 'mv ".venv" ".venv.pre-uv-$UV_MIGRATION_TS"' in script
    assert '"$UV_BIN" sync --locked --python 3.10 --extra cuda124' in script
    assert 'touch .uv-migrated' in script
    assert '"$UV_BIN" run --python 3.10 --extra cuda124 python worker.py' in script
    assert 'update_worker_phase "deps_verified"' in script


def test_rendered_startup_script_uses_backend_profile_warm_cache_manifest(monkeypatch):
    monkeypatch.setenv(
        "REIGH_WARM_CACHE_CONFIG",
        '{"routes":[{"backend":"vibecomfy","profile":"3","preload_model":"vibe-cache"}]}',
    )
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-2",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=False,
        worker_backend="vibecomfy",
        worker_profile="3",
    )

    assert 'export REIGH_WARM_CACHE_PRELOAD_MODEL="vibe-cache"' in script
    assert 'export REIGH_WARM_CACHE_SOURCE="manifest"' in script
    assert '--preload-model vibe-cache' in script


def test_rendered_startup_script_preserves_pending_task_warm_cache_skip():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-2",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=True,
        worker_backend="wgp",
        worker_profile="1",
    )

    assert 'export REIGH_WARM_CACHE_PRELOAD_MODEL=""' in script
    assert 'export REIGH_WARM_CACHE_SOURCE="pending_task_guard"' in script
    assert 'export REIGH_WARM_CACHE_SKIP_REASON="pending_tasks"' in script
    assert "--preload-model" not in script


def test_rendered_startup_script_gates_uv_sync_on_inputs_sentinel():
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-2",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=False,
    )

    assert 'SYNC_SENTINEL=".venv/.sync-inputs"' in script
    assert '"$UV_BIN" --version' in script
    assert "python: 3.10" in script
    assert "extras: cuda124" in script
    assert "sha256sum uv.lock" in script
    assert "sha256sum pyproject.toml" in script

    remove_sentinel_index = script.index('rm -f "$SYNC_SENTINEL"')
    sync_index = script.index('"$UV_BIN" sync --locked')
    write_sentinel_index = script.index(
        'printf \'%s\\n\' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"'
    )
    skip_log_index = script.index("\u23ed\ufe0f  Skipping uv sync")

    assert remove_sentinel_index < sync_index < write_sentinel_index
    assert skip_log_index < sync_index


def test_rendered_startup_script_auto_heals_lockfile_drift():
    """Existing sentinel test still passes because the success arm preserves the first sentinel write."""
    script = startup_script.render_startup_script(
        worker_id="gpu-worker-2",
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="service",
        replicate_api_token="replicate",
        max_task_wait_minutes=7,
        has_pending_tasks=False,
    )

    locked_sync_index = script.index('if "$UV_BIN" sync --locked')
    sync_rc_then_index = script.index("SYNC_RC=0", locked_sync_index)
    sync_rc_else_index = script.index("SYNC_RC=$?", sync_rc_then_index)
    warning_index = script.index("⚠️  Lockfile drift detected")
    lock_index = script.index('"$UV_BIN" lock --python 3.10', warning_index)
    recovery_sync_index = script.index(
        '"$UV_BIN" sync --python 3.10 --extra cuda124',
        lock_index,
    )
    recompute_index = script.index(
        "UV_LOCK_SHA256=\"$(sha256sum uv.lock | awk '{print $1}')\"",
        warning_index,
    )
    auto_heal_write_sentinel_index = script.rindex(
        'printf \'%s\\n\' "$EXPECTED_INPUTS_HASH" > "$SYNC_SENTINEL"'
    )

    assert locked_sync_index < sync_rc_then_index < sync_rc_else_index < warning_index
    assert "grep -qiE 'lockfile.*needs to be updated'" in script
    assert "⚠️  Lockfile drift detected" in script
    assert '"$UV_BIN" lock --python 3.10' in script
    assert '"$UV_BIN" sync --python 3.10 --extra cuda124' in script
    assert recompute_index > warning_index
    assert warning_index < lock_index < recovery_sync_index < auto_heal_write_sentinel_index
    assert "✅ uv sync complete via auto-heal" in script
    assert "output did not match lockfile-drift marker" in script


def test_launch_command_prefers_reigh_worker_workspace_layout():
    command = startup_script.build_launch_command("/tmp/start.sh", "gpu-worker-3")

    assert 'PRIMARY_DIR="$WORKSPACE_DIR/Reigh-Worker"' in command
    assert 'FALLBACK_DIR="$WORKSPACE_DIR/Headless-Wan2GP"' in command
    assert 'mkdir -p "$WORKDIR/logs"' in command
    assert 'nohup /tmp/start.sh > "$WORKDIR/logs/gpu-worker-3_startup.log" 2>&1 &' in command
