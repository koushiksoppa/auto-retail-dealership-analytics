"""Market metric formulas used by the analysis, the tests and the SQL reconciliation.

All functions accept scalars or pandas Series. Undefined results (zero or missing
denominator) return NaN; they are never coerced to zero.

    YoY growth (%)              = (current / prior - 1) * 100
    Market share (%)            = units / segment units * 100
    Share change (pp)           = current share % - prior share %
    Share change (bps)          = share change (pp) * 100   (= share fraction change * 10,000)
    Constant-share registrations = current segment units * prior share % / 100
    Registration gap            = constant-share registrations - actual registrations
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_divide(numerator, denominator):
    num = pd.to_numeric(numerator, errors="coerce") if isinstance(numerator, pd.Series) else numerator
    den = pd.to_numeric(denominator, errors="coerce") if isinstance(denominator, pd.Series) else denominator
    if isinstance(num, pd.Series) or isinstance(den, pd.Series):
        num_s = num if isinstance(num, pd.Series) else pd.Series(num, index=den.index)
        den_s = den if isinstance(den, pd.Series) else pd.Series(den, index=num_s.index)
        out = num_s.astype(float) / den_s.astype(float).where(den_s > 0)
        return out.where(num_s.notna())
    if num is None or den is None or pd.isna(num) or pd.isna(den) or den <= 0:
        return np.nan
    return float(num) / float(den)


def yoy_growth_pct(current, prior):
    """Year-over-year growth in percent; NaN when the prior value is zero or missing."""
    return (_safe_divide(current, prior) - 1) * 100


def market_share_pct(units, segment_units):
    """Share of the segment in percent; NaN when the segment total is zero or missing."""
    return _safe_divide(units, segment_units) * 100


def share_change_pp(current_share_pct, prior_share_pct):
    return current_share_pct - prior_share_pct


def share_change_bps(current_share_pct, prior_share_pct):
    """Basis points: 1 percentage point = 100 bps."""
    return (current_share_pct - prior_share_pct) * 100


def constant_share_units(current_segment_units, prior_share_pct):
    """Registrations the brand would have had at its prior-period share of today's market."""
    return current_segment_units * prior_share_pct / 100


def registration_gap(constant_share, actual_units):
    """Positive = registrations forgone versus holding prior share; negative = share gained."""
    return constant_share - actual_units


def weighted_share_pct(numerators, denominators):
    """Period share from summed units (never an average of monthly percentages)."""
    return market_share_pct(pd.Series(numerators).sum(), pd.Series(denominators).sum())


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of YYYY-MM months."""
    return [p.strftime("%Y-%m") for p in pd.period_range(start, end, freq="M")]
