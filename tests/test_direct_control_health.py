from gpu_orchestrator.control.phases.health import HealthPhaseMixin


def test_direct_control_health_module_reference():
    assert HealthPhaseMixin.__name__ == "HealthPhaseMixin"
