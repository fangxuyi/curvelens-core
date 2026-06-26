"""
CCVM Streamlit Dashboard.

Run:
    streamlit run app/dashboard.py

Or from project root:
    python -m streamlit run ccvm/app/dashboard.py

Reads data from the data/ directory (gold features, reports, quality reports).
No live data fetching — all data must be collected and processed first via:
    python scripts/collect_day.py --date YYYY-MM-DD --source all
    python scripts/normalize_day.py --date YYYY-MM-DD
    python scripts/compute_features.py --date YYYY-MM-DD
    python scripts/generate_report.py --date YYYY-MM-DD
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

# Make ccvm importable when run from the app/ directory
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    import streamlit as st
    import pyarrow as pa
    import pyarrow.compute as pc
except ImportError as exc:
    raise SystemExit(f"Missing dependency: {exc}. Install with: pip install streamlit pyarrow") from exc

DATA_DIR = _REPO_ROOT / "data"
GOLD_DIR = DATA_DIR / "gold"
QUALITY_DIR = DATA_DIR / "quality_reports"
REPORTS_DIR = DATA_DIR / "reports"

from ccvm.storage.parquet_store import ParquetStore
from ccvm.agents.catalyst_store import CatalystStore

pq = ParquetStore(DATA_DIR)
cat_store = CatalystStore(DATA_DIR)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _available_dates() -> list[str]:
    return sorted(pq.list_dates("gold", "futures_features"), reverse=True)


def _load_quality(date_str: str) -> dict:
    path = QUALITY_DIR / f"{date_str}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _load_report(date_str: str) -> str:
    path = REPORTS_DIR / f"{date_str}.md"
    if path.exists():
        return path.read_text()
    return "*No report generated for this date. Run generate_report.py.*"


def _load_agreement(date_str: str) -> dict:
    path = GOLD_DIR / "agreement" / f"trade_date={date_str}" / "agreement.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Page layout
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="CCVM — Commodity Catalyst & Volatility Monitor",
    page_icon="🛢",
    layout="wide",
)

st.title("CCVM — Commodity Catalyst & Volatility Monitor")
st.caption("WTI crude oil futures and options analytics • CME settlement data (CL futures + LO options)")

# ── Date selector ──
available = _available_dates()
if not available:
    st.warning(
        "No gold features found. Run the pipeline first:\n\n"
        "```\npython scripts/collect_day.py --date YYYY-MM-DD --source all\n"
        "python scripts/normalize_day.py --date YYYY-MM-DD\n"
        "python scripts/compute_features.py --date YYYY-MM-DD\n```"
    )
    st.stop()

selected_date = st.sidebar.selectbox("Trade date", available)
as_of = date.fromisoformat(selected_date)

# ── Quality badge ──
quality = _load_quality(selected_date)
q_status = quality.get("overall_status", "UNKNOWN")
status_color = {"PASS": "green", "WARN": "orange", "FAIL": "red"}.get(q_status, "gray")
st.sidebar.markdown(f"**Data quality:** :{status_color}[{q_status}]")

# ─────────────────────────────────────────────────────────────────────────────
# Tab layout
# ─────────────────────────────────────────────────────────────────────────────

tab_curve, tab_vol, tab_agree, tab_cats, tab_report, tab_quality = st.tabs([
    "Futures Curve", "Volatility Surface", "Agreement", "Catalysts", "Daily Brief", "Quality"
])

# ══════════════════════════════════════════════════════════════════════════════
# Tab 1: Futures curve
# ══════════════════════════════════════════════════════════════════════════════
with tab_curve:
    st.subheader("WTI Futures Curve")

    if not pq.exists("gold", "futures_features", selected_date):
        st.info("No gold futures features for this date.")
    else:
        gf = pq.read("gold", "futures_features", selected_date)
        d = gf.to_pydict()

        col1, col2, col3, col4 = st.columns(4)
        if d["contract_code"]:
            col1.metric("Front contract", d["contract_code"][0])
            col2.metric("Front settle", f"${d['settlement'][0]:.2f}/bbl" if d["settlement"] else "N/A")
            slope = d["front_back_slope"][0] if d["front_back_slope"] else None
            col3.metric("Curve slope", f"${slope:+.3f}/mo" if slope is not None else "N/A")
            col4.metric("Structure", "CONTANGO" if d["contango_flag"][0] else "BACKWARDATION")

        # Curve chart
        import json as _json
        try:
            import pandas as pd
            df_curve = pd.DataFrame({
                "contract": d["contract_code"],
                "delivery_month": d["delivery_month"],
                "settlement": d["settlement"],
            })
            st.line_chart(df_curve.set_index("delivery_month")["settlement"],
                          use_container_width=True)
        except ImportError:
            st.dataframe({"Contract": d["contract_code"], "Settlement": d["settlement"]})

        # Spreads and butterflies
        st.subheader("Calendar Spreads")
        try:
            df_spreads = pd.DataFrame({
                "Contract": d["contract_code"],
                "Spread to Next ($/bbl)": d["spread_to_next"],
                "Butterfly": d["butterfly"],
                "1-day Return": [f"{r:.2%}" if r is not None else "N/A" for r in d["return_1d"]],
                "Days to Expiry": d["days_to_expiry"],
            })
            st.dataframe(df_spreads, use_container_width=True)
        except Exception:
            st.write(d)

# ══════════════════════════════════════════════════════════════════════════════
# Tab 2: Volatility surface
# ══════════════════════════════════════════════════════════════════════════════
with tab_vol:
    st.subheader("Options Volatility Surface")
    st.caption("CME LO NYMEX crude oil options — daily bulletin settlements (source: cme_bulletin_lo_option)")

    if not pq.exists("gold", "option_features", selected_date):
        st.info("No gold option features for this date. Collect and process options data first.")
    else:
        go = pq.read("gold", "option_features", selected_date)
        od = go.to_pydict()

        # Surface summary
        expiries = sorted(set(od["option_expiry"]))
        atm_ivs = {e: None for e in expiries}
        rr25s = {e: None for e in expiries}
        bf25s = {e: None for e in expiries}

        for i in range(len(od["trade_date"])):
            exp = od["option_expiry"][i]
            if atm_ivs.get(exp) is None and od["atm_iv"][i] is not None:
                atm_ivs[exp] = od["atm_iv"][i]
                rr25s[exp] = od["risk_reversal_25d"][i]
                bf25s[exp] = od["butterfly_25d"][i]

        try:
            import pandas as pd
            df_vol = pd.DataFrame({
                "Expiry": list(atm_ivs.keys()),
                "ATM IV": [f"{v:.1%}" if v else "N/A" for v in atm_ivs.values()],
                "25Δ RR": [f"{v:.1%}" if v else "N/A" for v in rr25s.values()],
                "25Δ BF": [f"{v:.1%}" if v else "N/A" for v in bf25s.values()],
            })
            st.dataframe(df_vol, use_container_width=True)

            # IV by strike for front expiry
            if expiries:
                front_exp = expiries[0]
                mask = [e == front_exp for e in od["option_expiry"]]
                strikes = [s for s, m in zip(od["strike"], mask) if m]
                ivs = [v for v, m in zip(od["black76_iv"], mask) if m]
                cps = [c for c, m in zip(od["call_put"], mask) if m]

                if strikes and ivs:
                    st.subheader(f"IV Smile — {front_exp}")
                    smile_df = pd.DataFrame({"Strike": strikes, "IV": ivs, "CP": cps})
                    smile_df = smile_df.dropna().sort_values("Strike")
                    st.line_chart(smile_df.set_index("Strike")["IV"])
        except ImportError:
            st.write("Install pandas for charts")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 3: Agreement
# ══════════════════════════════════════════════════════════════════════════════
with tab_agree:
    st.subheader("Futures-Options Agreement")
    agr = _load_agreement(selected_date)

    if not agr:
        st.info("No agreement data. Run compute_features.py first.")
    else:
        state = agr.get("state", "unknown")
        conf = agr.get("confidence", "low")

        state_colors = {
            "confirmed_upside_risk": "🟢",
            "confirmed_downside_risk": "🔴",
            "cross_market_disagreement": "🟡",
            "non_directional_uncertainty": "🟡",
            "futures_only_repricing": "🔵",
            "options_only_repricing": "🔵",
            "no_material_change": "⚪",
            "insufficient_data": "⚫",
        }
        icon = state_colors.get(state, "⚪")
        st.markdown(f"## {icon} `{state}`")
        st.markdown(f"**Confidence:** {conf}")

        st.markdown("**Evidence:**")
        for ev in agr.get("evidence", []):
            st.markdown(f"- {ev}")

        inputs = agr.get("inputs", {})
        if inputs:
            col1, col2, col3 = st.columns(3)
            slope = inputs.get("front_back_slope")
            atm_iv = inputs.get("atm_iv")
            rr = inputs.get("risk_reversal_25d")
            col1.metric("Curve slope", f"${slope:+.3f}/mo" if slope is not None else "N/A")
            col2.metric("ATM IV", f"{atm_iv:.1%}" if atm_iv else "N/A")
            col3.metric("25Δ RR", f"{rr:+.1%}" if rr is not None else "N/A")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 4: Catalysts
# ══════════════════════════════════════════════════════════════════════════════
with tab_cats:
    st.subheader("Catalyst Events")
    catalysts = cat_store.load(as_of)
    catalysts.sort(key=lambda e: e.get("relevance_score", 0), reverse=True)

    if not catalysts:
        st.info(
            "No catalyst events for this date.\n\n"
            "Run: `python scripts/extract_catalysts.py --date " + selected_date + " --articles articles.json`"
        )
    else:
        for ev in catalysts[:10]:
            rank = ev.get("relevance_rank", "?")
            score_val = ev.get("relevance_score", 0)
            title = ev.get("title", "")
            direction = ev.get("direction", "")
            mag = ev.get("magnitude", "")
            horizon = ev.get("affected_horizon", "")
            eff_start = ev.get("effective_start", "N/A")
            url = ev.get("source_url", "")

            direction_icon = {"bullish_supply": "📈", "bearish_demand": "📉",
                              "two_sided": "↔", "unclear": "?"}.get(direction, "")

            with st.expander(f"#{rank} [{score_val:3d}] {direction_icon} {title}"):
                col1, col2, col3 = st.columns(3)
                col1.write(f"**Direction:** {direction}")
                col2.write(f"**Magnitude:** {mag}")
                col3.write(f"**Horizon:** {horizon}")
                st.write(f"**Effective start:** {eff_start}")
                if url:
                    st.write(f"**Source:** {url}")
                for snippet in ev.get("evidence", []):
                    st.caption(f"> {snippet}")

# ══════════════════════════════════════════════════════════════════════════════
# Tab 5: Daily brief
# ══════════════════════════════════════════════════════════════════════════════
with tab_report:
    st.subheader("Daily Forward-Risk Brief")
    report_md = _load_report(selected_date)
    st.markdown(report_md)

    report_json_path = REPORTS_DIR / f"{selected_date}.json"
    if report_json_path.exists():
        with st.expander("Raw JSON report"):
            st.json(json.loads(report_json_path.read_text()))

# ══════════════════════════════════════════════════════════════════════════════
# Tab 6: Quality
# ══════════════════════════════════════════════════════════════════════════════
with tab_quality:
    st.subheader("Data Quality Report")
    if not quality:
        st.info("No quality report. Run normalize_day.py first.")
    else:
        st.markdown(f"**Overall status:** `{quality.get('overall_status')}`")

        col1, col2, col3 = st.columns(3)
        with col1:
            fut_q = quality.get("futures", {})
            st.metric("Futures status", fut_q.get("status", "N/A"))
            st.write(f"Contracts: {fut_q.get('contract_count', 0)}")
            st.write(f"PASS: {fut_q.get('pass_count', 0)}  WARN: {fut_q.get('warn_count', 0)}  FAIL: {fut_q.get('fail_count', 0)}")
            for note in fut_q.get("notes", []):
                st.warning(note)

        with col2:
            opt_q = quality.get("options", {})
            st.metric("Options status", opt_q.get("status", "N/A"))
            st.write(f"Records: {opt_q.get('record_count', 0)}")
            st.write(f"Expiries: {opt_q.get('expiry_count', 0)}")
            for note in opt_q.get("notes", []):
                st.warning(note)

        with col3:
            fund_q = quality.get("fundamentals", {})
            st.metric("EIA status", fund_q.get("status", "N/A"))
            st.write(f"Latest period: {fund_q.get('latest_period', 'N/A')}")

        st.markdown("**Full quality report (JSON):**")
        st.json(quality)
