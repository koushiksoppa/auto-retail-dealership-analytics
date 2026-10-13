"""Runtime validation of the Power BI model inside Power BI Desktop's own engine.

Steps (default: all):
  launch     open powerbi/AutoRetailAnalytics.pbip in Power BI Desktop and wait for the model
  refresh    full refresh from SQL Server (database built by python/sql_server_pipeline.py)
  validate   run DAX/DMV queries against the engine and compare with the Python outputs
  screenshot capture each report page from a per-page copy of the project (optional)
  close      close the Power BI Desktop instance this script opened

Results: reports/powerbi_validation.csv (one row per check: area, key, metric, expected,
actual, passed). Exit code 1 if any check fails. Requires Windows, Power BI Desktop and SQL Server.

Run:
  python python/validate_powerbi.py                          # launch, refresh, validate, close
  python python/validate_powerbi.py --port 60416 --steps validate   # against an already open model
  python python/validate_powerbi.py --screenshots            # also capture page images
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import yaml

import build_powerbi_project as bpp
from paths import CONFIG_DIR, DEALER_TABLE_DIR, MARKET_TABLE_DIR, ROOT

PS_SCRIPT = ROOT / "powerbi" / "scripts" / "PbiDesktop.ps1"
PBIP = ROOT / "powerbi" / f"{bpp.NAME}.pbip"
RESULT_PATH = ROOT / "reports" / "powerbi_validation.csv"
SCREENSHOT_DIR = ROOT / "powerbi" / "screenshots"
PCT_TOL, UNIT_TOL = 1e-4, 1e-3
OFFICIAL_TABLES = {t for t, s in bpp.TABLES.items() if s["class"] in ("OFFICIAL", "DERIVED")}
SIMULATED_TABLES = {t for t, s in bpp.TABLES.items() if s["class"] == "SIMULATED"}


def ps(action: str, **kw) -> dict:
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS_SCRIPT), "-Action", action]
    for k, v in kw.items():
        args += [f"-{k}", str(v)]
    out = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(f"PbiDesktop.ps1 {action} failed: {out.stderr.strip() or out.stdout.strip()}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def run_queries(port: int, queries: dict[str, str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        qf, of = Path(tmp) / "q.json", Path(tmp) / "r.json"
        qf.write_text(json.dumps(queries), encoding="utf-8")
        ps("Query", Port=port, QueryFile=qf, OutFile=of, TimeoutSeconds=600)
        return json.loads(of.read_text(encoding="utf-8-sig"))


def rows(res: dict, name: str) -> pd.DataFrame:
    r = res[name]
    if "error" in r:
        raise RuntimeError(f"query {name} failed: {r['error']}")
    df = pd.DataFrame(r["rows"])
    df.columns = [c.split("[")[-1].rstrip("]") for c in df.columns]   # 'Table[Col]' / '[Name]' -> 'Col'
    return df


# ----------------------------------------------------------------------------- queries
def build_queries(sim_start: str, sim_end: str) -> dict[str, str]:
    y1, m1, d1 = sim_start.split("-")
    y2, m2, d2 = sim_end.split("-")
    sim_dates = (f"FILTER ( ALL ( DimDate[CalendarDate] ), DimDate[CalendarDate] >= DATE ( {int(y1)}, {int(m1)}, {int(d1)} ) "
                 f"&& DimDate[CalendarDate] <= DATE ( {int(y2)}, {int(m2)}, {int(d2)} ) )")
    return {
        "tables": "SELECT [ID], [Name], [DataCategory] FROM $SYSTEM.TMSCHEMA_TABLES",
        "partitions": "SELECT [TableID], [Name], [State], [ErrorMessage] FROM $SYSTEM.TMSCHEMA_PARTITIONS",
        "measures": "SELECT [TableID], [Name], [State], [ErrorMessage] FROM $SYSTEM.TMSCHEMA_MEASURES",
        "relationships": "SELECT [FromTableID], [ToTableID], [IsActive] FROM $SYSTEM.TMSCHEMA_RELATIONSHIPS",
        "row_counts": "EVALUATE { (\"FactMarketRegistrations\", COUNTROWS ( FactMarketRegistrations )), "
                      "(\"FactMarketSegmentMonth\", COUNTROWS ( FactMarketSegmentMonth )), "
                      "(\"FactLeadFunnel\", COUNTROWS ( FactLeadFunnel )), (\"FactVehicleDelivery\", COUNTROWS ( FactVehicleDelivery )), "
                      "(\"FactInventorySnapshot\", COUNTROWS ( FactInventorySnapshot )), (\"FactActionTrigger\", COUNTROWS ( FactActionTrigger )) }",
        "segment": "EVALUATE SUMMARIZECOLUMNS ( DimVehicleSegment[SegmentCode], \"UnitsPrior\", [Market Registrations PP], "
                   "\"UnitsCurrent\", [Market Registrations CP], \"Growth\", [Market YoY Growth % (Period)], "
                   "\"EVPrior\", [EV Share PP %], \"EVCurrent\", [EV Share CP %], \"EVUnitsPrior\", [EV Registrations PP (Est.)], "
                   "\"EVUnitsCurrent\", [EV Registrations CP (Est.)] )",
        "portfolio_brands": "EVALUATE SUMMARIZECOLUMNS ( DimOEM[PortfolioBrand], TREATAS ( { TRUE () }, DimOEM[IsPortfolioBrand] ), "
                            "\"UnitsPrior\", [Registrations PP], \"UnitsCurrent\", [Registrations CP], \"Growth\", [YoY Growth % (Period)], "
                            "\"SegGrowth\", [Segment YoY Growth % (Period)], \"GapPp\", [Growth Gap vs Segment pp], "
                            "\"SharePrior\", [Market Share PP %], \"ShareCurrent\", [Market Share CP %], \"Bps\", [Share Change bps], "
                            "\"ConstShare\", [Constant-Share Registrations], \"RegGap\", [Registration Gap] )",
        "portfolio_cars": "EVALUATE ROW ( \"UnitsPrior\", [Portfolio Car Registrations PP], \"UnitsCurrent\", [Portfolio Car Registrations CP], "
                          "\"Growth\", [Portfolio Car YoY Growth %], \"GapPp\", [Portfolio Car Growth Gap vs PV pp], "
                          "\"SharePrior\", [Portfolio Car Share PP %], \"ShareCurrent\", [Portfolio Car Share CP %], "
                          "\"Bps\", [Portfolio Car Share Change bps], \"ConstShare\", [Portfolio Car Constant-Share Registrations], "
                          "\"RegGap\", [Portfolio Car Registration Gap] )",
        "oem": "EVALUATE SUMMARIZECOLUMNS ( DimOEM[OEMName], \"UnitsPrior\", [Registrations PP], \"UnitsCurrent\", [Registrations CP], "
               "\"Complete\", [Is Coverage Complete], \"SharePrior\", [Market Share PP % (Complete Coverage)], "
               "\"ShareCurrent\", [Market Share CP % (Complete Coverage)], \"Bps\", [Share Change bps (Complete Coverage)], "
               "\"Growth\", [YoY Growth % (Complete Coverage)] )",
        "monthly": "EVALUATE SUMMARIZECOLUMNS ( DimDate[YearMonth], DimVehicleSegment[SegmentCode], \"Units\", [Market Registrations], "
                   "\"UnitsPY\", [Market Registrations PY], \"YoY\", [Market YoY Growth % (Monthly)], \"EV\", [EV Share %] )",
        "separation": "EVALUATE ROW ( \"SegmentRegistrations\", [Segment Registrations], \"MarketShare\", [Market Share %], "
                      "\"MarketRegistrations\", [Market Registrations], \"EVShare\", [EV Share %], "
                      "\"ShareCP\", [Market Share CP %], \"Registrations\", [Registrations] )",
        "funnel": "EVALUATE SUMMARIZECOLUMNS ( DimDealership[DealershipId], \"Leads\", [Leads], \"Qualified\", [Qualified Leads], "
                  "\"TestDrives\", [Test Drives], \"Bookings\", [Bookings], \"Deliveries\", [Deliveries (Lead Cohort)], "
                  "\"TD2B\", [Test Drive to Booking %], \"L2D\", [Lead to Delivery %], \"SLA\", [First Contact SLA %] )",
        "funnel_all": "EVALUATE ROW ( \"Leads\", [Leads], \"Qualified\", [Qualified Leads], \"TestDrives\", [Test Drives], "
                      "\"Bookings\", [Bookings], \"Deliveries\", [Deliveries (Lead Cohort)], \"L2D\", [Lead to Delivery %] )",
        "inventory": f"EVALUATE SUMMARIZECOLUMNS ( DimDealership[DealershipId], {sim_dates}, \"Stock\", [Current Inventory Units], "
                     "\"Aged\", [Aged Inventory Units], \"AgedPct\", [Aged Inventory %], \"AvgAge\", [Average Stock Age (Days)], "
                     "\"DOS\", [Days of Supply], \"SellThrough\", [Sell-Through %], \"Turnover\", [Inventory Turnover (Annualised)], "
                     "\"Opening\", [Opening Stock Units], \"Receipts\", [Receipts Units], \"Delivered\", [Deliveries (Delivery Date)] )",
        "buckets": "EVALUATE SUMMARIZECOLUMNS ( DimDealership[DealershipId], FactInventorySnapshot[AgeingBucket], "
                   "\"Units\", [Current Inventory Units] )",
        "actions": "EVALUATE ROW ( \"Triggered\", [Actions Triggered], \"Checks\", [Rule Checks] )",
    }


# ----------------------------------------------------------------------------- comparison
class Checks:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, area, key, metric, expected, actual, tol=0.0, note=""):
        def num(v):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        e, a = num(expected), num(actual)
        if e is None and a is None:
            ok = True
        elif e is None or a is None:
            ok = False
        elif isinstance(e, float) and isinstance(a, float):
            ok = abs(e - a) <= tol
        else:
            ok = e == a
        self.rows.append({"area": area, "key": key, "metric": metric, "expected": e, "actual": a,
                          "passed": bool(ok), "note": note})

    def compare_frames(self, area, py: pd.DataFrame, pbi: pd.DataFrame, key: str, metrics: dict, scale: dict | None = None):
        """metrics: python column -> (pbi column, tolerance); scale: pbi column -> multiplier (fraction -> %)."""
        scale = scale or {}
        p, b = py.set_index(key), pbi.set_index(key)
        for k in sorted(set(p.index) | set(b.index), key=str):
            if k not in p.index or k not in b.index:
                self.add(area, k, "row present in Python and Power BI", True, False)
                continue
            for pc, (bc, tol) in metrics.items():
                av = b.loc[k, bc]
                av = None if av is None or (isinstance(av, float) and math.isnan(av)) else av * scale.get(bc, 1)
                self.add(area, k, pc, p.loc[k, pc], av, tol)

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows)


def compare(res: dict, sim_cfg: dict) -> pd.DataFrame:
    c = Checks()
    pct = {"scale": 100}

    # ---- model structure
    tables = rows(res, "tables")
    names = dict(zip(tables.ID, tables.Name))
    expected_tables = {bpp.MEASURE_TABLE, *bpp.TABLES}
    c.add("model", "tables", "table set", sorted(expected_tables), sorted(tables.Name))
    c.add("model", "DimDate", "marked as date table (DataCategory = Time)", "Time",
          tables.set_index("Name").loc["DimDate", "DataCategory"])
    parts = rows(res, "partitions")
    c.add("model", "partitions", "partitions in Ready state (State = 1) after refresh", len(parts), int((parts.State == 1).sum()))
    for r in parts[parts.ErrorMessage.notna() & (parts.ErrorMessage != "")].itertuples():
        c.add("model", r.Name, "partition error", "", r.ErrorMessage)
    meas = rows(res, "measures")
    dax_names = [m["name"] for m in bpp.parse_measures()]
    c.add("model", "measures", "measure count equals measures.dax", len(dax_names), len(meas))
    c.add("model", "measures", "measures without semantic errors (State = 1)", len(meas), int((meas.State == 1).sum()))
    for r in meas[meas.State != 1].itertuples():
        c.add("model", r.Name, "measure error", "", r.ErrorMessage)
    rel = rows(res, "relationships")
    c.add("model", "relationships", "relationship count", len(bpp.RELATIONSHIPS), len(rel))
    c.add("model", "relationships", "all relationships active", len(rel), int(rel.IsActive.astype(bool).sum()))
    cross = [(names[f], names[t]) for f, t in zip(rel.FromTableID, rel.ToTableID)
             if {names[f], names[t]} & OFFICIAL_TABLES and {names[f], names[t]} & SIMULATED_TABLES]
    c.add("separation", "relationships", "no relationship joins official and simulated tables", 0, len(cross),
          note="; ".join(f"{a}->{b}" for a, b in cross))

    rc = rows(res, "row_counts")
    rc.columns = ["table", "rows"]
    sim = ROOT / "data" / "simulated" / "dealership"
    expected_counts = {"FactLeadFunnel": len(pd.read_csv(sim / "fact_lead_funnel.csv")),
                       "FactVehicleDelivery": len(pd.read_csv(sim / "fact_vehicle_delivery.csv")),
                       "FactInventorySnapshot": len(pd.read_csv(sim / "fact_inventory_snapshot.csv")),
                       "FactActionTrigger": len(pd.read_csv(DEALER_TABLE_DIR / "action_triggers.csv")),
                       "FactMarketSegmentMonth": 32}
    for t, n in expected_counts.items():
        c.add("model", t, "row count", n, int(rc.set_index("table").loc[t, "rows"]))

    # ---- official market data
    seg = pd.read_csv(MARKET_TABLE_DIR / "segment_period_summary.csv")
    ev = pd.read_csv(MARKET_TABLE_DIR / "ev_period_summary.csv")
    py_seg = seg.merge(ev, on="segment", suffixes=("", "_ev"))
    c.compare_frames("segment growth & EV", py_seg, rows(res, "segment").rename(columns={"SegmentCode": "segment"}), "segment", {
        "units_prior": ("UnitsPrior", 0), "units_current": ("UnitsCurrent", 0), "yoy_growth_pct": ("Growth", PCT_TOL),
        "ev_share_prior_pct": ("EVPrior", PCT_TOL), "ev_share_current_pct": ("EVCurrent", PCT_TOL),
        "ev_units_est_prior": ("EVUnitsPrior", UNIT_TOL), "ev_units_est_current": ("EVUnitsCurrent", UNIT_TOL)},
        scale={"Growth": 100, "EVPrior": 100, "EVCurrent": 100})

    port = pd.read_csv(MARKET_TABLE_DIR / "portfolio_brand_performance.csv")
    metrics = {"units_prior": ("UnitsPrior", 0), "units_current": ("UnitsCurrent", 0), "yoy_growth_pct": ("Growth", PCT_TOL),
               "growth_gap_pp": ("GapPp", PCT_TOL), "share_prior_pct": ("SharePrior", PCT_TOL),
               "share_current_pct": ("ShareCurrent", PCT_TOL), "share_change_bps": ("Bps", 1e-2),
               "constant_share_units": ("ConstShare", UNIT_TOL), "registration_gap_units": ("RegGap", UNIT_TOL)}
    scale = {"Growth": 100, "SharePrior": 100, "ShareCurrent": 100, "SegGrowth": 100}
    brands = rows(res, "portfolio_brands").rename(columns={"PortfolioBrand": "brand"})
    c.compare_frames("portfolio brands", port[~port.is_aggregate],
                     brands, "brand", {**metrics, "segment_growth_pct": ("SegGrowth", PCT_TOL)}, scale)
    cars = rows(res, "portfolio_cars").assign(brand=bpp_aggregate_name())
    c.compare_frames("portfolio car aggregate", port[port.is_aggregate], cars, "brand", metrics, scale)

    oem = pd.read_csv(MARKET_TABLE_DIR / "oem_period_share.csv")
    pbi_oem = rows(res, "oem").rename(columns={"OEMName": "oem_canonical"})
    pbi_oem["Complete"] = pbi_oem.Complete.astype(int)
    oem["coverage_complete"] = oem.coverage_complete.astype(int)
    c.compare_frames("OEM share", oem.fillna({"units_prior": 0, "units_current": 0}),
                     pbi_oem.fillna({"UnitsPrior": 0, "UnitsCurrent": 0}), "oem_canonical", {
        "units_prior": ("UnitsPrior", 0), "units_current": ("UnitsCurrent", 0), "coverage_complete": ("Complete", 0),
        "share_prior_pct": ("SharePrior", PCT_TOL), "share_current_pct": ("ShareCurrent", PCT_TOL),
        "share_change_bps": ("Bps", 1e-2), "yoy_growth_pct": ("Growth", PCT_TOL)},
        scale={"SharePrior": 100, "ShareCurrent": 100, "Growth": 100})

    monthly = pd.read_csv(MARKET_TABLE_DIR / "segment_monthly_yoy.csv")
    m = rows(res, "monthly")
    m["key"] = m.SegmentCode + "|" + m.YearMonth
    cur = monthly.assign(key=monthly.segment + "|" + monthly.month_current)
    c.compare_frames("monthly (current months)", cur, m[m.key.isin(cur.key)], "key", {
        "units_current": ("Units", 0), "units_prior": ("UnitsPY", 0), "yoy_growth_pct": ("YoY", PCT_TOL),
        "ev_share_pct_current": ("EV", PCT_TOL)}, scale={"YoY": 100, "EV": 100})
    pri = monthly.assign(key=monthly.segment + "|" + monthly.month_prior)
    c.compare_frames("monthly (prior months)", pri, m[m.key.isin(pri.key)], "key", {
        "units_prior": ("Units", 0), "ev_share_pct_prior": ("EV", PCT_TOL)}, scale={"EV": 100})

    sep = rows(res, "separation").iloc[0]
    for k in ["SegmentRegistrations", "MarketShare", "MarketRegistrations", "EVShare", "ShareCP"]:
        c.add("separation", "PV + 2W context", f"[{k}] is BLANK when both segments are in context", None, sep[k])
    c.add("separation", "PV + 2W context", "[Registrations] still sums units (no share is formed)",
          float(seg.units_current.sum() + seg.units_prior.sum()), sep["Registrations"], 0)

    # ---- simulated dealership data
    funnel = pd.read_csv(DEALER_TABLE_DIR / "funnel_by_dealership.csv")
    follow = pd.read_csv(DEALER_TABLE_DIR / "followup_by_dealership.csv")
    pyf = funnel[funnel.dealership_id != "ALL"].merge(follow, on="dealership_id")
    c.compare_frames("SIMULATED funnel", pyf, rows(res, "funnel").rename(columns={"DealershipId": "dealership_id"}),
                     "dealership_id", {"leads": ("Leads", 0), "qualified": ("Qualified", 0), "test_drives": ("TestDrives", 0),
                                       "bookings": ("Bookings", 0), "deliveries": ("Deliveries", 0),
                                       "test_drive_to_booking_pct": ("TD2B", PCT_TOL), "lead_to_delivery_pct": ("L2D", PCT_TOL),
                                       "first_contact_sla_pct": ("SLA", PCT_TOL)},
                     scale={"TD2B": 100, "L2D": 100, "SLA": 100})
    c.compare_frames("SIMULATED funnel (all)", funnel[funnel.dealership_id == "ALL"],
                     rows(res, "funnel_all").assign(dealership_id="ALL"), "dealership_id",
                     {"leads": ("Leads", 0), "bookings": ("Bookings", 0), "deliveries": ("Deliveries", 0),
                      "lead_to_delivery_pct": ("L2D", PCT_TOL)}, scale={"L2D": 100})

    inv = pd.read_csv(DEALER_TABLE_DIR / "inventory_by_dealership.csv")
    c.compare_frames("SIMULATED inventory", inv, rows(res, "inventory").rename(columns={"DealershipId": "dealership_id"}),
                     "dealership_id", {"closing_stock_units": ("Stock", 0), "aged_units": ("Aged", 0),
                                       "aged_inventory_share_pct": ("AgedPct", PCT_TOL), "avg_stock_age_days": ("AvgAge", PCT_TOL),
                                       "days_of_supply": ("DOS", 1e-3), "sell_through_pct": ("SellThrough", PCT_TOL),
                                       "inventory_turnover_annualised": ("Turnover", 1e-3),
                                       "opening_stock_units": ("Opening", 0), "receipts_units": ("Receipts", 0),
                                       "deliveries_units": ("Delivered", 0)},
                     scale={"AgedPct": 100, "SellThrough": 100})
    b = rows(res, "buckets")
    b["key"] = b.DealershipId + "|" + b.AgeingBucket
    long = inv.melt(id_vars="dealership_id", value_vars=[x for x in inv.columns if x.startswith("stock_") and x.endswith("_days")],
                    var_name="bucket", value_name="units")
    long["key"] = long.dealership_id + "|" + long.bucket.str[len("stock_"):-len("_days")]
    long = long[long.units > 0]                                   # Power BI omits empty buckets
    c.compare_frames("SIMULATED ageing buckets", long, b, "key", {"units": ("Units", 0)})

    acts = pd.read_csv(DEALER_TABLE_DIR / "action_triggers.csv")
    a = rows(res, "actions").iloc[0]
    c.add("SIMULATED actions", "all", "actions triggered", int(acts.breached.sum()), a["Triggered"])
    c.add("SIMULATED actions", "all", "rule checks", len(acts), a["Checks"])
    return c.frame()


def bpp_aggregate_name() -> str:
    from auto_retail_analysis import PORTFOLIO_CARS
    return PORTFOLIO_CARS


# ----------------------------------------------------------------------------- screenshots
PAGE_BACKGROUND = (245, 245, 243)   # page background colour set by powerbi/theme/auto_retail_theme.json


def crop_to_report_page(path: Path, margin: int = 2) -> bool:
    """Crop a Power BI Desktop window capture to the report page (drops the ribbon and side panes).

    The page is located as the bounding box of the theme's page-background colour inside the canvas
    (left of the Filters/Visualizations panes). Returns False and leaves the file untouched when no
    plausible page is found, e.g. for a minimised window."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    w, h = im.size
    canvas = (0, int(h * 0.2), int(w * 0.62), int(h * 0.95))
    region = im.crop(canvas)
    px, rw, rh = region.load(), region.size[0], region.size[1]
    xs = [x for y in range(0, rh, 2) for x in range(rw) if px[x, y] == PAGE_BACKGROUND]
    ys = [y for y in range(rh) for x in range(0, rw, 4) if px[x, y] == PAGE_BACKGROUND]
    if not xs or not ys:
        return False
    box = (max(min(xs) - margin, 0) + canvas[0], max(min(ys) - margin, 0) + canvas[1],
           min(max(xs) + 1 + margin, rw) + canvas[0], min(max(ys) + 1 + margin, rh) + canvas[1])
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw < w * 0.3 or bh < h * 0.3 or not 1.4 <= bw / bh <= 2.1:   # not a 16:9-ish report page
        return False
    im.crop(box).save(path, format="PNG")
    return True


def screenshots(pages: list[str], only: list[str] | None = None) -> list[Path]:
    """Open a temporary copy of the project per page (activePageName set), refresh, capture, close."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for i, page in enumerate(pages, start=1):
        if only and page not in only:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            bpp.build(out_root=Path(tmp), active_page=page)        # writes the .pbip into tmp as well
            info = ps("Launch", Path=Path(tmp) / PBIP.name, TimeoutSeconds=600)
            try:
                ps("Refresh", Port=info["port"], TimeoutSeconds=900)
                run_queries(info["port"], {"ready": "EVALUATE ROW(\"x\", [Leads])"})
                import time
                # Visuals render asynchronously after the refresh; capture until two consecutive
                # images are identical (render settled) or the attempts run out.
                target = SCREENSHOT_DIR / f"{i:02d}_{page}.png"
                previous = None
                for _ in range(12):
                    time.sleep(20)
                    ps("Screenshot", ProcessId=info["pid"], OutFile=target)
                    current = target.read_bytes()
                    if current == previous and len(current) > 80_000:
                        break
                    previous = current
                else:
                    print(f"[screenshot] WARNING: {page} did not settle; last capture kept", file=sys.stderr)
                if not crop_to_report_page(target):
                    print(f"[screenshot] WARNING: report page not found in {target.name}; kept full window", file=sys.stderr)
                out.append(target)
            finally:
                ps("Close", ProcessId=info["pid"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", nargs="*", default=["launch", "refresh", "validate", "close"],
                    choices=["launch", "refresh", "validate", "close"])
    ap.add_argument("--port", type=int, help="port of an already open Power BI Desktop model")
    ap.add_argument("--pid", type=int, help="process id to close (with --port)")
    ap.add_argument("--screenshots", action="store_true", help="also capture each report page")
    ap.add_argument("--pages", nargs="+", help="with --screenshots: only these page names (e.g. executive_overview)")
    a = ap.parse_args(argv)
    sim_cfg = yaml.safe_load((CONFIG_DIR / "simulation.yaml").read_text(encoding="utf-8"))
    port, pid = a.port, a.pid
    try:
        if "launch" in a.steps:
            info = ps("Launch", Path=PBIP, TimeoutSeconds=600)
            port, pid = info["port"], info["pid"]
            print(f"[launch] Power BI Desktop pid {pid}, engine port {port}, {info['tables']} tables loaded")
        if port is None and {"refresh", "validate"} & set(a.steps):
            raise RuntimeError("no engine port: use --steps launch ... or pass --port")
        if "refresh" in a.steps:
            ps("Refresh", Port=port, TimeoutSeconds=900)
            print("[refresh] full refresh from SQL Server completed")
        failed = 0
        if "validate" in a.steps:
            res = run_queries(port, build_queries(str(sim_cfg["start_date"]), str(sim_cfg["end_date"])))
            errors = {k: v["error"] for k, v in res.items() if "error" in v}
            if errors:
                for k, e in errors.items():
                    print(f"[validate] query {k} FAILED: {e}", file=sys.stderr)
                return 1
            out = compare(res, sim_cfg)
            RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
            out.to_csv(RESULT_PATH, index=False, float_format="%.6f", lineterminator="\n")
            summary = out.groupby("area").passed.agg(["count", "sum"]).rename(columns={"count": "checks", "sum": "passed"})
            print(summary.to_string())
            failed = int((~out.passed).sum())
            if failed:
                print(out[~out.passed].to_string(index=False), file=sys.stderr)
            print(f"[validate] {len(out) - failed} of {len(out)} checks passed -> {RESULT_PATH.relative_to(ROOT).as_posix()}")
        if "close" in a.steps and pid:
            ps("Close", ProcessId=pid)
            print(f"[close] closed Power BI Desktop pid {pid}")
        if a.screenshots:
            pages = [p.name for p in bpp.build_pages(yaml.safe_load((CONFIG_DIR / "market_analysis.yaml").read_text(encoding="utf-8")))]
            for path in screenshots(pages, only=a.pages):
                print(f"[screenshot] {path.relative_to(ROOT).as_posix()}")
        return 1 if failed else 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
