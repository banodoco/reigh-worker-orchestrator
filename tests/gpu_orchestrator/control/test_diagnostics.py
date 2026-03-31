from gpu_orchestrator.control import diagnostics


def test_worker_diagnostics_mixin_exists():
    assert hasattr(diagnostics, "WorkerDiagnosticsMixin")
