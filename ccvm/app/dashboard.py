"""
CCVM Streamlit Dashboard — Crude Terminal.

Run:
    streamlit run app/dashboard.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

try:
    import streamlit as st
    import streamlit.components.v1 as components
    import pyarrow as pa
    import pandas as pd
except ImportError as exc:
    raise SystemExit(f"Missing dependency: {exc}") from exc

try:
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

DATA_DIR    = _REPO_ROOT / "data"
GOLD_DIR    = DATA_DIR / "gold"
QUALITY_DIR = DATA_DIR / "quality_reports"
REPORTS_DIR = DATA_DIR / "reports"

from ccvm.storage.parquet_store import ParquetStore
from ccvm.agents.catalyst_store import CatalystStore

pq        = ParquetStore(DATA_DIR)
cat_store = CatalystStore(DATA_DIR)

C = {
    "bg":       "#0b0b0d",
    "surface":  "#101013",
    "lift":     "#15151a",
    "border":   "#1d1d27",
    "text":     "#ddd8cc",
    "muted":    "#62605a",
    "amber":    "#c4962a",
    "amber_hi": "#e8b84b",
    "bull":     "#3d9e6d",
    "bear":     "#c4443c",
    "neutral":  "#3d6fa0",
}

GF_LINK = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Syne:wght@700;800&display=swap"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _available_dates() -> list[str]:
    return sorted(pq.list_dates("gold", "futures_features"), reverse=True)

def _load_quality(ds: str) -> dict:
    p = QUALITY_DIR / f"{ds}.json"
    return json.loads(p.read_text()) if p.exists() else {}

def _load_report(ds: str) -> str:
    p = REPORTS_DIR / f"{ds}.md"
    return p.read_text() if p.exists() else "*No report generated for this date.*"

def _load_agreement(ds: str) -> dict:
    p = GOLD_DIR / "agreement" / f"trade_date={ds}" / "agreement.json"
    return json.loads(p.read_text()) if p.exists() else {}

def _status_color(s: str) -> str:
    return {"PASS": C["bull"], "WARN": "#b8942a", "FAIL": C["bear"]}.get(s, C["muted"])

def _state_color(s: str) -> str:
    if "upside" in s or "bull" in s:              return C["bull"]
    if "downside" in s or "bear" in s:            return C["bear"]
    if "disagreement" in s or "uncertainty" in s: return "#b8942a"
    return C["muted"]

def _draw_color(v) -> str:
    return C["bull"] if (v or 0) > 0 else (C["bear"] if (v or 0) < 0 else C["muted"])

def _iframe(html: str, height: int) -> None:
    """Render custom HTML in an iframe — always works regardless of Streamlit context."""
    css = (f'<link href="{GF_LINK}" rel="stylesheet">'
           f'<style>*{{box-sizing:border-box;margin:0;padding:0;font-family:"JetBrains Mono",monospace;}}'
           f'body{{background:transparent;overflow:hidden;}}</style>')
    components.html(css + html, height=height, scrolling=False)

def _section(text: str) -> None:
    st.markdown(
        f'<p style="font-size:0.62rem;letter-spacing:0.18em;text-transform:uppercase;'
        f'color:{C["muted"]};margin:1.25rem 0 0.5rem">{text}</p>',
        unsafe_allow_html=True,
    )

def _plot_layout(**kw) -> dict:
    base = dict(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="JetBrains Mono", color=C["muted"], size=10),
        xaxis=dict(gridcolor=C["border"], linecolor=C["border"], zeroline=False,
                   tickfont=dict(color=C["muted"])),
        yaxis=dict(gridcolor=C["border"], linecolor=C["border"], zeroline=False,
                   tickfont=dict(color=C["muted"])),
        margin=dict(l=10, r=10, t=36, b=10),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=C["muted"])),
    )
    base.update(kw)
    return base


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CCVM — Crude Terminal",
    page_icon="🛢",
    layout="wide",
    initial_sidebar_state="expanded",
)

CSS = f"""<style>
@import url('{GF_LINK}');
:root{{--bg:{C['bg']};--surface:{C['surface']};--lift:{C['lift']};--border:{C['border']};
      --text:{C['text']};--muted:{C['muted']};--amber:{C['amber']};--amber-hi:{C['amber_hi']};
      --bull:{C['bull']};--bear:{C['bear']};
      --mono:"JetBrains Mono",monospace;--display:"Syne",sans-serif;}}
.stApp{{background:var(--bg)!important;}}
#MainMenu,footer,header{{display:none!important;}}
.stDeployButton{{display:none!important;}}
html,body,p,div,span,li,label,td,th,button{{font-family:var(--mono)!important;color:var(--text)!important;}}
h1,h2,h3,h4{{font-family:var(--display)!important;color:var(--text)!important;letter-spacing:-0.02em!important;}}
.main .block-container{{padding:0 2rem 3rem!important;max-width:1480px!important;}}
[data-testid="stSidebar"]{{background:var(--surface)!important;border-right:1px solid var(--border)!important;}}
[data-testid="stSidebar"]>div:first-child{{padding:1.5rem 1.1rem!important;}}
[data-baseweb="select"]>div{{background:var(--bg)!important;border:1px solid var(--border)!important;border-radius:0!important;font-family:var(--mono)!important;font-size:0.8rem!important;}}
[data-baseweb="select"] span{{color:var(--amber-hi)!important;}}
[data-baseweb="menu"]{{background:var(--lift)!important;border:1px solid var(--border)!important;border-radius:0!important;}}
[role="option"]{{font-family:var(--mono)!important;font-size:0.78rem!important;}}
.stTabs [data-baseweb="tab-list"]{{background:transparent!important;border-bottom:1px solid var(--border)!important;gap:0!important;padding:0!important;}}
.stTabs [data-baseweb="tab"]{{background:transparent!important;color:var(--muted)!important;font-family:var(--mono)!important;font-size:0.63rem!important;font-weight:500!important;letter-spacing:0.14em!important;text-transform:uppercase!important;padding:0.65rem 1.4rem!important;border:none!important;border-bottom:2px solid transparent!important;margin-bottom:-1px!important;}}
.stTabs [aria-selected="true"]{{color:var(--amber-hi)!important;border-bottom-color:var(--amber)!important;}}
.stTabs [data-baseweb="tab-panel"]{{background:transparent!important;padding:1.5rem 0 0!important;}}
[data-testid="metric-container"]{{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:0!important;padding:0.9rem 1rem!important;}}
[data-testid="stMetricLabel"]>div{{font-family:var(--mono)!important;font-size:0.58rem!important;letter-spacing:0.16em!important;text-transform:uppercase!important;color:var(--muted)!important;}}
[data-testid="stMetricValue"]{{font-family:var(--mono)!important;font-size:1.15rem!important;font-weight:600!important;color:var(--amber-hi)!important;}}
[data-testid="stMetricDelta"] svg{{display:none!important;}}
[data-testid="stDataFrame"]{{border:1px solid var(--border)!important;}}
details{{background:var(--surface)!important;border:1px solid var(--border)!important;border-radius:0!important;}}
details>summary{{font-family:var(--mono)!important;font-size:0.72rem!important;color:var(--muted)!important;padding:0.7rem 1rem!important;cursor:pointer;}}
[data-testid="stAlert"]{{border-radius:0!important;border:1px solid var(--border)!important;border-left:2px solid var(--amber)!important;background:rgba(196,150,42,0.04)!important;}}
code,.stMarkdown code{{background:rgba(196,150,42,0.08)!important;color:var(--amber-hi)!important;font-family:var(--mono)!important;font-size:0.78em!important;padding:0.15em 0.4em!important;border:1px solid rgba(196,150,42,0.18)!important;border-radius:2px!important;}}
.stMarkdown h1{{font-size:1.2rem!important;border-bottom:1px solid var(--border)!important;padding-bottom:0.5rem!important;margin-bottom:1rem!important;}}
.stMarkdown h2{{font-size:0.78rem!important;text-transform:uppercase!important;letter-spacing:0.1em!important;color:var(--muted)!important;border-bottom:1px solid var(--border)!important;padding-bottom:0.3rem!important;margin-top:1.75rem!important;}}
.stMarkdown h3{{font-size:0.88rem!important;color:var(--amber)!important;}}
.stMarkdown table{{border-collapse:collapse!important;width:100%!important;font-size:0.76rem!important;}}
.stMarkdown th{{background:var(--lift)!important;color:var(--muted)!important;font-size:0.58rem!important;letter-spacing:0.14em!important;text-transform:uppercase!important;padding:0.5rem 0.75rem!important;border:1px solid var(--border)!important;}}
.stMarkdown td{{padding:0.4rem 0.75rem!important;border:1px solid var(--border)!important;}}
hr{{border-color:var(--border)!important;margin:1.5rem 0!important;}}
::-webkit-scrollbar{{width:4px;height:4px;}}
::-webkit-scrollbar-track{{background:var(--bg);}}
::-webkit-scrollbar-thumb{{background:var(--border);border-radius:2px;}}
</style>"""
st.markdown(CSS, unsafe_allow_html=True)


# ── Header (outside tabs — markdown works here) ───────────────────────────────
st.markdown(
    f'<div style="display:flex;align-items:center;gap:1.25rem;padding:1.25rem 0 1.1rem;'
    f'border-bottom:1px solid {C["border"]};margin-bottom:1.75rem;">'
    f'<span style="font-family:Syne,sans-serif;font-size:1.55rem;font-weight:800;'
    f'color:{C["amber"]};letter-spacing:-0.04em;line-height:1">CCVM</span>'
    f'<div style="width:1px;height:1.1rem;background:{C["border"]}"></div>'
    f'<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:{C["muted"]};'
    f'letter-spacing:0.16em;text-transform:uppercase;line-height:1.6">'
    f'Commodity Catalyst &amp; Volatility Monitor</span>'
    f'<span style="margin-left:auto;font-family:JetBrains Mono,monospace;font-size:0.58rem;'
    f'color:{C["muted"]};letter-spacing:0.14em;text-transform:uppercase">'
    f'WTI CRUDE OIL · SETTLEMENT DATA ONLY</span></div>',
    unsafe_allow_html=True,
)


# ── Date selector ─────────────────────────────────────────────────────────────
available = _available_dates()
if not available:
    st.warning(
        "No gold features found. Run the pipeline first:\n\n"
        "```\npython scripts/collect_day.py --date YYYY-MM-DD --source all\n"
        "python scripts/normalize_day.py --date YYYY-MM-DD\n"
        "python scripts/compute_features.py --date YYYY-MM-DD\n```"
    )
    st.stop()

st.sidebar.markdown(
    f'<p style="font-family:JetBrains Mono,monospace;font-size:0.58rem;letter-spacing:0.16em;'
    f'text-transform:uppercase;color:{C["muted"]};margin-bottom:0.5rem">TRADE DATE</p>',
    unsafe_allow_html=True,
)
selected_date = st.sidebar.selectbox("Trade date", available, label_visibility="collapsed")
as_of    = date.fromisoformat(selected_date)
quality  = _load_quality(selected_date)
q_status = quality.get("overall_status", "UNKNOWN")
q_color  = _status_color(q_status)
agr      = _load_agreement(selected_date)

# Sidebar badges (outside tabs — work fine)
st.sidebar.markdown(
    f'<div style="margin-top:1.5rem">'
    f'<p style="font-family:JetBrains Mono,monospace;font-size:0.58rem;letter-spacing:0.16em;'
    f'text-transform:uppercase;color:{C["muted"]};margin-bottom:0.5rem">DATA QUALITY</p>'
    f'<div style="display:inline-flex;align-items:center;gap:0.5rem;'
    f'border:1px solid {q_color}40;padding:0.3rem 0.7rem;">'
    f'<div style="width:6px;height:6px;border-radius:50%;background:{q_color}"></div>'
    f'<span style="font-family:JetBrains Mono,monospace;font-size:0.7rem;'
    f'color:{q_color};letter-spacing:0.08em">{q_status}</span>'
    f'</div></div>',
    unsafe_allow_html=True,
)

if agr:
    s_color = _state_color(agr.get("state", ""))
    s_label = agr.get("state", "").replace("_", " ").upper()
    s_conf  = agr.get("confidence", "")
    conf_w  = {"high": "85%", "medium": "60%", "low": "35%"}.get(s_conf, "30%")
    st.sidebar.markdown(
        f'<div style="margin-top:1.5rem;border-top:1px solid {C["border"]};padding-top:1.25rem;">'
        f'<p style="font-family:JetBrains Mono,monospace;font-size:0.58rem;letter-spacing:0.16em;'
        f'text-transform:uppercase;color:{C["muted"]};margin-bottom:0.5rem">SIGNAL</p>'
        f'<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;font-weight:500;'
        f'color:{s_color};line-height:1.4">{s_label}</div>'
        f'<div style="display:flex;align-items:center;gap:0.6rem;margin-top:0.5rem;">'
        f'<span style="font-family:JetBrains Mono,monospace;font-size:0.58rem;color:{C["muted"]}">CONF</span>'
        f'<div style="flex:1;height:3px;background:{C["border"]};border-radius:1px;">'
        f'<div style="height:100%;width:{conf_w};background:{s_color};border-radius:1px;"></div></div>'
        f'<span style="font-family:JetBrains Mono,monospace;font-size:0.58rem;color:{s_color}">{s_conf}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

st.sidebar.markdown(
    f'<div style="margin-top:1.5rem;border-top:1px solid {C["border"]};padding-top:1.25rem;">'
    f'<p style="font-family:JetBrains Mono,monospace;font-size:0.58rem;letter-spacing:0.16em;'
    f'text-transform:uppercase;color:{C["muted"]};margin-bottom:0.6rem">PIPELINE</p>'
    f'<div style="font-family:JetBrains Mono,monospace;font-size:0.58rem;color:{C["muted"]};line-height:2.1">'
    f'collect_day.py<br>normalize_day.py<br>compute_features.py<br>generate_report.py'
    f'</div></div>',
    unsafe_allow_html=True,
)


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_curve, tab_vol, tab_eia, tab_agree, tab_cats, tab_brief, tab_qual = st.tabs([
    "CURVE", "VOLATILITY", "FUNDAMENTALS", "SIGNAL", "CATALYSTS", "BRIEF", "QUALITY"
])


# ══════════════════════════════════════════════════════════════════════════════
# CURVE — native st.metric + plotly
# ══════════════════════════════════════════════════════════════════════════════
with tab_curve:
    if not pq.exists("gold", "futures_features", selected_date):
        st.info("No gold futures features for this date.")
    else:
        gf = pq.read("gold", "futures_features", selected_date)
        d  = gf.to_pydict()

        if d["contract_code"]:
            slope    = d["front_back_slope"][0] if d["front_back_slope"] else None
            contango = d["contango_flag"][0] if d["contango_flag"] else False

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Front Contract",  d["contract_code"][0])
            c2.metric("Settlement",      f"USD {d['settlement'][0]:.2f}",
                      "per barrel")
            c3.metric("Curve Slope",     f"USD {slope:+.3f}/mo" if slope is not None else "—",
                      "front → back")
            c4.metric("Structure",       "CONTANGO" if contango else "BACKWARDATION")

        if _PLOTLY and d["contract_code"]:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=d["contract_code"], y=d["settlement"],
                mode="lines+markers",
                line=dict(color=C["amber"], width=2),
                marker=dict(color=C["amber_hi"], size=5),
                fill="tozeroy", fillcolor="rgba(196,150,42,0.05)",
                hovertemplate="<b>%{x}</b><br>$%{y:.2f}/bbl<extra></extra>",
            ))
            fig.update_layout(**_plot_layout(
                title=dict(text=f"WTI Futures Curve — {selected_date}",
                           font=dict(family="Syne", size=13, color=C["text"]), x=0),
                yaxis_title="USD/bbl", height=290, showlegend=False,
            ))
            st.plotly_chart(fig, use_container_width=True)
        elif d["contract_code"]:
            st.line_chart(pd.DataFrame({"Settlement": d["settlement"]}, index=d["contract_code"]))

        _section("CALENDAR SPREADS")
        try:
            st.dataframe(pd.DataFrame({
                "Contract":     d["contract_code"],
                "Settlement":   [f"${s:.2f}"  if s is not None else "—" for s in d["settlement"]],
                "Spread →Next": [f"${s:+.3f}" if s is not None else "—" for s in d["spread_to_next"]],
                "Butterfly":    [f"${b:+.4f}" if b is not None else "—" for b in d["butterfly"]],
                "1D Return":    [f"{r:.2%}"   if r is not None else "—" for r in d["return_1d"]],
                "Days to Exp":  d["days_to_expiry"],
            }), hide_index=True, use_container_width=True)
        except Exception:
            st.write(d)


# ══════════════════════════════════════════════════════════════════════════════
# VOLATILITY — native components + plotly
# ══════════════════════════════════════════════════════════════════════════════
with tab_vol:
    if not pq.exists("gold", "option_features", selected_date):
        st.info("No gold option features for this date.")
    else:
        opt_tbl = pq.read("gold", "option_features", selected_date)
        od = opt_tbl.to_pydict()

        note = od.get("price_note", [""])[0] or ""
        if "CME" in note or "cme" in note.lower():
            st.success("Data source: CME daily bulletin — LO futures options settlements")
        elif "USO" in note or "etrade" in note.lower():
            st.warning("Data source: USO equity options (WTI proxy) — not CME LO futures options")
        elif note:
            st.caption(f"Data source: {note}")

        expiries = sorted(set(od["option_expiry"]))
        atm_ivs = {e: None for e in expiries}
        rr25s   = {e: None for e in expiries}
        bf25s   = {e: None for e in expiries}
        for i in range(len(od["trade_date"])):
            exp = od["option_expiry"][i]
            if atm_ivs[exp] is None and od["atm_iv"][i] is not None:
                atm_ivs[exp] = od["atm_iv"][i]
                rr25s[exp]   = od["risk_reversal_25d"][i]
                bf25s[exp]   = od["butterfly_25d"][i]

        if _PLOTLY and expiries:
            exp_iv  = [e for e in expiries if atm_ivs[e] is not None]
            iv_vals = [atm_ivs[e] * 100 for e in exp_iv]
            rr_vals = [(rr25s[e] or 0) * 100 for e in exp_iv]

            fig_vol = go.Figure()
            fig_vol.add_trace(go.Scatter(
                x=exp_iv, y=iv_vals, mode="lines+markers",
                line=dict(color=C["neutral"], width=2),
                marker=dict(color=C["neutral"], size=5), name="ATM IV",
                hovertemplate="<b>%{x}</b><br>IV: %{y:.1f}%<extra></extra>",
            ))
            fig_vol.add_trace(go.Bar(
                x=exp_iv, y=rr_vals, name="25Δ RR",
                marker_color=[C["bull"] if v >= 0 else C["bear"] for v in rr_vals],
                opacity=0.55, yaxis="y2",
                hovertemplate="<b>%{x}</b><br>RR: %{y:.2f}%<extra></extra>",
            ))
            fig_vol.update_layout(**_plot_layout(
                title=dict(text="Vol Term Structure", font=dict(family="Syne", size=13, color=C["text"]), x=0),
                yaxis=dict(title="ATM IV (%)", gridcolor=C["border"]),
                yaxis2=dict(title="25Δ RR (%)", overlaying="y", side="right", gridcolor="rgba(0,0,0,0)"),
                height=280,
            ))
            st.plotly_chart(fig_vol, use_container_width=True)

        st.dataframe(pd.DataFrame({
            "Expiry": list(atm_ivs.keys()),
            "ATM IV": [f"{v*100:.1f}%"  if v else "—" for v in atm_ivs.values()],
            "25Δ RR": [f"{v*100:+.2f}%" if v else "—" for v in rr25s.values()],
            "25Δ BF": [f"{v*100:.2f}%"  if v else "—" for v in bf25s.values()],
        }), hide_index=True, use_container_width=True)

        if expiries:
            fe    = expiries[0]
            mask  = [e == fe for e in od["option_expiry"]]
            stk   = [s for s, m in zip(od["strike"], mask) if m]
            ivs_f = [v for v, m in zip(od["black76_iv"], mask) if m]
            cps   = [c for c, m in zip(od["call_put"], mask) if m]

            if stk and any(v is not None for v in ivs_f):
                _section(f"IV SMILE — {fe}")
                smile_df = (pd.DataFrame({"Strike": stk, "IV": ivs_f, "CP": cps})
                            .dropna(subset=["IV"]).sort_values("Strike"))
                if _PLOTLY:
                    fig_sm = go.Figure()
                    for cp, col in [("C", C["bull"]), ("P", C["bear"])]:
                        sub = smile_df[smile_df["CP"] == cp]
                        if not sub.empty:
                            fig_sm.add_trace(go.Scatter(
                                x=sub["Strike"], y=sub["IV"] * 100,
                                mode="lines+markers",
                                name="Calls" if cp == "C" else "Puts",
                                line=dict(color=col, width=1.5), marker=dict(size=4),
                            ))
                    fig_sm.update_layout(**_plot_layout(
                        xaxis_title="Strike (USD)", yaxis_title="IV (%)", height=260,
                    ))
                    st.plotly_chart(fig_sm, use_container_width=True)
                else:
                    st.line_chart(smile_df.set_index("Strike")["IV"])


# ══════════════════════════════════════════════════════════════════════════════
# FUNDAMENTALS — iframe hero + native metrics + plotly
# ══════════════════════════════════════════════════════════════════════════════
with tab_eia:
    if not pq.exists("gold", "eia_features", selected_date):
        st.info("No EIA features for this date.")
    else:
        gold_eia = pq.read("gold", "eia_features", selected_date)
        ed = gold_eia.to_pydict()

        def _gv(k):
            return ed.get(k, [None])[0]

        period   = _gv("eia_period")        or "N/A"
        sig      = _gv("supply_signal")     or "N/A"
        scenario = _gv("scenario_trigger")  or "none"
        sig_col  = {"bull": C["bull"], "bear": C["bear"],
                    "pass": C["bull"], "warn": "#b8942a",
                    "fail": C["bear"]}.get(sig.lower(), C["muted"])

        _iframe(
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:1px;height:100%">'
            f'<div style="background:{C["surface"]};border:1px solid {C["border"]};'
            f'border-left:3px solid {sig_col};padding:1.25rem 1.5rem">'
            f'<div style="font-size:0.58rem;letter-spacing:0.16em;text-transform:uppercase;'
            f'color:{C["muted"]};margin-bottom:0.5rem">SUPPLY SIGNAL</div>'
            f'<div style="font-family:Syne,sans-serif;font-size:1.8rem;font-weight:800;'
            f'color:{sig_col};letter-spacing:-0.02em;line-height:1">{sig.upper()}</div>'
            f'<div style="font-size:0.62rem;color:{C["muted"]};margin-top:0.35rem">'
            f'week ending {period}</div></div>'
            f'<div style="background:{C["surface"]};border:1px solid {C["border"]};padding:1.25rem 1.5rem">'
            f'<div style="font-size:0.58rem;letter-spacing:0.16em;text-transform:uppercase;'
            f'color:{C["muted"]};margin-bottom:0.5rem">SCENARIO TRIGGER</div>'
            f'<div style="font-size:0.85rem;font-weight:600;color:{C["amber_hi"]};line-height:1.3">'
            f'{scenario.replace("_", " ").upper()}</div></div></div>',
            height=110,
        )

        crude_draw   = _gv("crude_draw")
        cush_draw    = _gv("cushing_draw")
        util         = _gv("refinery_utilization_pct")
        net_imp      = _gv("net_imports")
        crude_stocks = _gv("crude_stocks_ex_spr")
        cush_stocks  = _gv("cushing_stocks")
        gas_draw     = _gv("gasoline_draw")
        dist_draw    = _gv("distillate_draw")

        def _fd(v, unit="MBBL"):
            return f"{v:+,.0f} {unit}" if v is not None else "—"

        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Crude Draw (ex-SPR)", _fd(crude_draw), "WoW")
        c2.metric("Cushing Draw",        _fd(cush_draw),  "WoW")
        c3.metric("Refinery Util.",      f"{util:.1f}%" if util else "—", "% cap")
        c4.metric("Net Imports",         _fd(net_imp, "MBBL/D"))

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Crude Stocks (ex-SPR)", f"{crude_stocks:,.0f} MBBL" if crude_stocks else "—")
        c6.metric("Cushing Stocks",        f"{cush_stocks:,.0f} MBBL"  if cush_stocks else "—")
        c7.metric("Gasoline Draw",         _fd(gas_draw),  "WoW")
        c8.metric("Distillate Draw",       _fd(dist_draw), "WoW")

        if _PLOTLY and pq.exists("silver", "eia", selected_date):
            try:
                se  = pq.read("silver", "eia", selected_date).to_pydict()
                idx = [i for i, k in enumerate(se.get("series_key", [])) if k == "us_crude_ex_spr"]
                if idx:
                    wc = se.get("wow_change", [None] * len(se["period"]))
                    pairs = sorted(zip(
                        [se["period"][i] for i in idx],
                        [se["value"][i]  for i in idx],
                        [wc[i]           for i in idx],
                    ))
                    ph, vh, wh = zip(*pairs) if pairs else ([], [], [])
                    _section("U.S. CRUDE STOCKS ex-SPR — TRAILING 52W")
                    fig_h = go.Figure()
                    fig_h.add_trace(go.Scatter(
                        x=ph, y=vh, mode="lines",
                        line=dict(color=C["amber"], width=2),
                        fill="tozeroy", fillcolor="rgba(196,150,42,0.06)",
                        hovertemplate="<b>%{x}</b><br>%{y:,.0f} MBBL<extra></extra>",
                    ))
                    fig_h.add_trace(go.Bar(
                        x=ph, y=wh, name="WoW", yaxis="y2",
                        marker_color=[C["bull"] if (w or 0) > 0 else C["bear"] for w in wh],
                        opacity=0.45,
                    ))
                    fig_h.update_layout(**_plot_layout(
                        yaxis=dict(title="MBBL", gridcolor=C["border"]),
                        yaxis2=dict(title="WoW Δ", overlaying="y", side="right",
                                    gridcolor="rgba(0,0,0,0)"),
                        height=260, showlegend=False,
                    ))
                    st.plotly_chart(fig_h, use_container_width=True)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL — iframe hero + native metrics
# ══════════════════════════════════════════════════════════════════════════════
with tab_agree:
    if not agr:
        st.info("No agreement data. Run compute_features.py first.")
    else:
        state  = agr.get("state", "unknown")
        conf   = agr.get("confidence", "low")
        sc     = _state_color(state)
        disp   = state.replace("_", " ").upper()
        conf_w = {"high": "88%", "medium": "62%", "low": "32%"}.get(conf, "30%")

        _iframe(
            f'<div style="background:{C["surface"]};border:1px solid {sc}33;'
            f'border-left:3px solid {sc};padding:1.5rem 2rem;height:100%">'
            f'<div style="font-size:0.58rem;letter-spacing:0.16em;text-transform:uppercase;'
            f'color:{C["muted"]};margin-bottom:0.5rem">AGREEMENT STATE</div>'
            f'<div style="font-family:Syne,sans-serif;font-size:2rem;font-weight:800;'
            f'color:{sc};letter-spacing:-0.02em;line-height:1.1">{disp}</div>'
            f'<div style="display:flex;align-items:center;gap:0.75rem;margin-top:0.85rem">'
            f'<div style="font-size:0.6rem;color:{C["muted"]}">CONFIDENCE</div>'
            f'<div style="flex:1;max-width:180px;height:3px;background:{C["border"]};border-radius:1px">'
            f'<div style="height:100%;width:{conf_w};background:{sc};border-radius:1px"></div></div>'
            f'<div style="font-size:0.6rem;color:{sc}">{conf}</div>'
            f'</div></div>',
            height=140,
        )

        evidence = agr.get("evidence", [])
        if evidence:
            _section("EVIDENCE")
            for ev in evidence:
                st.markdown(f"**›** {ev}")

        inputs = agr.get("inputs", {})
        if inputs:
            _section("INPUT SIGNALS")
            slope  = inputs.get("front_back_slope")
            atm_iv = inputs.get("atm_iv")
            rr     = inputs.get("risk_reversal_25d")
            eia_s  = inputs.get("eia_supply_signal")
            ci1, ci2, ci3, ci4 = st.columns(4)
            ci1.metric("Curve Slope",       f"USD {slope:+.3f}/mo" if slope is not None else "—")
            ci2.metric("ATM IV",            f"{atm_iv*100:.1f}%" if atm_iv else "—")
            ci3.metric("25Δ Risk Reversal", f"{rr*100:+.2f}%"    if rr is not None else "—")
            ci4.metric("EIA Signal",        (eia_s or "—").upper())


# ══════════════════════════════════════════════════════════════════════════════
# CATALYSTS — native expanders + columns
# ══════════════════════════════════════════════════════════════════════════════
with tab_cats:
    catalysts = cat_store.load(as_of)
    catalysts.sort(key=lambda e: e.get("relevance_score", 0), reverse=True)

    if not catalysts:
        st.info(
            "No catalyst events for this date.\n\n"
            f"Run: `python scripts/extract_catalysts.py --date {selected_date} --articles articles.json`"
        )
    else:
        _section(f"{len(catalysts)} EVENTS — RANKED BY RELEVANCE")
        dir_c = {"bullish_supply": C["bull"], "bearish_demand": C["bear"], "two_sided": "#b8942a"}

        for ev in catalysts[:10]:
            rank      = ev.get("relevance_rank", "?")
            score     = ev.get("relevance_score", 0)
            title     = ev.get("title", "")
            direction = ev.get("direction", "unclear")
            mag       = ev.get("magnitude", "—")
            horizon   = ev.get("affected_horizon", "—")
            eff       = ev.get("effective_start", "N/A")
            url       = ev.get("source_url", "")

            with st.expander(f"#{rank:02d}  {title[:80]}"):
                cc1, cc2, cc3 = st.columns(3)
                dc = dir_c.get(direction, C["muted"])
                cc1.metric("Direction", direction.replace("_", " ").upper())
                cc2.metric("Magnitude", mag)
                cc3.metric("Horizon",   horizon)
                st.caption(f"Effective: {eff}  ·  Score: {score}"
                           + (f"  ·  {url}" if url else ""))
                for snippet in ev.get("evidence", []):
                    st.caption(f"> {snippet}")


# ══════════════════════════════════════════════════════════════════════════════
# BRIEF
# ══════════════════════════════════════════════════════════════════════════════
with tab_brief:
    st.markdown(_load_report(selected_date))
    rp = REPORTS_DIR / f"{selected_date}.json"
    if rp.exists():
        with st.expander("Raw JSON"):
            st.json(json.loads(rp.read_text()))


# ══════════════════════════════════════════════════════════════════════════════
# QUALITY
# ══════════════════════════════════════════════════════════════════════════════
with tab_qual:
    if not quality:
        st.info("No quality report. Run normalize_day.py first.")
    else:
        st.metric("Overall Status", q_status)
        st.divider()
        col1, col2, col3 = st.columns(3)
        for col, key, label in [
            (col1, "futures",      "FUTURES"),
            (col2, "options",      "OPTIONS"),
            (col3, "fundamentals", "EIA FUNDAMENTALS"),
        ]:
            q  = quality.get(key, {})
            qs = q.get("status", "N/A")
            with col:
                st.metric(label, qs)
                if key == "futures":
                    st.caption(f"Contracts: {q.get('contract_count','—')}  "
                               f"P:{q.get('pass_count',0)} W:{q.get('warn_count',0)} F:{q.get('fail_count',0)}")
                elif key == "options":
                    st.caption(f"Records: {q.get('record_count','—')}  "
                               f"Expiries: {q.get('expiry_count','—')}")
                else:
                    st.caption(f"Latest: {q.get('latest_period','—')}")
                for note in q.get("notes", []):
                    st.warning(note)

        with st.expander("Full quality JSON"):
            st.json(quality)
