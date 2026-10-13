"""Layer A - automotive market intelligence from official FADA retail data.

Reads the validated FADA extract (python/fada_extract.py), checks it again,
normalises OEM names with config/oem_mapping.csv and produces:

    reports/auto_retail/tables/*.csv     analytical tables (also loaded by SQL / Power BI)
    reports/auto_retail/charts/*.png     report charts
    reports/auto_retail/executive_report.md

Run (from the repository root):
    python python/fada_extract.py          # once, or after changing source PDFs
    python python/auto_retail_analysis.py

The script stops with a DataValidationError listing every problem found when the
input fails schema, coverage, mapping or reconciliation checks.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from market_metrics import (constant_share_units, market_share_pct, month_range, registration_gap,
                            share_change_bps, share_change_pp, yoy_growth_pct)
from paths import CONFIG_DIR, FADA_PROCESSED_DIR, MARKET_CHART_DIR, MARKET_REPORT_DIR, MARKET_TABLE_DIR

VALID_SEGMENTS = {"PV", "2W"}
VALID_ROW_LEVELS = {"top", "member", "others", "total"}
OEM_COLUMNS = {
    "period_month": "object", "segment": "object", "oem_raw": "object", "row_level": "object",
    "parent_oem_raw": "object", "units": "int64", "reported_as_dash": "bool",
    "published_share_pct": "float64", "source_file": "object",
}
FUEL_COLUMNS = {"period_month": "object", "value_role": "object", "segment": "object",
                "fuel": "object", "share_pct": "float64"}
MAPPING_COLUMNS = ["segment", "oem_raw", "oem_canonical", "portfolio_brand"]
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PORTFOLIO_CARS = "Portfolio car brands"


class DataValidationError(ValueError):
    """Raised with every problem found, so one run shows all defects."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Input validation failed:\n  - " + "\n  - ".join(problems))


# =========================================================================== configuration
def load_config(path: Path = CONFIG_DIR / "market_analysis.yaml") -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    validate_periods(cfg)
    return cfg


def validate_periods(cfg: dict) -> None:
    cur = month_range(cfg["current_period"]["start"], cfg["current_period"]["end"])
    pri = month_range(cfg["prior_period"]["start"], cfg["prior_period"]["end"])
    problems = []
    if not cur or not pri:
        problems.append("current and prior periods must each contain at least one month")
    elif len(cur) != len(pri):
        problems.append(f"period lengths differ: current {len(cur)} months, prior {len(pri)} months")
    elif any((pd.Period(c, "M") - pd.Period(p, "M")).n != 12 for c, p in zip(cur, pri)):
        problems.append("prior period must be exactly 12 months before the current period")
    if problems:
        raise DataValidationError(problems)


def period_months(cfg: dict) -> tuple[list[str], list[str]]:
    return (month_range(cfg["current_period"]["start"], cfg["current_period"]["end"]),
            month_range(cfg["prior_period"]["start"], cfg["prior_period"]["end"]))


# =========================================================================== loading
def load_inputs(processed_dir: Path = FADA_PROCESSED_DIR) -> dict[str, pd.DataFrame]:
    files = {"oem": "fada_oem_monthly.csv", "fuel": "fada_fuel_mix_monthly.csv"}
    missing = [f for f in files.values() if not (processed_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"Missing {missing} in {processed_dir}. Run: python python/fada_extract.py")
    return {k: pd.read_csv(processed_dir / f, dtype={"period_month": str, "oem_raw": str,
                                                      "parent_oem_raw": str})
            for k, f in files.items()}


def load_mapping(path: Path = CONFIG_DIR / "oem_mapping.csv") -> pd.DataFrame:
    mapping = pd.read_csv(path, dtype=str)
    missing = [c for c in MAPPING_COLUMNS if c not in mapping.columns]
    if missing:
        raise DataValidationError([f"oem_mapping.csv is missing columns {missing}"])
    dups = mapping[mapping.duplicated(["segment", "oem_raw"], keep=False)]
    if len(dups):
        raise DataValidationError([f"oem_mapping.csv maps {r.segment}/{r.oem_raw} more than once"
                                   for r in dups.drop_duplicates(["segment", "oem_raw"]).itertuples()])
    return mapping


# =========================================================================== validation
def validate_oem_frame(oem: pd.DataFrame, mapping: pd.DataFrame, cfg: dict) -> None:
    """Schema, period, duplicate, coverage, mapping and reconciliation checks."""
    problems: list[str] = []
    if oem.empty:
        raise DataValidationError(["OEM input is empty"])
    missing_cols = [c for c in OEM_COLUMNS if c not in oem.columns]
    if missing_cols:
        raise DataValidationError([f"OEM input is missing required columns {missing_cols}"])

    bad_period = oem.loc[~oem.period_month.astype(str).str.match(PERIOD_RE), "period_month"].unique()
    if len(bad_period):
        problems.append(f"invalid period_month values (expected YYYY-MM): {list(bad_period)[:5]}")
    bad_seg = set(oem.segment.unique()) - VALID_SEGMENTS
    if bad_seg:
        problems.append(f"unexpected segment codes {sorted(bad_seg)}")
    bad_level = set(oem.row_level.unique()) - VALID_ROW_LEVELS
    if bad_level:
        problems.append(f"unexpected row_level values {sorted(bad_level)}")
    units = pd.to_numeric(oem.units, errors="coerce")
    if units.isna().any():
        problems.append(f"{int(units.isna().sum())} rows have missing or non-numeric units "
                        "(missing values are not treated as zero)")
    elif (units < 0).any() or (units % 1 != 0).any():
        problems.append("units must be non-negative whole registrations")
    if problems:
        raise DataValidationError(problems)

    key = ["segment", "period_month", "oem_raw", "row_level", "parent_oem_raw"]
    dups = oem[oem.duplicated(key, keep=False)]
    for r in dups.drop_duplicates(key).itertuples():
        problems.append(f"duplicate source record: {r.segment} {r.period_month} '{r.oem_raw}' ({r.row_level})")

    cur, pri = period_months(cfg)
    for seg in sorted(VALID_SEGMENTS):
        have = set(oem.loc[oem.segment == seg, "period_month"])
        gaps = [m for m in cur + pri if m not in have]
        if gaps:
            problems.append(f"{seg}: no source data for months {gaps}")

    totals = oem[oem.row_level == "total"]
    tcount = totals.groupby(["segment", "period_month"]).size()
    for (seg, month), n in tcount[tcount != 1].items():
        problems.append(f"{seg} {month}: expected one Total row, found {n}")

    body = oem[oem.row_level.isin(["top", "others"])]
    known = set(zip(mapping.segment, mapping.oem_raw))
    for seg, raw in sorted(set(zip(body.segment, body.oem_raw)) - known):
        problems.append(f"unexpected OEM not in config/oem_mapping.csv: {seg} '{raw}'")

    parts = body.groupby(["segment", "period_month"]).units.sum()
    for (seg, month), total in totals.set_index(["segment", "period_month"]).units.items():
        got = parts.get((seg, month))
        if got is None or got != total:
            problems.append(f"{seg} {month}: OEM rows sum to {got} but FADA Total is {total} "
                            f"(difference {None if got is None else got - total})")

    members = oem[oem.row_level == "member"]
    msum = members.groupby(["segment", "period_month", "parent_oem_raw"]).units.sum()
    for (seg, month, parent), s in msum.items():
        p = oem.loc[(oem.segment == seg) & (oem.period_month == month) & (oem.oem_raw == parent)
                    & (oem.row_level == "top"), "units"]
        if len(p) != 1 or p.iloc[0] != s:
            problems.append(f"{seg} {month}: group '{parent}' does not equal the sum of its members ({s})")

    mapped = body.merge(mapping, on=["segment", "oem_raw"], how="inner")
    multi = mapped.groupby(["segment", "period_month", "oem_canonical"]).oem_raw.nunique()
    for (seg, month, canon), n in multi[multi > 1].items():
        problems.append(f"{seg} {month}: {n} raw rows map to '{canon}' in the same month (possible double count)")

    if problems:
        raise DataValidationError(problems)


def validate_fuel_frame(fuel: pd.DataFrame, cfg: dict) -> None:
    problems = []
    if fuel.empty:
        raise DataValidationError(["fuel-mix input is empty"])
    missing_cols = [c for c in FUEL_COLUMNS if c not in fuel.columns]
    if missing_cols:
        raise DataValidationError([f"fuel-mix input is missing required columns {missing_cols}"])
    ev = canonical_ev_share(fuel)
    cur, pri = period_months(cfg)
    for seg in sorted(VALID_SEGMENTS):
        have = set(ev.loc[ev.segment == seg, "period_month"])
        gaps = [m for m in cur + pri if m not in have]
        if gaps:
            problems.append(f"{seg}: no EV share published for months {gaps}")
    if ((ev.ev_share_pct < 0) | (ev.ev_share_pct > 100)).any():
        problems.append("EV share outside 0-100%")
    if problems:
        raise DataValidationError(problems)


# =========================================================================== core tables
def canonical_ev_share(fuel: pd.DataFrame) -> pd.DataFrame:
    """EV share for each month from the release whose OEM table covers that month
    (value_role current or prior_year), so EV and OEM data share one denominator."""
    ev = fuel[(fuel.fuel == "EV") & fuel.value_role.isin(["current", "prior_year"])]
    dup = ev.duplicated(["segment", "period_month"], keep=False)
    if dup.any():
        raise DataValidationError([f"EV share for {r.segment} {r.period_month} appears in more than one release"
                                   for r in ev[dup].drop_duplicates(["segment", "period_month"]).itertuples()])
    return ev[["segment", "period_month", "share_pct"]].rename(columns={"share_pct": "ev_share_pct"})


def build_oem_month(oem: pd.DataFrame, mapping: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One row per segment, month and canonical OEM, using top-level and residual rows only
    (group members are already inside their group row)."""
    cur, pri = period_months(cfg)
    body = oem[oem.row_level.isin(["top", "others"]) & oem.period_month.isin(cur + pri)]
    m = body.merge(mapping[MAPPING_COLUMNS], on=["segment", "oem_raw"], how="left", validate="many_to_one")
    totals = (oem[(oem.row_level == "total")].set_index(["segment", "period_month"]).units
              .rename("segment_units"))
    out = (m.groupby(["segment", "period_month", "oem_canonical"], as_index=False)
           .agg(units=("units", "sum"), portfolio_brand=("portfolio_brand", "first"),
                is_residual=("row_level", lambda s: (s == "others").all())))
    out = out.join(totals, on=["segment", "period_month"])
    out["period"] = np.where(out.period_month.isin(cur), "current", "prior")
    out["month_num"] = out.period_month.str[5:7].astype(int)
    out["share_pct"] = market_share_pct(out.units, out.segment_units)
    return out.sort_values(["segment", "period_month", "units"], ascending=[True, True, False]).reset_index(drop=True)


def segment_totals(oem: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cur, pri = period_months(cfg)
    t = oem[(oem.row_level == "total") & oem.period_month.isin(cur + pri)][["segment", "period_month", "units"]]
    t = t.assign(period=np.where(t.period_month.isin(cur), "current", "prior"),
                 month_num=t.period_month.str[5:7].astype(int))
    return t.reset_index(drop=True)


def segment_monthly_yoy(totals: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    t = totals.merge(ev, on=["segment", "period_month"], how="left")
    w = t.pivot_table(index=["segment", "month_num"], columns="period",
                      values=["units", "ev_share_pct"], aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w = w.reset_index()
    months = totals.pivot_table(index=["segment", "month_num"], columns="period", values="period_month",
                                aggfunc="first").reset_index()
    w = w.merge(months.rename(columns={"current": "month_current", "prior": "month_prior"}),
                on=["segment", "month_num"])
    w["yoy_growth_pct"] = yoy_growth_pct(w.units_current, w.units_prior)
    w["ev_share_change_bps"] = share_change_bps(w.ev_share_pct_current, w.ev_share_pct_prior)
    cols = ["segment", "month_current", "month_prior", "units_prior", "units_current", "yoy_growth_pct",
            "ev_share_pct_prior", "ev_share_pct_current", "ev_share_change_bps"]
    return w[cols].sort_values(["segment", "month_current"]).reset_index(drop=True)


def segment_period_summary(totals: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    p = totals.pivot_table(index="segment", columns="period", values="units", aggfunc="sum")
    out = pd.DataFrame({
        "segment": p.index, "segment_name": [cfg["segments"][s] for s in p.index],
        "units_prior": p["prior"].values, "units_current": p["current"].values,
    })
    out["yoy_growth_pct"] = yoy_growth_pct(out.units_current, out.units_prior)
    out["incremental_units"] = out.units_current - out.units_prior
    return out.reset_index(drop=True)


def _period_metrics(df: pd.DataFrame, seg_units: pd.DataFrame) -> pd.DataFrame:
    """df: [segment, key..., units_prior, units_current]; seg_units: [segment, seg_prior, seg_current]."""
    df = df.merge(seg_units, on="segment", how="left")
    df["share_prior_pct"] = market_share_pct(df.units_prior, df.seg_prior)
    df["share_current_pct"] = market_share_pct(df.units_current, df.seg_current)
    df["share_change_pp"] = share_change_pp(df.share_current_pct, df.share_prior_pct)
    df["share_change_bps"] = share_change_bps(df.share_current_pct, df.share_prior_pct)
    df["yoy_growth_pct"] = yoy_growth_pct(df.units_current, df.units_prior)
    df["segment_growth_pct"] = yoy_growth_pct(df.seg_current, df.seg_prior)
    df["growth_gap_pp"] = df.yoy_growth_pct - df.segment_growth_pct
    df["constant_share_units"] = constant_share_units(df.seg_current, df.share_prior_pct)
    df["registration_gap_units"] = registration_gap(df.constant_share_units, df.units_current)
    return df


def _segment_units(totals: pd.DataFrame) -> pd.DataFrame:
    p = totals.pivot_table(index="segment", columns="period", values="units", aggfunc="sum")
    return pd.DataFrame({"segment": p.index, "seg_prior": p["prior"].values, "seg_current": p["current"].values})


def oem_period_table(oem_month: pd.DataFrame, totals: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Period metrics per canonical OEM. OEMs that FADA lists only in some months (others folded
    into 'Others' below the reporting threshold) are flagged and get no growth/share metrics."""
    n_months = len(period_months(cfg)[0])
    g = (oem_month.groupby(["segment", "oem_canonical", "period"])
         .agg(units=("units", "sum"), months=("period_month", "nunique"),
              is_residual=("is_residual", "all"), portfolio_brand=("portfolio_brand", "first"))
         .reset_index())
    w = g.pivot_table(index=["segment", "oem_canonical"], columns="period", values=["units", "months"],
                      aggfunc="first")
    w.columns = [f"{a}_{b}" for a, b in w.columns]
    w = w.reset_index()
    for c in ["units_prior", "units_current", "months_prior", "months_current"]:
        if c not in w:
            w[c] = np.nan
    attrs = g.groupby(["segment", "oem_canonical"]).agg(is_residual=("is_residual", "all"),
                                                         portfolio_brand=("portfolio_brand", "first"))
    w = w.join(attrs, on=["segment", "oem_canonical"])
    w["coverage_complete"] = (w.months_prior == n_months) & (w.months_current == n_months)
    w = _period_metrics(w, _segment_units(totals))
    metric_cols = ["share_prior_pct", "share_current_pct", "share_change_pp", "share_change_bps",
                   "yoy_growth_pct", "growth_gap_pp", "constant_share_units", "registration_gap_units"]
    partial = ~w.coverage_complete
    w.loc[partial, metric_cols] = np.nan
    min_base = cfg.get("min_base_units_for_growth", 0)
    low_base = w.coverage_complete & (w.units_prior < min_base)
    w.loc[low_base, ["yoy_growth_pct", "growth_gap_pp"]] = np.nan
    w["coverage_note"] = np.select(
        [w.is_residual, partial, low_base],
        ["FADA residual row (composition varies by month)",
         "Listed by FADA in only some months (below its reporting threshold otherwise); metrics not computed",
         f"Prior-period base below {min_base:,} units; growth % not meaningful"],
        default="")
    w = w.drop(columns=["seg_prior", "seg_current"])
    return w.sort_values(["segment", "units_current"], ascending=[True, False]).reset_index(drop=True)


def portfolio_tables(oem_month: pd.DataFrame, totals: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    brands = cfg["portfolio_brands"]
    pb = oem_month[oem_month.portfolio_brand.notna()]
    unknown = set(pb.portfolio_brand) - set(brands)
    missing = set(brands) - set(pb.portfolio_brand)
    if unknown or missing:
        raise DataValidationError([f"portfolio brand mismatch: in mapping only {sorted(unknown)}, "
                                   f"in config only {sorted(missing)}"])
    wrong_seg = pb[pb.apply(lambda r: brands[r.portfolio_brand] != r.segment, axis=1)]
    if len(wrong_seg):
        raise DataValidationError([f"{r.portfolio_brand} mapped in {r.segment} but configured for "
                                   f"{brands[r.portfolio_brand]}" for r in wrong_seg.itertuples()])
    n_months = len(period_months(cfg)[0])
    months = pb.groupby(["portfolio_brand", "period"]).period_month.nunique()
    gaps = months[months != n_months]
    if len(gaps):
        raise DataValidationError([f"portfolio brand {b} reported in {n} of {n_months} {p} months"
                                   for (b, p), n in gaps.items()])

    by_brand = pb.pivot_table(index=["segment", "portfolio_brand"], columns="period", values="units",
                              aggfunc="sum").reset_index()
    by_brand = by_brand.rename(columns={"prior": "units_prior", "current": "units_current",
                                        "portfolio_brand": "brand"})
    pv = by_brand[by_brand.segment == "PV"]
    agg = pd.DataFrame([{"segment": "PV", "brand": PORTFOLIO_CARS,
                         "units_prior": pv.units_prior.sum(), "units_current": pv.units_current.sum()}])
    out = _period_metrics(pd.concat([by_brand, agg], ignore_index=True), _segment_units(totals))
    out["is_aggregate"] = out.brand == PORTFOLIO_CARS
    order = {b: i for i, b in enumerate(list(brands) + [PORTFOLIO_CARS])}
    return out.sort_values("brand", key=lambda s: s.map(order)).reset_index(drop=True)


def portfolio_monthly_share(oem_month: pd.DataFrame) -> pd.DataFrame:
    pv = oem_month[(oem_month.segment == "PV")]
    port = pv[pv.portfolio_brand.notna()].groupby(["period_month", "period", "month_num"]).units.sum()
    seg = pv.groupby(["period_month", "period", "month_num"]).segment_units.first()
    out = pd.DataFrame({"portfolio_units": port, "segment_units": seg}).reset_index()
    out["portfolio_share_pct"] = market_share_pct(out.portfolio_units, out.segment_units)
    brand = (pv[pv.portfolio_brand.notna()].pivot_table(index="period_month", columns="portfolio_brand",
                                                        values="share_pct", aggfunc="sum")
             .add_suffix("_share_pct").reset_index())
    return out.merge(brand, on="period_month").sort_values("period_month").reset_index(drop=True)


def ev_period_table(totals: pd.DataFrame, ev: pd.DataFrame, oem_month: pd.DataFrame,
                    cfg: dict) -> pd.DataFrame:
    """Weighted EV share by segment and period. EV units are DERIVED: published EV share (2 dp)
    x published segment total, so each monthly estimate carries up to +/-0.005% of the total."""
    t = totals.merge(ev, on=["segment", "period_month"], how="left", validate="one_to_one")
    t["ev_units_est"] = t.units * t.ev_share_pct / 100
    rows = []
    for seg, g in t.groupby("segment"):
        r = {"segment": seg}
        for per in ("prior", "current"):
            gp = g[g.period == per]
            r[f"units_{per}"] = gp.units.sum()
            r[f"ev_units_est_{per}"] = gp.ev_units_est.sum()
            r[f"ev_share_{per}_pct"] = market_share_pct(gp.ev_units_est.sum(), gp.units.sum())
            r[f"ev_rounding_band_units_{per}"] = (gp.units * 0.005 / 100).sum()
        rows.append(r)
    out = pd.DataFrame(rows)
    out["ev_share_change_bps"] = share_change_bps(out.ev_share_current_pct, out.ev_share_prior_pct)
    out["ev_units_growth_pct"] = yoy_growth_pct(out.ev_units_est_current, out.ev_units_est_prior)
    out["segment_growth_pct"] = yoy_growth_pct(out.units_current, out.units_prior)

    # Illustrative: EV-only brands' share of their segment's estimated EV retail.
    brand_rows = []
    for brand in cfg.get("ev_only_brands", []):
        seg = cfg["portfolio_brands"][brand]
        b = oem_month[oem_month.portfolio_brand == brand].groupby("period").units.sum()
        e = out.set_index("segment").loc[seg]
        brand_rows.append({
            "brand": brand, "segment": seg, "units_prior": b["prior"], "units_current": b["current"],
            "share_of_segment_ev_prior_pct": market_share_pct(b["prior"], e.ev_units_est_prior),
            "share_of_segment_ev_current_pct": market_share_pct(b["current"], e.ev_units_est_current),
        })
    brand_ev = pd.DataFrame(brand_rows)
    if len(brand_ev):
        brand_ev["share_change_bps"] = share_change_bps(brand_ev.share_of_segment_ev_current_pct,
                                                        brand_ev.share_of_segment_ev_prior_pct)
    out.attrs["brand_ev"] = brand_ev
    return out


def attention_list(portfolio: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    rules = cfg["attention_rules"]
    rows = []
    for r in portfolio.itertuples():
        reasons = []
        if r.growth_gap_pp <= rules["growth_gap_pp_warning"]:
            reasons.append(f"growth {r.growth_gap_pp:+.1f} pp vs segment")
        if r.share_change_bps <= rules["share_change_bps_warning"]:
            reasons.append(f"share {r.share_change_bps:+.0f} bps")
        if r.registration_gap_units >= rules["registration_gap_units_warning"]:
            reasons.append(f"{r.registration_gap_units:,.0f} registrations below constant share")
        status = "Attention" if reasons else "On track / outperforming"
        rows.append({"brand": r.brand, "segment": r.segment, "status": status,
                     "triggers": "; ".join(reasons) if reasons else "none",
                     "yoy_growth_pct": r.yoy_growth_pct, "segment_growth_pct": r.segment_growth_pct,
                     "share_change_bps": r.share_change_bps, "registration_gap_units": r.registration_gap_units})
    return pd.DataFrame(rows)


def competitor_moves(oem_period: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    ok = oem_period[oem_period.coverage_complete & ~oem_period.is_residual]
    out = []
    for seg, g in ok.groupby("segment"):
        g = g.sort_values("share_change_bps")
        gainers = g[g.share_change_bps > 0].tail(n).iloc[::-1]
        losers = g[g.share_change_bps < 0].head(n)
        for label, part in (("Top share gainers", gainers), ("Top share losers", losers)):
            out.append(part.assign(list=label))
    cols = ["segment", "list", "oem_canonical", "units_prior", "units_current", "share_prior_pct",
            "share_current_pct", "share_change_bps", "yoy_growth_pct", "portfolio_brand"]
    return pd.concat(out)[cols].reset_index(drop=True)


def headline_metrics(seg: pd.DataFrame, port: pd.DataFrame, ev: pd.DataFrame,
                     ev_monthly: pd.DataFrame, cfg: dict) -> dict[str, float]:
    s = seg.set_index("segment")
    p = port.set_index("brand")
    e = ev.set_index("segment")
    cur = period_months(cfg)[0]
    pv_ev = ev_monthly[ev_monthly.segment == "PV"].set_index("period_month").ev_share_pct
    return {
        "pv_market_growth_pct": s.loc["PV", "yoy_growth_pct"],
        "tw_market_growth_pct": s.loc["2W", "yoy_growth_pct"],
        "portfolio_car_growth_pct": p.loc[PORTFOLIO_CARS, "yoy_growth_pct"],
        "portfolio_share_prior_pct": p.loc[PORTFOLIO_CARS, "share_prior_pct"],
        "portfolio_share_current_pct": p.loc[PORTFOLIO_CARS, "share_current_pct"],
        "portfolio_share_change_bps": p.loc[PORTFOLIO_CARS, "share_change_bps"],
        "portfolio_constant_share_units": p.loc[PORTFOLIO_CARS, "constant_share_units"],
        "portfolio_registration_gap_units": p.loc[PORTFOLIO_CARS, "registration_gap_units"],
        "toyota_share_change_bps": p.loc["Toyota", "share_change_bps"],
        "ather_growth_pct": p.loc["Ather", "yoy_growth_pct"],
        "pv_ev_share_prior_pct": e.loc["PV", "ev_share_prior_pct"],
        "pv_ev_share_current_pct": e.loc["PV", "ev_share_current_pct"],
        "tw_ev_share_prior_pct": e.loc["2W", "ev_share_prior_pct"],
        "tw_ev_share_current_pct": e.loc["2W", "ev_share_current_pct"],
        "pv_ev_share_first_month_current_pct": pv_ev.loc[cur[0]],
        "pv_ev_share_last_month_current_pct": pv_ev.loc[cur[-1]],
    }


def verify_brief_figures(headline: dict[str, float], cfg: dict) -> pd.DataFrame:
    rows = []
    for f in cfg.get("brief_figures", []):
        val = headline.get(f["id"])
        ok = val is not None and abs(val - f["value"]) <= f["tolerance"]
        note = ""
        if f["id"].startswith("pv_ev_share") and not ok:
            first, last = headline["pv_ev_share_first_month_current_pct"], headline["pv_ev_share_last_month_current_pct"]
            note = (f"Not a period comparison: {first:.2f}% and {last:.2f}% are the single months "
                    f"{cfg['current_period']['start']} and {cfg['current_period']['end']}. "
                    "Weighted period shares are shown in 'reproduced'.")
        rows.append({"figure": f["label"], "brief_value": f["claimed"], "reproduced": val,
                     "status": "Reproduced" if ok else "Does not reproduce as stated", "note": note})
    return pd.DataFrame(rows)


# =========================================================================== charts
PALETTE = {"current": "#2a78d6", "prior": "#86b6ef", "gain": "#2a78d6", "loss": "#e34948",
           "segment": "#8c8b86", "text": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df"}


def _style(ax, title: str, ylabel: str | None = None):
    ax.set_title(title, loc="left", fontsize=11, color=PALETTE["text"], fontweight="bold")
    if ylabel:
        ax.set_ylabel(ylabel, color=PALETTE["muted"], fontsize=9)
    ax.tick_params(colors=PALETTE["muted"], labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(PALETTE["grid"])
    ax.grid(axis="y", color=PALETTE["grid"], linewidth=0.8)
    ax.set_axisbelow(True)


def make_charts(seg_monthly, port, port_monthly, oem_period, out_dir: Path, cfg: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "svg.hashsalt": "fixed"})
    out_dir.mkdir(parents=True, exist_ok=True)
    cur_l, pri_l = cfg["current_period"]["label"], cfg["prior_period"]["label"]
    written: list[Path] = []
    meta = {"Software": None}

    def save(fig, name):
        path = out_dir / name
        fig.tight_layout()
        fig.savefig(path, dpi=150, metadata=meta, facecolor="white")
        plt.close(fig)
        written.append(path)

    # 1. Monthly YoY growth by segment (small multiples, one axis each).
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
    for ax, seg in zip(axes, ["PV", "2W"]):
        d = seg_monthly[seg_monthly.segment == seg]
        labels = pd.to_datetime(d.month_current).dt.strftime("%b")
        bars = ax.bar(labels, d.yoy_growth_pct, color=PALETTE["current"], width=0.6)
        ax.bar_label(bars, fmt="%.1f%%", fontsize=7, color=PALETTE["muted"], padding=2)
        _style(ax, f"{cfg['segments'][seg]}: YoY retail growth by month", "YoY growth (%)")
        ax.axhline(0, color=PALETTE["muted"], linewidth=0.8)
    save(fig, "01_segment_monthly_yoy_growth.png")

    # 2. Portfolio brand growth vs own-segment growth.
    d = port
    fig, ax = plt.subplots(figsize=(9, 4))
    x = np.arange(len(d))
    b1 = ax.bar(x - 0.18, d.yoy_growth_pct, 0.34, color=PALETTE["current"], label="Brand growth")
    b2 = ax.bar(x + 0.18, d.segment_growth_pct, 0.34, color=PALETTE["segment"], label="Own-segment growth")
    for b in (b1, b2):
        ax.bar_label(b, fmt="%.1f%%", fontsize=7, color=PALETTE["muted"], padding=2)
    ax.set_xticks(x, [f"{r.brand}\n({r.segment})" for r in d.itertuples()], fontsize=8)
    ax.axhline(0, color=PALETTE["muted"], linewidth=0.8)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    _style(ax, f"Portfolio brand growth vs segment growth, {cur_l} vs {pri_l}", "YoY growth (%)")
    save(fig, "02_portfolio_growth_vs_segment.png")

    # 3-4. Share change (bps) by OEM, diverging bars, complete-coverage OEMs only.
    for i, seg in enumerate(["PV", "2W"], start=3):
        d = oem_period[(oem_period.segment == seg) & oem_period.coverage_complete & ~oem_period.is_residual]
        d = d.sort_values("share_change_bps")
        fig, ax = plt.subplots(figsize=(8, 0.32 * len(d) + 1.2))
        colors = [PALETTE["gain"] if v >= 0 else PALETTE["loss"] for v in d.share_change_bps]
        bars = ax.barh(d.oem_canonical, d.share_change_bps, color=colors, height=0.6)
        ax.bar_label(bars, fmt="%+.0f", fontsize=7, color=PALETTE["muted"], padding=2)
        ax.axvline(0, color=PALETTE["muted"], linewidth=0.8)
        for lbl in ax.get_yticklabels():
            if lbl.get_text() in set(d.loc[d.portfolio_brand.notna(), "oem_canonical"]):
                lbl.set_fontweight("bold")
        _style(ax, f"{cfg['segments'][seg]}: market-share change\n{cur_l} vs {pri_l} (basis points)")
        ax.margins(x=0.12)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=PALETTE["grid"], linewidth=0.8)
        ax.set_xlabel("Share change (basis points); portfolio brands in bold", color=PALETTE["muted"], fontsize=8)
        save(fig, f"0{i}_{seg.lower()}_share_change_bps.png")

    # 5. Portfolio PV share by month, prior vs current year.
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for per, lbl in (("prior", pri_l), ("current", cur_l)):
        d = port_monthly[port_monthly.period == per]
        ax.plot(pd.to_datetime(d.period_month).dt.strftime("%b"), d.portfolio_share_pct, marker="o",
                markersize=5, linewidth=2, color=PALETTE[per], label=lbl)
    ax.legend(frameon=False, fontsize=8)
    _style(ax, "Portfolio car brands: combined share of PV retail by month", "Share of PV retail (%)")
    save(fig, "05_portfolio_pv_share_monthly.png")

    # 6. EV share by month, PV and 2W (separate panels; never combined).
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for ax, seg in zip(axes, ["PV", "2W"]):
        d = seg_monthly[seg_monthly.segment == seg]
        labels = pd.to_datetime(d.month_current).dt.strftime("%b")
        ax.plot(labels, d.ev_share_pct_prior, marker="o", markersize=5, linewidth=2, color=PALETTE["prior"], label=pri_l)
        ax.plot(labels, d.ev_share_pct_current, marker="o", markersize=5, linewidth=2, color=PALETTE["current"], label=cur_l)
        ax.legend(frameon=False, fontsize=8)
        _style(ax, f"{cfg['segments'][seg]}: EV share of retail", "EV share (%)")
    save(fig, "06_ev_share_monthly.png")

    # 7. Registration gap vs constant share.
    d = port
    fig, ax = plt.subplots(figsize=(9, 3.8))
    colors = [PALETTE["loss"] if v > 0 else PALETTE["gain"] for v in d.registration_gap_units]
    bars = ax.bar([f"{r.brand}\n({r.segment})" for r in d.itertuples()], d.registration_gap_units, color=colors, width=0.6)
    ax.bar_label(bars, labels=[f"{v:+,.0f}" for v in d.registration_gap_units], fontsize=7, color=PALETTE["muted"], padding=2)
    ax.axhline(0, color=PALETTE["muted"], linewidth=0.8)
    ax.tick_params(axis="x", labelsize=8)
    _style(ax, "Registration gap vs prior-period share (positive = registrations forgone)", "Registrations")
    save(fig, "07_registration_gap.png")
    return written


# =========================================================================== report
def md_table(df: pd.DataFrame, formats: dict[str, str] | None = None) -> str:
    formats = formats or {}

    def fmt(col, v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "n/a"
        f = formats.get(col)
        return format(v, f) if f else str(v)

    head = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "|".join("---" for _ in df.columns) + "|"
    body = ["| " + " | ".join(fmt(c, v) for c, v in zip(df.columns, row)) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, sep, *body])


def write_report(r: dict, cfg: dict, path: Path) -> None:
    h, seg, port, ev = r["headline"], r["segment_summary"], r["portfolio"], r["ev_period"]
    cur_l, pri_l = cfg["current_period"]["label"], cfg["prior_period"]["label"]
    pS = port.set_index("brand")
    att = r["attention"]
    moves = r["competitors"]
    brand_ev = ev.attrs.get("brand_ev", pd.DataFrame())
    meta = r["release_meta"]

    def pct(v, d=1):
        return f"{v:+.{d}f}%"

    lines = [
        "# Automotive Retail Market Intelligence: Executive Report",
        "",
        f"**Scope:** India vehicle retail registrations, {cur_l} vs {pri_l}. "
        "**Source:** FADA monthly vehicle retail data press releases (official, public). "
        "**Generated by:** `python python/auto_retail_analysis.py` from validated extracts; every number below is "
        "computed from `data/processed/fada/`, which is extracted from checksummed PDFs.",
        "",
        "> Layer A uses real published data only. Dealership operating KPIs (Layer B) are simulated and are reported "
        "separately in `reports/dealership/`.",
        "",
        "## 1. Headline answers",
        "",
        f"1. **Market growth.** Passenger-vehicle (car/UV) retail grew **{pct(h['pv_market_growth_pct'])}** "
        f"({seg.set_index('segment').loc['PV','units_prior']:,.0f} to {seg.set_index('segment').loc['PV','units_current']:,.0f}); "
        f"two-wheeler retail grew **{pct(h['tw_market_growth_pct'])}**.",
        f"2. **Portfolio vs segment.** The four portfolio car brands grew **{pct(h['portfolio_car_growth_pct'])}**, "
        f"{abs(h['portfolio_car_growth_pct'] - h['pv_market_growth_pct']):.1f} pp slower than the PV market. "
        f"Ather grew **{pct(h['ather_growth_pct'])}** against two-wheeler growth of {pct(h['tw_market_growth_pct'])}.",
        f"3. **Market share.** Combined portfolio PV share fell from **{h['portfolio_share_prior_pct']:.2f}%** to "
        f"**{h['portfolio_share_current_pct']:.2f}%** ({h['portfolio_share_change_bps']:+.0f} bps). "
        f"Toyota's share moved {h['toyota_share_change_bps']:+.0f} bps.",
        "4. **Competitors.** Largest share gainers and losers in each segment are listed in section 4.",
        f"5. **EV adoption.** Weighted EV share of PV retail rose from **{h['pv_ev_share_prior_pct']:.2f}%** to "
        f"**{h['pv_ev_share_current_pct']:.2f}%**; of two-wheeler retail from {h['tw_ev_share_prior_pct']:.2f}% to "
        f"{h['tw_ev_share_current_pct']:.2f}%.",
        f"6. **Constant-share opportunity.** At its {pri_l} share, the car portfolio would have retailed "
        f"**{h['portfolio_constant_share_units']:,.0f}** units in {cur_l} versus "
        f"{pS.loc[PORTFOLIO_CARS,'units_current']:,.0f} actual: a gap of **{h['portfolio_registration_gap_units']:,.0f}** registrations.",
        "7. **Management attention.** " + "; ".join(
            f"{a.brand}: {a.status.lower()} ({a.triggers})" for a in att.itertuples() if a.brand != PORTFOLIO_CARS) + ".",
        "",
        "## 2. Segment growth",
        "",
        md_table(seg[["segment_name", "units_prior", "units_current", "incremental_units", "yoy_growth_pct"]]
                 .rename(columns={"segment_name": "Segment", "units_prior": pri_l, "units_current": cur_l,
                                  "incremental_units": "Incremental units", "yoy_growth_pct": "YoY growth %"}),
                 {pri_l: ",.0f", cur_l: ",.0f", "Incremental units": "+,.0f", "YoY growth %": "+.2f"}),
        "",
        "Monthly YoY growth (current month vs same month of prior year):",
        "",
        md_table(r["segment_monthly"][["segment", "month_current", "units_prior", "units_current", "yoy_growth_pct"]]
                 .rename(columns={"month_current": "month", "units_prior": "prior-year units",
                                  "units_current": "units", "yoy_growth_pct": "YoY %"}),
                 {"prior-year units": ",.0f", "units": ",.0f", "YoY %": "+.2f"}),
        "",
        "Note: FADA describes September 2025 as depressed by purchase deferral ahead of GST 2.0 (effective "
        "22 Sep 2025), so September 2026 YoY growth is flattered by a low base.",
        "",
        "![Monthly YoY growth](charts/01_segment_monthly_yoy_growth.png)",
        "",
        "## 3. Portfolio brand performance",
        "",
        "Each brand is benchmarked only against its own segment: car brands against total PV retail, Ather "
        "against total two-wheeler retail.",
        "",
        md_table(port[["brand", "segment", "units_prior", "units_current", "yoy_growth_pct", "segment_growth_pct",
                       "growth_gap_pp", "share_prior_pct", "share_current_pct", "share_change_bps",
                       "constant_share_units", "registration_gap_units"]]
                 .rename(columns={"units_prior": pri_l, "units_current": cur_l, "yoy_growth_pct": "growth %",
                                  "segment_growth_pct": "segment growth %", "growth_gap_pp": "gap pp",
                                  "share_prior_pct": "share prior %", "share_current_pct": "share current %",
                                  "share_change_bps": "share chg bps", "constant_share_units": "constant-share units",
                                  "registration_gap_units": "registration gap"}),
                 {pri_l: ",.0f", cur_l: ",.0f", "growth %": "+.2f", "segment growth %": "+.2f", "gap pp": "+.2f",
                  "share prior %": ".2f", "share current %": ".2f", "share chg bps": "+.1f",
                  "constant-share units": ",.0f", "registration gap": "+,.0f"}),
        "",
        "![Portfolio growth vs segment](charts/02_portfolio_growth_vs_segment.png)",
        "",
        "![Registration gap](charts/07_registration_gap.png)",
        "",
        "![Portfolio PV share by month](charts/05_portfolio_pv_share_monthly.png)",
        "",
        "## 4. OEM market share and competitor moves",
        "",
        "Only OEMs that FADA lists in every month of both periods are ranked. Share change uses weighted period "
        "shares (summed units / summed segment total), not averages of monthly percentages.",
        "",
        md_table(moves.drop(columns=["portfolio_brand"]).rename(columns={
            "oem_canonical": "OEM", "units_prior": pri_l, "units_current": cur_l, "share_prior_pct": "share prior %",
            "share_current_pct": "share current %", "share_change_bps": "chg bps", "yoy_growth_pct": "growth %"}),
            {pri_l: ",.0f", cur_l: ",.0f", "share prior %": ".2f", "share current %": ".2f", "chg bps": "+.1f",
             "growth %": "+.2f"}),
        "",
        "![PV share change](charts/03_pv_share_change_bps.png)",
        "",
        "![2W share change](charts/04_2w_share_change_bps.png)",
        "",
        "Full table: `tables/oem_period_share.csv`.",
        "",
        "## 5. EV adoption",
        "",
        "EV units are **derived**: FADA publishes the EV percentage of each segment's retail to two decimals; "
        "EV units = share x segment total. Period shares are weighted by monthly segment volume. PV and 2W EV "
        "shares are never combined.",
        "",
        md_table(ev[["segment", "ev_units_est_prior", "ev_units_est_current", "ev_share_prior_pct",
                     "ev_share_current_pct", "ev_share_change_bps", "ev_units_growth_pct", "segment_growth_pct"]]
                 .rename(columns={"ev_units_est_prior": f"EV units (est) {pri_l}",
                                  "ev_units_est_current": f"EV units (est) {cur_l}",
                                  "ev_share_prior_pct": "EV share prior %", "ev_share_current_pct": "EV share current %",
                                  "ev_share_change_bps": "chg bps", "ev_units_growth_pct": "EV growth %",
                                  "segment_growth_pct": "segment growth %"}),
                 {f"EV units (est) {pri_l}": ",.0f", f"EV units (est) {cur_l}": ",.0f", "EV share prior %": ".2f",
                  "EV share current %": ".2f", "chg bps": "+.0f", "EV growth %": "+.1f", "segment growth %": "+.1f"}),
        "",
    ]
    if len(brand_ev):
        lines += [
            "Illustrative (assumes Ather retails only battery-electric two-wheelers, an analyst assumption "
            "rather than a FADA field): Ather's share of estimated two-wheeler EV retail.",
            "",
            md_table(brand_ev.rename(columns={"share_of_segment_ev_prior_pct": "share of 2W EV prior %",
                                              "share_of_segment_ev_current_pct": "share of 2W EV current %",
                                              "units_prior": pri_l, "units_current": cur_l, "share_change_bps": "chg bps"}),
                     {pri_l: ",.0f", cur_l: ",.0f", "share of 2W EV prior %": ".2f",
                      "share of 2W EV current %": ".2f", "chg bps": "+.0f"}),
            "",
        ]
    lines += [
        "![EV share by month](charts/06_ev_share_monthly.png)",
        "",
        "## 6. Management attention",
        "",
        "Rules (illustrative thresholds in `config/market_analysis.yaml`, not industry benchmarks): growth gap "
        f"<= {cfg['attention_rules']['growth_gap_pp_warning']} pp, share change <= "
        f"{cfg['attention_rules']['share_change_bps_warning']} bps, registration gap >= "
        f"{cfg['attention_rules']['registration_gap_units_warning']:,} units.",
        "",
        md_table(att.rename(columns={"yoy_growth_pct": "growth %", "segment_growth_pct": "segment growth %",
                                     "share_change_bps": "share chg bps", "registration_gap_units": "registration gap"}),
                 {"growth %": "+.2f", "segment growth %": "+.2f", "share chg bps": "+.1f", "registration gap": "+,.0f"}),
        "",
        "## 7. Verification of figures quoted in the project brief",
        "",
        "Figures from the brief were treated as claims and re-derived from source data.",
        "",
        md_table(r["brief_check"], {"reproduced": ".2f"}),
        "",
        "## 8. Data quality and reconciliation",
        "",
        f"- Extraction checks: **{r['extract_checks_passed']} of {r['extract_checks_total']} passed** "
        "(`data/processed/fada/extraction_checks.csv`): OEM rows sum to each published segment total for every "
        "segment-month; group rows equal the sum of their members; every recomputed share matches FADA's printed "
        "share within rounding; OEM totals equal the category summary table.",
        "- The analysis re-validates schema, periods, duplicates, OEM mapping coverage and reconciliation before "
        "computing anything.",
        "- RTO coverage per release: " + "; ".join(
            f"{m.release_month}: {m.rtos_reporting:,}/{m.rtos_total:,} RTOs, collated {m.data_collated_as_on}"
            for m in meta.itertuples()) + ".",
        f"- FADA revises earlier months in later releases ({r['revision_count']} category-months differ between "
        "releases; see `data/processed/fada/category_revisions_across_releases.csv`). OEM detail exists only in "
        "each month's own release, so all shares use those first-published OEM tables consistently.",
        "- Prior-year (2025) values are the comparison columns printed in the 2026 releases.",
        "",
        "## 9. Method notes",
        "",
        "- YoY growth % = (current / prior - 1) x 100; NaN when the prior value is zero or missing.",
        "- Market share % = OEM units / segment units x 100, with the OEM's own segment as denominator.",
        "- Share change bps = (current share % - prior share %) x 100.",
        "- Constant-share registrations = current segment units x prior share % / 100; "
        "registration gap = constant-share registrations - actual registrations.",
        "- Group rows (e.g. Mercedes-Benz Group, Skoda Auto Volkswagen Group) are used; their member rows are "
        "never added again. Mercedes-Benz = Mercedes-Benz India + Mercedes-Benz AG + Daimler AG + 'Mercedes Benz' rows.",
        "- FADA prints '-' for nil registrations in some member rows (with a 0.00% share); these are stored as 0 "
        "with `reported_as_dash = True`.",
        "- Presentation rounding only; calculations use full precision.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# =========================================================================== orchestration
def run_analysis(cfg: dict | None = None, processed_dir: Path = FADA_PROCESSED_DIR,
                 mapping_path: Path = CONFIG_DIR / "oem_mapping.csv") -> dict:
    cfg = cfg or load_config()
    data = load_inputs(processed_dir)
    mapping = load_mapping(mapping_path)
    validate_oem_frame(data["oem"], mapping, cfg)
    validate_fuel_frame(data["fuel"], cfg)

    ev_month = canonical_ev_share(data["fuel"])
    oem_month = build_oem_month(data["oem"], mapping, cfg)
    totals = segment_totals(data["oem"], cfg)
    seg_monthly = segment_monthly_yoy(totals, ev_month)
    seg_summary = segment_period_summary(totals, cfg)
    oem_period = oem_period_table(oem_month, totals, cfg)
    port = portfolio_tables(oem_month, totals, cfg)
    port_monthly = portfolio_monthly_share(oem_month)
    ev = ev_period_table(totals, ev_month, oem_month, cfg)
    headline = headline_metrics(seg_summary, port, ev, ev_month, cfg)

    checks_path = processed_dir / "extraction_checks.csv"
    checks = pd.read_csv(checks_path) if checks_path.exists() else pd.DataFrame({"passed": []})
    rev_path = processed_dir / "category_revisions_across_releases.csv"
    meta_path = processed_dir / "fada_release_metadata.csv"
    return {
        "config": cfg, "oem_month": oem_month, "segment_totals": totals, "segment_monthly": seg_monthly,
        "segment_summary": seg_summary, "oem_period": oem_period, "portfolio": port,
        "portfolio_monthly": port_monthly, "ev_period": ev, "ev_month": ev_month, "headline": headline,
        "attention": attention_list(port, cfg), "competitors": competitor_moves(oem_period),
        "brief_check": verify_brief_figures(headline, cfg),
        "extract_checks_passed": int(checks.passed.sum()), "extract_checks_total": len(checks),
        "revision_count": len(pd.read_csv(rev_path)) if rev_path.exists() else 0,
        "release_meta": pd.read_csv(meta_path) if meta_path.exists() else pd.DataFrame(),
    }


def write_outputs(r: dict, table_dir: Path = MARKET_TABLE_DIR, chart_dir: Path = MARKET_CHART_DIR,
                  report_dir: Path = MARKET_REPORT_DIR, charts: bool = True) -> list[Path]:
    table_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "oem_monthly_share.csv": r["oem_month"],
        "segment_monthly_yoy.csv": r["segment_monthly"],
        "segment_period_summary.csv": r["segment_summary"],
        "oem_period_share.csv": r["oem_period"],
        "portfolio_brand_performance.csv": r["portfolio"],
        "portfolio_monthly_share.csv": r["portfolio_monthly"],
        "ev_period_summary.csv": r["ev_period"],
        "ev_brand_share.csv": r["ev_period"].attrs.get("brand_ev", pd.DataFrame()),
        "management_attention.csv": r["attention"],
        "competitor_moves.csv": r["competitors"],
        "brief_figure_verification.csv": r["brief_check"],
        "headline_metrics.csv": pd.DataFrame(sorted(r["headline"].items()), columns=["metric", "value"]),
    }
    written = []
    for name, df in tables.items():
        p = table_dir / name
        df.to_csv(p, index=False, float_format="%.6f", lineterminator="\n")
        written.append(p)
    if charts:
        written += make_charts(r["segment_monthly"], r["portfolio"], r["portfolio_monthly"], r["oem_period"],
                               chart_dir, r["config"])
    report = report_dir / "executive_report.md"
    write_report(r, r["config"], report)
    written.append(report)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=CONFIG_DIR / "market_analysis.yaml")
    ap.add_argument("--no-charts", action="store_true", help="skip PNG chart generation")
    args = ap.parse_args(argv)
    try:
        r = run_analysis(load_config(args.config))
    except (DataValidationError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    written = write_outputs(r, charts=not args.no_charts)
    h = r["headline"]
    print(f"PV market growth          {h['pv_market_growth_pct']:+.2f}%")
    print(f"2W market growth          {h['tw_market_growth_pct']:+.2f}%")
    print(f"Portfolio car growth      {h['portfolio_car_growth_pct']:+.2f}%")
    print(f"Portfolio PV share        {h['portfolio_share_prior_pct']:.2f}% -> {h['portfolio_share_current_pct']:.2f}%")
    print(f"Registration gap (cars)   {h['portfolio_registration_gap_units']:,.0f}")
    print(f"Brief figures reproduced  {(r['brief_check'].status == 'Reproduced').sum()} of {len(r['brief_check'])}")
    print(f"Wrote {len(written)} files under {MARKET_REPORT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
