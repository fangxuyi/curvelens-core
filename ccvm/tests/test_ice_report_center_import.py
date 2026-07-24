"""Official ICE Report Center Brent CSV importer."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date

import pytest

from ccvm.importers.ice_report_center import import_brent_reports
from ccvm.reference.product import load_product

FUTURES = """TRADE_DATE,SETTLEMENT_PRICE,LONG_NAME,TOTAL_VOLUME,PRODUCT,OPEN_INTEREST,STRIP
07/22/2026,82.15,Brent Crude Futures,1200,Brent Crude Futures,3400,Sep26
07/22/2026,81.70,Brent Crude Futures,900,Brent Crude Futures,3200,Oct-26
07/22/2026,70.00,WTI Crude Futures,1,WTI Crude Futures,2,Sep26
"""

OPTIONS = """TRADE_DATE,SETTLEMENT_PRICE,LONG_NAME,TOTAL_VOLUME,PRODUCT,OPEN_INTEREST,STRIP,PUT_CALL,STRIKE,OPTION_VOLATILITY,DELTA_FACTOR
2026-07-22,2.10,Options on Brent Futures,25,Brent Crude Futures,100,Sep26,Call,82,0.31,0.52
2026-07-22,1.95,Options on Brent Futures,20,Brent Crude Futures,90,Sep26,Put,82,0.30,-0.48
2026-07-22,0.50,Options on WTI Futures,1,WTI Crude Futures,2,Sep26,Call,82,0.20,0.10
"""


def _write_inputs(tmp_path, futures=FUTURES, options=OPTIONS):
    futures_path = tmp_path / "futures.csv"
    options_path = tmp_path / "options.csv"
    futures_path.write_text(futures)
    options_path.write_text(options)
    return futures_path, options_path


def test_imports_official_ice_schema_and_records_provenance(tmp_path):
    futures_path, options_path = _write_inputs(tmp_path)
    data_dir = tmp_path / "data"
    result = import_brent_reports(
        futures_csv=futures_path,
        options_csv=options_path,
        trade_date=date(2026, 7, 22),
        data_dir=data_dir,
        product=load_product("brent"),
    )

    futures = json.loads(result.futures_path.read_text())
    options = json.loads(result.options_path.read_text())
    manifest = json.loads(result.manifest_path.read_text())
    assert [row["contract_code"] for row in futures["settlements"]] == [
        "BU26", "BV26",
    ]
    assert options["settlements"][0] == {
        "call_put": "C",
        "delta": 0.52,
        "implied_vol": 0.31,
        "open_interest": 100.0,
        "option_expiry": "2026-07-28",
        "settlement": 2.1,
        "strike": 82.0,
        "trade_date": "2026-07-22",
        "underlying_contract": "BU26",
        "underlying_delivery_month": "2026-09",
        "volume": 25.0,
    }
    assert manifest["sources"]["futures"]["report_url"].endswith("/report/10")
    assert manifest["sources"]["options"]["report_url"].endswith("/report/166")
    assert manifest["sources"]["futures"]["excluded_non_brent_rows"] == 1
    archived = data_dir / "ice_report_center/trade_date=2026-07-22/report-10-futures.csv"
    assert manifest["sources"]["futures"]["sha256"] == hashlib.sha256(
        archived.read_bytes()
    ).hexdigest()


def test_rejects_requested_date_not_present(tmp_path):
    futures_path, options_path = _write_inputs(tmp_path)
    with pytest.raises(ValueError, match="does not contain requested trade date"):
        import_brent_reports(
            futures_csv=futures_path,
            options_csv=options_path,
            trade_date=date(2026, 7, 23),
            data_dir=tmp_path / "data",
            product=load_product("brent"),
        )


def test_rejects_file_without_identifiable_brent_rows(tmp_path):
    futures_path, options_path = _write_inputs(
        tmp_path,
        futures=FUTURES.replace("Brent", "WTI"),
    )
    with pytest.raises(ValueError, match="no rows identifiable as ICE Brent"):
        import_brent_reports(
            futures_csv=futures_path,
            options_csv=options_path,
            trade_date=date(2026, 7, 22),
            data_dir=tmp_path / "data",
            product=load_product("brent"),
        )


def test_rejects_conflicting_duplicate_settlements(tmp_path):
    duplicate = FUTURES + (
        "07/22/2026,99.00,Brent Crude Futures,1200,"
        "Brent Crude Futures,3400,Sep26\n"
    )
    futures_path, options_path = _write_inputs(tmp_path, futures=duplicate)
    with pytest.raises(ValueError, match="conflicting duplicate futures"):
        import_brent_reports(
            futures_csv=futures_path,
            options_csv=options_path,
            trade_date=date(2026, 7, 22),
            data_dir=tmp_path / "data",
            product=load_product("brent"),
        )


def test_rejects_overwrite_of_different_archived_source(tmp_path):
    futures_path, options_path = _write_inputs(tmp_path)
    data_dir = tmp_path / "data"
    kwargs = {
        "futures_csv": futures_path,
        "options_csv": options_path,
        "trade_date": date(2026, 7, 22),
        "data_dir": data_dir,
        "product": load_product("brent"),
    }
    import_brent_reports(**kwargs)
    futures_path.write_text(FUTURES.replace("82.15", "82.16"))
    with pytest.raises(ValueError, match="different source bytes"):
        import_brent_reports(**kwargs)


def test_missing_handoff_reports_official_source_urls(tmp_path):
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update({"CCVM_PRODUCT": "brent", "CCVM_DATA_DIR": str(tmp_path)})
    process = subprocess.run(
        [
            sys.executable,
            str(root / "agent/run_analysis_workflow.py"),
            "--date",
            "2026-07-22",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert process.returncode == 0
    output = json.loads(process.stdout)
    assert output["result"] == "NEED_AUTHORIZED_MARKET_DATA"
    assert output["futures_source_url"] == "https://www.ice.com/report/10"
    assert output["options_source_url"] == "https://www.ice.com/report/166"
    assert output["source_contract"] == "B"


def test_skill_and_runbook_pin_official_reports_and_human_gate():
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    skill = (
        root / ".agents/skills/curvelens-ice-report-download/SKILL.md"
    ).read_text()
    runbook = (root / "deployments/brent/AGENTS.md").read_text()
    for text in (skill, runbook):
        assert "https://www.ice.com/report/10" in text
        assert "https://www.ice.com/report/166" in text
        assert "CAPTCHA" in text
        assert "never bypass" in text
