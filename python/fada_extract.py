"""Extract monthly retail tables from the official FADA press-release PDFs.

Inputs  : data/external/fada/raw/fada_retail_YYYY-MM.pdf (immutable, checksummed
          against data/external/fada/source_manifest.csv)
Outputs : data/processed/fada/
            fada_oem_monthly.csv       OEM rows from the 2W and PV annexures
            fada_category_monthly.csv  "All India Vehicle Retail Data for <Mon'YY>" table
            fada_fuel_mix_monthly.csv  2W and PV fuel-mix percentages
            fada_release_metadata.csv  collation date and RTO coverage per release
            extraction_checks.csv      every validation check and its result

Each release prints the current month next to the same month one year
earlier, so eight 2026 releases provide both Feb-Sep 2026 and Feb-Sep 2025.

Run:  python python/fada_extract.py
The script exits non-zero if any validation check fails.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pdfplumber

from paths import FADA_MANIFEST, FADA_PROCESSED_DIR, FADA_RAW_DIR

MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
MONTH_TOKEN = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'\d{2}"
NUM = r"(?:\d{1,3}(?:,\d{2})*,\d{3}|\d{1,3}|-)"  # Indian digit grouping, or '-' for nil
PCT = r"-?\d+(?:\.\d+)?%"

# Segments whose OEM annexure is extracted. Header text -> segment code.
OEM_TABLE_HEADERS = {"Two-Wheeler OEM": "2W", "PV OEM": "PV"}
CATEGORY_CODES = {"2W": "2W", "3W": "3W", "PV": "PV", "TRAC": "TRAC",
                  "CE": "CE", "CV": "CV", "Total": "TOTAL"}
# Construction Equipment is labelled "CE" (Feb-Apr'26), "Wheeled CE" (May'26) and "Wheeled - CE" (Jun'26 on).
CE_LABEL = re.compile(r"^Wheeled\s*-?\s*CE$")
# Fuel tables are printed side by side; the PV header is sometimes split over three lines
# ("Passenger" / "<months> Tractor <months>" / "Vehicle"), so anchor on the paired table name.
FUEL_HEADERS = {r"Two-Wheeler (MON) (MON) (MON) Three-Wheeler": "2W",
                r"(?:Passenger Vehicle )?(MON) (MON) (MON) Tractor": "PV"}
# Indentation (points) beyond the table's left edge that marks a member row of an OEM group.
MEMBER_INDENT_PTS = 4.0
SHARE_TOLERANCE_PP = 0.011  # published shares are rounded to 2 dp (PV total to 1 dp)

NOISE_PATTERNS = [
    r"^FEDERATION OF AUTOMOBILE", r"^804-805-806", r"^New Delhi", r"^T \+91", r"^E fada@",
    r"^CIN ", r"^Media Contact", r"^www\.fada\.in", r"^Market Share", r"^Market Market",
    r"^\(%\)", r"^Share \(%\)", r"^Sep'|^Aug'|^Jul'|^Jun'|^May'|^Apr'|^Mar'|^Feb'|^Jan'",
]


class ExtractionError(RuntimeError):
    """Raised when a PDF does not have the structure the parser expects."""


@dataclass
class Line:
    text: str
    x0: float
    page: int


def norm(text: str) -> str:
    return (text.replace("’", "'").replace("‘", "'").replace("−", "-")
            .replace("–", "-").replace(" ", " ").strip())


def month_token_to_period(token: str) -> str:
    mon, yy = token.split("'")
    return f"20{yy}-{MONTHS[mon]:02d}"


def parse_units(token: str) -> tuple[int, bool]:
    """Return (units, reported_as_dash). FADA prints '-' (with a 0.00% share) for nil."""
    if token == "-":
        return 0, True
    return int(token.replace(",", "")), False


def parse_pct(token: str) -> float:
    return float(token.rstrip("%"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_lines(pdf_path: Path) -> list[Line]:
    lines: list[Line] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for raw in page.extract_text_lines():
                text = norm(raw["text"])
                if text:
                    lines.append(Line(text, float(raw["x0"]), page_no))
    return lines


def is_noise(text: str) -> bool:
    return any(re.match(p, text) for p in NOISE_PATTERNS)


# --------------------------------------------------------------------------- metadata
def parse_metadata(lines: list[Line], release_month: str) -> dict:
    blob = " ".join(l.text for l in lines)
    collated = re.search(r"collated as on (\d{2})\.(\d{2})\.(\d{2})", blob)
    rtos = re.search(r"from ([\d,]+) out of ([\d,]+) RTOs", blob)
    return {
        "release_month": release_month,
        "data_collated_as_on": (f"20{collated.group(3)}-{collated.group(2)}-{collated.group(1)}"
                                if collated else None),
        "rtos_reporting": int(rtos.group(1).replace(",", "")) if rtos else None,
        "rtos_total": int(rtos.group(2).replace(",", "")) if rtos else None,
    }


# --------------------------------------------------------------------------- category table
def parse_category_table(lines: list[Line], release_month: str, source_file: str) -> list[dict]:
    header_re = re.compile(rf"^CATEGORY ({MONTH_TOKEN}) ({MONTH_TOKEN}) ({MONTH_TOKEN}) MoM% YoY%$")
    for i, line in enumerate(lines):
        m = header_re.match(line.text)
        if not m:
            continue
        cur, prev, prior = (month_token_to_period(t) for t in m.groups())
        if cur != release_month:
            continue
        rows = []
        row_re = re.compile(rf"^(2W|3W|PV|TRAC|Wheeled\s*-?\s*CE|CE|CV|Total) ({NUM}) ({NUM}) ({NUM}) ")
        for nxt in lines[i + 1:]:
            r = row_re.match(nxt.text + " ")
            if r:
                code = "CE" if CE_LABEL.match(r.group(1)) else CATEGORY_CODES[r.group(1)]
                for period, role, tok in ((cur, "current", r.group(2)), (prev, "previous_month", r.group(3)),
                                          (prior, "prior_year", r.group(4))):
                    units, dash = parse_units(tok)
                    rows.append({"release_month": release_month, "period_month": period,
                                 "value_role": role, "category": code, "units": units,
                                 "reported_as_dash": dash, "source_file": source_file,
                                 "source_page": nxt.page})
                if code == "TOTAL":
                    return rows
        raise ExtractionError(f"{source_file}: category table has no Total row")
    raise ExtractionError(f"{source_file}: monthly category table for {release_month} not found")


# --------------------------------------------------------------------------- fuel mix
def parse_fuel_tables(lines: list[Line], release_month: str, source_file: str) -> list[dict]:
    rows: list[dict] = []
    for header, segment in FUEL_HEADERS.items():
        header_re = re.compile("^" + header.replace("MON", MONTH_TOKEN) + r"\b")
        start = next((i for i, l in enumerate(lines) if (m := header_re.match(l.text))
                      and month_token_to_period(m.group(1)) == release_month), None)
        if start is None:
            raise ExtractionError(f"{source_file}: monthly {segment} fuel table not found")
        periods = [month_token_to_period(t) for t in header_re.match(lines[start].text).groups()]
        roles = ["current", "previous_month", "prior_year"]
        row_re = re.compile(rf"^([A-Za-z/]+) ({PCT}) ({PCT}) ({PCT})")
        body = [l for l in lines[start + 1:start + 30] if not is_noise(l.text)][:12]  # tables can span a page break
        for nxt in body:
            r = row_re.match(nxt.text)
            if not r:
                continue
            fuel = r.group(1).upper()
            if fuel == "TOTAL":
                break
            for period, role, tok in zip(periods, roles, r.groups()[1:]):
                rows.append({"release_month": release_month, "period_month": period, "value_role": role,
                             "segment": segment, "fuel": fuel, "share_pct": parse_pct(tok),
                             "source_file": source_file, "source_page": nxt.page})
        else:
            raise ExtractionError(f"{source_file}: {segment} fuel table has no Total row")
    return rows


# --------------------------------------------------------------------------- OEM tables
def parse_oem_table(lines: list[Line], header: str, segment: str, release_month: str,
                    source_file: str) -> list[dict]:
    header_re = re.compile(rf"^{re.escape(header)} ({MONTH_TOKEN}) ({MONTH_TOKEN})$")
    start = None
    for i, line in enumerate(lines):
        m = header_re.match(line.text)
        if m and month_token_to_period(m.group(1)) == release_month:
            start = i
            cur, prior = (month_token_to_period(t) for t in m.groups())
            break
    if start is None:
        raise ExtractionError(f"{source_file}: monthly {segment} OEM table for {release_month} not found")

    row_re = re.compile(rf"^(.*?)\s*({NUM}) ({PCT}) ({NUM}) ({PCT})$")
    table_x0 = lines[start].x0
    parsed: list[dict] = []
    pending_name: list[str] = []
    current_group: str | None = None
    for line in lines[start + 1:]:
        text = line.text
        if is_noise(text):
            continue
        m = row_re.match(text)
        if not m:
            if text.startswith(("Source:", "Disclaimer")):
                raise ExtractionError(f"{source_file}: {segment} OEM table ended before Total row")
            if parsed and parsed[-1]["oem_raw"] == "":  # name printed after its numbers
                parsed[-1]["oem_raw"] = " ".join(pending_name + [text]).strip()
                pending_name = []
            else:
                pending_name.append(text)
            continue
        name = " ".join(pending_name + [m.group(1)]).strip()
        pending_name = []
        cur_units, cur_dash = parse_units(m.group(2))
        pri_units, pri_dash = parse_units(m.group(4))
        if name == "Total":
            level = "total"
        elif name.lower().startswith("others"):
            level = "others"
        elif line.x0 > table_x0 + MEMBER_INDENT_PTS:
            level = "member"
        else:
            level = "top"
        if level == "top":
            current_group = name
        if level == "member" and current_group is None:
            raise ExtractionError(f"{source_file}: member row '{name}' has no parent group")
        for period, units, dash, share in ((cur, cur_units, cur_dash, m.group(3)),
                                           (prior, pri_units, pri_dash, m.group(5))):
            parsed.append({
                "release_month": release_month, "period_month": period,
                "value_role": "current" if period == cur else "prior_year",
                "segment": segment, "oem_raw": name, "row_level": level,
                "parent_oem_raw": current_group if level == "member" else None,
                "units": units, "reported_as_dash": dash,
                "published_share_pct": parse_pct(share),
                "source_file": source_file, "source_page": line.page,
            })
        if level == "total":
            # Keep row order stable: emit both periods for each row, then stop.
            return parsed
    raise ExtractionError(f"{source_file}: {segment} OEM table has no Total row")


# --------------------------------------------------------------------------- validation
def validate(oem: pd.DataFrame, cat: pd.DataFrame, fuel: pd.DataFrame) -> pd.DataFrame:
    checks: list[dict] = []

    def add(check: str, key: str, expected, actual, passed: bool, note: str = "") -> None:
        checks.append({"check": check, "key": key, "expected": expected, "actual": actual,
                       "passed": bool(passed), "note": note})

    # 1. Top-level OEM rows + Others reconcile to the table Total, per segment and period.
    for (seg, period), g in oem.groupby(["segment", "period_month"]):
        total = g.loc[g.row_level == "total", "units"]
        add("oem_rows_sum_to_table_total", f"{seg}|{period}", int(total.sum()),
            int(g.loc[g.row_level.isin(["top", "others"]), "units"].sum()),
            len(total) == 1 and total.iloc[0] == g.loc[g.row_level.isin(["top", "others"]), "units"].sum())

    # 2. Group rows equal the sum of their member rows.
    members = oem[oem.row_level == "member"]
    for (seg, period, parent), g in members.groupby(["segment", "period_month", "parent_oem_raw"]):
        parent_units = oem.loc[(oem.segment == seg) & (oem.period_month == period)
                               & (oem.oem_raw == parent) & (oem.row_level == "top"), "units"]
        add("group_equals_sum_of_members", f"{seg}|{period}|{parent}", int(parent_units.sum()),
            int(g.units.sum()), len(parent_units) == 1 and parent_units.iloc[0] == g.units.sum())

    # 3. Recomputed share matches the share printed by FADA (rounding tolerance).
    totals = oem[oem.row_level == "total"].set_index(["segment", "period_month"])["units"]
    for r in oem[oem.row_level != "total"].itertuples():
        denom = totals.loc[(r.segment, r.period_month)]
        recomputed = round(r.units / denom * 100, 4)
        add("recomputed_share_matches_published", f"{r.segment}|{r.period_month}|{r.oem_raw}",
            r.published_share_pct, recomputed,
            abs(recomputed - r.published_share_pct) <= SHARE_TOLERANCE_PP)

    # 4. OEM table total equals the category summary table for the same release and period.
    for r in oem[oem.row_level == "total"].itertuples():
        match = cat[(cat.release_month == r.release_month) & (cat.period_month == r.period_month)
                    & (cat.category == r.segment)]
        add("oem_total_equals_category_table", f"{r.segment}|{r.period_month}",
            int(match.units.iloc[0]) if len(match) else None, int(r.units),
            len(match) == 1 and match.units.iloc[0] == r.units)

    # 5. Category rows add up to the published Total row.
    for (rel, period, role), g in cat.groupby(["release_month", "period_month", "value_role"]):
        total = g.loc[g.category == "TOTAL", "units"]
        parts = g.loc[g.category != "TOTAL", "units"].sum()
        add("categories_sum_to_total", f"{rel}|{period}|{role}", int(total.sum()), int(parts),
            len(total) == 1 and total.iloc[0] == parts)

    # 6. Fuel mix sums to ~100% (published percentages are individually rounded).
    for (rel, seg, period), g in fuel.groupby(["release_month", "segment", "period_month"]):
        s = round(g.share_pct.sum(), 2)
        add("fuel_mix_sums_to_100", f"{rel}|{seg}|{period}", 100.0, s, abs(s - 100) <= 0.1)

    # 7. One OEM table per segment and period (no duplicate coverage across releases).
    dup = oem.groupby(["segment", "period_month", "oem_raw", "row_level"]).size()
    add("no_duplicate_oem_rows", "all", 0, int((dup > 1).sum()), (dup > 1).sum() == 0)

    return pd.DataFrame(checks)


def revisions(cat: pd.DataFrame) -> pd.DataFrame:
    """Compare the same category-month printed in different releases (informational)."""
    piv = cat.pivot_table(index=["category", "period_month"], columns="release_month",
                          values="units", aggfunc="first")
    out = []
    for (category, period), row in piv.iterrows():
        vals = row.dropna()
        if len(vals) > 1 and vals.nunique() > 1:
            out.append({"category": category, "period_month": period,
                        "values_by_release": "; ".join(f"{k}={int(v)}" for k, v in vals.items())})
    return pd.DataFrame(out, columns=["category", "period_month", "values_by_release"])


# --------------------------------------------------------------------------- driver
def verify_checksums(manifest: pd.DataFrame, raw_dir: Path) -> None:
    for r in manifest.itertuples():
        path = raw_dir / r.local_file
        if not path.exists():
            raise ExtractionError(f"Missing source file {path}. Run: python python/fetch_fada_sources.py")
        actual = sha256(path)
        if actual != r.sha256:
            raise ExtractionError(f"Checksum mismatch for {path.name}: manifest {r.sha256}, file {actual}")


def extract_release(pdf_path: Path, release_month: str) -> dict[str, list[dict]]:
    lines = read_lines(pdf_path)
    name = pdf_path.name
    oem_rows: list[dict] = []
    for header, seg in OEM_TABLE_HEADERS.items():
        oem_rows += parse_oem_table(lines, header, seg, release_month, name)
    return {"oem": oem_rows, "category": parse_category_table(lines, release_month, name),
            "fuel": parse_fuel_tables(lines, release_month, name),
            "meta": [parse_metadata(lines, release_month) | {"source_file": name}]}


def run(raw_dir: Path = FADA_RAW_DIR, out_dir: Path = FADA_PROCESSED_DIR,
        manifest_path: Path = FADA_MANIFEST) -> pd.DataFrame:
    manifest = pd.read_csv(manifest_path, dtype=str)
    verify_checksums(manifest, raw_dir)
    acc: dict[str, list[dict]] = {"oem": [], "category": [], "fuel": [], "meta": []}
    for r in manifest.sort_values("release_month").itertuples():
        for k, v in extract_release(raw_dir / r.local_file, r.release_month).items():
            acc[k] += v
    oem, cat, fuel = (pd.DataFrame(acc[k]) for k in ("oem", "category", "fuel"))
    checks = validate(oem, cat, fuel)

    out_dir.mkdir(parents=True, exist_ok=True)
    oem.to_csv(out_dir / "fada_oem_monthly.csv", index=False)
    cat.to_csv(out_dir / "fada_category_monthly.csv", index=False)
    fuel.to_csv(out_dir / "fada_fuel_mix_monthly.csv", index=False)
    pd.DataFrame(acc["meta"]).to_csv(out_dir / "fada_release_metadata.csv", index=False)
    checks.to_csv(out_dir / "extraction_checks.csv", index=False)
    revisions(cat).to_csv(out_dir / "category_revisions_across_releases.csv", index=False)
    return checks


def main() -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    checks = run()
    failed = checks[~checks.passed]
    summary = checks.groupby("check").passed.agg(["count", "sum"])
    print(summary.rename(columns={"count": "checks", "sum": "passed"}).to_string())
    if len(failed):
        print(f"\nFAILED {len(failed)} extraction checks:\n{failed.to_string(index=False)}", file=sys.stderr)
        return 1
    print(f"\nAll {len(checks)} extraction checks passed. Outputs in {FADA_PROCESSED_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
