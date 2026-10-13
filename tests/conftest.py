"""Shared fixtures: a small, hand-checkable FADA-shaped dataset.

Prior month 2025-02 / current month 2026-02.
PV  prior : Toyota 100, Honda Cars 50, JSW MG 30, Mercedes group 20 (18 + 2), Maruti 700, Others 100 = 1,000
PV  curr  : Toyota 110, Honda Cars 60, JSW MG 30, Mercedes group 20 (19 + 1), Maruti 900, Others  80 = 1,200
2W  prior : Ather 10, Hero 900, HMSI 90, Others 0 = 1,000
2W  curr  : Ather 30, Hero 1,000, HMSI 170, Others 0 = 1,200
"""
from __future__ import annotations

import copy

import pandas as pd
import pytest

MINI_CFG = {
    "current_period": {"start": "2026-02", "end": "2026-02", "label": "Feb 2026"},
    "prior_period": {"start": "2025-02", "end": "2025-02", "label": "Feb 2025"},
    "segments": {"PV": "Passenger vehicles", "2W": "Two-wheelers"},
    "portfolio_brands": {"Toyota": "PV", "Honda Cars": "PV", "JSW MG Motor": "PV",
                         "Mercedes-Benz": "PV", "Ather": "2W"},
    "ev_only_brands": ["Ather"],
    "min_base_units_for_growth": 0,
    "attention_rules": {"growth_gap_pp_warning": -5.0, "share_change_bps_warning": -10.0,
                        "registration_gap_units_warning": 10},
    "brief_figures": [],
}

MINI_MAPPING = pd.DataFrame([
    ("PV", "TOYOTA KIRLOSKAR MOTOR PVT LTD", "Toyota Kirloskar Motor", "Toyota"),
    ("PV", "HONDA CARS INDIA LTD", "Honda Cars India", "Honda Cars"),
    ("PV", "JSW MG MOTOR INDIA PVT LTD", "JSW MG Motor", "JSW MG Motor"),
    ("PV", "MERCEDES -BENZ GROUP", "Mercedes-Benz Group", "Mercedes-Benz"),
    ("PV", "MARUTI SUZUKI INDIA LTD", "Maruti Suzuki", None),
    ("PV", "Others", "Others (PV)", None),
    ("2W", "ATHER ENERGY LTD", "Ather Energy", "Ather"),
    ("2W", "HERO MOTOCORP LTD", "Hero MotoCorp", None),
    ("2W", "HONDA MOTORCYCLE AND SCOOTER INDIA (P) LTD", "Honda Motorcycle & Scooter India", None),
    ("2W", "Others Including EV", "Others (2W)", None),
], columns=["segment", "oem_raw", "oem_canonical", "portfolio_brand"])

PV = {"2025-02": {"TOYOTA KIRLOSKAR MOTOR PVT LTD": 100, "HONDA CARS INDIA LTD": 50,
                  "JSW MG MOTOR INDIA PVT LTD": 30, "MERCEDES -BENZ GROUP": (20, {"MERCEDES-BENZ INDIA PVT LTD": 18, "MERCEDES -BENZ AG": 2}),
                  "MARUTI SUZUKI INDIA LTD": 700, "Others": 100},
      "2026-02": {"TOYOTA KIRLOSKAR MOTOR PVT LTD": 110, "HONDA CARS INDIA LTD": 60,
                  "JSW MG MOTOR INDIA PVT LTD": 30, "MERCEDES -BENZ GROUP": (20, {"MERCEDES-BENZ INDIA PVT LTD": 19, "MERCEDES -BENZ AG": 1}),
                  "MARUTI SUZUKI INDIA LTD": 900, "Others": 80}}
TW = {"2025-02": {"ATHER ENERGY LTD": 10, "HERO MOTOCORP LTD": 900,
                  "HONDA MOTORCYCLE AND SCOOTER INDIA (P) LTD": 90, "Others Including EV": 0},
      "2026-02": {"ATHER ENERGY LTD": 30, "HERO MOTOCORP LTD": 1000,
                  "HONDA MOTORCYCLE AND SCOOTER INDIA (P) LTD": 170, "Others Including EV": 0}}
EV = {("PV", "2025-02"): 2.0, ("PV", "2026-02"): 5.0, ("2W", "2025-02"): 4.0, ("2W", "2026-02"): 6.0}


def build_oem_frame(pv=PV, tw=TW) -> pd.DataFrame:
    rows = []
    for seg, data in (("PV", pv), ("2W", tw)):
        for month, oems in data.items():
            total = 0
            for name, val in oems.items():
                units, members = (val if isinstance(val, tuple) else (val, {}))
                level = "others" if name.startswith("Others") else "top"
                rows.append(_row(seg, month, name, level, None, units))
                for m, mu in members.items():
                    rows.append(_row(seg, month, m, "member", name, mu))
                total += units
            rows.append(_row(seg, month, "Total", "total", None, total))
    return pd.DataFrame(rows)


def _row(seg, month, name, level, parent, units):
    return {"release_month": "2026-02", "period_month": month,
            "value_role": "current" if month.startswith("2026") else "prior_year",
            "segment": seg, "oem_raw": name, "row_level": level, "parent_oem_raw": parent,
            "units": units, "reported_as_dash": False, "published_share_pct": 0.0,
            "source_file": "fixture.pdf", "source_page": 1}


def build_fuel_frame(ev=EV) -> pd.DataFrame:
    rows = []
    for (seg, month), share in ev.items():
        role = "current" if month.startswith("2026") else "prior_year"
        rows.append({"release_month": "2026-02", "period_month": month, "value_role": role, "segment": seg,
                     "fuel": "EV", "share_pct": share, "source_file": "fixture.pdf", "source_page": 1})
        rows.append({"release_month": "2026-02", "period_month": month, "value_role": role, "segment": seg,
                     "fuel": "PETROL/ETHANOL", "share_pct": 100 - share, "source_file": "fixture.pdf", "source_page": 1})
    return pd.DataFrame(rows)


@pytest.fixture
def cfg():
    return copy.deepcopy(MINI_CFG)


@pytest.fixture
def mapping():
    return MINI_MAPPING.copy()


@pytest.fixture
def oem():
    return build_oem_frame()


@pytest.fixture
def fuel():
    return build_fuel_frame()


@pytest.fixture
def processed_dir(tmp_path, oem, fuel, mapping):
    """Write the fixture tables where run_analysis expects them."""
    oem.to_csv(tmp_path / "fada_oem_monthly.csv", index=False)
    fuel.to_csv(tmp_path / "fada_fuel_mix_monthly.csv", index=False)
    mapping.to_csv(tmp_path / "oem_mapping.csv", index=False)
    return tmp_path
