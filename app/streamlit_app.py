"""Read-only local viewer for the generated analytics outputs.

It displays tables written by python/auto_retail_analysis.py (OFFICIAL FADA data) and
python/dealership_kpi_simulation.py (SIMULATED data). It performs no business calculations
of its own beyond filtering and sorting, so its numbers always match the reports.

    python -m pip install -r requirements-app.txt
    streamlit run app/streamlit_app.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "reports" / "auto_retail" / "tables"
DEALER = ROOT / "reports" / "dealership" / "tables"
OFFICIAL_NOTE = "OFFICIAL - FADA monthly vehicle retail data. Each OEM is compared only with its own segment."
SIMULATED_NOTE = "SIMULATED - seeded synthetic dealership data. Not actual performance of any dealership or brand."


@st.cache_data
def load(folder: str, name: str) -> pd.DataFrame:
    return pd.read_csv(Path(folder) / name)


def table(folder: Path, name: str) -> pd.DataFrame | None:
    if not (folder / name).exists():
        st.error(f"Missing `{(folder / name).relative_to(ROOT).as_posix()}`. Run the pipelines first "
                 "(see README: python python/fada_extract.py, auto_retail_analysis.py, dealership_kpi_simulation.py).")
        return None
    return load(str(folder), name)


def download(df: pd.DataFrame, name: str) -> None:
    st.download_button(f"Download {name}", df.to_csv(index=False).encode("utf-8"), file_name=name, mime="text/csv")


def market_page() -> None:
    st.header("Market intelligence")
    st.info(OFFICIAL_NOTE)
    head = table(MARKET, "headline_metrics.csv")
    port = table(MARKET, "portfolio_brand_performance.csv")
    seg = table(MARKET, "segment_period_summary.csv")
    if head is None or port is None or seg is None:
        return
    h = dict(zip(head.metric, head.value))
    c = st.columns(4)
    c[0].metric("PV market growth", f"{h['pv_market_growth_pct']:+.1f}%")
    c[1].metric("Two-wheeler market growth", f"{h['tw_market_growth_pct']:+.1f}%")
    c[2].metric("Portfolio car growth", f"{h['portfolio_car_growth_pct']:+.1f}%",
                f"{h['portfolio_car_growth_pct'] - h['pv_market_growth_pct']:+.1f} pp vs PV")
    c[3].metric("Portfolio PV share", f"{h['portfolio_share_current_pct']:.2f}%",
                f"{h['portfolio_share_change_bps']:+.0f} bps")

    tabs = st.tabs(["Portfolio brands", "OEM share", "Monthly trend", "EV"])
    with tabs[0]:
        st.dataframe(port, hide_index=True, use_container_width=True)
        st.bar_chart(port.set_index("brand")[["yoy_growth_pct", "segment_growth_pct"]])
        download(port, "portfolio_brand_performance.csv")
    with tabs[1]:
        oem = table(MARKET, "oem_period_share.csv")
        if oem is not None:
            segment = st.radio("Segment", sorted(oem.segment.unique()), horizontal=True)
            complete = st.checkbox("Only OEMs listed by FADA in every month", value=True)
            view = oem[oem.segment == segment]
            if complete:
                view = view[view.coverage_complete & ~view.is_residual]
            view = view.sort_values("share_change_bps", ascending=False)
            st.dataframe(view[["oem_canonical", "units_prior", "units_current", "share_prior_pct",
                               "share_current_pct", "share_change_bps", "yoy_growth_pct", "coverage_note"]],
                         hide_index=True, use_container_width=True)
            download(view, f"oem_period_share_{segment}.csv")
    with tabs[2]:
        monthly = table(MARKET, "segment_monthly_yoy.csv")
        pm = table(MARKET, "portfolio_monthly_share.csv")
        if monthly is not None:
            st.subheader("YoY retail growth by month (%)")
            st.bar_chart(monthly.pivot(index="month_current", columns="segment", values="yoy_growth_pct"))
        if pm is not None:
            st.subheader("Portfolio share of PV retail by month (%)")
            st.line_chart(pm.assign(month=pm.period_month.str[5:7])
                          .pivot(index="month", columns="period", values="portfolio_share_pct"))
    with tabs[3]:
        ev = table(MARKET, "ev_period_summary.csv")
        if monthly is not None and ev is not None:
            st.caption("EV units are derived: published EV % (2 dp) x published segment total.")
            st.dataframe(ev, hide_index=True, use_container_width=True)
            seg_choice = st.radio("EV segment", ["PV", "2W"], horizontal=True)
            m = monthly[monthly.segment == seg_choice].set_index("month_current")
            st.line_chart(m[["ev_share_pct_prior", "ev_share_pct_current"]])


def dealer_page() -> None:
    st.header("Dealership operations")
    st.warning(SIMULATED_NOTE)
    funnel = table(DEALER, "funnel_by_dealership.csv")
    inv = table(DEALER, "inventory_by_dealership.csv")
    acts = table(DEALER, "action_triggers.csv")
    if funnel is None or inv is None or acts is None:
        return
    dealers = ["All dealerships"] + funnel.loc[funnel.dealership_id != "ALL", "dealership_name"].tolist()
    pick = st.selectbox("Dealership", dealers)
    row = funnel[funnel.dealership_name == pick].iloc[0]
    c = st.columns(5)
    for col, (label, field) in zip(c, [("Leads", "leads"), ("Test drives", "test_drives"), ("Bookings", "bookings"),
                                       ("Deliveries", "deliveries"), ("Lead to delivery", "lead_to_delivery_pct")]):
        col.metric(label, f"{row[field]:.1f}%" if field.endswith("pct") else f"{int(row[field]):,}")

    tabs = st.tabs(["Funnel", "Inventory ageing", "Action triggers", "Salespeople"])
    with tabs[0]:
        stages = table(DEALER, "funnel_stages.csv")
        did = "ALL" if pick == "All dealerships" else row.dealership_id
        if stages is not None:
            st.bar_chart(stages[stages.dealership_id == did].set_index("stage")["count"], horizontal=True)
        lost = table(DEALER, "lost_reasons.csv")
        if lost is not None:
            st.subheader("Lost-lead reasons (all dealerships)")
            st.dataframe(lost, hide_index=True, use_container_width=True)
    with tabs[1]:
        buckets = [c for c in inv.columns if c.startswith("stock_") and c.endswith("_days")]
        st.bar_chart(inv.set_index("dealership_id")[buckets])
        st.dataframe(inv, hide_index=True, use_container_width=True)
        download(inv, "inventory_by_dealership.csv")
    with tabs[2]:
        only = st.checkbox("Show breached rules only", value=True)
        view = acts[acts.breached] if only else acts
        st.dataframe(view.drop(columns=["data_classification"]), hide_index=True, use_container_width=True)
        st.caption("Thresholds are illustrative business rules unless threshold_basis cites a published source.")
        download(view, "action_triggers.csv")
    with tabs[3]:
        sp = table(DEALER, "salesperson_performance.csv")
        if sp is not None:
            st.dataframe(sp if pick == "All dealerships" else sp[sp.dealership_id == row.dealership_id],
                         hide_index=True, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="Auto Retail Analytics", layout="wide")
    st.title("Auto Retail Market & Dealership Analytics")
    page = st.sidebar.radio("View", ["Market (official FADA data)", "Dealership (simulated data)"])
    st.sidebar.caption("Read-only viewer of generated outputs. Regenerate with the Python pipelines.")
    market_page() if page.startswith("Market") else dealer_page()


main()
