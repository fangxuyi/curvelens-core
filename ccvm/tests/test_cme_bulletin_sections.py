"""CME bulletin product-section isolation regression tests."""
from __future__ import annotations

from datetime import date

from ccvm.collectors import cme_bulletin_pdf
from ccvm.reference.product import get_product


def _row(strike: int, settlement: str) -> str:
    return (f"{strike} ---- ---- ---- ---- ---- {settlement} UNCH .5000 "
            "---- ---- 10 ---- 100 UNCH")


def test_numbered_and_weekday_gold_options_do_not_leak(monkeypatch, tmp_path):
    text = "\n".join([
        "OG CALL COMEX GOLD OPTIONS", "AUG26", _row(4000, "53.70"), "TOTAL",
        # A numeric line after TOTAL must not inherit AUG26.
        _row(4010, "999.00"),
        "OG1 CALL GOLD OPTIONS", "AUG26", _row(4000, "73.50"), "TOTAL",
        "GWW WED GOLD WEEKLY WEDNESDAY OPTION WEEK1", "AUG26",
        _row(4000, "91.40"), "TOTAL",
        "OG PUT COMEX GOLD OPTIONS", "AUG26", _row(4000, "48.90"), "TOTAL",
    ])
    monkeypatch.setattr(cme_bulletin_pdf, "_pdftotext", lambda _path: text)
    monkeypatch.setattr(cme_bulletin_pdf, "get_product", lambda: get_product("gold"))
    records = cme_bulletin_pdf.parse(tmp_path / "unused.pdf", date(2026, 7, 17))
    assert [(r["call_put"], r["strike"], r["settlement"]) for r in records] == [
        ("C", 4000.0, 53.7), ("P", 4000.0, 48.9),
    ]
    assert {r["option_expiry"] for r in records} == {"2026-07-28"}
