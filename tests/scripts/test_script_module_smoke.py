"""Additional direct imports to keep script test modules non-single-use."""

from __future__ import annotations

import scripts.tests.test_fal_tasks as fal_script_checks
import scripts.tests.test_qwen_edit_models as qwen_script_checks


def test_fal_script_module_exposes_runner() -> None:
    assert callable(fal_script_checks.run_parameter_building_checks)


def test_qwen_script_module_exposes_runner() -> None:
    assert callable(qwen_script_checks.run_parameter_building_checks)
