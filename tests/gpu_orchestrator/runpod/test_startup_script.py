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


def test_launch_command_prefers_reigh_worker_workspace_layout():
    command = startup_script.build_launch_command("/tmp/start.sh", "gpu-worker-3")

    assert 'PRIMARY_DIR="$WORKSPACE_DIR/Reigh-Worker"' in command
    assert 'FALLBACK_DIR="$WORKSPACE_DIR/Headless-Wan2GP"' in command
    assert 'mkdir -p "$WORKDIR/logs"' in command
    assert 'nohup /tmp/start.sh > "$WORKDIR/logs/gpu-worker-3_startup.log" 2>&1 &' in command
