"""Layer B - SIMULATED dealership operations and KPIs.

Everything produced by this script is synthetic. It illustrates how a multi-brand
dealer group would measure its sales funnel, follow-up discipline and inventory
health; it does not describe any real dealership, salesperson or customer.

Assumptions : config/simulation.yaml (seed, period, volumes, probabilities, rules)
Observations: data/simulated/dealership/*.csv   (generated star-schema tables)
KPIs        : reports/dealership/tables/*.csv, charts/*.png, dealership_kpi_report.md

Run (from the repository root):
    python python/dealership_kpi_simulation.py                      # defaults from config
    python python/dealership_kpi_simulation.py --seed 7 --scale 0.5 # override seed / size
    python python/dealership_kpi_simulation.py --start 2026-01-01 --end 2026-06-30

The same seed and configuration always produce byte-identical output files.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from market_metrics import market_share_pct
from paths import CONFIG_DIR, DEALER_CHART_DIR, DEALER_REPORT_DIR, DEALER_TABLE_DIR, SIMULATED_DIR

SIM_LABEL = "SIMULATED"
STAGES = ["Lead", "Qualified", "Test drive", "Booking", "Delivery"]


class SimulationIntegrityError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Simulated data failed integrity checks:\n  - " + "\n  - ".join(problems))


# =========================================================================== config
def load_config(path: Path = CONFIG_DIR / "simulation.yaml", **overrides) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for k, v in overrides.items():
        if v is not None:
            cfg[k] = v
    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    if pd.isna(start) or pd.isna(end) or end < start:
        raise ValueError(f"invalid simulation period {cfg['start_date']} to {cfg['end_date']}")
    if cfg["scale"] <= 0:
        raise ValueError("scale must be positive")
    shares = sum(v[0] for v in cfg["lead_sources"].values())
    if abs(shares - 1) > 1e-9:
        raise ValueError(f"lead source shares must sum to 1 (got {shares})")
    for brand, b in cfg["brands"].items():
        w = sum(m[3] for m in b["models"].values())
        if abs(w - 1) > 1e-9:
            raise ValueError(f"{brand}: model demand weights must sum to 1 (got {w})")
    return cfg


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:16]


def ageing_bucket(age_days, bounds=(30, 60, 90)) -> str:
    """0-30, 31-60, 61-90, >90 with inclusive upper bounds (30 -> '0-30', 31 -> '31-60')."""
    if age_days is None or pd.isna(age_days) or age_days < 0:
        raise ValueError(f"invalid inventory age {age_days}")
    lo = 0
    for b in bounds:
        if age_days <= b:
            return f"{lo}-{b}"
        lo = b + 1
    return f">{bounds[-1]}"


def bucket_labels(bounds=(30, 60, 90)) -> list[str]:
    labels, lo = [], 0
    for b in bounds:
        labels.append(f"{lo}-{b}")
        lo = b + 1
    return labels + [f">{bounds[-1]}"]


# =========================================================================== dimensions
def build_dimensions(cfg: dict, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    regions = ["North", "South", "West", "East"]
    dealers, people, models, latent = [], [], [], []
    d_seq = m_seq = 0
    for brand, b in cfg["brands"].items():
        for name, (cat, power, price, weight, stock_f) in b["models"].items():
            m_seq += 1
            models.append({"model_id": f"MDL{m_seq:02d}", "brand": brand, "model_name": name,
                           "vehicle_category": cat, "powertrain": power, "segment": b["segment"],
                           "ex_showroom_price_inr": int(price), "data_classification": SIM_LABEL})
        for k in range(b["dealerships"]):
            d_seq += 1
            did = f"DLR{d_seq:02d}"
            dealers.append({"dealership_id": did, "dealership_name": f"Synthetic {brand} Dealership {k + 1}",
                            "brand": brand, "segment": b["segment"], "region": regions[(d_seq - 1) % 4],
                            "city": f"City {chr(64 + d_seq)}", "data_classification": SIM_LABEL})
            for j in range(1, b["salespeople_per_dealership"] + 1):
                sid = f"SP-{did}-{j:02d}"
                people.append({"salesperson_id": sid, "dealership_id": did,
                               "salesperson_name": f"Salesperson {did}-{j:02d}",
                               "data_classification": SIM_LABEL})
                latent.append({"salesperson_id": sid,
                               "conversion_factor": round(float(np.clip(rng.normal(1.0, 0.12), 0.75, 1.25)), 4),
                               "median_first_contact_hours": round(float(rng.uniform(0.5, 4.5)), 2)})
    return pd.DataFrame(dealers), pd.DataFrame(people), pd.DataFrame(models), pd.DataFrame(latent)


# =========================================================================== leads / funnel
def simulate_leads(cfg: dict, rng: np.random.Generator, dealers: pd.DataFrame, people: pd.DataFrame,
                   models: pd.DataFrame, latent: pd.DataFrame) -> pd.DataFrame:
    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    sources = list(cfg["lead_sources"])
    src_share = np.array([cfg["lead_sources"][s][0] for s in sources])
    src_qual = {s: cfg["lead_sources"][s][1] for s in sources}
    sla = cfg["first_contact"]["sla_hours"]
    late_pen = cfg["first_contact"]["qualify_penalty_if_late"]
    reasons = cfg["lost_reasons"]
    lat = latent.set_index("salesperson_id")
    months = pd.period_range(start, end, freq="M")
    rows, lead_seq, book_seq = [], 0, 0

    for d in dealers.itertuples():
        b = cfg["brands"][d.brand]
        sp_ids = people.loc[people.dealership_id == d.dealership_id, "salesperson_id"].tolist()
        bm = models[models.brand == d.brand]
        weights = np.array([b["models"][n][3] for n in bm.model_name])
        lo_lead, hi_lead = b["delivery_lead_days"]
        for month in months:
            m_start = max(month.start_time.normalize(), start)
            m_end = min(month.end_time.normalize(), end)
            n_days = (m_end - m_start).days + 1
            n = rng.poisson(b["leads_per_month"] * cfg["scale"] * n_days / month.days_in_month)
            for _ in range(n):
                lead_seq += 1
                lead_date = m_start + pd.Timedelta(days=int(rng.integers(0, n_days)))
                sp = sp_ids[int(rng.integers(0, len(sp_ids)))]
                model_id = bm.model_id.iloc[int(rng.choice(len(bm), p=weights))]
                source = sources[int(rng.choice(len(sources), p=src_share))]
                hours = round(float(rng.lognormal(np.log(lat.loc[sp, "median_first_contact_hours"]), 0.8)), 1)
                f = lat.loc[sp, "conversion_factor"]
                p_q = src_qual[source] * (1 if hours <= sla else late_pen)
                r = {"lead_id": f"LD{lead_seq:06d}", "dealership_id": d.dealership_id, "salesperson_id": sp,
                     "model_id": model_id, "lead_source": source, "lead_date": lead_date,
                     "first_contact_hours": hours, "first_contact_within_sla": hours <= sla,
                     "qualified_date": pd.NaT, "test_drive_date": pd.NaT, "booking_id": None,
                     "booking_date": pd.NaT, "requested_delivery_date": pd.NaT, "booking_cancelled_date": pd.NaT,
                     "lead_status": "Open", "lost_stage": None, "lost_reason": None, "lost_date": pd.NaT}
                cur_date, stage = lead_date, "Lead"
                transitions = [("Qualified", "qualified_date", p_q, (0, 3), "lead"),
                               ("Test drive", "test_drive_date", min(1.0, b["p_test_drive"] * f), (0, 8), "qualified"),
                               ("Booking", "booking_date", min(1.0, b["p_booking"] * f), (0, 15), "test_drive")]
                for nxt, col, p, (g0, g1), reason_key in transitions:
                    if rng.random() < p:
                        nd = cur_date + pd.Timedelta(days=int(rng.integers(g0, g1)))
                        if nd > end:
                            break                       # stage not reached by the snapshot: lead stays open
                        r[col], cur_date, stage = nd, nd, nxt
                    else:
                        ld = cur_date + pd.Timedelta(days=int(rng.integers(1, 21)))
                        if ld <= end:
                            r.update(lead_status="Lost", lost_stage=stage, lost_date=ld,
                                     lost_reason=reasons[reason_key][int(rng.integers(0, len(reasons[reason_key])))])
                        break
                if stage == "Booking":
                    book_seq += 1
                    r["booking_id"] = f"BK{book_seq:06d}"
                    req = cur_date + pd.Timedelta(days=int(rng.integers(lo_lead, hi_lead + 1)))
                    r["requested_delivery_date"] = req
                    r["lead_status"] = "Booked - awaiting delivery"
                    if rng.random() < b["p_cancel"]:
                        cd = cur_date + pd.Timedelta(days=int(rng.integers(1, max(2, (req - cur_date).days + 1))))
                        if cd <= end:
                            r.update(booking_cancelled_date=cd, lead_status="Lost", lost_stage="Booking",
                                     lost_date=cd, lost_reason=reasons["booking"][int(rng.integers(0, len(reasons["booking"])))])
                rows.append(r)
    leads = pd.DataFrame(rows)
    leads["delivery_id"] = None
    leads["delivery_date"] = pd.NaT
    leads["vin"] = None
    leads["data_classification"] = SIM_LABEL
    return leads


# =========================================================================== inventory
def simulate_inventory(cfg: dict, rng: np.random.Generator, dealers: pd.DataFrame, models: pd.DataFrame,
                       leads: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Stock receipts (opening stock + weekly replenishment) and FIFO allocation to bookings."""
    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    inv = cfg["inventory"]
    prep = pd.Timedelta(days=inv["allocation_prep_days"])
    n_weeks = max(1, int(np.ceil(((end - start).days + 1) / 7)))
    n_months = ((end - start).days + 1) / 30.4
    lo_disc_aged, hi_disc_aged = inv["aged_discount_pct"]
    vehicles, deliveries = [], []
    vin_seq = del_seq = 0
    leads = leads.copy()

    for d in dealers.itertuples():
        b = cfg["brands"][d.brand]
        for m in models[models.brand == d.brand].itertuples():
            stock_f = b["models"][m.model_name][4]
            booked = leads[(leads.dealership_id == d.dealership_id) & (leads.model_id == m.model_id)
                           & leads.booking_id.notna() & leads.booking_cancelled_date.isna()]
            weekly = len(booked) / n_weeks
            receipts = []
            n_open = int(round(len(booked) / n_months * b["opening_stock_months"] * stock_f))
            for _ in range(n_open):
                age = int(np.clip(rng.exponential(35) + 1, 1, inv["opening_stock_max_age_days"]))
                receipts.append(start - pd.Timedelta(days=age))
            for w in range(n_weeks):
                wk = start + pd.Timedelta(days=7 * w)
                for _ in range(rng.poisson(weekly * stock_f)):
                    rd = wk + pd.Timedelta(days=int(rng.integers(0, 7)))
                    if rd <= end:
                        receipts.append(rd)
            receipts.sort()
            stock = []
            for rd in receipts:
                vin_seq += 1
                stock.append({"vin": f"SIMVIN{vin_seq:07d}", "dealership_id": d.dealership_id,
                              "model_id": m.model_id, "receipt_date": rd,
                              "purchase_cost_inr": int(round(m.ex_showroom_price_inr * inv["cost_to_price_ratio"])),
                              "delivery_date": pd.NaT})
            free = list(range(len(stock)))          # indices in receipt-date order (FIFO)
            requests = booked[booked.requested_delivery_date <= end].sort_values(["requested_delivery_date", "booking_id"])
            for req in requests.itertuples():
                ready = [i for i in free if stock[i]["receipt_date"] + prep <= req.requested_delivery_date]
                if ready:
                    i, dd = ready[0], req.requested_delivery_date
                elif free:
                    i = free[0]
                    dd = stock[i]["receipt_date"] + prep
                    if dd > end:
                        continue                    # no vehicle can be ready before the snapshot
                else:
                    continue
                free.remove(i)
                stock[i]["delivery_date"] = dd
                del_seq += 1
                age = (dd - stock[i]["receipt_date"]).days
                disc = float(rng.uniform(0, 3)) + (float(rng.uniform(lo_disc_aged, hi_disc_aged))
                                                   if age > inv["aged_threshold_days"] else 0.0)
                deliveries.append({
                    "delivery_id": f"DL{del_seq:06d}", "booking_id": req.booking_id, "lead_id": req.lead_id,
                    "vin": stock[i]["vin"], "dealership_id": d.dealership_id, "model_id": m.model_id,
                    "salesperson_id": req.salesperson_id, "delivery_date": dd,
                    "days_from_booking": (dd - req.booking_date).days, "days_in_inventory": age,
                    "ex_showroom_price_inr": int(m.ex_showroom_price_inr), "discount_pct": round(disc, 2),
                    "invoice_amount_inr": int(round(m.ex_showroom_price_inr * (1 - disc / 100))),
                    "purchase_cost_inr": stock[i]["purchase_cost_inr"], "data_classification": SIM_LABEL})
                idx = leads.index[leads.lead_id == req.lead_id][0]
                leads.loc[idx, ["delivery_id", "delivery_date", "vin", "lead_status"]] = [
                    f"DL{del_seq:06d}", dd, stock[i]["vin"], "Delivered"]
            vehicles += stock

    veh = pd.DataFrame(vehicles)
    veh["stock_status"] = np.where(veh.delivery_date.notna(), "Delivered", "In stock")
    veh["days_in_inventory"] = (veh.delivery_date.fillna(end) - veh.receipt_date).dt.days
    veh["data_classification"] = SIM_LABEL
    return veh, pd.DataFrame(deliveries), leads


def month_end_snapshots(vehicles: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    dates = [d for d in pd.date_range(start, end, freq="ME")]
    if not dates or dates[-1] != end:
        dates.append(end)
    bounds = tuple(cfg["inventory"]["ageing_buckets"])
    parts = []
    for sd in dates:
        s = vehicles[(vehicles.receipt_date <= sd) & (vehicles.delivery_date.isna() | (vehicles.delivery_date > sd))]
        s = s[["vin", "dealership_id", "model_id", "receipt_date", "purchase_cost_inr"]].copy()
        s.insert(0, "snapshot_date", sd)
        s["age_days"] = (sd - s.receipt_date).dt.days
        s["ageing_bucket"] = [ageing_bucket(a, bounds) for a in s.age_days]
        s["is_aged"] = s.age_days > cfg["inventory"]["aged_threshold_days"]
        parts.append(s)
    out = pd.concat(parts, ignore_index=True).drop(columns=["receipt_date"])
    out["data_classification"] = SIM_LABEL
    return out


# =========================================================================== integrity
def validate_simulation(t: dict[str, pd.DataFrame], cfg: dict) -> None:
    p: list[str] = []
    end = pd.Timestamp(cfg["end_date"])
    leads, veh, dl = t["fact_lead_funnel"], t["fact_vehicle_inventory"], t["fact_vehicle_delivery"]
    dims = {"dim_dealership": "dealership_id", "dim_salesperson": "salesperson_id", "dim_vehicle_model": "model_id"}
    for name, key in {**dims, "fact_lead_funnel": "lead_id", "fact_vehicle_inventory": "vin",
                      "fact_vehicle_delivery": "delivery_id"}.items():
        if t[name][key].duplicated().any() or t[name][key].isna().any():
            p.append(f"{name}.{key} is not a unique, non-null key")
    for name, df in t.items():
        if "data_classification" in df and not (df.data_classification == SIM_LABEL).all():
            p.append(f"{name} has rows not labelled {SIM_LABEL}")

    def fk(child, col, parent, pcol):
        bad = ~t[child][col].dropna().isin(t[parent][pcol])
        if bad.any():
            p.append(f"{child}.{col} has {int(bad.sum())} values missing from {parent}.{pcol}")

    fk("dim_salesperson", "dealership_id", "dim_dealership", "dealership_id")
    for child in ("fact_lead_funnel", "fact_vehicle_inventory", "fact_vehicle_delivery"):
        fk(child, "dealership_id", "dim_dealership", "dealership_id")
        fk(child, "model_id", "dim_vehicle_model", "model_id")
    fk("fact_lead_funnel", "salesperson_id", "dim_salesperson", "salesperson_id")
    fk("fact_vehicle_delivery", "lead_id", "fact_lead_funnel", "lead_id")
    fk("fact_vehicle_delivery", "vin", "fact_vehicle_inventory", "vin")
    fk("fact_inventory_snapshot", "vin", "fact_vehicle_inventory", "vin")

    sp_home = t["dim_salesperson"].set_index("salesperson_id").dealership_id
    if (leads.salesperson_id.map(sp_home) != leads.dealership_id).any():
        p.append("a lead is assigned to a salesperson from another dealership")
    brand_of_model = t["dim_vehicle_model"].set_index("model_id").brand
    brand_of_dealer = t["dim_dealership"].set_index("dealership_id").brand
    if (leads.model_id.map(brand_of_model) != leads.dealership_id.map(brand_of_dealer)).any():
        p.append("a lead is for a model of another brand")

    seq = ["lead_date", "qualified_date", "test_drive_date", "booking_date", "delivery_date"]
    for a, b in zip(seq, seq[1:]):
        has_b = leads[b].notna()
        if leads.loc[has_b, a].isna().any():
            p.append(f"stage skipped: {b} present without {a}")
        if (leads.loc[has_b, b] < leads.loc[has_b, a]).any():
            p.append(f"date order violated: {b} before {a}")
    for col in seq + ["lost_date", "booking_cancelled_date"]:
        if (leads[col].dropna() > end).any():
            p.append(f"{col} after snapshot date")
    lost = leads.lead_status == "Lost"
    if leads.loc[lost, ["lost_stage", "lost_reason", "lost_date"]].isna().any().any():
        p.append("lost lead without stage, reason or date")
    if leads.loc[lost, "delivery_id"].notna().any():
        p.append("lost lead has a delivery")
    if leads.loc[leads.delivery_id.notna(), "booking_cancelled_date"].notna().any():
        p.append("cancelled booking was delivered")
    if leads.loc[leads.booking_id.isna(), "delivery_id"].notna().any():
        p.append("delivery without booking")
    status_ok = {"Open", "Lost", "Booked - awaiting delivery", "Delivered"}
    if not set(leads.lead_status) <= status_ok:
        p.append(f"unexpected lead_status {set(leads.lead_status) - status_ok}")

    if dl.vin.duplicated().any() or dl.booking_id.duplicated().any():
        p.append("a vehicle or booking was delivered more than once")
    m = dl.merge(veh[["vin", "dealership_id", "model_id", "receipt_date"]], on="vin", suffixes=("", "_veh"))
    if ((m.dealership_id != m.dealership_id_veh) | (m.model_id != m.model_id_veh)).any():
        p.append("delivered vehicle does not match the booking's dealership/model")
    if (m.delivery_date < m.receipt_date + pd.Timedelta(days=cfg["inventory"]["allocation_prep_days"])).any():
        p.append("vehicle delivered before it was received and prepared")
    ml = dl.merge(leads[["lead_id", "booking_id", "model_id", "delivery_date"]], on="lead_id", suffixes=("", "_lead"))
    if ((ml.booking_id != ml.booking_id_lead) | (ml.model_id != ml.model_id_lead)
            | (ml.delivery_date != ml.delivery_date_lead)).any():
        p.append("delivery record inconsistent with its lead/booking")
    if (veh.receipt_date > end).any():
        p.append("vehicle received after snapshot date")
    snap = t["fact_inventory_snapshot"]
    if (snap.age_days < 0).any():
        p.append("negative inventory age in snapshot")
    if p:
        raise SimulationIntegrityError(p)


# =========================================================================== KPIs
def _pct(n, d):
    return market_share_pct(n, d)


def funnel_counts(leads: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    g = leads.groupby(by, dropna=False) if by else leads.assign(_all="All").groupby("_all")
    out = g.agg(leads=("lead_id", "count"), qualified=("qualified_date", "count"),
                test_drives=("test_drive_date", "count"), bookings=("booking_id", "count"),
                deliveries=("delivery_id", "count"),
                lost=("lead_status", lambda s: (s == "Lost").sum()),
                open_leads=("lead_status", lambda s: (s == "Open").sum()),
                booked_awaiting=("lead_status", lambda s: (s == "Booked - awaiting delivery").sum())).reset_index()
    out["lead_to_qualified_pct"] = _pct(out.qualified, out.leads)
    out["qualified_to_test_drive_pct"] = _pct(out.test_drives, out.qualified)
    out["test_drive_to_booking_pct"] = _pct(out.bookings, out.test_drives)
    out["booking_to_delivery_pct"] = _pct(out.deliveries, out.bookings)
    out["lead_to_booking_pct"] = _pct(out.bookings, out.leads)
    out["lead_to_delivery_pct"] = _pct(out.deliveries, out.leads)
    return out.drop(columns=["_all"], errors="ignore")


def compute_kpis(t: dict[str, pd.DataFrame], cfg: dict) -> dict[str, pd.DataFrame]:
    start, end = pd.Timestamp(cfg["start_date"]), pd.Timestamp(cfg["end_date"])
    inv = cfg["inventory"]
    bounds = tuple(inv["ageing_buckets"])
    dealers = t["dim_dealership"][["dealership_id", "dealership_name", "brand", "segment"]]
    leads = t["fact_lead_funnel"].merge(dealers, on="dealership_id")
    veh, dl, snap = t["fact_vehicle_inventory"], t["fact_vehicle_delivery"], t["fact_inventory_snapshot"]
    k: dict[str, pd.DataFrame] = {}

    total = funnel_counts(leads, []).assign(dealership_id="ALL", dealership_name="All dealerships",
                                            brand="All", segment="All")
    k["funnel_by_dealership"] = pd.concat([funnel_counts(leads, ["dealership_id", "dealership_name", "brand", "segment"]),
                                           total], ignore_index=True)
    leads["lead_month"] = leads.lead_date.dt.strftime("%Y-%m")
    k["funnel_by_lead_month"] = funnel_counts(leads, ["lead_month"])
    k["funnel_by_source"] = funnel_counts(leads, ["lead_source"])
    stage_rows = []
    for d, g in [("ALL", leads)] + list(leads.groupby("dealership_id")):
        counts = [len(g), g.qualified_date.notna().sum(), g.test_drive_date.notna().sum(),
                  g.booking_id.notna().sum(), g.delivery_id.notna().sum()]
        for i, (stage, c) in enumerate(zip(STAGES, counts)):
            stage_rows.append({"dealership_id": d, "stage_order": i + 1, "stage": stage, "count": int(c),
                               "conversion_from_previous_pct": _pct(c, counts[i - 1]) if i else 100.0,
                               "conversion_from_lead_pct": _pct(c, counts[0])})
    k["funnel_stages"] = pd.DataFrame(stage_rows)

    lost = leads[leads.lead_status == "Lost"]
    lr = lost.groupby(["lost_stage", "lost_reason"]).size().rename("lost_leads").reset_index()
    lr["share_of_lost_pct"] = _pct(lr.lost_leads, len(lost))
    k["lost_reasons"] = lr.sort_values(["lost_stage", "lost_leads"], ascending=[True, False]).reset_index(drop=True)

    sp = t["dim_salesperson"][["salesperson_id", "dealership_id"]]
    perf = funnel_counts(leads, ["salesperson_id"]).merge(sp, on="salesperson_id")
    fu = leads.groupby("salesperson_id").agg(first_contact_sla_pct=("first_contact_within_sla", lambda s: s.mean() * 100),
                                             median_first_contact_hours=("first_contact_hours", "median")).reset_index()
    perf = perf.merge(fu, on="salesperson_id")
    rev = dl.groupby("salesperson_id").invoice_amount_inr.sum().rename("invoiced_revenue_inr")
    perf = perf.join(rev, on="salesperson_id").fillna({"invoiced_revenue_inr": 0})
    perf["rank_in_dealership"] = perf.groupby("dealership_id").deliveries.rank(ascending=False, method="min").astype(int)
    k["salesperson_performance"] = perf.sort_values(["dealership_id", "rank_in_dealership", "salesperson_id"]).reset_index(drop=True)

    fd = leads.groupby("dealership_id").agg(first_contact_sla_pct=("first_contact_within_sla", lambda s: s.mean() * 100),
                                            median_first_contact_hours=("first_contact_hours", "median"),
                                            p90_first_contact_hours=("first_contact_hours", lambda s: s.quantile(0.9)))
    k["followup_by_dealership"] = fd.reset_index()

    cur = snap[snap.snapshot_date == end].merge(t["dim_vehicle_model"][["model_id", "model_name"]], on="model_id")
    labels = bucket_labels(bounds)
    ib = cur.pivot_table(index=["dealership_id", "model_id", "model_name"], columns="ageing_bucket",
                         values="vin", aggfunc="count", fill_value=0).reindex(columns=labels, fill_value=0)
    stats = cur.groupby(["dealership_id", "model_id", "model_name"]).agg(
        stock_units=("vin", "count"), stock_cost_inr=("purchase_cost_inr", "sum"),
        avg_age_days=("age_days", "mean"), max_age_days=("age_days", "max"), aged_units=("is_aged", "sum"))
    im = stats.join(ib).reset_index()
    im["aged_share_pct"] = _pct(im.aged_units, im.stock_units)
    im["share_of_dealer_stock_pct"] = _pct(im.stock_units, im.groupby("dealership_id").stock_units.transform("sum"))
    k["inventory_by_model"] = im

    rows = []
    months_in_period = ((end - start).days + 1) / 30.4
    for d in t["dim_dealership"].itertuples():
        v = veh[veh.dealership_id == d.dealership_id]
        s = cur[cur.dealership_id == d.dealership_id]
        dd = dl[dl.dealership_id == d.dealership_id]
        opening = int(((v.receipt_date < start) & (v.delivery_date.isna() | (v.delivery_date >= start))).sum())
        receipts = int(((v.receipt_date >= start) & (v.receipt_date <= end)).sum())
        delivered = int(((dd.delivery_date >= start) & (dd.delivery_date <= end)).sum())
        last30 = int((dd.delivery_date > end - pd.Timedelta(days=30)).sum())
        month_end = snap[snap.dealership_id == d.dealership_id].groupby("snapshot_date").purchase_cost_inr.sum()
        shares = s.groupby("model_id").size() / max(len(s), 1)
        bucket_counts = s.ageing_bucket.value_counts()
        rows.append({
            "dealership_id": d.dealership_id, "dealership_name": d.dealership_name, "brand": d.brand,
            "segment": d.segment, "opening_stock_units": opening, "receipts_units": receipts,
            "deliveries_units": delivered, "closing_stock_units": len(s),
            "closing_stock_cost_inr": int(s.purchase_cost_inr.sum()),
            **{f"stock_{lbl}_days": int(bucket_counts.get(lbl, 0)) for lbl in labels},
            "aged_units": int(s.is_aged.sum()),
            "aged_inventory_share_pct": _pct(int(s.is_aged.sum()), len(s)),
            "avg_stock_age_days": s.age_days.mean() if len(s) else np.nan,
            "sell_through_pct": _pct(delivered, opening + receipts),
            "inventory_turnover_annualised": (dd.purchase_cost_inr.sum() / month_end.mean() * 12 / months_in_period
                                              if len(month_end) and month_end.mean() > 0 else np.nan),
            "avg_days_in_inventory_delivered": dd.days_in_inventory.mean() if len(dd) else np.nan,
            "deliveries_last_30_days": last30,
            "days_of_supply": len(s) / (last30 / 30) if last30 else np.nan,
            "top_model_id": shares.idxmax() if len(shares) else None,
            "top_model_stock_share_pct": shares.max() * 100 if len(shares) else np.nan,
            "stock_hhi": float((shares ** 2).sum() * 10_000) if len(shares) else np.nan,
        })
    k["inventory_by_dealership"] = pd.DataFrame(rows)

    trend = snap.groupby(["snapshot_date", "dealership_id"]).agg(stock_units=("vin", "count"),
                                                                 aged_units=("is_aged", "sum"),
                                                                 avg_age_days=("age_days", "mean")).reset_index()
    trend["aged_share_pct"] = _pct(trend.aged_units, trend.stock_units)
    k["inventory_trend"] = trend

    fbd = k["funnel_by_dealership"]
    awaiting = leads[(leads.lead_status == "Booked - awaiting delivery") & (leads.requested_delivery_date <= end)]
    score = (fbd[fbd.dealership_id != "ALL"][["dealership_id", "dealership_name", "brand", "segment",
                                               "lead_to_delivery_pct", "test_drive_to_booking_pct"]]
             .merge(fd.reset_index()[["dealership_id", "first_contact_sla_pct"]], on="dealership_id")
             .merge(k["inventory_by_dealership"][["dealership_id", "aged_inventory_share_pct", "days_of_supply",
                                                  "top_model_stock_share_pct", "sell_through_pct"]], on="dealership_id"))
    score["awaiting_allocation_bookings"] = score.dealership_id.map(awaiting.groupby("dealership_id").size()).fillna(0).astype(int)
    k["dealership_scorecard"] = score
    k["action_triggers"] = evaluate_actions(score, cfg)
    return k


def evaluate_actions(score: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    ops = {">": np.greater, "<": np.less, ">=": np.greater_equal, "<=": np.less_equal}
    rows = []
    for rule in cfg["action_rules"]:
        segs = rule.get("segments")
        for s in score.itertuples():
            if segs and s.segment not in segs:
                continue
            actual = getattr(s, rule["kpi"])
            breached = bool(pd.notna(actual) and ops[rule["op"]](actual, rule["threshold"]))
            rows.append({"dealership_id": s.dealership_id, "dealership_name": s.dealership_name, "brand": s.brand,
                         "kpi": rule["kpi"], "actual": actual, "comparator": rule["op"], "threshold": rule["threshold"],
                         "breached": breached, "status": "ACTION" if breached else ("NO DATA" if pd.isna(actual) else "OK"),
                         "responsible_role": rule["responsible_role"], "review_cadence": rule["review_cadence"],
                         "recommended_action": rule["recommended_action"] if breached else "",
                         "threshold_basis": rule["basis"], "data_classification": SIM_LABEL})
    return pd.DataFrame(rows)


# =========================================================================== charts / report
def make_charts(k: dict, out_dir: Path, cfg: dict) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out_dir.mkdir(parents=True, exist_ok=True)
    blue, ramp = "#2a78d6", ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
    text, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"
    written = []

    def style(ax, title):
        ax.set_title(title, loc="left", fontsize=11, fontweight="bold", color=text)
        ax.tick_params(colors=muted, labelsize=8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(grid)

    def save(fig, name):
        p = out_dir / name
        fig.text(0.99, 0.01, "SIMULATED DATA - illustrative only", ha="right", fontsize=7, color=muted)
        fig.tight_layout(rect=(0, 0.03, 1, 1))
        fig.savefig(p, dpi=150, metadata={"Software": None}, facecolor="white")
        plt.close(fig)
        written.append(p)

    st = k["funnel_stages"][k["funnel_stages"].dealership_id == "ALL"]
    fig, ax = plt.subplots(figsize=(8, 3.6))
    bars = ax.barh(st.stage[::-1], st["count"][::-1], color=ramp[::-1], height=0.6)
    ax.bar_label(bars, labels=[f"{c:,}  ({p:.1f}% of leads)" for c, p in zip(st["count"][::-1], st.conversion_from_lead_pct[::-1])],
                 fontsize=8, color=muted, padding=3)
    ax.margins(x=0.3)
    style(ax, "Sales funnel, all dealerships (lead cohort)")
    save(fig, "01_sales_funnel.png")

    inv = k["inventory_by_dealership"]
    labels = bucket_labels(tuple(cfg["inventory"]["ageing_buckets"]))
    fig, ax = plt.subplots(figsize=(9, 3.8))
    left = np.zeros(len(inv))
    names = [f"{r.dealership_id}\n{r.brand}" for r in inv.itertuples()]
    for lbl, c in zip(labels, ramp[:4]):
        vals = inv[f"stock_{lbl}_days"].to_numpy()
        ax.barh(names, vals, left=left, color=c, label=f"{lbl} days", height=0.6, edgecolor="white", linewidth=1)
        left += vals
    ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    style(ax, f"Current stock by age bucket at {cfg['end_date']} (units)")
    save(fig, "02_inventory_ageing.png")

    sc = k["dealership_scorecard"]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    bars = ax.bar([f"{r.dealership_id}\n{r.brand}" for r in sc.itertuples()], sc.lead_to_delivery_pct, color=blue, width=0.6)
    ax.bar_label(bars, fmt="%.1f%%", fontsize=8, color=muted, padding=2)
    style(ax, "Lead-to-delivery conversion by dealership (%)")
    save(fig, "03_lead_to_delivery_by_dealership.png")
    return written


def write_report(k: dict, t: dict, cfg: dict, meta: dict, path: Path) -> None:
    from auto_retail_analysis import md_table
    acts = k["action_triggers"]
    breached = acts[acts.breached]
    allf = k["funnel_by_dealership"].set_index("dealership_id").loc["ALL"]
    lines = [
        "# Dealership Operations KPI Report (SIMULATED DATA)",
        "",
        "> **All figures in this report are generated by a seeded simulation** (`python/dealership_kpi_simulation.py`, "
        f"seed {meta['seed']}, {meta['start_date']} to {meta['end_date']}). They illustrate the KPI framework and "
        "do not describe any real dealership, brand, salesperson or customer. Thresholds are illustrative business rules "
        "unless a published basis is stated.",
        "",
        "## 1. Sales funnel (lead cohort through the snapshot date)",
        "",
        f"{int(allf.leads):,} leads -> {int(allf.qualified):,} qualified -> {int(allf.test_drives):,} test drives -> "
        f"{int(allf.bookings):,} bookings -> {int(allf.deliveries):,} deliveries "
        f"(lead-to-delivery {allf.lead_to_delivery_pct:.1f}%). Leads created near the snapshot date have had less time "
        "to convert; they are counted as open, not lost.",
        "",
        md_table(k["funnel_by_dealership"][["dealership_id", "brand", "leads", "qualified", "test_drives", "bookings",
                                            "deliveries", "lead_to_qualified_pct", "qualified_to_test_drive_pct",
                                            "test_drive_to_booking_pct", "booking_to_delivery_pct", "lead_to_delivery_pct"]],
                 {c: ".1f" for c in ["lead_to_qualified_pct", "qualified_to_test_drive_pct", "test_drive_to_booking_pct",
                                     "booking_to_delivery_pct", "lead_to_delivery_pct"]}),
        "",
        "![Funnel](charts/01_sales_funnel.png)",
        "",
        "## 2. Lost-lead reasons",
        "",
        md_table(k["lost_reasons"], {"share_of_lost_pct": ".1f"}),
        "",
        "## 3. Lead follow-up",
        "",
        f"First-contact standard: within {cfg['first_contact']['sla_hours']} hours (illustrative).",
        "",
        md_table(k["followup_by_dealership"], {"first_contact_sla_pct": ".1f", "median_first_contact_hours": ".1f",
                                               "p90_first_contact_hours": ".1f"}),
        "",
        "## 4. Inventory and ageing",
        "",
        f"Ageing buckets use inclusive upper bounds {cfg['inventory']['ageing_buckets']} days; aged inventory = older "
        f"than {cfg['inventory']['aged_threshold_days']} days. Sell-through = deliveries / (opening stock + receipts). "
        "Turnover = cost of vehicles delivered / average month-end stock cost, annualised. Days of supply = closing "
        "stock / average daily deliveries over the last 30 days.",
        "",
        md_table(k["inventory_by_dealership"].drop(columns=["dealership_name", "top_model_id"]),
                 {"aged_inventory_share_pct": ".1f", "avg_stock_age_days": ".1f", "sell_through_pct": ".1f",
                  "inventory_turnover_annualised": ".2f", "avg_days_in_inventory_delivered": ".1f",
                  "days_of_supply": ".1f", "top_model_stock_share_pct": ".1f", "stock_hhi": ".0f",
                  "closing_stock_cost_inr": ",.0f"}),
        "",
        "![Inventory ageing](charts/02_inventory_ageing.png)",
        "",
        "## 5. Management actions triggered",
        "",
        f"{len(breached)} of {len(acts)} rule checks breached their threshold.",
        "",
        md_table(breached[["dealership_id", "brand", "kpi", "actual", "comparator", "threshold", "responsible_role",
                           "review_cadence", "recommended_action", "threshold_basis"]], {"actual": ".1f"})
        if len(breached) else "No thresholds breached.",
        "",
        "## 6. Data integrity",
        "",
        "Generated tables passed all integrity checks (unique keys, foreign keys, salesperson-dealership and "
        "model-brand consistency, stage order and dates, one delivery per booking and per vehicle, delivery after "
        "receipt). Row counts: " + ", ".join(f"{k_}: {v:,}" for k_, v in meta["row_counts"].items()) + ".",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# =========================================================================== orchestration
def simulate(cfg: dict) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(cfg["seed"])
    dealers, people, models, latent = build_dimensions(cfg, rng)
    leads = simulate_leads(cfg, rng, dealers, people, models, latent)
    veh, dl, leads = simulate_inventory(cfg, rng, dealers, models, leads)
    snap = month_end_snapshots(veh, cfg)
    t = {"dim_dealership": dealers, "dim_salesperson": people, "dim_vehicle_model": models,
         "fact_lead_funnel": leads, "fact_vehicle_inventory": veh, "fact_vehicle_delivery": dl,
         "fact_inventory_snapshot": snap, "simulation_latent_parameters": latent}
    validate_simulation(t, cfg)
    return t


def write_outputs(t: dict, k: dict, cfg: dict, data_dir: Path = SIMULATED_DIR, table_dir: Path = DEALER_TABLE_DIR,
                  chart_dir: Path = DEALER_CHART_DIR, report_dir: Path = DEALER_REPORT_DIR,
                  charts: bool = True) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)
    for name, df in t.items():
        df.to_csv(data_dir / f"{name}.csv", index=False, date_format="%Y-%m-%d", float_format="%.4f", lineterminator="\n")
    for name, df in k.items():
        df.to_csv(table_dir / f"{name}.csv", index=False, date_format="%Y-%m-%d", float_format="%.4f", lineterminator="\n")
    meta = {"data_classification": SIM_LABEL, "seed": cfg["seed"], "start_date": str(cfg["start_date"]),
            "end_date": str(cfg["end_date"]), "scale": cfg["scale"], "config_sha256_16": config_hash(cfg),
            "generator": "python/dealership_kpi_simulation.py",
            "row_counts": {n: len(df) for n, df in t.items()}}
    (data_dir / "simulation_run_metadata.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if charts:
        make_charts(k, chart_dir, cfg)
    write_report(k, t, cfg, meta, report_dir / "dealership_kpi_report.md")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=CONFIG_DIR / "simulation.yaml")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--start", dest="start_date")
    ap.add_argument("--end", dest="end_date")
    ap.add_argument("--scale", type=float, help="multiplier on lead volumes (dataset size)")
    ap.add_argument("--no-charts", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config, seed=a.seed, start_date=a.start_date, end_date=a.end_date, scale=a.scale)
        t = simulate(cfg)
    except (ValueError, SimulationIntegrityError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    k = compute_kpis(t, cfg)
    meta = write_outputs(t, k, cfg, charts=not a.no_charts)
    acts = k["action_triggers"]
    print(f"[{SIM_LABEL}] seed={cfg['seed']} period={cfg['start_date']}..{cfg['end_date']} scale={cfg['scale']}")
    for n, c in meta["row_counts"].items():
        print(f"  {n:32s}{c:>8,}")
    print(f"  action rule checks breached      {int(acts.breached.sum())} of {len(acts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
