"""Tests for python/dealership_kpi_simulation.py (SIMULATED Layer B)."""
import hashlib

import numpy as np
import pandas as pd
import pytest

import dealership_kpi_simulation as sim


@pytest.fixture(scope="module")
def small_cfg():
    return sim.load_config(scale=0.15, start_date="2026-02-01", end_date="2026-05-31")


@pytest.fixture(scope="module")
def tables(small_cfg):
    return sim.simulate(small_cfg)


@pytest.fixture(scope="module")
def kpis(tables, small_cfg):
    return sim.compute_kpis(tables, small_cfg)


# ------------------------------------------------------------------ determinism
def test_same_seed_gives_identical_tables(small_cfg, tables):
    again = sim.simulate(small_cfg)
    for name in tables:
        pd.testing.assert_frame_equal(tables[name], again[name])


def test_different_seed_changes_observations(small_cfg, tables):
    other = sim.simulate({**small_cfg, "seed": small_cfg["seed"] + 1})
    assert not tables["fact_lead_funnel"].equals(other["fact_lead_funnel"])


def test_written_files_are_byte_identical_across_runs(small_cfg, tmp_path):
    digests = []
    for run in ("a", "b"):
        t = sim.simulate(small_cfg)
        k = sim.compute_kpis(t, small_cfg)
        out = tmp_path / run
        sim.write_outputs(t, k, small_cfg, data_dir=out / "data", table_dir=out / "tables",
                          chart_dir=out / "charts", report_dir=out, charts=False)
        digests.append({p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted(out.rglob("*")) if p.is_file()})
    assert digests[0] == digests[1]


def test_scale_controls_dataset_size(small_cfg, tables):
    bigger = sim.simulate({**small_cfg, "scale": 0.3})
    assert len(bigger["fact_lead_funnel"]) > 1.6 * len(tables["fact_lead_funnel"])


# ------------------------------------------------------------------ integrity
def test_generated_data_passes_integrity_checks(tables, small_cfg):
    sim.validate_simulation(tables, small_cfg)   # raises on failure


def test_every_table_is_labelled_simulated(tables, kpis):
    for name, df in tables.items():
        if name != "simulation_latent_parameters":
            assert (df.data_classification == sim.SIM_LABEL).all(), name
    assert (kpis["action_triggers"].data_classification == sim.SIM_LABEL).all()


def test_no_personal_information_patterns(tables):
    sp = tables["dim_salesperson"]
    assert sp.salesperson_name.str.match(r"^Salesperson DLR\d{2}-\d{2}$").all()
    leads = tables["fact_lead_funnel"]
    assert not any(c in leads.columns for c in ["customer_name", "phone", "email", "address"])
    assert tables["fact_vehicle_inventory"].vin.str.startswith("SIMVIN").all()


def test_stage_order_and_states(tables):
    l = tables["fact_lead_funnel"]
    for a, b in [("lead_date", "qualified_date"), ("qualified_date", "test_drive_date"),
                 ("test_drive_date", "booking_date"), ("booking_date", "delivery_date")]:
        has_b = l[b].notna()
        assert l.loc[has_b, a].notna().all()
        assert (l.loc[has_b, b] >= l.loc[has_b, a]).all()
    assert l.loc[l.delivery_id.notna(), "lead_status"].eq("Delivered").all()
    assert l.loc[l.lead_status == "Lost", "delivery_id"].isna().all()


def test_deliveries_consistent_with_bookings_and_stock(tables, small_cfg):
    d, v, l = tables["fact_vehicle_delivery"], tables["fact_vehicle_inventory"], tables["fact_lead_funnel"]
    m = d.merge(v, on="vin", suffixes=("", "_v")).merge(l, on="lead_id", suffixes=("", "_l"))
    assert (m.model_id == m.model_id_v).all() and (m.model_id == m.model_id_l).all()
    assert (m.delivery_date >= m.receipt_date).all()
    assert (m.delivery_date >= m.booking_date).all()
    assert d.vin.is_unique and d.booking_id.is_unique
    assert (v.loc[v.stock_status == "Delivered", "vin"].sort_values().tolist() == d.vin.sort_values().tolist())


def test_integrity_check_detects_broken_foreign_key(tables, small_cfg):
    t = {k: v.copy() for k, v in tables.items()}
    t["fact_lead_funnel"].loc[0, "salesperson_id"] = "SP-NOPE-01"
    with pytest.raises(sim.SimulationIntegrityError, match="values missing from dim_salesperson"):
        sim.validate_simulation(t, small_cfg)


def test_integrity_check_detects_impossible_sequence(tables, small_cfg):
    t = {k: v.copy() for k, v in tables.items()}
    l = t["fact_lead_funnel"]
    i = l.index[l.booking_date.notna()][0]
    l.loc[i, "test_drive_date"] = pd.NaT
    with pytest.raises(sim.SimulationIntegrityError, match="stage skipped"):
        sim.validate_simulation(t, small_cfg)


def test_integrity_check_detects_double_delivery(tables, small_cfg):
    t = {k: v.copy() for k, v in tables.items()}
    d = t["fact_vehicle_delivery"]
    dup = d.iloc[[0]].assign(delivery_id="DL999999", booking_id=d.booking_id.iloc[1])
    t["fact_vehicle_delivery"] = pd.concat([d, dup], ignore_index=True)
    with pytest.raises(sim.SimulationIntegrityError, match="delivered more than once"):
        sim.validate_simulation(t, small_cfg)


def test_integrity_check_detects_delivery_before_receipt(tables, small_cfg):
    t = {k: v.copy() for k, v in tables.items()}
    vin = t["fact_vehicle_delivery"].vin.iloc[0]
    dd = t["fact_vehicle_delivery"].delivery_date.iloc[0]
    t["fact_vehicle_inventory"].loc[t["fact_vehicle_inventory"].vin == vin, "receipt_date"] = dd
    with pytest.raises(sim.SimulationIntegrityError, match="before it was received"):
        sim.validate_simulation(t, small_cfg)


# ------------------------------------------------------------------ inventory ageing
@pytest.mark.parametrize("age, bucket", [(0, "0-30"), (30, "0-30"), (31, "31-60"), (60, "31-60"),
                                         (61, "61-90"), (90, "61-90"), (91, ">90"), (400, ">90")])
def test_ageing_bucket_boundaries(age, bucket):
    assert sim.ageing_bucket(age) == bucket


@pytest.mark.parametrize("bad", [-1, None, np.nan])
def test_invalid_age_is_rejected(bad):
    with pytest.raises(ValueError):
        sim.ageing_bucket(bad)


def test_snapshot_ages_and_aged_flag(tables, small_cfg):
    s = tables["fact_inventory_snapshot"]
    assert (s.age_days >= 0).all()
    assert (s.is_aged == (s.age_days > small_cfg["inventory"]["aged_threshold_days"])).all()
    assert (s.ageing_bucket == [sim.ageing_bucket(a) for a in s.age_days]).all()


def test_inventory_flow_balances(kpis):
    inv = kpis["inventory_by_dealership"]
    assert (inv.opening_stock_units + inv.receipts_units - inv.deliveries_units == inv.closing_stock_units).all()
    buckets = inv[[c for c in inv.columns if c.startswith("stock_") and c.endswith("_days")]].sum(axis=1)
    assert (buckets == inv.closing_stock_units).all()


# ------------------------------------------------------------------ funnel KPIs on hand data
def test_funnel_conversion_calculation():
    leads = pd.DataFrame({
        "lead_id": [f"L{i}" for i in range(10)],
        "qualified_date": [pd.Timestamp("2026-01-02")] * 6 + [pd.NaT] * 4,
        "test_drive_date": [pd.Timestamp("2026-01-03")] * 4 + [pd.NaT] * 6,
        "booking_id": ["B1", "B2", None, None, None, None, None, None, None, None],
        "delivery_id": ["D1"] + [None] * 9,
        "lead_status": ["Delivered", "Booked - awaiting delivery"] + ["Lost"] * 6 + ["Open"] * 2,
    })
    f = sim.funnel_counts(leads, []).iloc[0]
    assert (f.leads, f.qualified, f.test_drives, f.bookings, f.deliveries) == (10, 6, 4, 2, 1)
    assert f.lead_to_qualified_pct == pytest.approx(60.0)
    assert f.qualified_to_test_drive_pct == pytest.approx(4 / 6 * 100)
    assert f.test_drive_to_booking_pct == pytest.approx(50.0)
    assert f.booking_to_delivery_pct == pytest.approx(50.0)
    assert f.lead_to_delivery_pct == pytest.approx(10.0)
    assert (f.lost, f.open_leads, f.booked_awaiting) == (6, 2, 1)


def test_empty_funnel_gives_nan_rates_not_zero():
    empty = pd.DataFrame({"lead_id": ["L1"], "qualified_date": [pd.NaT], "test_drive_date": [pd.NaT],
                          "booking_id": [None], "delivery_id": [None], "lead_status": ["Open"]})
    f = sim.funnel_counts(empty, []).iloc[0]
    assert f.lead_to_qualified_pct == 0
    assert np.isnan(f.test_drive_to_booking_pct)


def test_action_rules_thresholds():
    score = pd.DataFrame([{"dealership_id": "D1", "dealership_name": "x", "brand": "b", "segment": "PV",
                           "aged_inventory_share_pct": 15.0, "days_of_supply": 22.0},
                          {"dealership_id": "D2", "dealership_name": "y", "brand": "c", "segment": "2W",
                           "aged_inventory_share_pct": 15.01, "days_of_supply": np.nan}])
    cfg = {"action_rules": [
        {"kpi": "aged_inventory_share_pct", "op": ">", "threshold": 15.0, "responsible_role": "IM",
         "review_cadence": "Weekly", "recommended_action": "act", "basis": "Illustrative business rule"},
        {"kpi": "days_of_supply", "op": ">", "threshold": 21, "segments": ["PV"], "responsible_role": "DP",
         "review_cadence": "Weekly", "recommended_action": "act", "basis": "FADA"}]}
    a = sim.evaluate_actions(score, cfg).set_index(["dealership_id", "kpi"])
    assert not a.loc[("D1", "aged_inventory_share_pct"), "breached"]       # exactly at threshold
    assert a.loc[("D2", "aged_inventory_share_pct"), "breached"]
    assert a.loc[("D1", "days_of_supply"), "breached"]
    assert ("D2", "days_of_supply") not in a.index                          # rule limited to PV


def test_funnel_totals_match_fact_table(tables, kpis):
    allrow = kpis["funnel_by_dealership"].set_index("dealership_id").loc["ALL"]
    assert allrow.leads == len(tables["fact_lead_funnel"])
    assert allrow.deliveries == len(tables["fact_vehicle_delivery"])
    stages = kpis["funnel_stages"].query("dealership_id == 'ALL'")["count"].tolist()
    assert stages == sorted(stages, reverse=True)                            # funnel never widens


# ------------------------------------------------------------------ configuration
@pytest.mark.parametrize("kwargs", [{"start_date": "2026-05-01", "end_date": "2026-04-30"},
                                    {"start_date": "not-a-date"}, {"scale": 0}])
def test_invalid_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        sim.load_config(**kwargs)
