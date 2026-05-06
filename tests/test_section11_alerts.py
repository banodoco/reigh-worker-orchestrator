from __future__ import annotations

from pathlib import Path

import yaml


ALERTS_PATH = Path(__file__).resolve().parents[1] / "config" / "alerts" / "section11-canary.yaml"

REQUIRED_RULES = {
    "latency_p95_ratio": {
        "name": "canary_latency_p95_ratio_high",
        "severity": "warning",
    },
    "vram_peak_ratio": {
        "name": "canary_vram_peak_ratio_high",
        "severity": "warning",
    },
    "oom_count": {
        "name": "canary_oom_count_nonzero",
        "severity": "critical",
    },
    "canary_output_divergence": {
        "name": "canary_output_divergence_detected",
        "severity": "critical",
    },
    "missing_runtime_evidence": {
        "name": "canary_missing_runtime_evidence",
        "severity": "critical",
    },
    "stale_route_workers": {
        "name": "canary_stale_route_workers",
        "severity": "critical",
    },
    "quota_alerts": {
        "name": "canary_quota_alerts_active",
        "severity": "warning",
    },
    "claim_suppression": {
        "name": "canary_claim_suppression_active",
        "severity": "warning",
    },
    "completion_billing_failure": {
        "name": "canary_completion_billing_failure",
        "severity": "critical",
    },
    "non_rayworker_route_smoke_failure": {
        "name": "canary_non_rayworker_route_smoke_failure",
        "severity": "critical",
    },
}


def _load_alert_config() -> dict:
    with ALERTS_PATH.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_section11_canary_alerts_cover_required_rules() -> None:
    config = _load_alert_config()

    assert config["schema_version"] == 1
    assert config["section"] == 11
    assert config["provider"] == "provider-neutral"

    rules = config["rules"]
    by_metric = {rule["metric_key"]: rule for rule in rules}
    assert set(by_metric) == set(REQUIRED_RULES)

    for metric_key, expected in REQUIRED_RULES.items():
        rule = by_metric[metric_key]
        assert rule["name"] == expected["name"]
        assert rule["severity"] == expected["severity"]
        assert rule["metric_key"] == metric_key


def test_section11_canary_alerts_have_machine_readable_runbook_refs() -> None:
    config = _load_alert_config()

    names = set()
    for rule in config["rules"]:
        names.add(rule["name"])
        assert rule["name"].startswith("canary_")
        assert rule["severity"] in {"warning", "critical"}
        assert rule["expression"]
        assert rule["for"].endswith("m")
        assert rule["summary"]
        assert rule["runbook"].startswith("tasks/")
        assert "#" in rule["runbook"]
        assert rule["labels"]["gate"]
        assert rule["labels"]["source"]

    assert len(names) == len(config["rules"])
