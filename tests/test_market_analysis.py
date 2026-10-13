"""Validation and calculation tests for python/auto_retail_analysis.py on hand-checked fixtures."""
import math

import pandas as pd
import pytest

import auto_retail_analysis as ara
from conftest import PV, TW, build_fuel_frame, build_oem_frame


def run(processed_dir, cfg):
    return ara.run_analysis(cfg, processed_dir=processed_dir, mapping_path=processed_dir / "oem_mapping.csv")


def write(processed_dir, oem=None, fuel=None):
    if oem is not None:
        oem.to_csv(processed_dir / "fada_oem_monthly.csv", index=False)
    if fuel is not None:
        fuel.to_csv(processed_dir / "fada_fuel_mix_monthly.csv", index=False)


# ------------------------------------------------------------------ calculations
def test_portfolio_metrics_match_hand_calculation(processed_dir, cfg):
    r = run(processed_dir, cfg)
    p = r["portfolio"].set_index("brand")
    cars = p.loc[ara.PORTFOLIO_CARS]
    assert cars.units_prior == 200 and cars.units_current == 220
    assert cars.yoy_growth_pct == pytest.approx(10.0)
    assert cars.segment_growth_pct == pytest.approx(20.0)
    assert cars.growth_gap_pp == pytest.approx(-10.0)
    assert cars.share_prior_pct == pytest.approx(20.0)
    assert cars.share_current_pct == pytest.approx(220 / 1200 * 100)
    assert cars.share_change_bps == pytest.approx((220 / 1200 * 100 - 20.0) * 100)
    assert cars.constant_share_units == pytest.approx(240.0)
    assert cars.registration_gap_units == pytest.approx(20.0)
    assert p.loc["Toyota"].share_change_bps == pytest.approx((110 / 1200 - 0.10) * 10_000)


def test_ather_is_benchmarked_against_two_wheelers_only(processed_dir, cfg):
    r = run(processed_dir, cfg)
    a = r["portfolio"].set_index("brand").loc["Ather"]
    assert a.segment == "2W"
    assert a.share_prior_pct == pytest.approx(1.0)          # 10 / 1,000 two-wheelers
    assert a.share_current_pct == pytest.approx(2.5)        # 30 / 1,200 two-wheelers
    assert a.segment_growth_pct == pytest.approx(20.0)
    assert a.constant_share_units == pytest.approx(12.0)
    assert a.registration_gap_units == pytest.approx(-18.0)


def test_portfolio_car_aggregate_excludes_ather(processed_dir, cfg):
    r = run(processed_dir, cfg)
    cars = r["portfolio"].set_index("brand").loc[ara.PORTFOLIO_CARS]
    assert cars.units_current == 110 + 60 + 30 + 20


def test_segment_isolation_two_wheeler_change_does_not_move_pv_metrics(processed_dir, cfg):
    base = run(processed_dir, cfg)["portfolio"].set_index("brand")
    tw = {m: dict(v) for m, v in TW.items()}
    tw["2026-02"]["HERO MOTOCORP LTD"] = 5000
    write(processed_dir, oem=build_oem_frame(tw=tw))
    changed = run(processed_dir, cfg)["portfolio"].set_index("brand")
    pd.testing.assert_series_equal(base.loc[ara.PORTFOLIO_CARS], changed.loc[ara.PORTFOLIO_CARS])
    assert changed.loc["Ather"].share_current_pct < base.loc["Ather"].share_current_pct


def test_honda_two_wheeler_entity_is_not_honda_cars(processed_dir, cfg):
    r = run(processed_dir, cfg)
    om = r["oem_month"]
    hmsi = om[om.oem_canonical == "Honda Motorcycle & Scooter India"]
    assert hmsi.portfolio_brand.isna().all()
    assert r["portfolio"].set_index("brand").loc["Honda Cars"].units_current == 60


def test_group_members_are_not_double_counted(processed_dir, cfg):
    r = run(processed_dir, cfg)
    merc = r["portfolio"].set_index("brand").loc["Mercedes-Benz"]
    assert merc.units_current == 20       # group row only, not 20 + 19 + 1


def test_ev_share_is_volume_weighted(processed_dir, cfg):
    r = run(processed_dir, cfg)
    ev = r["ev_period"].set_index("segment")
    assert ev.loc["PV"].ev_units_est_prior == pytest.approx(20.0)
    assert ev.loc["PV"].ev_units_est_current == pytest.approx(60.0)
    assert ev.loc["PV"].ev_share_change_bps == pytest.approx(300.0)
    assert ev.loc["2W"].ev_share_current_pct == pytest.approx(6.0)
    brand = r["ev_period"].attrs["brand_ev"].set_index("brand").loc["Ather"]
    assert brand.share_of_segment_ev_current_pct == pytest.approx(30 / 72 * 100)


def test_two_month_period_weighting(tmp_path, cfg, mapping):
    """Period share = summed units / summed totals; months with different market size are weighted."""
    cfg["current_period"] = {"start": "2026-02", "end": "2026-03", "label": "c"}
    cfg["prior_period"] = {"start": "2025-02", "end": "2025-03", "label": "p"}
    pv = {**PV, "2025-03": {**PV["2025-02"], "MARUTI SUZUKI INDIA LTD": 1700},
          "2026-03": {**PV["2026-02"], "MARUTI SUZUKI INDIA LTD": 1900}}
    tw = {**TW, "2025-03": TW["2025-02"], "2026-03": TW["2026-02"]}
    ev = {(s, m): 3.0 for s in ("PV", "2W") for m in ("2025-02", "2025-03", "2026-02", "2026-03")}
    build_oem_frame(pv, tw).to_csv(tmp_path / "fada_oem_monthly.csv", index=False)
    build_fuel_frame(ev).to_csv(tmp_path / "fada_fuel_mix_monthly.csv", index=False)
    mapping.to_csv(tmp_path / "oem_mapping.csv", index=False)
    r = run(tmp_path, cfg)
    toyota = r["portfolio"].set_index("brand").loc["Toyota"]
    assert toyota.share_prior_pct == pytest.approx(200 / 3000 * 100)      # not mean(10%, 5%)
    assert toyota.share_current_pct == pytest.approx(220 / 3400 * 100)


def test_zero_prior_registrations_gives_nan_growth_not_zero(processed_dir, cfg):
    pv = {m: dict(v) for m, v in PV.items()}
    pv["2025-02"]["MARUTI SUZUKI INDIA LTD"] = 0
    pv["2025-02"]["Others"] = 800
    write(processed_dir, oem=build_oem_frame(pv=pv))
    r = run(processed_dir, cfg)
    maruti = r["oem_period"].set_index("oem_canonical").loc["Maruti Suzuki"]
    assert maruti.units_prior == 0
    assert math.isnan(maruti.yoy_growth_pct)
    assert maruti.share_prior_pct == pytest.approx(0.0)


def test_oem_listed_only_in_some_months_is_flagged(processed_dir, cfg, mapping):
    pv = {m: dict(v) for m, v in PV.items()}
    pv["2026-02"]["NEW ENTRANT LTD"] = 40
    pv["2026-02"]["Others"] = 40
    mapping.loc[len(mapping)] = ["PV", "NEW ENTRANT LTD", "New Entrant", None]
    mapping.to_csv(processed_dir / "oem_mapping.csv", index=False)
    write(processed_dir, oem=build_oem_frame(pv=pv))
    row = run(processed_dir, cfg)["oem_period"].set_index("oem_canonical").loc["New Entrant"]
    assert not row.coverage_complete
    assert math.isnan(row.share_change_bps) and math.isnan(row.yoy_growth_pct)
    assert "only some months" in row.coverage_note


def test_competitor_lists_respect_sign(processed_dir, cfg):
    moves = run(processed_dir, cfg)["competitors"]
    assert (moves.loc[moves.list == "Top share gainers", "share_change_bps"] > 0).all()
    assert (moves.loc[moves.list == "Top share losers", "share_change_bps"] < 0).all()


def test_attention_rules(processed_dir, cfg):
    att = run(processed_dir, cfg)["attention"].set_index("brand")
    assert att.loc["Ather", "status"] == "On track / outperforming"
    assert att.loc["JSW MG Motor", "status"] == "Attention"   # flat while segment +20%


# ------------------------------------------------------------------ validation failures
def expect_error(processed_dir, cfg, match):
    with pytest.raises(ara.DataValidationError, match=match):
        run(processed_dir, cfg)


def test_empty_input_fails(processed_dir, cfg, oem):
    write(processed_dir, oem=oem.iloc[0:0])
    expect_error(processed_dir, cfg, "empty")


def test_missing_required_column_fails(processed_dir, cfg, oem):
    write(processed_dir, oem=oem.drop(columns=["units"]))
    expect_error(processed_dir, cfg, "missing required columns")


def test_invalid_period_fails(processed_dir, cfg, oem):
    oem.loc[0, "period_month"] = "2026-13"
    write(processed_dir, oem=oem)
    expect_error(processed_dir, cfg, "invalid period_month")


def test_missing_units_are_not_treated_as_zero(processed_dir, cfg, oem):
    oem["units"] = oem["units"].astype(object)
    oem.loc[0, "units"] = None
    write(processed_dir, oem=oem)
    expect_error(processed_dir, cfg, "missing or non-numeric units")


def test_duplicate_source_record_fails(processed_dir, cfg, oem):
    write(processed_dir, oem=pd.concat([oem, oem.iloc[[0]]]))
    expect_error(processed_dir, cfg, "duplicate source record")


def test_missing_month_fails(processed_dir, cfg, oem):
    write(processed_dir, oem=oem[~((oem.segment == "PV") & (oem.period_month == "2025-02"))])
    expect_error(processed_dir, cfg, r"PV: no source data for months \['2025-02'\]")


def test_unexpected_oem_fails(processed_dir, cfg, oem):
    oem.loc[(oem.oem_raw == "MARUTI SUZUKI INDIA LTD"), "oem_raw"] = "MARUTI SUZUKI (RENAMED) LTD"
    write(processed_dir, oem=oem)
    expect_error(processed_dir, cfg, "unexpected OEM not in config/oem_mapping.csv")


def test_segment_total_that_does_not_reconcile_fails(processed_dir, cfg, oem):
    mask = (oem.segment == "PV") & (oem.period_month == "2026-02") & (oem.row_level == "total")
    oem.loc[mask, "units"] = 1199
    write(processed_dir, oem=oem)
    expect_error(processed_dir, cfg, r"PV 2026-02: OEM rows sum to 1200 but FADA Total is 1199")


def test_group_not_equal_members_fails(processed_dir, cfg, oem):
    oem.loc[oem.oem_raw == "MERCEDES -BENZ AG", "units"] = 5
    write(processed_dir, oem=oem)
    expect_error(processed_dir, cfg, "does not equal the sum of its members")


def test_missing_ev_month_fails(processed_dir, cfg, fuel):
    write(processed_dir, fuel=fuel[~((fuel.segment == "2W") & (fuel.period_month == "2026-02"))])
    expect_error(processed_dir, cfg, "no EV share published")


def test_mismatched_periods_fail(cfg):
    cfg["prior_period"] = {"start": "2025-01", "end": "2025-02", "label": "x"}
    with pytest.raises(ara.DataValidationError, match="period lengths differ"):
        ara.validate_periods(cfg)
    cfg["prior_period"] = {"start": "2024-02", "end": "2024-02", "label": "x"}
    with pytest.raises(ara.DataValidationError, match="exactly 12 months"):
        ara.validate_periods(cfg)


def test_duplicate_mapping_entry_fails(tmp_path, mapping):
    pd.concat([mapping, mapping.iloc[[0]]]).to_csv(tmp_path / "m.csv", index=False)
    with pytest.raises(ara.DataValidationError, match="more than once"):
        ara.load_mapping(tmp_path / "m.csv")


def test_two_raw_names_for_one_oem_in_same_month_fails(processed_dir, cfg, oem, mapping):
    pv = {m: dict(v) for m, v in PV.items()}
    pv["2026-02"]["MARUTI SUZUKI INDIA LIMITED"] = 10
    pv["2026-02"]["Others"] = 70
    mapping.loc[len(mapping)] = ["PV", "MARUTI SUZUKI INDIA LIMITED", "Maruti Suzuki", None]
    mapping.to_csv(processed_dir / "oem_mapping.csv", index=False)
    write(processed_dir, oem=build_oem_frame(pv=pv))
    expect_error(processed_dir, cfg, "possible double count")


# ------------------------------------------------------------------ outputs
def test_report_contains_required_sections(processed_dir, cfg, tmp_path):
    r = run(processed_dir, cfg)
    out = tmp_path / "out"
    written = ara.write_outputs(r, table_dir=out / "tables", chart_dir=out / "charts", report_dir=out, charts=False)
    report = (out / "executive_report.md").read_text(encoding="utf-8")
    for heading in ["## 1. Headline answers", "## 2. Segment growth", "## 3. Portfolio brand performance",
                    "## 4. OEM market share", "## 5. EV adoption", "## 6. Management attention",
                    "## 7. Verification", "## 8. Data quality", "## 9. Method notes"]:
        assert heading in report
    assert (out / "tables" / "portfolio_brand_performance.csv") in written
