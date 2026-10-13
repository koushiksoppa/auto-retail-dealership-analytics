"""Static checks of the Power BI specification. The DAX is NOT executed here (that needs
Power BI Desktop); these tests only prove internal consistency with the SQL model."""
import json
import re

import pytest

from paths import ROOT

DAX = (ROOT / "powerbi" / "measures" / "measures.dax").read_text(encoding="utf-8")
DDL = (ROOT / "sql" / "03_dw_tables.sql").read_text(encoding="utf-8")
DEF_RE = re.compile(r"^(?!VAR\b|RETURN\b)([A-Za-z][^=\n\[]*?)\s*=(?!=)", re.MULTILINE)  # names never contain "["


def measure_definitions() -> dict[str, str]:
    code = "\n".join(l for l in DAX.splitlines() if not l.lstrip().startswith("//"))
    matches = list(DEF_RE.finditer(code))
    return {m.group(1).strip(): code[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(code)]
            for i, m in enumerate(matches)}


def ddl_columns() -> dict[str, set[str]]:
    tables = {}
    for m in re.finditer(r"CREATE TABLE dw\.(\w+) \((.*?)\n\);", DDL, re.DOTALL):
        cols = set()
        for line in m.group(2).splitlines():
            c = re.match(r"\s+([A-Z]\w*)\s+(?:AS\b|INT|TINYINT|SMALLINT|BIGINT|BIT|DATE|DATETIME2|DECIMAL|NVARCHAR|NCHAR|VARCHAR|CHAR)", line)
            if c and c.group(1) != "CONSTRAINT":
                cols.add(c.group(1))
        tables[m.group(1)] = cols
    return tables


def test_measures_parse_and_are_unique():
    names = [m.group(1).strip() for m in DEF_RE.finditer(
        "\n".join(l for l in DAX.splitlines() if not l.lstrip().startswith("//")))]
    assert len(names) >= 60
    assert len(names) == len(set(names)), "duplicate measure names"


def test_every_measure_reference_is_defined():
    defs = measure_definitions()
    for name, body in defs.items():
        for ref in re.findall(r"(?<![\w\]'])\[([^\]]+)\]", body):
            assert ref in defs, f"{name} references undefined measure [{ref}]"


def test_every_column_reference_exists_in_model():
    tables = ddl_columns()
    for name, body in measure_definitions().items():
        for table, col in re.findall(r"\b([A-Za-z_]\w*)\[([^\]]+)\]", body):
            assert table in tables, f"{name}: unknown table {table}"
            assert col in tables[table], f"{name}: unknown column {table}[{col}]"


def test_required_measures_present():
    defs = measure_definitions()
    for m in ["Registrations", "Registrations PP", "YoY Growth % (Period)", "Market Share %", "Share Change pp",
              "Share Change bps", "Constant-Share Registrations", "Registration Gap", "Growth Gap vs Segment pp",
              "EV Registrations (Est.)", "EV Share %", "Leads", "Bookings", "Lead to Delivery %",
              "Deliveries (Delivery Date)", "Current Inventory Units", "Aged Inventory Units", "Aged Inventory %",
              "Inventory Ageing %"]:
        assert m in defs, m


def test_growth_measures_guard_against_zero_prior():
    defs = measure_definitions()
    for m in ["YoY Growth % (Monthly)", "YoY Growth % (Period)", "Segment YoY Growth % (Period)"]:
        assert "ISBLANK" in defs[m], f"{m} would return -100% when the prior value is blank"


def test_catalog_and_guide_cover_pages_and_status():
    guide = (ROOT / "powerbi" / "POWERBI_BUILD_GUIDE.md").read_text(encoding="utf-8")
    for page in ["Executive Overview", "OEM & Market Share", "EV Analytics", "Portfolio Opportunity",
                 "Dealership Operations (Simulated)"]:
        assert page in guide
    pbix = list((ROOT / "powerbi").rglob("*.pbix"))
    if pbix:   # a .pbix may only appear after it was really saved from Power BI Desktop
        assert "Not created" not in guide, "guide still says the .pbix is not created"
    else:
        assert "Not created: one manual step" in guide
    catalog = (ROOT / "powerbi" / "MEASURE_CATALOG.md").read_text(encoding="utf-8")
    for word in ["OFFICIAL", "SIMULATED", "DERIVED", "Numerator", "Filter"]:
        assert word in catalog


def test_theme_is_valid_json():
    theme = json.loads((ROOT / "powerbi" / "theme" / "auto_retail_theme.json").read_text(encoding="utf-8"))
    assert len(theme["dataColors"]) == 8
