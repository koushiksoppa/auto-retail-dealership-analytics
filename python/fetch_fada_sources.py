"""Verify (and, if missing, download) the FADA source PDFs listed in the source manifest.

    python python/fetch_fada_sources.py            # verify checksums; download only missing files
    python python/fetch_fada_sources.py --verify   # verify only, never download

Downloads come only from the `source_url` recorded in data/external/fada/source_manifest.csv,
and a downloaded file is kept only if its SHA-256 matches the manifest. Existing files are
never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

import pandas as pd

from paths import FADA_MANIFEST, FADA_RAW_DIR


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verify", action="store_true", help="only verify files that are present")
    a = ap.parse_args(argv)
    manifest = pd.read_csv(FADA_MANIFEST, dtype=str)
    FADA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for r in manifest.itertuples():
        path = Path(FADA_RAW_DIR) / r.local_file
        if path.exists():
            ok = sha256_bytes(path.read_bytes()) == r.sha256
            print(f"{'OK      ' if ok else 'MISMATCH'} {r.local_file}")
            failures += not ok
            continue
        if a.verify:
            print(f"MISSING  {r.local_file}")
            failures += 1
            continue
        req = urllib.request.Request(r.source_url, headers={"User-Agent": "auto-retail-dealership-analytics/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if sha256_bytes(data) != r.sha256:
            print(f"REJECTED {r.local_file}: downloaded content does not match the manifest checksum")
            failures += 1
            continue
        path.write_bytes(data)
        print(f"FETCHED  {r.local_file} ({len(data):,} bytes)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
