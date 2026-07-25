"""Official ICE Report Center Brent CSV importer."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from ccvm.importers import ice_report_center
from ccvm.importers.ice_report_center import (
    import_brent_pdf_directory,
    import_brent_pdf_reports,
    import_brent_reports,
    parse_brent_futures_pdf_text,
    parse_brent_options_pdf_text,
)
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

FUTURES_PDF_TEXT = """Futures Daily Market Report for ICE Brent Futures
22-Jul-2026

B-Brent Crude Future

 B Sep26 95.36 102.00 94.89 101.04 100.69 6.62 445,173 281,481 -13,146 0 2,500 15,827 203,537
 B Oct26 94.26 4.08 3,232 28,328 162 0 0 897 2,335
"""

OPTIONS_PDF_TEXT = """Options Daily Market Report for ICE Brent Futures
22-Jul-2026

B-Option on Brent Crude Future

 B Sep26 80.00 C 0.9815 20.34 20.82 20.30 20.82 20.77 6.49 1,993 20,094 -30 0 945 0 1,230
 B Sep26 80.25 C 0.9808 20.52 6.48 0 280 0 0 0 0 0
 B Sep26 80.00 P -0.0185 0.20 -0.01 50 10,000 25 0 0 0 50
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


def test_parses_official_pdf_layout_with_and_without_ohlc():
    product = load_product("brent")
    futures = parse_brent_futures_pdf_text(
        FUTURES_PDF_TEXT, date(2026, 7, 22), product,
    )
    options = parse_brent_options_pdf_text(
        OPTIONS_PDF_TEXT, date(2026, 7, 22), product,
    )
    assert futures == [
        {
            "contract_code": "BU26",
            "delivery_month": "2026-09",
            "open_interest": 281481,
            "settlement": 100.69,
            "settlement_change": 6.62,
            "trade_date": "2026-07-22",
            "volume": 445173,
        },
        {
            "contract_code": "BV26",
            "delivery_month": "2026-10",
            "open_interest": 28328,
            "settlement": 94.26,
            "settlement_change": 4.08,
            "trade_date": "2026-07-22",
            "volume": 3232,
        },
    ]
    assert len(options) == 3
    call = options[0]
    assert call["underlying_contract"] == "BU26"
    assert call["option_expiry"] == "2026-07-28"
    assert call["strike"] == 80.0
    assert call["call_put"] == "C"
    assert call["delta"] == pytest.approx(0.9815)
    assert call["settlement"] == pytest.approx(20.77)
    assert call["volume"] == 1993
    assert call["open_interest"] == 20094


def test_pdf_parser_rejects_wrong_internal_date_and_identity():
    product = load_product("brent")
    with pytest.raises(ValueError, match="does not match"):
        parse_brent_futures_pdf_text(
            FUTURES_PDF_TEXT, date(2026, 7, 23), product,
        )
    with pytest.raises(ValueError, match="does not identify"):
        parse_brent_options_pdf_text(
            OPTIONS_PDF_TEXT.replace(
                "B-Option on Brent Crude Future", "B-Option on WTI Crude Future",
            ),
            date(2026, 7, 22),
            product,
        )


def test_imports_pdf_pair_to_same_canonical_handoff(
    tmp_path, monkeypatch,
):
    futures_pdf = tmp_path / "futures.pdf"
    options_pdf = tmp_path / "options.pdf"
    futures_pdf.write_bytes(b"%PDF-futures")
    options_pdf.write_bytes(b"%PDF-options")
    monkeypatch.setattr(
        ice_report_center,
        "_extract_pdf_text",
        lambda path: (
            FUTURES_PDF_TEXT if Path(path).name == "futures.pdf"
            else OPTIONS_PDF_TEXT
        ),
    )
    data_dir = tmp_path / "data"
    result = import_brent_pdf_reports(
        futures_pdf=futures_pdf,
        options_pdf=options_pdf,
        trade_date=date(2026, 7, 22),
        data_dir=data_dir,
        product=load_product("brent"),
    )
    futures = json.loads(result.futures_path.read_text())
    options = json.loads(result.options_path.read_text())
    manifest = json.loads(result.manifest_path.read_text())
    assert len(futures["settlements"]) == 2
    assert len(options["settlements"]) == 3
    assert manifest["sources"]["futures"]["format"] == "pdf"
    assert manifest["sources"]["options"]["format"] == "pdf"
    assert (
        data_dir / "ice_report_center/trade_date=2026-07-22/"
        "report-10-futures.pdf"
    ).read_bytes() == b"%PDF-futures"


def test_batch_pdf_import_pairs_by_internal_date(tmp_path, monkeypatch):
    batch = tmp_path / "downloads"
    futures_dir = batch / "futures"
    options_dir = batch / "options"
    futures_dir.mkdir(parents=True)
    options_dir.mkdir()
    (futures_dir / "arbitrary-name.pdf").write_bytes(b"%PDF-futures")
    (options_dir / "different-name.pdf").write_bytes(b"%PDF-options")
    monkeypatch.setattr(
        ice_report_center,
        "_extract_pdf_text",
        lambda path: (
            FUTURES_PDF_TEXT if Path(path).parent.name == "futures"
            else OPTIONS_PDF_TEXT
        ),
    )
    results = import_brent_pdf_directory(
        batch_root=batch,
        data_dir=tmp_path / "data",
        product=load_product("brent"),
    )
    assert len(results) == 1
    assert results[0].futures_rows == 2
    assert results[0].options_rows == 3


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
    root = Path(__file__).resolve().parents[2]
    skill = (
        root / ".agents/skills/curvelens-ice-report-download/SKILL.md"
    ).read_text()
    runbook = (root / "deployments/brent/AGENTS.md").read_text()
    for text in (skill, runbook):
        assert "https://www.ice.com/report/10" in text
        assert "https://www.ice.com/report/166" in text
        assert "CAPTCHA" in text
        assert "never bypass" in text
        assert "--futures-pdf" in text
        assert "--batch-pdf-root" in text
