from gpu_orchestrator.control.phases import scaling_decision


def test_scaling_decision_phase_mixin_exists():
    assert hasattr(scaling_decision, "ScalingDecisionPhaseMixin")
