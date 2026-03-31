"""Direct tests for script-based regression check modules."""

from __future__ import annotations

import scripts.tests.test_fal_tasks as fal_script_checks
import scripts.tests.test_qwen_edit_models as qwen_script_checks


def test_fal_script_parameter_checks_pass() -> None:
    assert fal_script_checks.run_parameter_building_checks()


def test_qwen_script_parameter_checks_pass() -> None:
    assert qwen_script_checks.run_parameter_building_checks()
