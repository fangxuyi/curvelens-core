"""CurveLens product dashboard.

Run from ``ccvm/`` with an explicit product::

    CCVM_PRODUCT=gold streamlit run app/dashboard.py --server.port 8502
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_CCVM_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_CCVM_ROOT / "src"))

try:
    import pandas as pd
    import streamlit as st
except ImportError as exc:
    raise SystemExit(f"Missing dashboard dependency: {exc}") from exc

try:
    import plotly.graph_objects as go
    _PLOTLY = True
except ImportError:
    _PLOTLY = False

from ccvm.reference.product import available_products, get_product
from ccvm.reporting.dashboard_news import build_validated_news, news_artifacts_ready
from ccvm.reporting.probability_chart import fitted_probability_above_curve
from ccvm.runtime import data_dir
from ccvm.storage.parquet_store import ParquetStore


C = {
    "bg": "#0b0b0d", "surface": "#101013", "border": "#24242d",
    "text": "#ddd8cc", "muted": "#77736b", "amber": "#d3a437",
    "bull": "#3d9e6d", "bear": "#c4443c", "blue": "#477fb5",
}


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text()) if path.exists() else {}
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _markdown(path: Path, missing: str) -> str:
    try:
        return path.read_text() if path.exists() else f"*{missing}*"
    except OSError:
        return f"*Unable to read {path.name}.*"


def _dated_dir(root: Path, trade_date: str) -> Path:
    return root / f"trade_date={trade_date}"


def _analysis_dates() -> list[str]:
    return sorted(
        (p.parent.name.removeprefix("trade_date=")
         for p in ANALYSIS_DIR.glob("trade_date=*/analysis.md")),
        reverse=True,
    )


def _role_packets(run_dir: Path) -> list[dict[str, Any]]:
    return [
        packet for path in sorted(run_dir.glob("*.packet.json"))
        if (packet := _json(path)).get("role")
    ]


def _available_dates() -> list[str]:
    dates = set(pq.list_dates("gold", "futures_features"))
    dates.update(_analysis_dates())
    dates.update(
        p.parent.name.removeprefix("trade_date=")
        for p in FEATURE_DIR.glob("rnd/trade_date=*/rnd.json")
    )
    return sorted(dates, reverse=True)


def _plot_layout(**overrides: Any) -> dict[str, Any]:
    layout: dict[str, Any] = {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": "monospace", "color": C["muted"], "size": 11},
        "margin": {"l": 10, "r": 10, "t": 42, "b": 10},
        "height": 300,
        "legend": {"bgcolor": "rgba(0,0,0,0)"},
    }
    layout.update(overrides)
    return layout


def _status_color(status: str) -> str:
    value = status.lower()
    if value in {"complete", "pass", "valid", "available"}:
        return C["bull"]
    if value in {"fail", "failed", "invalid"}:
        return C["bear"]
    return C["amber"]


st.set_page_config(
    page_title="CurveLens Markets", page_icon="📈",
    layout="wide", initial_sidebar_state="expanded",
)

product_profiles = {key: get_product(key) for key in available_products()}
default_product = os.environ.get("CCVM_PRODUCT", "wti")
if default_product not in product_profiles:
    default_product = next(iter(product_profiles))
selected_product = st.sidebar.selectbox(
    "Product",
    list(product_profiles),
    index=list(product_profiles).index(default_product),
    format_func=lambda key: product_profiles[key].display_name,
    key="active-product",
)
PRODUCT = product_profiles[selected_product]
DATA_DIR = data_dir(selected_product)
FEATURE_DIR = DATA_DIR / "gold"
ANALYSIS_DIR = DATA_DIR / "analysis"
WORKFLOW_DIR = DATA_DIR / "analysis_workflow"
QUALITY_DIR = DATA_DIR / "quality_reports"
pq = ParquetStore(DATA_DIR)

st.markdown(
    f"""<style>
    .stApp {{background:{C['bg']}; color:{C['text']}}}
    #MainMenu, footer, header {{display:none}}
    [data-testid="stSidebar"] {{background:{C['surface']}; border-right:1px solid {C['border']}}}
    [data-testid="metric-container"] {{background:{C['surface']}; border:1px solid {C['border']}; padding:.8rem}}
    .stTabs [data-baseweb="tab-list"] {{border-bottom:1px solid {C['border']}}}
    .stTabs [data-baseweb="tab"] {{font-family:monospace; font-size:.68rem; letter-spacing:.08em}}
    .stTabs [aria-selected="true"] {{color:{C['amber']} !important}}
    h1,h2,h3 {{color:{C['text']} !important}}
    hr {{border-color:{C['border']} !important}}
    </style>""",
    unsafe_allow_html=True,
)

st.markdown(
    f"<h2 style='margin:.8rem 0 0'>CurveLens · {PRODUCT.display_name}</h2>"
    f"<p style='color:{C['muted']};margin:.2rem 0 1.2rem'>"
    "Settled-market analytics, validated probabilities, and native-agent daily analysis</p>",
    unsafe_allow_html=True,
)

dates = _available_dates()
if not dates:
    st.warning("No computed features or daily analysis outputs are available for this product.")
    st.stop()

selected_date = st.sidebar.selectbox("Trade date", dates, key=f"trade-date-{selected_product}")
if st.sidebar.button("Refresh files", width="stretch"):
    st.rerun()

analysis_run_dir = _dated_dir(ANALYSIS_DIR, selected_date)
workflow_run_dir = _dated_dir(WORKFLOW_DIR, selected_date)
analysis_json = _json(analysis_run_dir / "analysis.json")
run_state = _json(workflow_run_dir / "run.json")
monitor_json = _json(workflow_run_dir / "workflow_monitor.json")
quality = _json(QUALITY_DIR / f"{selected_date}.json")

st.sidebar.markdown("---")
st.sidebar.caption("ACTIVE PRODUCT DATA")
st.sidebar.markdown(f"**{PRODUCT.display_name}** (`{PRODUCT.name}`)")
st.sidebar.caption("WORKFLOW")
phase = str(monitor_json.get("phase", "not run"))
st.sidebar.markdown(
    f"<span style='color:{_status_color(phase)}'>●</span> {phase}",
    unsafe_allow_html=True,
)
st.sidebar.caption("ANALYSIS STATUS")
st.sidebar.write(str(analysis_json.get("status", "not available")))

tab_curve, tab_vol, tab_news, tab_analysis, tab_stats, tab_monitor, tab_history, tab_quality = st.tabs([
    "CURVE", "VOL + PROBABILITY", "TOP NEWS", "DAILY ANALYSIS", "STATISTICS",
    "MONITOR", "HISTORY", "QUALITY",
])


with tab_curve:
    if not pq.exists("gold", "futures_features", selected_date):
        st.info("No futures features for this date.")
    else:
        data = pq.read("gold", "futures_features", selected_date).to_pydict()
        contracts = data.get("contract_code", [])
        if not contracts:
            st.info("The futures feature table is empty.")
        else:
            slope = (data.get("front_back_slope") or [None])[0]
            contango = bool((data.get("contango_flag") or [False])[0])
            cols = st.columns(4)
            cols[0].metric("Front Contract", contracts[0])
            cols[1].metric("Settlement", f"{data['settlement'][0]:.2f} {PRODUCT.price_unit}")
            cols[2].metric("Curve Slope", f"{slope:+.3f}/mo" if slope is not None else "—")
            cols[3].metric("Structure", "CONTANGO" if contango else "BACKWARDATION")

            if _PLOTLY:
                fig = go.Figure(go.Scatter(
                    x=contracts, y=data["settlement"], mode="lines+markers",
                    line={"color": C["amber"], "width": 2},
                    hovertemplate="%{x}<br>%{y:.2f}<extra></extra>",
                ))
                fig.update_layout(**_plot_layout(
                    title=f"Futures Curve — {selected_date}",
                    yaxis_title=PRODUCT.price_unit,
                ))
                st.plotly_chart(fig, width="stretch")

            st.subheader("Calendar spreads and butterflies")
            st.dataframe(pd.DataFrame({
                "Contract": contracts,
                "Settlement": data.get("settlement", []),
                "Spread → Next": data.get("spread_to_next", []),
                "Butterfly": data.get("butterfly", []),
                "1D Return": data.get("return_1d", []),
                "Days to Expiry": data.get("days_to_expiry", []),
            }), hide_index=True, width="stretch")


with tab_vol:
    if not pq.exists("gold", "option_features", selected_date):
        st.info("No option features for this date.")
    else:
        options = pq.read("gold", "option_features", selected_date).to_pydict()
        expiries = sorted(set(options.get("option_expiry", [])))
        rows: list[dict[str, Any]] = []
        for expiry in expiries:
            index = next((i for i, value in enumerate(options["option_expiry"])
                          if value == expiry and options.get("atm_iv", [None])[i] is not None), None)
            if index is not None:
                rows.append({
                    "Expiry": expiry,
                    "ATM IV (%)": options["atm_iv"][index] * 100,
                    "25Δ RR (vol pts)": options["risk_reversal_25d"][index] * 100
                    if options["risk_reversal_25d"][index] is not None else None,
                    "25Δ BF (vol pts)": options["butterfly_25d"][index] * 100
                    if options["butterfly_25d"][index] is not None else None,
                })
        vol_df = pd.DataFrame(rows)
        if not vol_df.empty:
            if _PLOTLY:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=vol_df["Expiry"], y=vol_df["ATM IV (%)"], mode="lines+markers",
                    name="ATM IV", line={"color": C["blue"]},
                ))
                fig.add_trace(go.Bar(
                    x=vol_df["Expiry"], y=vol_df["25Δ RR (vol pts)"],
                    name="25Δ RR", opacity=.55, yaxis="y2",
                ))
                fig.update_layout(**_plot_layout(
                    title="Volatility term structure and skew",
                    yaxis={"title": "ATM IV (%)"},
                    yaxis2={"title": "RR (vol pts)", "overlaying": "y", "side": "right"},
                ))
                st.plotly_chart(fig, width="stretch")
            st.dataframe(vol_df, hide_index=True, width="stretch")

    st.divider()
    st.subheader("Risk-neutral probability diagnostics")
    rnd = _json(FEATURE_DIR / "rnd" / f"trade_date={selected_date}" / "rnd.json")
    rnd_expiries = rnd.get("expiries", [])
    if not rnd_expiries:
        st.info("No risk-neutral density output for this date.")
    else:
        labels = [str(item.get("expiry", "unknown")) for item in rnd_expiries]
        expiry = st.selectbox("Probability expiry", labels, key="rnd-expiry")
        result = rnd_expiries[labels.index(expiry)]
        valid = result.get("status") == "available" and bool(result.get("prob_ladder"))

        cols = st.columns(4)
        cols[0].metric("RND Status", "VALIDATED" if valid else str(result.get("status", "—")).upper())
        cols[1].metric("Forward", f"{result['forward']:.2f}" if result.get("forward") is not None else "—")
        cols[2].metric("Expected Move", f"±{result['expected_move_straddle']:.2f}"
                       if result.get("expected_move_straddle") is not None else "—")
        cols[3].metric("R.N. Std. Dev.", f"{result['rn_std']:.2f}"
                       if result.get("rn_std") is not None else "—")
        mass_cols = st.columns(4)
        mass_cols[0].metric("Fitted Mass", f"{result['projected_mass']:.4f}"
                            if result.get("projected_mass") is not None else "—")
        mass_cols[1].metric("Raw Positive Mass", f"{result['positive_mass']:.4f}"
                            if result.get("positive_mass") is not None else "—")
        mass_cols[2].metric("Raw Negative Mass", f"{result['negative_mass']:.4f}"
                            if result.get("negative_mass") is not None else "—")
        fit_ticks = result.get("fit_max_residual_ticks", result.get("projection_max_adjustment_ticks"))
        mass_cols[3].metric("Max Fit Residual", f"{fit_ticks:.2f} ticks"
                            if fit_ticks is not None else "—")

        ladder = result.get("prob_ladder") or {}
        if valid:
            probability = pd.DataFrame({
                "Threshold": [key.removeprefix("p_above_") for key in ladder],
                "Probability Above (%)": [value * 100 for value in ladder.values()],
            })
            if _PLOTLY:
                density_points = result.get("density_points") or []
                numeric_thresholds = pd.to_numeric(
                    probability["Threshold"], errors="coerce"
                ).dropna()
                fitted_curve = (
                    fitted_probability_above_curve(
                        density_points,
                        lower=float(numeric_thresholds.min()),
                        upper=float(numeric_thresholds.max()),
                    )
                    if len(numeric_thresholds) >= 2 else []
                )
                fig = go.Figure()
                if fitted_curve:
                    fitted_df = pd.DataFrame(fitted_curve)
                    fig.add_trace(go.Scatter(
                        x=fitted_df["strike"],
                        y=fitted_df["probability_at_or_above"] * 100,
                        mode="lines+markers",
                        name="Fitted-grid cumulative",
                        line={"color": C["blue"], "width": 2, "shape": "hv"},
                        marker={"size": 3},
                        hovertemplate=(
                            "Strike %{x:.2f}<br>"
                            "Probability at or above %{y:.2f}%<extra></extra>"
                        ),
                    ))
                fig.add_trace(go.Scatter(
                    x=numeric_thresholds,
                    y=probability.loc[numeric_thresholds.index, "Probability Above (%)"],
                    mode="markers",
                    name="Published ladder",
                    marker={"color": C["amber"], "size": 8},
                    hovertemplate=(
                        "Strike %{x:.2f}<br>"
                        "Probability above %{y:.2f}%<extra></extra>"
                    ),
                ))
                fig.update_layout(**_plot_layout(
                    title=f"Implied probability ladder — {expiry}",
                    xaxis={"title": "Settlement threshold"},
                    yaxis={"title": "Probability (%)", "range": [0, 100]},
                ))
                st.plotly_chart(fig, width="stretch")
                if fitted_curve:
                    st.caption(
                        "The step line is the direct reverse cumulative sum of "
                        "probability mass at the fitted strike nodes shown. No "
                        "additional strikes or probabilities are interpolated."
                    )
            st.dataframe(probability, hide_index=True, width="stretch")

            quantiles = result.get("quantiles") or {}
            if quantiles:
                st.caption("Implied terminal-price percentiles")
                st.dataframe(pd.DataFrame({
                    "Percentile": [key.upper() for key in quantiles],
                    "Settlement": list(quantiles.values()),
                }), hide_index=True, width="stretch")

            density_points = result.get("density_points") or []
            if density_points and _PLOTLY:
                density_df = pd.DataFrame(density_points)
                fig = go.Figure(go.Scatter(
                    x=density_df["strike"], y=density_df["density"],
                    mode="lines", fill="tozeroy", line={"color": C["blue"]},
                ))
                fig.update_layout(**_plot_layout(
                    title=f"Risk-neutral terminal-price density — {expiry}",
                    xaxis={"title": "Settlement price"},
                    yaxis={"title": "Probability density"},
                ))
                st.plotly_chart(fig, width="stretch")
        else:
            st.warning("Probabilities are withheld because this expiry did not pass validation.")

        with st.expander("Density calibration details", expanded=bool(result.get("validation_warnings"))):
            st.write(f"Method: `{result.get('method', '—')}`")
            st.write(f"Exercise adjustment: `{result.get('exercise_adjustment', '—')}`")
            st.write(
                f"Convexity violations: **{result.get('convexity_violations', '—')}** · "
                f"Monotonicity violations: **{result.get('monotonicity_violations', '—')}** · "
                f"Fit-residual limit: **{result.get('fit_residual_limit_ticks', result.get('projection_limit_ticks', '—'))} ticks**"
            )
            st.write(
                f"Fitted forward: **{result.get('fitted_forward', '—')}** · "
                f"Tail-boundary mass: **{result.get('tail_boundary_mass', '—')}**"
            )
            for warning in result.get("validation_warnings", []):
                st.warning(warning)


with tab_news:
    role_packets = _role_packets(workflow_run_dir)
    news_ready, news_status = news_artifacts_ready(analysis_json, run_state, role_packets)
    news = (
        build_validated_news(
            analysis_json, role_packets,
            expected_packet_id=str(analysis_json.get("packet_id") or ""),
        )
        if news_ready else []
    )
    st.subheader(f"What matters for {PRODUCT.display_name} — {selected_date}")
    st.caption(
        "Only stories cited in validated specialist findings are shown. "
        "Articles routed by keywords but not used by an analyst are excluded."
    )
    if not news_ready:
        st.info(news_status)
    elif not news:
        st.info(
            "The agents did not validate a product-relevant news catalyst for this trade date. "
            "The daily view therefore rests on market and fundamental data rather than a headline explanation."
        )
    else:
        st.caption(f"Showing the top {min(3, len(news))} of {len(news)} validated stories.")
        for rank, story in enumerate(news[:3], start=1):
            title = str(story.get("title") or story["article_id"])
            source = str(story.get("source_name") or "Source unavailable")
            published = str(story.get("published_at") or "date unavailable")
            url = story.get("url")
            top_marker = " · TOP-VIEW EVIDENCE" if story.get("top_view_titles") else ""
            timing = (
                " · POST-TRADE-DATE CONTEXT"
                if story.get("timing") == "post_trade_date" else ""
            )
            st.markdown(f"### {rank}. {title}")
            st.caption(
                f"{source} · published {published} · reviewed by "
                f"{', '.join(role.replace('_', ' ').title() for role in story['roles'])}"
                f"{top_marker}{timing}"
            )
            if url:
                st.markdown(f"[Open source article]({url})")

            findings = story.get("findings") or []
            if findings:
                st.markdown(f"**What the agents took from it:** {findings[0]}")
                for finding in findings[1:]:
                    st.markdown(f"- {finding}")

            comparisons = story.get("market_comparisons") or []
            if comparisons:
                st.markdown("**How it compares with market data:**")
                for comparison in comparisons[:3]:
                    st.markdown(f"- {comparison}")

            relationships = story.get("top_view_relationships") or []
            if relationships:
                st.markdown(
                    "**Role in the daily view:** "
                    + ", ".join(value.replace("_", " ") for value in relationships)
                )
            st.divider()


with tab_analysis:
    st.markdown(_markdown(
        analysis_run_dir / "analysis.md",
        "No validated daily analysis was generated for this date.",
    ))
    if analysis_json:
        with st.expander("Validated analysis JSON"):
            st.json(analysis_json)


with tab_stats:
    st.markdown(_markdown(
        analysis_run_dir / "statistics.md",
        "No statistics supplement was generated for this date.",
    ))


with tab_monitor:
    if not monitor_json and not (workflow_run_dir / "workflow_monitor.md").exists():
        st.info("No workflow monitor was generated for this date.")
    else:
        if monitor_json:
            cols = st.columns(4)
            cols[0].metric("Phase", monitor_json.get("phase", "—"))
            cols[1].metric("Run ID", monitor_json.get("run_id", "—"))
            cols[2].metric("Workers", len(monitor_json.get("agents", [])))
            cols[3].metric("Delivery Queued", str(bool(monitor_json.get("delivery_queued"))).upper())
            agents = monitor_json.get("agents", [])
            if agents:
                st.dataframe(pd.DataFrame([{
                    "Worker": item.get("name"),
                    "Type": item.get("agent_type"),
                    "Status": item.get("status"),
                    "Corrections": item.get("corrections", 0),
                    "Task readable": bool((item.get("inputs", {}).get("task") or {}).get("exists")),
                    "Response written": bool((item.get("output") or {}).get("exists")),
                    "Last validation error": item.get("last_validation_error", ""),
                } for item in agents]), hide_index=True, width="stretch")
        st.markdown(_markdown(
            workflow_run_dir / "workflow_monitor.md",
            "The JSON monitor exists, but its Markdown rendering is unavailable.",
        ))
        if monitor_json:
            with st.expander("Full monitor JSON"):
                st.json(monitor_json)


with tab_history:
    history_dates = _analysis_dates()
    if not history_dates:
        st.info("No daily analysis history is available yet.")
    else:
        history_rows: list[dict[str, Any]] = []
        for trade_date in history_dates:
            record = _json(_dated_dir(ANALYSIS_DIR, trade_date) / "analysis.json")
            synthesis = record.get("synthesis") or {}
            views = synthesis.get("top_views") or []
            history_rows.append({
                "Trade date": trade_date,
                "Status": record.get("status", "—"),
                "Headline": synthesis.get("headline", "—"),
                "#1 view": views[0].get("title", "—") if views else "—",
                "Evidence relationship": views[0].get("evidence_relationship", "—") if views else "—",
                "Confidence": views[0].get("confidence", "—") if views else "—",
            })
        st.subheader("Daily view ledger")
        st.caption("One row per validated daily synthesis. Older schema runs may not contain ranked views.")
        st.dataframe(pd.DataFrame(history_rows), hide_index=True, width="stretch")
        historical_date = st.selectbox("Read a historical analysis", history_dates, key="history-date")
        st.markdown(_markdown(
            _dated_dir(ANALYSIS_DIR, historical_date) / "analysis.md",
            "No Markdown analysis is available for this date.",
        ))


with tab_quality:
    if not quality:
        st.info("No quality report for this date.")
    else:
        st.metric("Overall Status", quality.get("overall_status", "—"))
        sections = []
        for name, value in quality.items():
            if isinstance(value, dict) and "status" in value:
                sections.append({
                    "Section": name.replace("_", " ").title(),
                    "Status": value.get("status"),
                    "Notes": " · ".join(str(note) for note in value.get("notes", [])),
                })
        if sections:
            st.dataframe(pd.DataFrame(sections), hide_index=True, width="stretch")
        with st.expander("Full quality JSON"):
            st.json(quality)
