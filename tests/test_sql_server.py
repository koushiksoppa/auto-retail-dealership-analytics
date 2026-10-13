"""SQL Server tests: static checks always run; the integration test needs a reachable server
(set AUTORETAIL_SQL_SERVER, default .\\SQLEXPRESS) and is SKIPPED - i.e. blocked, not passed - otherwise."""
import os
import re

import pandas as pd
import pytest

import sql_server_pipeline as ssp

SQL_FILES = sorted(ssp.SQL_DIR.glob("*.sql"))


def test_sql_scripts_present_in_execution_order():
    names = [p.name for p in SQL_FILES]
    for expected in ["00_create_database.sql", "01_schemas.sql", "02_staging_tables.sql", "03_dw_tables.sql",
                     "04_dim_date.sql", "05_validate_staging.sql", "06_transform_load.sql", "07_analysis_views.sql",
                     "08_reconciliation.sql", "09_business_queries.sql"]:
        assert expected in names


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_sql_comments_balanced_and_no_secrets(path):
    text = path.read_text(encoding="utf-8")
    assert text.count("/*") == text.count("*/"), "unbalanced or nested block comment"
    assert not re.search(r"(password|pwd)\s*=", text, re.IGNORECASE)


def test_split_batches_on_go_lines_only():
    sql = "SELECT 1;\nGO\nSELECT 'GOOD';\n  go  \nSELECT 2"
    assert ssp.split_batches(sql) == ["SELECT 1;", "SELECT 'GOOD';", "SELECT 2"]


def test_compare_flags_value_mismatch_and_missing_rows():
    py = pd.DataFrame({"k": ["a", "b"], "x": [1.0, 2.0]})
    sq = pd.DataFrame({"k": ["a", "c"], "X": [1.00001, 5.0]})
    out = pd.DataFrame(ssp._compare("t", py, sq, ["k"], {"x": ("X", 1e-4)}))
    assert out.loc[out.key == "a", "passed"].all()
    assert not out.loc[out.key.isin(["b", "c"]), "passed"].any()


def test_compare_treats_null_vs_value_as_mismatch():
    py = pd.DataFrame({"k": ["a"], "x": [float("nan")]})
    sq = pd.DataFrame({"k": ["a"], "X": [0.0]})
    assert not ssp._compare("t", py, sq, ["k"], {"x": ("X", 1e-4)})[0]["passed"]


def test_connection_string_uses_windows_auth_without_password(monkeypatch):
    monkeypatch.delenv("AUTORETAIL_SQL_CONNECTION_STRING", raising=False)
    try:
        cs = ssp.connection_string(r".\SQLEXPRESS", "AutoRetailAnalytics", None)
    except RuntimeError:
        pytest.skip("no SQL Server ODBC driver installed (BLOCKED)")
    assert "Trusted_Connection=yes" in cs and "PWD" not in cs.upper()


def _server_available() -> bool:
    try:
        import pyodbc  # noqa: F401
        server = os.environ.get("AUTORETAIL_SQL_SERVER", r".\SQLEXPRESS")
        with ssp.connect(ssp.connection_string(server, "master", None)):
            return True
    except Exception:
        return False


@pytest.mark.sqlserver
@pytest.mark.skipif(not _server_available(), reason="SQL Server not reachable (BLOCKED, not passed)")
def test_full_sql_pipeline_reconciles_with_python():
    assert ssp.main([]) == 0
    recon = pd.read_csv(ssp.RECON_PATH)
    assert len(recon) > 0 and recon.passed.all()
    for area in ["segment growth", "portfolio brands", "OEM share", "EV adoption", "SIMULATED funnel", "SIMULATED inventory"]:
        assert area in set(recon.area)
