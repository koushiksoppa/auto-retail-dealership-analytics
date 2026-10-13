import math

import numpy as np
import pandas as pd
import pytest

from market_metrics import (constant_share_units, market_share_pct, month_range, registration_gap,
                            share_change_bps, share_change_pp, weighted_share_pct, yoy_growth_pct)


def test_yoy_growth_scalar():
    assert yoy_growth_pct(122, 100) == pytest.approx(22.0)
    assert yoy_growth_pct(80, 100) == pytest.approx(-20.0)


@pytest.mark.parametrize("prior", [0, None, np.nan])
def test_yoy_growth_zero_or_missing_prior_is_nan_not_zero(prior):
    assert math.isnan(yoy_growth_pct(50, prior))


def test_yoy_growth_series_handles_zero_denominator():
    out = yoy_growth_pct(pd.Series([110, 5, 7]), pd.Series([100, 0, np.nan]))
    assert out.iloc[0] == pytest.approx(10.0)
    assert out.iloc[1:].isna().all()


def test_market_share_and_missing_numerator():
    assert market_share_pct(25, 200) == pytest.approx(12.5)
    s = market_share_pct(pd.Series([25, np.nan]), pd.Series([200, 200]))
    assert s.iloc[0] == pytest.approx(12.5) and math.isnan(s.iloc[1])
    assert math.isnan(market_share_pct(5, 0))


def test_basis_points_and_percentage_points():
    # 11.02% -> 9.70% is -1.32 pp = -132 bps (= (0.0970 - 0.1102) * 10,000)
    assert share_change_pp(9.70, 11.02) == pytest.approx(-1.32)
    assert share_change_bps(9.70, 11.02) == pytest.approx(-132.0)
    assert share_change_bps(9.70, 11.02) == pytest.approx((0.0970 - 0.1102) * 10_000)


def test_constant_share_and_gap():
    cs = constant_share_units(3_000, 10.0)
    assert cs == pytest.approx(300.0)
    assert registration_gap(cs, 250) == pytest.approx(50.0)   # forgone registrations
    assert registration_gap(cs, 330) == pytest.approx(-30.0)  # share gained


def test_weighted_share_is_not_average_of_monthly_shares():
    units, totals = [10, 90], [100, 900]          # monthly shares 10% and 10%
    assert weighted_share_pct(units, totals) == pytest.approx(10.0)
    units, totals = [50, 10], [100, 900]          # monthly shares 50% and 1.11%
    assert weighted_share_pct(units, totals) == pytest.approx(6.0)
    assert weighted_share_pct(units, totals) != pytest.approx((50 + 10 / 9) / 2)


def test_month_range_inclusive():
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
