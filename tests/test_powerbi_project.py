"""Static tests of the generated Power BI Project (powerbi/AutoRetailAnalytics.*).

Runtime behaviour (measure results inside Power BI Desktop's engine) is checked by
python/validate_powerbi.py, which needs Power BI Desktop and SQL Server; its recorded
result file is checked at the end of this module."""
import json
import re

import pandas as pd
import pytest

import build_powerbi_project as bpp
from paths import ROOT

REPORT_DEF = bpp.RP_DIR / "definition"
MODEL_DEF = bpp.SM_DIR / "definition"
VISUALS = sorted(REPORT_DEF.glob("pages/*/visuals/*/visual.json"))


def model_fields() -> tuple[dict[str, set[str]], set[str]]:
    columns = {t: {c for c, _ in cols} for t, cols in bpp.parse_ddl().items() if t in bpp.TABLES}
    measures = {m["name"] for m in bpp.parse_measures()}
    return columns, measures


def field_refs(obj):
    """Yield ('Column'|'Measure', entity, property) for every field reference in a visual."""
    if isinstance(obj, dict):
        for kind in ("Column", "Measure"):
            if kind in obj and isinstance(obj[kind], dict) and "Property" in obj[kind]:
                src = obj[kind]["Expression"]["SourceRef"]
                if "Entity" in src:
                    yield kind, src["Entity"], obj[kind]["Property"]
        for v in obj.values():
            yield from field_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from field_refs(v)


def test_generated_project_is_up_to_date_and_deterministic(tmp_path):
    bpp.build(out_root=tmp_path)
    generated = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    committed = {p.relative_to(bpp.PBI_DIR).as_posix(): p.read_bytes()
                 for d in (bpp.SM_DIR, bpp.RP_DIR) for p in d.rglob("*")
                 if p.is_file() and ".pbi" not in p.parts}
    committed[f"{bpp.NAME}.pbip"] = (bpp.PBI_DIR / f"{bpp.NAME}.pbip").read_bytes()
    assert generated.keys() == committed.keys()
    stale = [k for k in generated if generated[k] != committed[k]]
    assert not stale, f"run python python/build_powerbi_project.py; stale: {stale[:5]}"


def test_auto_date_time_is_off_and_no_hidden_date_tables():
    model = (MODEL_DEF / "model.tmdl").read_text(encoding="utf-8")
    assert "annotation __PBI_TimeIntelligenceEnabled = 0" in model
    hidden = [p.name for p in (MODEL_DEF / "tables").glob("*.tmdl")
              if p.name.startswith(("LocalDateTable_", "DateTableTemplate_"))]
    assert not hidden, f"Power BI Desktop auto date/time tables found (re-run the generator): {hidden[:3]}"


def test_all_report_json_parses_and_declares_schema():
    files = list(REPORT_DEF.rglob("*.json"))
    assert len(files) > 50
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["$schema"].startswith(bpp.SCHEMA), f


def test_five_pages_in_requested_order():
    pages = json.loads((REPORT_DEF / "pages" / "pages.json").read_text(encoding="utf-8"))
    names = [json.loads((REPORT_DEF / "pages" / p / "page.json").read_text(encoding="utf-8"))["displayName"]
             for p in pages["pageOrder"]]
    assert names == ["Executive Overview", "OEM & Market Share", "EV Analytics", "Portfolio Opportunity",
                     "Dealership Operations (Simulated)"]


def test_every_visual_field_exists_in_the_model():
    columns, measures = model_fields()
    assert VISUALS
    for v in VISUALS:
        for kind, entity, prop in field_refs(json.loads(v.read_text(encoding="utf-8"))):
            if kind == "Measure":
                assert entity == bpp.MEASURE_TABLE and prop in measures, f"{v.parent.name}: [{prop}]"
            else:
                assert prop in columns.get(entity, set()), f"{v.parent.name}: {entity}[{prop}]"


def test_tmdl_contains_every_measure_once():
    text = (MODEL_DEF / "tables" / f"{bpp.MEASURE_TABLE}.tmdl").read_text(encoding="utf-8")
    declared = re.findall(r"^\tmeasure '((?:[^']|'')+)'", text, re.MULTILINE)
    assert sorted(declared) == sorted(m["name"] for m in bpp.parse_measures())


def test_measure_parser_does_not_split_continuation_lines():
    names = [m["name"] for m in bpp.parse_measures()]
    assert not [n for n in names if "[" in n or n.startswith(("CALCULATE", "DIVIDE", "IF "))]
    assert "Portfolio Car Registrations CP" in names


def test_no_relationship_joins_official_and_simulated_tables():
    official = {t for t, s in bpp.TABLES.items() if s["class"] in ("OFFICIAL", "DERIVED")}
    simulated = {t for t, s in bpp.TABLES.items() if s["class"] == "SIMULATED"}
    for ft, _, tt, _ in bpp.RELATIONSHIPS:
        assert not ({ft, tt} & official and {ft, tt} & simulated), (ft, tt)


def _visual_text(v) -> str:
    return json.dumps(json.loads(v.read_text(encoding="utf-8")))


def test_simulated_data_is_labelled_wherever_it_is_shown():
    _, measures = model_fields()
    sim_measures = {m["name"] for m in bpp.parse_measures() if m["folder"] in ("5 Funnel", "6 Inventory", "7 Actions")}
    for v in VISUALS:
        data = json.loads(v.read_text(encoding="utf-8"))
        refs = list(field_refs(data))
        uses_sim = any((k == "Measure" and p in sim_measures) or (k == "Column" and bpp.TABLES.get(e, {}).get("class") == "SIMULATED")
                       for k, e, p in refs)
        if uses_sim:
            text = json.dumps(data)
            page = v.parents[2].name
            assert "SIMULATED" in text or page == "dealership_operations", f"unlabelled simulated visual {v.parent.name}"
    dealer_page = " ".join(_visual_text(v) for v in VISUALS if v.parents[2].name == "dealership_operations")
    assert "SIMULATED DATA" in dealer_page and "not actual dealership performance" in dealer_page
    exec_page = " ".join(_visual_text(v) for v in VISUALS if v.parents[2].name == "executive_overview")
    assert "SIMULATED DEALERSHIP DATA" in exec_page


def test_official_pages_do_not_use_simulated_fields():
    sim_measures = {m["name"] for m in bpp.parse_measures() if m["folder"] in ("5 Funnel", "6 Inventory", "7 Actions")}
    for v in VISUALS:
        if v.parents[2].name in ("oem_market_share", "ev_analytics", "portfolio_opportunity"):
            for k, e, p in field_refs(json.loads(v.read_text(encoding="utf-8"))):
                assert p not in sim_measures and bpp.TABLES.get(e, {}).get("class") != "SIMULATED", (v.parent.name, p)


def test_no_absolute_paths_or_credentials_in_project():
    for p in list(bpp.SM_DIR.rglob("*")) + list(bpp.RP_DIR.rglob("*")):
        if p.is_file() and ".pbi" not in p.parts:
            text = p.read_text(encoding="utf-8", errors="ignore")
            assert not re.search(r"[A-Za-z]:\\\\Users|[A-Za-z]:/Users|password|pwd=", text, re.IGNORECASE), p


RESULTS = ROOT / "reports" / "powerbi_validation.csv"


@pytest.mark.skipif(not RESULTS.exists(), reason="no recorded Power BI engine validation (BLOCKED, not passed)")
def test_recorded_engine_validation_passed():
    """Checks the evidence written by python/validate_powerbi.py; it does not re-run Power BI."""
    r = pd.read_csv(RESULTS)
    assert len(r) > 500 and r.passed.all(), r[~r.passed]
    for area in ["model", "separation", "portfolio brands", "OEM share", "segment growth & EV", "SIMULATED inventory"]:
        assert area in set(r.area)
