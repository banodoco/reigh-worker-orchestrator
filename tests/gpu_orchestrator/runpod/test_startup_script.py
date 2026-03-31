from gpu_orchestrator.runpod import startup_script


def test_startup_script_builders_exist():
    assert callable(startup_script.render_startup_script)
    assert callable(startup_script.build_launch_command)
