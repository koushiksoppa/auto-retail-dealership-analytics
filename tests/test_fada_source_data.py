"""Tests against the committed FADA extract and (when present) the source PDFs."""
import hashlib

import pandas as pd
import pytest

import auto_retail_analysis as ara
import fada_extract as fx
from paths import CONFIG_DIR, FADA_MANIFEST, FADA_PROCESSED_DIR, FADA_RAW_DIR

PDFS_PRESENT = all((FADA_RAW_DIR / f).exists() for f in pd.read_csv(FADA_MANIFEST).local_file)
needs_pdfs = pytest.mark.skipif(not PDFS_PRESENT, reason="FADA source PDFs not present (BLOCKED, not passed)")


@pytest.fixture(scope="module")
def oem():
    return pd.read_csv(FADA_PROCESSED_DIR / "fada_oem_monthly.csv", dtype={"period_month": str})


def test_manifest_has_required_provenance_fields():
    m = pd.read_csv(FADA_MANIFEST)
    for col in ["source_url", "source_organization", "reporting_periods", "vehicle_segments_used",
                "retrieved_date", "sha256", "extraction_method", "validation_status", "known_limitations",
                "data_classification"]:
        assert col in m.columns and m[col].notna().all(), col
    assert (m.data_classification == "official").all()
    assert m.source_url.str.startswith("https://fada.in/").all()


@needs_pdfs
@pytest.mark.source_pdfs
def test_source_pdf_checksums_match_manifest():
    for r in pd.read_csv(FADA_MANIFEST).itertuples():
        assert hashlib.sha256((FADA_RAW_DIR / r.local_file).read_bytes()).hexdigest() == r.sha256


@needs_pdfs
@pytest.mark.source_pdfs
def test_reextraction_reproduces_committed_extract(tmp_path):
    checks = fx.run(out_dir=tmp_path)
    assert checks.passed.all(), checks[~checks.passed]
    for name in ["fada_oem_monthly.csv", "fada_category_monthly.csv", "fada_fuel_mix_monthly.csv"]:
        pd.testing.assert_frame_equal(pd.read_csv(tmp_path / name), pd.read_csv(FADA_PROCESSED_DIR / name))


def test_committed_extraction_checks_all_passed():
    checks = pd.read_csv(FADA_PROCESSED_DIR / "extraction_checks.csv")
    assert len(checks) > 0 and checks.passed.all()


def test_every_month_reconciles_to_published_segment_total(oem):
    body = oem[oem.row_level.isin(["top", "others"])].groupby(["segment", "period_month"]).units.sum()
    total = oem[oem.row_level == "total"].set_index(["segment", "period_month"]).units
    pd.testing.assert_series_equal(body.sort_index(), total.sort_index(), check_names=False)
    assert len(total) == 2 * 16   # PV and 2W x (8 current + 8 prior months)


def test_required_columns_and_types(oem):
    for col, dtype in ara.OEM_COLUMNS.items():
        assert col in oem.columns
    assert pd.api.types.is_integer_dtype(oem.units)
    assert oem.period_month.str.match(r"^\d{4}-\d{2}$").all()
    assert set(oem.segment) == {"PV", "2W"}


def test_every_raw_oem_is_mapped(oem):
    mapping = ara.load_mapping(CONFIG_DIR / "oem_mapping.csv")
    body = oem[oem.row_level.isin(["top", "others"])]
    unmapped = set(zip(body.segment, body.oem_raw)) - set(zip(mapping.segment, mapping.oem_raw))
    assert not unmapped


def test_dash_values_are_flagged(oem):
    dash = oem[oem.reported_as_dash]
    assert (dash.units == 0).all() and (dash.published_share_pct == 0).all()


def test_indian_number_parsing():
    assert fx.parse_units("17,90,188") == (1790188, False)
    assert fx.parse_units("4,27,213") == (427213, False)
    assert fx.parse_units("-") == (0, True)


def test_real_data_pipeline_runs_and_reproduces_known_totals():
    r = ara.run_analysis()
    seg = r["segment_summary"].set_index("segment")
    assert seg.loc["PV", "units_current"] == 3_301_877
    assert seg.loc["PV", "units_prior"] == 2_703_948
    port = r["portfolio"].set_index("brand")
    assert port.loc["Toyota", "units_current"] == 218_392
    assert port.loc["Ather", "segment"] == "2W"
