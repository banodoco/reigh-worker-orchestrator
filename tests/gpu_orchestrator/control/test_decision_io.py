from gpu_orchestrator.control import decision_io


def test_decision_logging_mixin_exists():
    assert hasattr(decision_io, "DecisionLoggingMixin")
