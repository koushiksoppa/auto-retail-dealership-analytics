"""Headless test of the optional viewer (requires requirements-app.txt; otherwise SKIPPED = blocked)."""
import pandas as pd
import pytest

from paths import MARKET_TABLE_DIR, ROOT

streamlit_testing = pytest.importorskip("streamlit.testing.v1", reason="streamlit not installed (optional; BLOCKED)")
APP = str(ROOT / "app" / "streamlit_app.py")


def test_market_page_renders_official_figures_from_outputs():
    at = streamlit_testing.AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("OFFICIAL" in i.value for i in at.info)
    head = pd.read_csv(MARKET_TABLE_DIR / "headline_metrics.csv").set_index("metric").value
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["PV market growth"] == f"{head['pv_market_growth_pct']:+.1f}%"
    assert metrics["Portfolio PV share"] == f"{head['portfolio_share_current_pct']:.2f}%"


def test_dealer_page_is_labelled_simulated():
    at = streamlit_testing.AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.radio[0].set_value("Dealership (simulated data)").run()
    assert not at.exception
    assert any("SIMULATED" in w.value for w in at.warning)
    assert not any("OFFICIAL" in i.value for i in at.info)
