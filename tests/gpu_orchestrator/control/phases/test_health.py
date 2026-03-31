from gpu_orchestrator.control.phases import health


def test_health_phase_mixin_exists():
    assert hasattr(health, "HealthPhaseMixin")
