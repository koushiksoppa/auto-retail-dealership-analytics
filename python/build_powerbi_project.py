"""Generate the Power BI Project (PBIP) for the Auto Retail report.

Output (Power BI Desktop opens the .pbip directly; File > Save As produces a .pbix):
    powerbi/AutoRetailAnalytics.pbip
    powerbi/AutoRetailAnalytics.SemanticModel/   semantic model in TMDL
    powerbi/AutoRetailAnalytics.Report/          report in PBIR (one JSON file per page / visual)

Inputs (nothing is typed in by hand):
    sql/03_dw_tables.sql            table and column definitions (types, computed columns)
    powerbi/measures/measures.dax   all DAX measures and their display folders
    powerbi/theme/auto_retail_theme.json
    config/market_analysis.yaml     period labels used in page subtitles

Run:  python python/build_powerbi_project.py
The output is deterministic: the same inputs always produce identical files.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path

import yaml

from paths import CONFIG_DIR, ROOT

PBI_DIR = ROOT / "powerbi"
NAME = "AutoRetailAnalytics"
SM_DIR = PBI_DIR / f"{NAME}.SemanticModel"
RP_DIR = PBI_DIR / f"{NAME}.Report"
MEASURE_TABLE = "_Measures"
THEME_FILE = "auto_retail_theme.json"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
PLATFORM_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json"
NS = uuid.UUID("5d0f6b8e-3c1a-4f7e-9b2d-a1c3e5f7a9b1")  # namespace for deterministic ids

# ----------------------------------------------------------------------------- model specification
OFFICIAL, SIMULATED, DERIVED, SHARED = "OFFICIAL", "SIMULATED", "DERIVED", "SHARED"
TABLES: dict[str, dict] = {
    "DimDate": {"class": SHARED, "desc": "Calendar (day grain); marked as the date table."},
    "DimVehicleSegment": {"class": OFFICIAL, "desc": "FADA segments used: PV and 2W."},
    "DimOEM": {"class": OFFICIAL, "desc": "Canonical OEM within a segment (from config/oem_mapping.csv)."},
    "AnalysisPeriod": {"class": OFFICIAL, "desc": "Configured comparison periods (disconnected)."},
    "FactMarketRegistrations": {"class": OFFICIAL, "desc": "FADA retail registrations, month x OEM (top-level and Others rows)."},
    "FactMarketSegmentMonth": {"class": OFFICIAL, "desc": "FADA published segment total and EV share, month x segment."},
    "MarketAttention": {"class": DERIVED, "desc": "Portfolio attention flags computed by python/auto_retail_analysis.py."},
    "DimDealership": {"class": SIMULATED, "desc": "SIMULATED synthetic dealerships."},
    "DimSalesperson": {"class": SIMULATED, "desc": "SIMULATED synthetic salespeople."},
    "DimVehicleModel": {"class": SIMULATED, "desc": "SIMULATED generic vehicle models."},
    "FactLeadFunnel": {"class": SIMULATED, "desc": "SIMULATED leads with stage dates (lead cohort)."},
    "FactVehicleDelivery": {"class": SIMULATED, "desc": "SIMULATED deliveries."},
    "FactVehicleInventory": {"class": SIMULATED, "desc": "SIMULATED stocked vehicles."},
    "FactInventorySnapshot": {"class": SIMULATED, "desc": "SIMULATED month-end stock snapshots."},
    "FactActionTrigger": {"class": SIMULATED, "desc": "SIMULATED management action rule checks (illustrative thresholds)."},
}
# (from table, from column, to table, to column). Deliberately absent: DimOEM->DimVehicleSegment,
# DimSalesperson->DimDealership (would create ambiguous paths) and any official<->simulated link.
RELATIONSHIPS = [
    ("FactMarketRegistrations", "MonthDateKey", "DimDate", "DateKey"),
    ("FactMarketRegistrations", "OEMKey", "DimOEM", "OEMKey"),
    ("FactMarketRegistrations", "SegmentKey", "DimVehicleSegment", "SegmentKey"),
    ("FactMarketSegmentMonth", "MonthDateKey", "DimDate", "DateKey"),
    ("FactMarketSegmentMonth", "SegmentKey", "DimVehicleSegment", "SegmentKey"),
    ("FactLeadFunnel", "LeadDateKey", "DimDate", "DateKey"),
    ("FactLeadFunnel", "DealershipId", "DimDealership", "DealershipId"),
    ("FactLeadFunnel", "SalespersonId", "DimSalesperson", "SalespersonId"),
    ("FactLeadFunnel", "ModelId", "DimVehicleModel", "ModelId"),
    ("FactVehicleDelivery", "DeliveryDateKey", "DimDate", "DateKey"),
    ("FactVehicleDelivery", "DealershipId", "DimDealership", "DealershipId"),
    ("FactVehicleDelivery", "ModelId", "DimVehicleModel", "ModelId"),
    ("FactVehicleDelivery", "SalespersonId", "DimSalesperson", "SalespersonId"),
    ("FactInventorySnapshot", "SnapshotDateKey", "DimDate", "DateKey"),
    ("FactInventorySnapshot", "DealershipId", "DimDealership", "DealershipId"),
    ("FactInventorySnapshot", "ModelId", "DimVehicleModel", "ModelId"),
    ("FactVehicleInventory", "DealershipId", "DimDealership", "DealershipId"),
    ("FactVehicleInventory", "ModelId", "DimVehicleModel", "ModelId"),
    ("FactActionTrigger", "DealershipId", "DimDealership", "DealershipId"),
]
HIDDEN_COLUMNS = {
    "DimDate": {"DateKey"}, "DimVehicleSegment": {"SegmentKey"}, "DimOEM": {"OEMKey", "SegmentKey"},
    "FactMarketRegistrations": {"MonthDateKey", "SegmentKey", "OEMKey", "Units"},
    "FactMarketSegmentMonth": {"MonthDateKey", "SegmentKey", "PublishedTotalUnits", "EVUnitsEstimated"},
    "FactLeadFunnel": {"LeadDateKey"}, "FactVehicleDelivery": {"DeliveryDateKey"},
    "FactInventorySnapshot": {"SnapshotDateKey", "AgeingBucketOrder"},
}
SORT_BY = {("DimDate", "MonthName"): "MonthNumber", ("DimDate", "MonthShortName"): "MonthNumber",
           ("FactInventorySnapshot", "AgeingBucket"): "AgeingBucketOrder"}
TYPE_MAP = {"INT": "int64", "TINYINT": "int64", "SMALLINT": "int64", "BIGINT": "int64", "BIT": "boolean",
            "DATE": "dateTime", "DATETIME2": "dateTime", "DECIMAL": "double", "FLOAT": "double",
            "NVARCHAR": "string", "VARCHAR": "string", "CHAR": "string", "NCHAR": "string"}
COLUMN_FORMAT = {"int64": "0", "double": "#,0.00", "dateTime": "yyyy-mm-dd"}
FORMAT_RULES = [  # (predicate on measure name, format string); first match wins
    (lambda n: n.endswith("Key") or n == "Period Months" or n.startswith("OEM Months") or n == "Is Coverage Complete", "0"),
    (lambda n: "bps" in n, '+#,0;-#,0;0'),
    (lambda n: n.endswith(" pp"), '+0.00;-0.00;0.00'),
    (lambda n: "Growth %" in n, '+0.0%;-0.0%;0.0%'),
    (lambda n: "%" in n, "0.00%"),
    (lambda n: n == "Registration Gap" or n.endswith("Registration Gap"), '+#,0;-#,0;0'),
    (lambda n: "Revenue" in n or "Cost" in n, '"₹"#,0'),
    (lambda n: "Hours" in n or "Days" in n or "Turnover" in n or n == "Days of Supply", "0.0"),
]


def _id(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(parts)))


def _short_id(*parts: str) -> str:
    return uuid.uuid5(NS, "|".join(parts)).hex[:20]


def q(name: str) -> str:
    """Quote a TMDL object name."""
    return "'" + name.replace("'", "''") + "'"


def parse_ddl(path: Path = ROOT / "sql" / "03_dw_tables.sql") -> dict[str, list[tuple[str, str]]]:
    ddl = path.read_text(encoding="utf-8")
    tables = {}
    for m in re.finditer(r"CREATE TABLE dw\.(\w+) \((.*?)\n\);", ddl, re.DOTALL):
        cols = []
        for line in m.group(2).splitlines():
            c = re.match(r"\s+([A-Z]\w*)\s+(AS\s+CAST\b.*|(?:" + "|".join(TYPE_MAP) + r")\b.*)", line)
            if not c or c.group(1) == "CONSTRAINT":
                continue
            spec = c.group(2)
            sql_type = re.findall(r"\bAS\s+([A-Z]+)", spec)[-1] if spec.startswith("AS") else re.match(r"[A-Z0-9]+", spec).group(0)
            if sql_type not in TYPE_MAP:
                raise ValueError(f"unmapped SQL type {sql_type} for {m.group(1)}.{c.group(1)}")
            cols.append((c.group(1), TYPE_MAP[sql_type]))
        tables[m.group(1)] = cols
    return tables


def parse_measures(path: Path = PBI_DIR / "measures" / "measures.dax") -> list[dict]:
    """Measures in file order with display folder taken from the nearest '[Folder: x]' header."""
    measures, folder, current = [], "", None
    for line in path.read_text(encoding="utf-8").splitlines():
        f = re.search(r"\[Folder: ([^\]]+)\]", line)
        if f and line.lstrip().startswith("//"):
            folder = f.group(1).strip()
            continue
        if line.lstrip().startswith("//") or not line.strip():
            if current is not None and not line.strip():
                current["body"].append("")
            continue
        # A definition starts at column 0 with a name that never contains "[" (DAX continuation
        # lines such as "CALCULATE ( [x], T[c] = TRUE () )" do).
        d = re.match(r"^(?!VAR\b|RETURN\b)([A-Za-z][^=\n\[]*?)\s*=(?!=)\s*(.*)$", line)
        if d:
            current = {"name": d.group(1).strip(), "folder": folder, "body": [d.group(2)] if d.group(2) else []}
            measures.append(current)
        elif current is not None:
            current["body"].append(line)
    for m in measures:
        m["expression"] = "\n".join(m.pop("body")).strip()
        m["format"] = next((fmt for pred, fmt in FORMAT_RULES if pred(m["name"])), "#,0")
        if m["folder"] == "9 Labels" or "Label" in m["name"]:
            m["format"] = None
    names = [m["name"] for m in measures]
    if len(names) != len(set(names)):
        raise ValueError("duplicate measure names in measures.dax")
    return measures


def tmdl_value(v: str) -> str:
    return '"' + v.replace('"', '""') + '"' if ('"' in v or v != v.strip()) else v


def table_tmdl(name: str, cols: list[tuple[str, str]], measures: list[dict] | None = None) -> str:
    spec = TABLES.get(name, {})
    out = []
    if spec:
        out.append(f"/// {spec['class']}: {spec['desc']}")
    out.append(f"table {name}")
    if name == "DimDate":
        out.append("\tdataCategory: Time")
    out.append("")
    for m in measures or []:
        expr = m["expression"].splitlines()
        if len(expr) == 1:
            out.append(f"\tmeasure {q(m['name'])} = {expr[0]}")
        else:
            out.append(f"\tmeasure {q(m['name'])} =")
            out += [f"\t\t\t{l}" if l.strip() else "" for l in expr]
        if m["format"]:
            out.append(f"\t\tformatString: {tmdl_value(m['format'])}")
        out.append(f"\t\tdisplayFolder: {m['folder']}")
        out.append("")
    for col, dtype in cols:
        out.append(f"\tcolumn {col}")
        out.append(f"\t\tdataType: {dtype}")
        if dtype in COLUMN_FORMAT:
            out.append(f"\t\tformatString: {COLUMN_FORMAT[dtype]}")
        if col in HIDDEN_COLUMNS.get(name, set()) or name == MEASURE_TABLE:
            out.append("\t\tisHidden")
        if name == "DimDate" and col == "CalendarDate":
            out.append("\t\tisKey")
        if (name, col) in SORT_BY:
            out.append(f"\t\tsortByColumn: {SORT_BY[(name, col)]}")
        out.append("\t\tsummarizeBy: none")
        out.append(f"\t\tsourceColumn: {col}")
        out.append("")
    out.append(f"\tpartition {name} = m")
    out.append("\t\tmode: import")
    out.append("\t\tsource =")
    if name == MEASURE_TABLE:
        src = ["let", "    Source = #table(type table [Placeholder = text], {})", "in", "    Source"]
    else:
        src = ["let", "    Source = Sql.Database(SqlServer, SqlDatabase),",
               f'    Data = Source{{[Schema="dw", Item="{name}"]}}[Data]', "in", "    Data"]
    out += [f"\t\t\t\t{l}" for l in src]
    out.append("")
    return "\n".join(out) + "\n"


def build_semantic_model(measures: list[dict], ddl: dict) -> dict[Path, str]:
    files: dict[Path, str] = {}
    files[SM_DIR / "definition.pbism"] = json.dumps({"version": "4.2", "settings": {}}, indent=2) + "\n"
    files[SM_DIR / ".platform"] = json.dumps({"$schema": PLATFORM_SCHEMA, "metadata": {"type": "SemanticModel",
                                              "displayName": NAME}, "config": {"version": "2.0",
                                              "logicalId": _id("semanticmodel")}}, indent=2) + "\n"
    d = SM_DIR / "definition"
    files[d / "database.tmdl"] = "database\n\tcompatibilityLevel: 1601\n"
    order = [MEASURE_TABLE] + list(TABLES)
    files[d / "model.tmdl"] = ("model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
                               "\tdiscourageImplicitMeasures\n\tsourceQueryCulture: en-US\n\n"
                               # Auto date/time off: DimDate is the marked date table, so Desktop must not
                               # add hidden LocalDateTable_* tables for every date column when the file is saved.
                               "\tannotation __PBI_TimeIntelligenceEnabled = 0\n\n"
                               + "".join(f"ref table {t}\n" for t in order))
    files[d / "expressions.tmdl"] = (
        '/// SQL Server instance. Change in Power BI Desktop: Transform data > Edit parameters.\n'
        'expression SqlServer = ".\\SQLEXPRESS" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n\n'
        '/// Database built by python/sql_server_pipeline.py.\n'
        'expression SqlDatabase = "AutoRetailAnalytics" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n')
    files[d / "tables" / f"{MEASURE_TABLE}.tmdl"] = table_tmdl(MEASURE_TABLE, [("Placeholder", "string")], measures)
    for t in TABLES:
        if t not in ddl:
            raise ValueError(f"table {t} not found in sql/03_dw_tables.sql")
        files[d / "tables" / f"{t}.tmdl"] = table_tmdl(t, ddl[t])
    rel = []
    for ft, fc, tt, tc in RELATIONSHIPS:
        for tbl, col in ((ft, fc), (tt, tc)):
            if col not in dict(ddl[tbl]):
                raise ValueError(f"relationship column {tbl}.{col} not in DDL")
        rel += [f"relationship {_id('rel', ft, fc, tt, tc)}", f"\tfromColumn: {ft}.{fc}", f"\ttoColumn: {tt}.{tc}", ""]
    files[d / "relationships.tmdl"] = "\n".join(rel)
    return files


# ----------------------------------------------------------------------------- report helpers
def lit(v) -> dict:
    return {"expr": {"Literal": {"Value": v}}}


def s(text: str) -> dict:
    return lit("'" + text.replace("'", "''") + "'")


def col(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def mea(prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": MEASURE_TABLE}}, "Property": prop}}


def proj(field: dict, display: str | None = None) -> dict:
    kind = "Column" if "Column" in field else "Measure"
    ent, prop = field[kind]["Expression"]["SourceRef"]["Entity"], field[kind]["Property"]
    p = {"field": field, "queryRef": f"{ent}.{prop}", "nativeQueryRef": prop}
    if display:
        p["displayName"] = display
    return p


def cat_filter(entity: str, prop: str, value_literal: str, name: str) -> dict:
    return {"name": name, "field": col(entity, prop), "type": "Categorical",
            "filter": {"Version": 2, "From": [{"Name": "t", "Entity": entity, "Type": 0}],
                       "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "t"}},
                                                                                    "Property": prop}}],
                                                       "Values": [[{"Literal": {"Value": value_literal}}]]}}}]}}


PV = ("DimVehicleSegment", "SegmentCode", "'PV'")
TW = ("DimVehicleSegment", "SegmentCode", "'2W'")
PORTFOLIO = ("DimOEM", "IsPortfolioBrand", "true")
NOT_RESIDUAL = ("DimOEM", "IsResidual", "false")
BREACHED = ("FactActionTrigger", "Breached", "true")

AMBER_BG, AMBER_TEXT, BLUE_BG, BLUE_TEXT = "#FFF4E0", "#8A5A00", "#E8F1FB", "#1C5CAB"
ALT_TEXT_MAX = 250  # Power BI's limit for visual alt text
SEGMENT_NAMES = {"'PV'": "passenger vehicles", "'2W'": "two-wheelers"}


class Page:
    def __init__(self, name: str, display: str):
        self.name, self.display, self.visuals = name, display, []

    def add(self, visual_type: str, x, y, w, h, *, title: str | None = None, query: dict | None = None,
            objects: dict | None = None, filters: list[tuple] | None = None, sort: tuple | None = None,
            background: str | None = None, subtitle: str | None = None, alt_text: str | None = None):
        n = len(self.visuals) + 1
        vname = f"{self.name}_v{n:02d}"
        visual: dict = {"visualType": visual_type}
        if query:
            visual["query"] = {"queryState": query}
            if sort:
                field, direction = sort
                visual["query"]["sortDefinition"] = {"sort": [{"field": field, "direction": direction}], "isDefaultSort": False}
        if objects:
            visual["objects"] = objects
        vco: dict = {}
        if title is not None:
            vco["title"] = [{"properties": {"show": lit("true"), "text": s(title)}}]
        if subtitle:
            vco["subTitle"] = [{"properties": {"show": lit("true"), "text": s(subtitle)}}]
        if background:
            vco["background"] = [{"properties": {"show": lit("true"), "color": {"solid": {"color": s(background)}},
                                                 "transparency": lit("0D")}}]
        if alt_text:
            if len(alt_text) > ALT_TEXT_MAX:
                raise ValueError(f"alt text longer than {ALT_TEXT_MAX} characters: {alt_text[:60]}...")
            vco["general"] = [{"properties": {"altText": s(alt_text)}}]
        if vco:
            visual["visualContainerObjects"] = vco
        visual["drillFilterOtherVisuals"] = True
        container = {"$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json", "name": vname,
                     "position": {"x": x, "y": y, "z": n * 1000, "width": w, "height": h, "tabOrder": n * 1000},
                     "visual": visual}
        if filters:
            container["filterConfig"] = {"filters": [cat_filter(e, p, v, f"{vname}_f{i}") for i, (e, p, v) in enumerate(filters)]}
        self.visuals.append(container)

    # ---- convenience builders
    def text(self, x, y, w, h, runs: list[tuple[str, dict]], background: str | None = None):
        paragraphs = [{"textRuns": [{"value": t, "textStyle": st}]} for t, st in runs]
        self.add("textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": paragraphs}}]},
                 background=background)

    def header(self, title: str, subtitle: str, simulated: bool):
        self.text(20, 10, 960, 40, [(title, {"fontWeight": "bold", "fontSize": "18pt", "color": "#0B0B0B"})])
        self.text(20, 48, 960, 30, [(subtitle, {"fontSize": "10pt", "color": "#52514E"})])
        badge = "SIMULATED DATA" if simulated else "OFFICIAL DATA - FADA"
        self.text(1000, 16, 260, 34, [(badge, {"fontWeight": "bold", "fontSize": "11pt",
                                               "color": AMBER_TEXT if simulated else BLUE_TEXT})],
                  background=AMBER_BG if simulated else BLUE_BG)

    def card(self, x, y, w, h, measure: str, label: str, filters=None, simulated=False):
        segment = next((SEGMENT_NAMES[v] for _, prop, v in filters or [] if prop == "SegmentCode"), None)
        alt = (f"SIMULATED dealership data. Card showing {label}." if simulated else
               f"Card showing {label}{' for ' + segment if segment and segment not in label.lower() else ''}. "
               "Official FADA data.")
        self.add("card", x, y, w, h, query={"Values": {"projections": [proj(mea(measure), label)]}},
                 alt_text=alt,
                 objects={"labels": [{"properties": {"fontSize": lit("20D"), "labelDisplayUnits": lit("1D")}}],
                          "categoryLabels": [{"properties": {"show": lit("true"), "fontSize": lit("9D")}}]},
                 filters=filters, background=AMBER_BG if simulated else None,
                 title="SIMULATED" if simulated else None)

    def table(self, x, y, w, h, title, fields: list[tuple[dict, str | None]], *, alt_text: str, filters=None, sort=None,
              background=None, subtitle=None):
        self.add("tableEx", x, y, w, h, title=title, subtitle=subtitle, alt_text=alt_text,
                 query={"Values": {"projections": [proj(f, d) for f, d in fields]}},
                 objects={"total": [{"properties": {"totals": lit("false")}}],
                          "values": [{"properties": {"wordWrap": lit("true")}}],
                          "columnHeaders": [{"properties": {"wordWrap": lit("true")}}]},
                 filters=filters, sort=sort, background=background)

    def chart(self, vtype, x, y, w, h, title, category=None, values=(), series=None, tooltips=(), filters=None,
              sort=None, colors: dict | None = None, labels=True, background=None, subtitle=None,
              series_colors: dict | None = None, *, alt_text: str):
        qs: dict = {}
        if category is not None:
            qs["Category"] = {"projections": [proj(category)]}
        qs["Y"] = {"projections": [proj(f, d) for f, d in values]}
        if series is not None:
            qs["Series"] = {"projections": [proj(series)]}
        if tooltips:
            qs["Tooltips"] = {"projections": [proj(f) for f in tooltips]}
        objects: dict = {"labels": [{"properties": {"show": lit("true" if labels else "false"),
                                                    "labelDisplayUnits": lit("1D")}}],
                         "legend": [{"properties": {"show": lit("true"), "position": s("Top"), "showTitle": lit("false")}}],
                         "categoryAxis": [{"properties": {"showAxisTitle": lit("false")}}],
                         "valueAxis": [{"properties": {"showAxisTitle": lit("false")}}]}
        if colors:
            objects["dataPoint"] = [{"properties": {"fill": {"solid": {"color": s(c)}}}, "selector": {"metadata": qr}}
                                    for qr, c in colors.items()]
        if series_colors and series is not None:
            kind = "Column"
            ent, prop = series[kind]["Expression"]["SourceRef"]["Entity"], series[kind]["Property"]
            objects.setdefault("dataPoint", []).extend(
                {"properties": {"fill": {"solid": {"color": s(c)}}},
                 "selector": {"data": [{"scopeId": {"Comparison": {"ComparisonKind": 0, "Left": col(ent, prop),
                                                                   "Right": {"Literal": {"Value": "'" + v + "'"}}}}}]}}
                for v, c in series_colors.items())
        self.add(vtype, x, y, w, h, title=title, subtitle=subtitle, query=qs, objects=objects, filters=filters,
                 sort=sort, background=background, alt_text=alt_text)

    def files(self, base: Path) -> dict[Path, str]:
        out = {base / self.name / "page.json": json.dumps(
            {"$schema": f"{SCHEMA}/page/1.0.0/schema.json", "name": self.name, "displayName": self.display,
             "displayOption": "FitToPage", "height": 720, "width": 1280}, indent=2) + "\n"}
        for v in self.visuals:
            out[base / self.name / "visuals" / v["name"] / "visual.json"] = json.dumps(v, indent=2, ensure_ascii=False) + "\n"
        return out


def build_pages(cfg: dict) -> list[Page]:
    cur, pri = cfg["current_period"]["label"], cfg["prior_period"]["label"]
    period = f"{cur} vs {pri}"
    month_axis, year_series = col("DimDate", "MonthShortName"), col("DimDate", "YearNumber")
    month_sort = (month_axis, "Ascending")
    blue, grey, red = "#2A78D6", "#8C8B86", "#E34948"
    pages = []

    # 1 ---------------------------------------------------------------- Executive Overview
    p = Page("executive_overview", "Executive Overview")
    p.header("Is the portfolio keeping pace with the market?",
             f"{period}  |  Retail registrations from FADA; each brand compared only with its own segment", False)
    cards = [("Market YoY Growth % (Period)", "PV market growth", [PV]),
             ("Market YoY Growth % (Period)", "Two-wheeler market growth", [TW]),
             ("Portfolio Car YoY Growth %", "Portfolio car growth", None),
             ("Portfolio Car Share CP %", "Portfolio share of PV", None),
             ("Portfolio Car Share Change bps", "Portfolio share change (bps)", None),
             ("Portfolio Car Registration Gap", "Registrations below constant share", None)]
    for i, (m, label, flt) in enumerate(cards):
        p.card(20 + i * 208, 86, 200, 96, m, label, flt)
    p.chart("lineChart", 20, 194, 620, 330, "PV retail registrations by month", category=month_axis,
            values=[(mea("Market Registrations"), "PV registrations")], series=year_series, filters=[PV], sort=month_sort,
            labels=False, alt_text="Line chart of passenger-vehicle retail registrations by month, February to September, "
                                   "one line per year. Official FADA data.")
    p.chart("clusteredBarChart", 652, 194, 608, 330, "Brand growth vs own-segment growth (period YoY)",
            category=col("DimOEM", "PortfolioBrand"),
            values=[(mea("YoY Growth % (Period)"), "Brand growth"), (mea("Segment YoY Growth % (Period)"), "Own-segment growth")],
            filters=[PORTFOLIO], colors={f"{MEASURE_TABLE}.YoY Growth % (Period)": blue,
                                         f"{MEASURE_TABLE}.Segment YoY Growth % (Period)": grey},
            alt_text="Bar chart comparing each portfolio brand's period growth with its own segment's growth: car brands "
                     "against passenger vehicles, Ather against two-wheelers. Official FADA data.")
    p.text(20, 536, 1240, 30, [("SIMULATED DEALERSHIP DATA - synthetic dealerships for illustration; not actual "
                                "performance and not part of FADA totals", {"fontWeight": "bold", "fontSize": "10pt",
                                                                             "color": AMBER_TEXT})], background=AMBER_BG)
    for i, (m, label) in enumerate([("Lead to Delivery %", "Lead-to-delivery conversion"),
                                    ("First Contact SLA %", "First contact within SLA"),
                                    ("Aged Inventory %", "Aged inventory (>60 days)"),
                                    ("Actions Triggered", "Management actions triggered")]):
        p.card(20 + i * 313, 574, 305, 130, m, label, simulated=True)
    pages.append(p)

    # 2 ---------------------------------------------------------------- OEM & Market Share
    p = Page("oem_market_share", "OEM & Market Share")
    p.header("Who is gaining share in each segment?",
             f"{period}  |  Weighted period shares; OEMs listed by FADA in only some months are not ranked", False)
    for i, (m, label, flt) in enumerate([("Market Registrations CP", "PV retail (current)", [PV]),
                                         ("Market YoY Growth % (Period)", "PV growth", [PV]),
                                         ("Market Registrations CP", "Two-wheeler retail (current)", [TW]),
                                         ("Market YoY Growth % (Period)", "Two-wheeler growth", [TW])]):
        p.card(20 + i * 313, 86, 305, 86, m, label, flt)
    bps = mea("Share Change bps (Complete Coverage)")
    for x, w, flt, seg in ((20, 620, PV, "Passenger vehicles"), (652, 608, TW, "Two-wheelers")):
        p.chart("barChart", x, 184, w, 300, f"{seg}: share change by OEM (bps)", category=col("DimOEM", "OEMName"),
                values=[(mea("Share Gain bps (Complete Coverage)"), "Gain"), (mea("Share Loss bps (Complete Coverage)"), "Loss")],
                tooltips=[bps], filters=[flt, NOT_RESIDUAL], sort=(bps, "Descending"),
                colors={f"{MEASURE_TABLE}.Share Gain bps (Complete Coverage)": blue,
                        f"{MEASURE_TABLE}.Share Loss bps (Complete Coverage)": red},
                alt_text=f"Bar chart ranking {seg.lower()} OEMs by change in market share in basis points, gains in blue "
                         "and losses in red. Only OEMs listed by FADA in every month are shown. Official FADA data.")
    p.table(20, 496, 1240, 212, "OEM registrations and share (blank = not listed by FADA in every month)",
            [(col("DimOEM", "OEMName"), "OEM"), (col("DimVehicleSegment", "SegmentCode"), "Segment"),
             (mea("Registrations PP"), pri), (mea("Registrations CP"), cur),
             (mea("YoY Growth % (Complete Coverage)"), "YoY growth"),
             (mea("Market Share PP % (Complete Coverage)"), "Share " + pri),
             (mea("Market Share CP % (Complete Coverage)"), "Share " + cur),
             (mea("Share Change bps (Complete Coverage)"), "Change (bps)")],
            sort=(mea("Registrations CP"), "Descending"),
            alt_text="Table of OEM registrations, growth and market share for both periods, sorted by current "
                     "registrations. Blank shares mean FADA did not list the OEM every month. Official FADA data.")
    pages.append(p)

    # 3 ---------------------------------------------------------------- EV Analytics
    p = Page("ev_analytics", "EV Analytics")
    p.header("How fast is electrification moving?",
             f"{period}  |  EV share of each segment's retail, volume-weighted; PV and 2W never combined", False)
    for i, (m, label, flt) in enumerate([("EV Share PP %", f"PV EV share {pri}", [PV]),
                                         ("EV Share CP %", f"PV EV share {cur}", [PV]),
                                         ("EV Share Change bps", "PV EV share change (bps)", [PV]),
                                         ("EV Share PP %", f"2W EV share {pri}", [TW]),
                                         ("EV Share CP %", f"2W EV share {cur}", [TW]),
                                         ("EV Share Change bps", "2W EV share change (bps)", [TW])]):
        p.card(20 + i * 208, 86, 200, 96, m, label, flt)
    for x, w, flt, seg in ((20, 620, PV, "Passenger vehicles"), (652, 608, TW, "Two-wheelers")):
        p.chart("lineChart", x, 194, w, 300, f"{seg}: EV share of retail by month", category=month_axis,
                values=[(mea("EV Share %"), "EV share")], series=year_series, filters=[flt], sort=month_sort,
                alt_text=f"Line chart of the EV share of {seg.lower()} retail by month, February to September, "
                         "one line per year. Official FADA data.")
    p.table(20, 506, 1240, 150, "EV volume by segment (estimated)",
            [(col("DimVehicleSegment", "SegmentName"), "Segment"),
             (mea("EV Registrations PP (Est.)"), f"EV units {pri} (est.)"),
             (mea("EV Registrations CP (Est.)"), f"EV units {cur} (est.)"),
             (mea("EV Registrations Growth % (Est.)"), "EV growth"),
             (mea("Market YoY Growth % (Period)"), "Segment growth"),
             (mea("EV Share PP %"), "EV share " + pri), (mea("EV Share CP %"), "EV share " + cur),
             (mea("EV Share Change bps"), "Change (bps)")],
            alt_text="Table of estimated EV units, EV growth, segment growth and EV share for each segment in both "
                     "periods. EV units are derived from FADA's published EV share.")
    p.text(20, 664, 1240, 44, [("EV units are derived: FADA publishes each segment's EV share to two decimals; "
                                "EV units = EV share x published segment total. Months without a published EV share "
                                "would make the period share blank rather than understated.",
                                {"fontSize": "9pt", "color": "#52514E"})])
    pages.append(p)

    # 4 ---------------------------------------------------------------- Portfolio Opportunity
    p = Page("portfolio_opportunity", "Portfolio Opportunity")
    p.header("What did losing share cost the portfolio?",
             f"{period}  |  Constant share = current segment volume x prior-period share", False)
    for i, (m, label) in enumerate([("Portfolio Car Registrations PP", f"Car portfolio {pri}"),
                                    ("Portfolio Car Registrations CP", f"Car portfolio {cur}"),
                                    ("Portfolio Car Constant-Share Registrations", "At constant share"),
                                    ("Portfolio Car Registration Gap", "Registration gap"),
                                    ("Portfolio Car Growth Gap vs PV pp", "Growth vs PV market (pp)")]):
        p.card(20 + i * 250, 86, 242, 96, m, label)
    p.table(20, 194, 1240, 214, "Portfolio brands against their own segment (Ather vs two-wheelers; car brands vs PV)",
            [(col("DimOEM", "PortfolioBrand"), "Brand"), (col("DimVehicleSegment", "SegmentCode"), "Segment"),
             (mea("Registrations PP"), pri), (mea("Registrations CP"), cur),
             (mea("YoY Growth % (Period)"), "Growth"), (mea("Segment YoY Growth % (Period)"), "Segment growth"),
             (mea("Growth Gap vs Segment pp"), "Gap (pp)"), (mea("Market Share PP %"), "Share " + pri),
             (mea("Market Share CP %"), "Share " + cur), (mea("Share Change bps"), "Change (bps)"),
             (mea("Constant-Share Registrations"), "Constant-share units"), (mea("Registration Gap"), "Registration gap")],
            filters=[PORTFOLIO], sort=(mea("Registrations CP"), "Descending"),
            alt_text="Table of each portfolio brand against its own segment: registrations, growth, growth gap, market "
                     "share, share change, constant-share units and registration gap. Official FADA data.")
    p.chart("clusteredColumnChart", 20, 420, 400, 288, "Registration gap by brand (positive = forgone)",
            category=col("DimOEM", "PortfolioBrand"), values=[(mea("Registration Gap"), "Registration gap")],
            filters=[PORTFOLIO], sort=(mea("Registration Gap"), "Descending"),
            alt_text="Column chart of registrations each portfolio brand forgone (positive) or gained (negative) "
                     "compared with holding its prior-period share. Official FADA data.")
    p.chart("lineChart", 432, 420, 420, 288, "Car portfolio share of PV retail by month", category=month_axis,
            values=[(mea("Market Share %"), "Portfolio share")], series=year_series, filters=[PV, PORTFOLIO],
            sort=month_sort, labels=False,
            alt_text="Line chart of the car portfolio's combined share of passenger-vehicle retail by month, one line "
                     "per year. Official FADA data.")
    p.table(864, 420, 396, 288, "Attention flags (illustrative rules)",
            [(col("MarketAttention", "Brand"), "Brand"), (col("MarketAttention", "AttentionStatus"), "Status"),
             (col("MarketAttention", "Triggers"), "Triggers")],
            alt_text="Table of attention status and the rules that triggered it for each portfolio brand. Thresholds "
                     "are illustrative and are applied to official FADA data.")
    pages.append(p)

    # 5 ---------------------------------------------------------------- Dealership Operations (SIMULATED)
    p = Page("dealership_operations", "Dealership Operations (Simulated)")
    p.header("Where do we lose customers, and is our stock healthy?",
             "SIMULATED synthetic dealerships (seeded); illustrative only - not actual dealership performance", True)
    for i, (m, label) in enumerate([("Leads", "Leads"), ("Bookings", "Bookings"),
                                    ("Deliveries (Lead Cohort)", "Deliveries (lead cohort)"),
                                    ("Lead to Delivery %", "Lead-to-delivery"), ("First Contact SLA %", "First contact SLA"),
                                    ("Current Inventory Units", "Current stock (units)"), ("Aged Inventory %", "Aged stock")]):
        p.card(20 + i * 154, 86, 148, 90, m, label, simulated=True)
    p.add("slicer", 1100, 86, 160, 90, title="Dealership (simulated)",
          query={"Values": {"projections": [proj(col("DimDealership", "DealershipName"))]}},
          alt_text="Dropdown that filters this page to one simulated dealership.",
          objects={"data": [{"properties": {"mode": s("Dropdown")}}],
                   "header": [{"properties": {"show": lit("false")}}]}, background=AMBER_BG)
    p.chart("clusteredBarChart", 20, 188, 400, 260, "Sales funnel (SIMULATED)",
            values=[(mea("Leads"), "Leads"), (mea("Qualified Leads"), "Qualified"), (mea("Test Drives"), "Test drives"),
                    (mea("Bookings"), "Bookings"), (mea("Deliveries (Lead Cohort)"), "Deliveries")], background=AMBER_BG,
            alt_text="SIMULATED data. Bar chart of the sales funnel for the lead cohort: leads, qualified leads, test "
                     "drives, bookings and deliveries.")
    p.chart("barChart", 432, 188, 420, 260, "Current stock by age bucket (SIMULATED)",
            category=col("DimDealership", "DealershipId"), values=[(mea("Current Inventory Units"), "Units")],
            series=col("FactInventorySnapshot", "AgeingBucket"), background=AMBER_BG,
            series_colors={"0-30": "#B7D3F6", "31-60": "#6DA7EC", "61-90": "#2A78D6", ">90": "#104281"},
            alt_text="SIMULATED data. Stacked bar chart of current stock per dealership, split into age buckets of "
                     "0-30, 31-60, 61-90 and over 90 days.")
    p.table(864, 188, 396, 260, "Dealership KPIs (SIMULATED)",
            [(col("DimDealership", "DealershipId"), "Dealer"), (col("DimDealership", "Brand"), "Brand"),
             (mea("Lead to Delivery %"), "Lead-to-delivery"), (mea("First Contact SLA %"), "SLA"),
             (mea("Aged Inventory %"), "Aged stock"), (mea("Days of Supply"), "Days of supply")], background=AMBER_BG,
            alt_text="SIMULATED data. Table of lead-to-delivery conversion, first-contact SLA, aged stock share and "
                     "days of supply for each dealership.")
    p.table(20, 460, 1240, 248, "Management actions triggered (SIMULATED; thresholds illustrative unless a basis is cited)",
            [(col("FactActionTrigger", "DealershipId"), "Dealer"), (col("FactActionTrigger", "Kpi"), "KPI"),
             (col("FactActionTrigger", "ActualValue"), "Actual"), (col("FactActionTrigger", "Comparator"), "Rule"),
             (col("FactActionTrigger", "Threshold"), "Threshold"), (col("FactActionTrigger", "ResponsibleRole"), "Owner"),
             (col("FactActionTrigger", "ReviewCadence"), "Cadence"),
             (col("FactActionTrigger", "RecommendedAction"), "Recommended action"),
             (col("FactActionTrigger", "ThresholdBasis"), "Threshold basis")],
            filters=[BREACHED], background=AMBER_BG,
            alt_text="SIMULATED data. Table of breached management rules with KPI, actual value, threshold, owner, "
                     "review cadence, recommended action and threshold basis.")
    pages.append(p)
    return pages


def build_report(cfg: dict, active_page: str | None = None) -> dict[Path, str]:
    files: dict[Path, str] = {}
    files[RP_DIR / "definition.pbir"] = json.dumps({"version": "4.0", "datasetReference": {
        "byPath": {"path": f"../{NAME}.SemanticModel"}}}, indent=2) + "\n"
    files[RP_DIR / ".platform"] = json.dumps({"$schema": PLATFORM_SCHEMA, "metadata": {"type": "Report", "displayName": NAME},
                                              "config": {"version": "2.0", "logicalId": _id("report")}}, indent=2) + "\n"
    d = RP_DIR / "definition"
    files[d / "version.json"] = json.dumps({"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"},
                                           indent=2) + "\n"
    files[d / "report.json"] = json.dumps({
        "$schema": f"{SCHEMA}/report/1.0.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.59", "type": "SharedResources"},
            "customTheme": {"name": THEME_FILE, "reportVersionAtImport": "5.59", "type": "RegisteredResources"}},
        "layoutOptimization": "None",
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": THEME_FILE, "path": THEME_FILE, "type": "CustomTheme"}]}],
    }, indent=2) + "\n"
    files[RP_DIR / "StaticResources" / "RegisteredResources" / THEME_FILE] = (
        PBI_DIR / "theme" / THEME_FILE).read_text(encoding="utf-8")
    pages = build_pages(cfg)
    names = [p.name for p in pages]
    files[d / "pages" / "pages.json"] = json.dumps({"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
                                                   "pageOrder": names, "activePageName": active_page or names[0]},
                                                  indent=2) + "\n"
    for p in pages:
        files.update(p.files(d / "pages"))
    return files


def build(out_root: Path = PBI_DIR, active_page: str | None = None) -> list[Path]:
    """Write the PBIP. out_root lets validation write copies elsewhere (e.g. per-page screenshots)."""
    cfg = yaml.safe_load((CONFIG_DIR / "market_analysis.yaml").read_text(encoding="utf-8"))
    files = {**build_semantic_model(parse_measures(), parse_ddl()), **build_report(cfg, active_page)}
    files[PBI_DIR / f"{NAME}.pbip"] = json.dumps({"version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                                  "settings": {"enableAutoRecovery": True}}, indent=2) + "\n"
    for generated in (SM_DIR / "definition", RP_DIR / "definition"):
        target = out_root / generated.relative_to(PBI_DIR)
        if target.exists():
            shutil.rmtree(target)          # remove pages/tables that no longer exist
    written = []
    for path, content in sorted(files.items()):
        target = out_root / path.relative_to(PBI_DIR)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(target)
    return written


def main() -> int:
    written = build()
    print(f"Wrote {len(written)} files for {NAME}.pbip under {PBI_DIR.relative_to(ROOT).as_posix()}/")
    print(f"  measures: {len(parse_measures())}, tables: {len(TABLES) + 1}, relationships: {len(RELATIONSHIPS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
