"""Repository-relative paths. Every script resolves files from here so the
project runs from a fresh clone without absolute paths."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CONFIG_DIR = ROOT / "config"
FADA_RAW_DIR = ROOT / "data" / "external" / "fada" / "raw"
FADA_MANIFEST = ROOT / "data" / "external" / "fada" / "source_manifest.csv"
FADA_PROCESSED_DIR = ROOT / "data" / "processed" / "fada"
SIMULATED_DIR = ROOT / "data" / "simulated" / "dealership"

MARKET_REPORT_DIR = ROOT / "reports" / "auto_retail"
MARKET_TABLE_DIR = MARKET_REPORT_DIR / "tables"
MARKET_CHART_DIR = MARKET_REPORT_DIR / "charts"
DEALER_REPORT_DIR = ROOT / "reports" / "dealership"
DEALER_TABLE_DIR = DEALER_REPORT_DIR / "tables"
DEALER_CHART_DIR = DEALER_REPORT_DIR / "charts"
