"""Build, load, validate and reconcile the SQL Server model.

Steps (all by default, in order):
  create     create the database if missing (connects to master)
  schema     sql/01_schemas.sql, 02_staging_tables.sql, 03_dw_tables.sql, 04_dim_date.sql
  stage      load repository CSVs into stg.* (text columns; empty cells -> NULL)
  transform  sql/05_validate_staging.sql, 06_transform_load.sql, 07_analysis_views.sql, 08_reconciliation.sql
  reconcile  compare rpt.* views with the Python outputs in reports/ (same inputs, same definitions)

Connection (Windows authentication; no secrets are stored in the repository):
  --server   default: env AUTORETAIL_SQL_SERVER or .\\SQLEXPRESS
  --database default: env AUTORETAIL_SQL_DATABASE or AutoRetailAnalytics
  --driver   default: newest installed "ODBC Driver 1x for SQL Server"
  A full ODBC string (e.g. for SQL authentication) can be supplied through the environment
  variable AUTORETAIL_SQL_CONNECTION_STRING; it is never written to disk.

Run (from the repository root, after the Python pipelines):
  python python/sql_server_pipeline.py
  python python/sql_server_pipeline.py --steps reconcile
  python python/sql_server_pipeline.py --rebuild          # drop and recreate dw tables and views
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from paths import CONFIG_DIR, DEALER_TABLE_DIR, FADA_PROCESSED_DIR, MARKET_TABLE_DIR, ROOT, SIMULATED_DIR

SQL_DIR = ROOT / "sql"
RECON_PATH = ROOT / "reports" / "sql_python_reconciliation.csv"
STEPS = ["create", "schema", "stage", "transform", "reconcile"]
STAGE_FILES = {
    "stg.FadaOemMonthly": FADA_PROCESSED_DIR / "fada_oem_monthly.csv",
    "stg.FadaFuelMix": FADA_PROCESSED_DIR / "fada_fuel_mix_monthly.csv",
    "stg.OemMapping": CONFIG_DIR / "oem_mapping.csv",
    "stg.SimDealership": SIMULATED_DIR / "dim_dealership.csv",
    "stg.SimSalesperson": SIMULATED_DIR / "dim_salesperson.csv",
    "stg.SimVehicleModel": SIMULATED_DIR / "dim_vehicle_model.csv",
    "stg.SimLeadFunnel": SIMULATED_DIR / "fact_lead_funnel.csv",
    "stg.SimVehicleInventory": SIMULATED_DIR / "fact_vehicle_inventory.csv",
    "stg.SimVehicleDelivery": SIMULATED_DIR / "fact_vehicle_delivery.csv",
    "stg.SimInventorySnapshot": SIMULATED_DIR / "fact_inventory_snapshot.csv",
    "stg.SimActionTrigger": DEALER_TABLE_DIR / "action_triggers.csv",
    "stg.MarketAttention": MARKET_TABLE_DIR / "management_attention.csv",
}
PCT_TOL, UNIT_TOL = 1e-4, 1e-3


# =========================================================================== connection
def connection_string(server: str, database: str, driver: str | None) -> str:
    env = os.environ.get("AUTORETAIL_SQL_CONNECTION_STRING")
    if env:
        return env if "database=" in env.lower() else f"{env};DATABASE={database}"
    import pyodbc
    if driver is None:
        drivers = sorted(d for d in pyodbc.drivers() if re.match(r"ODBC Driver \d+ for SQL Server", d))
        if not drivers:
            raise RuntimeError("No 'ODBC Driver xx for SQL Server' installed. Install ODBC Driver 18 for SQL Server.")
        driver = drivers[-1]
    return (f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
            "Trusted_Connection=yes;TrustServerCertificate=yes;")


def connect(conn_str: str, autocommit: bool = False):
    import pyodbc
    return pyodbc.connect(conn_str, autocommit=autocommit, timeout=15)


def split_batches(sql: str) -> list[str]:
    return [b.strip() for b in re.split(r"^\s*GO\s*;?\s*$", sql, flags=re.IGNORECASE | re.MULTILINE) if b.strip()]


def run_sql_file(conn, path: Path, verbose: bool = True) -> list[pd.DataFrame]:
    """Execute a GO-separated script; returns every result set. SQL errors (THROW) propagate."""
    results = []
    cur = conn.cursor()
    for batch in split_batches(path.read_text(encoding="utf-8")):
        cur.execute(batch)
        while True:
            if cur.description:
                cols = [c[0] for c in cur.description]
                results.append(pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()], columns=cols))
            if not cur.nextset():
                break
    conn.commit()
    if verbose:
        print(f"  ran {path.relative_to(ROOT).as_posix()}")
    return results


# =========================================================================== steps
def create_database(server: str, database: str, driver: str | None) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", database):
        raise ValueError(f"unsafe database name {database!r}")
    with connect(connection_string(server, "master", driver), autocommit=True) as conn:
        conn.cursor().execute(f"IF DB_ID(N'{database}') IS NULL CREATE DATABASE [{database}];")
    print(f"  database {database} ready on {server}")


def stage(conn) -> dict[str, int]:
    cur = conn.cursor()
    cur.fast_executemany = True
    counts = {}
    frames = {t: pd.read_csv(p, dtype=str, keep_default_na=False) for t, p in STAGE_FILES.items()}
    cfg = yaml.safe_load((CONFIG_DIR / "market_analysis.yaml").read_text(encoding="utf-8"))
    frames["stg.AnalysisPeriod"] = pd.DataFrame([
        {"period_name": n, "start_month": cfg[k]["start"], "end_month": cfg[k]["end"], "period_label": cfg[k]["label"]}
        for n, k in (("current", "current_period"), ("prior", "prior_period"))])
    for table, df in frames.items():
        cur.execute(f"TRUNCATE TABLE {table};")
        rows = [tuple(None if v == "" else v for v in r) for r in df.itertuples(index=False)]
        if rows:
            cols = ", ".join(f"[{c}]" for c in df.columns)
            cur.executemany(f"INSERT {table} ({cols}) VALUES ({', '.join('?' * len(df.columns))})", rows)
        counts[table] = len(rows)
        print(f"  staged {len(rows):>7,} rows -> {table}")
    conn.commit()
    return counts


# =========================================================================== reconciliation
def _compare(area, py: pd.DataFrame, sq: pd.DataFrame, keys: list[str], metrics: dict[str, tuple[str, float]]) -> list[dict]:
    """metrics: python column -> (sql column, tolerance). Rows are matched on keys."""
    m = py.merge(sq, on=keys, how="outer", indicator="merge_status")
    out = []
    for rd in m.to_dict("records"):
        key = "|".join(str(rd[k]) for k in keys)
        if rd["merge_status"] != "both":
            out.append({"area": area, "key": key, "metric": "row present in both", "python": rd["merge_status"] != "right_only",
                        "sql": rd["merge_status"] != "left_only", "abs_diff": np.nan, "passed": False})
            continue
        for pcol, (scol, tol) in metrics.items():
            pv, sv = rd[pcol], rd[scol]
            pv = None if pv is None or (isinstance(pv, float) and np.isnan(pv)) else float(pv)
            sv = None if sv is None or (isinstance(sv, float) and np.isnan(sv)) else float(sv)
            if pv is None and sv is None:
                diff, ok = 0.0, True
            elif pv is None or sv is None:
                diff, ok = np.nan, False
            else:
                diff = abs(pv - sv)
                ok = diff <= tol
            out.append({"area": area, "key": key, "metric": pcol, "python": pv, "sql": sv, "abs_diff": diff, "passed": ok})
    return out


def reconcile(conn) -> pd.DataFrame:
    def q(sql: str) -> pd.DataFrame:
        cur = conn.cursor().execute(sql)
        cols = [c[0] for c in cur.description]
        df = pd.DataFrame.from_records([tuple(r) for r in cur.fetchall()], columns=cols)
        return df.loc[:, ~df.columns.duplicated()]

    rows: list[dict] = []

    py = pd.read_csv(MARKET_TABLE_DIR / "segment_period_summary.csv")
    sq = q("SELECT SegmentCode AS segment, UnitsPrior, UnitsCurrent, YoYGrowthPct FROM rpt.vSegmentPeriodSummary")
    rows += _compare("segment growth", py, sq, ["segment"], {
        "units_prior": ("UnitsPrior", 0), "units_current": ("UnitsCurrent", 0), "yoy_growth_pct": ("YoYGrowthPct", PCT_TOL)})

    py = pd.read_csv(MARKET_TABLE_DIR / "portfolio_brand_performance.csv")
    sq = q("SELECT SegmentCode AS segment, Brand AS brand, * FROM rpt.vPortfolioBrandPerformance")
    rows += _compare("portfolio brands", py, sq, ["segment", "brand"], {
        "units_prior": ("UnitsPrior", 0), "units_current": ("UnitsCurrent", 0),
        "yoy_growth_pct": ("YoYGrowthPct", PCT_TOL), "segment_growth_pct": ("SegmentGrowthPct", PCT_TOL),
        "growth_gap_pp": ("GrowthGapPp", PCT_TOL), "share_prior_pct": ("SharePriorPct", PCT_TOL),
        "share_current_pct": ("ShareCurrentPct", PCT_TOL), "share_change_bps": ("ShareChangeBps", 1e-2),
        "constant_share_units": ("ConstantShareUnits", UNIT_TOL), "registration_gap_units": ("RegistrationGapUnits", UNIT_TOL)})

    py = pd.read_csv(MARKET_TABLE_DIR / "oem_period_share.csv")
    py["coverage_complete"] = py.coverage_complete.astype(int)
    sq = q("SELECT SegmentCode AS segment, OEMName AS oem_canonical, UnitsPrior, UnitsCurrent, SharePriorPct, "
           "ShareCurrentPct, ShareChangeBps, CAST(IsCoverageComplete AS INT) AS IsCoverageComplete FROM rpt.vOEMPeriodShare")
    rows += _compare("OEM share", py.fillna({"units_prior": 0, "units_current": 0}), sq, ["segment", "oem_canonical"], {
        "units_prior": ("UnitsPrior", 0), "units_current": ("UnitsCurrent", 0),
        "coverage_complete": ("IsCoverageComplete", 0), "share_prior_pct": ("SharePriorPct", PCT_TOL),
        "share_current_pct": ("ShareCurrentPct", PCT_TOL), "share_change_bps": ("ShareChangeBps", 1e-2)})

    py = pd.read_csv(MARKET_TABLE_DIR / "ev_period_summary.csv")
    sq = q("SELECT SegmentCode AS segment, * FROM rpt.vEVPeriodSummary")
    rows += _compare("EV adoption", py, sq, ["segment"], {
        "ev_units_est_prior": ("EVUnitsEstPrior", UNIT_TOL), "ev_units_est_current": ("EVUnitsEstCurrent", UNIT_TOL),
        "ev_share_prior_pct": ("EVSharePriorPct", PCT_TOL), "ev_share_current_pct": ("EVShareCurrentPct", PCT_TOL)})

    py = pd.read_csv(DEALER_TABLE_DIR / "funnel_by_dealership.csv")
    py = py[py.dealership_id != "ALL"]
    sq = q("SELECT DealershipId AS dealership_id, * FROM rpt.vDealerFunnel")
    rows += _compare("SIMULATED funnel", py, sq, ["dealership_id"], {
        "leads": ("Leads", 0), "qualified": ("Qualified", 0), "test_drives": ("TestDrives", 0),
        "bookings": ("Bookings", 0), "deliveries": ("Deliveries", 0),
        "test_drive_to_booking_pct": ("TestDriveToBookingPct", PCT_TOL), "lead_to_delivery_pct": ("LeadToDeliveryPct", PCT_TOL)})

    py = pd.read_csv(DEALER_TABLE_DIR / "inventory_by_dealership.csv")
    sq = q("SELECT DealershipId AS dealership_id, * FROM rpt.vInventoryCurrentByDealership")
    rows += _compare("SIMULATED inventory", py, sq, ["dealership_id"], {
        "closing_stock_units": ("StockUnits", 0), "stock_0-30_days": ("Stock0To30", 0),
        "stock_31-60_days": ("Stock31To60", 0), "stock_61-90_days": ("Stock61To90", 0),
        "stock_>90_days": ("StockOver90", 0), "aged_units": ("AgedUnits", 0),
        "aged_inventory_share_pct": ("AgedInventorySharePct", PCT_TOL), "avg_stock_age_days": ("AvgAgeDays", PCT_TOL)})

    out = pd.DataFrame(rows)
    RECON_PATH.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(RECON_PATH, index=False, float_format="%.6f", lineterminator="\n")
    return out


# =========================================================================== main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("AUTORETAIL_SQL_SERVER", r".\SQLEXPRESS"))
    ap.add_argument("--database", default=os.environ.get("AUTORETAIL_SQL_DATABASE", "AutoRetailAnalytics"))
    ap.add_argument("--driver")
    ap.add_argument("--steps", nargs="+", choices=STEPS, default=STEPS)
    ap.add_argument("--rebuild", action="store_true", help="drop dw tables and rpt views before 'schema'")
    a = ap.parse_args(argv)
    try:
        if "create" in a.steps:
            print("[create]")
            create_database(a.server, a.database, a.driver)
        with connect(connection_string(a.server, a.database, a.driver)) as conn:
            if "schema" in a.steps:
                print("[schema]")
                if a.rebuild:
                    run_sql_file(conn, SQL_DIR / "99_drop_dw_objects.sql")
                for f in ("01_schemas.sql", "02_staging_tables.sql", "03_dw_tables.sql", "04_dim_date.sql"):
                    run_sql_file(conn, SQL_DIR / f)
            if "stage" in a.steps:
                print("[stage]")
                stage(conn)
            if "transform" in a.steps:
                print("[transform]")
                for f in ("05_validate_staging.sql", "06_transform_load.sql", "07_analysis_views.sql", "08_reconciliation.sql"):
                    res = run_sql_file(conn, SQL_DIR / f)
                    for df in res:
                        if "Result" in df.columns:
                            print(f"    {int((df.Result == 'PASS').sum())} of {len(df)} checks passed")
            if "reconcile" in a.steps:
                print("[reconcile]")
                r = reconcile(conn)
                summary = r.groupby("area").passed.agg(["count", "sum"]).rename(columns={"count": "checks", "sum": "passed"})
                print(summary.to_string())
                if not r.passed.all():
                    print(f"FAILED {int((~r.passed).sum())} Python/SQL comparisons; see {RECON_PATH}", file=sys.stderr)
                    print(r[~r.passed].to_string(index=False), file=sys.stderr)
                    return 1
                print(f"  all {len(r)} Python/SQL comparisons agree -> {RECON_PATH.relative_to(ROOT).as_posix()}")
    except Exception as exc:  # pyodbc errors carry the SQL Server message
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
