"""Guardrails for product-scoped agent instructions and schedules."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_root_agent_instructions_are_framework_scoped():
    root = _read("AGENTS.md")
    assert "CurveLens Core Framework" in root
    assert "deployments/wti/AGENTS.md" in root
    assert "deployments/brent/AGENTS.md" in root
    assert "deployments/gold/AGENTS.md" in root
    assert "deployments/copper/AGENTS.md" in root
    assert "deployments/corn/AGENTS.md" in root
    assert "deployments/silver/AGENTS.md" in root
    assert "CCVM_PRODUCT=wti" not in root
    assert "Section 63" not in root
    assert "EIA flash" not in root


def test_readme_exposes_one_sentence_product_deployment():
    readme = _read("README.md")
    deployments = _read("deployments/README.md")
    for sentence in (
        "Operate the CurveLens WTI deployment.",
        "Operate the CurveLens Brent deployment.",
        "Operate the CurveLens Gold deployment.",
        "Operate the CurveLens Copper deployment.",
        "Operate the CurveLens Corn deployment.",
        "Operate the CurveLens Silver deployment.",
    ):
        assert sentence in readme
        assert sentence in deployments
    assert "$curvelens-daily-analysis" in readme
    assert "agent/analysis_orchestrator.py" in readme


def test_each_product_has_a_minimal_deployment_instruction_set():
    for product in ("wti", "brent", "gold", "copper", "corn", "silver"):
        base = ROOT / "deployments" / product
        for filename in ("AGENTS.md", "cron.example"):
            assert (base / filename).exists(), f"missing deployments/{product}/{filename}"
        for filename in ("IDENTITY.md", "SOUL.md", "HEARTBEAT.md"):
            assert not (base / filename).exists(), f"duplicated deployments/{product}/{filename}"


def test_wti_runbook_is_explicit_and_product_scoped():
    runbook = _read("deployments/wti/AGENTS.md")
    cron = _read("deployments/wti/cron.example")
    assert "export CCVM_PRODUCT=wti" in runbook
    assert "ccvm/data/products/wti" in runbook
    assert "Section 63" in runbook
    assert "$curvelens-daily-analysis" in runbook
    assert "CCVM_PRODUCT=wti" in cron
    assert "$curvelens-daily-analysis" in cron
    assert "--session isolated" in cron
    assert "--no-deliver" in cron
    assert cron.count("--disabled") >= 1
    assert "python agent/" not in cron


def test_gold_runbook_is_experimental_and_cannot_schedule_itself():
    runbook = _read("deployments/gold/AGENTS.md")
    cron = _read("deployments/gold/cron.example")
    assert "export CCVM_PRODUCT=gold" in runbook
    assert "ccvm/data/products/gold" in runbook
    assert "Section 64" in runbook
    assert "experimental — validation only" in runbook
    assert "Never run `agent/event_run.py --event eia`" in runbook
    assert "openclaw cron add" not in cron
    assert "Do not register or enable schedules" in cron


def test_corn_runbook_is_experimental_and_product_scoped():
    runbook = _read("deployments/corn/AGENTS.md")
    cron = _read("deployments/corn/cron.example")
    assert "export CCVM_PRODUCT=corn" in runbook
    assert "ccvm/data/products/corn" in runbook
    assert "Section 56" in runbook
    assert "experimental — validation only" in runbook
    assert "USDA_NASS_API_KEY" in runbook
    assert "openclaw cron add" not in cron
    assert "Do not register or enable schedules" in cron


def test_silver_runbook_is_experimental_and_product_scoped():
    runbook = _read("deployments/silver/AGENTS.md")
    cron = _read("deployments/silver/cron.example")
    assert "export CCVM_PRODUCT=silver" in runbook
    assert "ccvm/data/products/silver" in runbook
    assert "Section 64" in runbook
    assert "experimental — validation only" in runbook
    assert "FRED_API_KEY" in runbook
    assert "SO1" in runbook
    assert "openclaw cron add" not in cron
    assert "Do not register or enable schedules" in cron


def test_copper_runbook_is_experimental_and_product_scoped():
    runbook = _read("deployments/copper/AGENTS.md")
    cron = _read("deployments/copper/cron.example")
    assert "export CCVM_PRODUCT=copper" in runbook
    assert "ccvm/data/products/copper" in runbook
    assert "Section 64" in runbook
    assert "experimental — validation only" in runbook
    assert "FRED_API_KEY" in runbook
    assert "HX CALL" in runbook
    assert "openclaw cron add" not in cron
    assert "Do not register or enable schedules" in cron


def test_brent_runbook_requires_authorized_ice_data():
    runbook = _read("deployments/brent/AGENTS.md")
    cron = _read("deployments/brent/cron.example")
    assert "export CCVM_PRODUCT=brent" in runbook
    assert "ccvm/data/products/brent" in runbook
    assert "authorized_market_data" in runbook
    assert "NEED_AUTHORIZED_MARKET_DATA" in runbook
    assert "experimental — validation only" in runbook
    assert "EIA_API_KEY" in runbook
    assert "FRED_API_KEY" in runbook
    assert "openclaw cron add" not in cron
    assert "Do not register or enable schedules" in cron


def test_legacy_root_cron_template_is_removed():
    assert not (ROOT / "config" / "cron.example").exists()


def test_gold_has_one_operational_document():
    assert not (ROOT / "GOLD_SETUP.md").exists()
    runbook = _read("deployments/gold/AGENTS.md")
    assert "not a fork" in runbook
    assert "ccvm/config/markets/gold.yaml" in runbook
