# Data Sources and Provenance

## Classification

| Class | Meaning | Where |
|---|---|---|
| **OFFICIAL** | Published by the Federation of Automobile Dealers Associations (FADA), India | `data/external/fada/raw/*.pdf`: immutable inputs, **not distributed with the repository** (see Redistribution) |
| **DERIVED** | Extracted or computed from official data by this project, with no new observations | `data/processed/fada/`, `reports/auto_retail/` |
| **CONFIG** | Analyst decisions: OEM name mapping, comparison periods, illustrative thresholds | `config/` |
| **SIMULATED** | Seeded synthetic dealership operations; no real dealership, person or customer | `data/simulated/dealership/`, `reports/dealership/` |

No observation in this project was typed in by hand or invented. Every official number comes from the PDFs through `python/fada_extract.py`.

## FADA monthly vehicle retail data

- **Publisher:** FADA India, `https://fada.in/press-release-list.php`, monthly releases titled "FADA releases <Month YYYY> Vehicle Retail Data".
- **Basis:** retail registrations collated with the Ministry of Road Transport & Highways from Regional Transport Offices (RTOs). The number of RTOs reporting each month is recorded in the manifest.
- **Files used:** the eight releases for **February–September 2026**. Each prints the current month next to the same month of the previous year, so these eight files give both comparison periods (Feb–Sep 2026 and Feb–Sep 2025) in one consistent format.
  - The FADA list page has no February or March 2025 release, which is another reason the 2025 values are taken from the comparison columns of the 2026 releases.
- **Retrieved:** 2026-10-09 from the URLs in [`data/external/fada/source_manifest.csv`](../data/external/fada/source_manifest.csv). The download was approved by the repository owner.
- **Integrity:**
  - Each file's SHA-256 is recorded in the manifest.
  - `python python/fetch_fada_sources.py --verify` re-checks the files.
  - The extractor refuses to run if a checksum differs.
- **Tables extracted, per release:**
  1. *All India Vehicle Retail Data for <Mon'YY>*: category totals (2W, 3W, PV, TRAC, CE, CV, Total) for the current month, previous month and same month of the prior year.
  2. *Fuel-wise retail data*: fuel-mix percentages for two-wheelers and passenger vehicles, including EV share.
  3. *OEM-wise market share*: annexures for two-wheelers and passenger vehicles, with units and printed shares for the current month and the prior-year month.

  3W, CV, tractor and construction-equipment OEM tables are not extracted because the analysis doesn't use them.
- **Units:** vehicle retail registrations (count). Shares are percentages as printed, to 2 decimal places.

## Manifest fields

`release_month`, `local_file`, `source_url`, `source_organization`, `publication_title`, `publication_date`, `reporting_periods`, `vehicle_segments_used`, `data_collated_as_on`, `rto_coverage`, `retrieved_date`, `sha256`, `bytes`, `data_classification`, `extraction_method`, `validation_status`, `known_limitations`.

## Known source limitations

- **Revisions.** FADA restates earlier months in later releases. For example, April 2026 PV is 407,355 in the April release and 428,734 in the May release. OEM-level detail exists only in each month's own release, so this project uses those first-published OEM tables throughout. Totals and shares therefore come from one consistent source per month. The differences are listed in `data/processed/fada/category_revisions_across_releases.csv`.
- **Prior-year columns.** The 2025 figures are as printed in the 2026 releases and may differ from the releases originally published in 2025.
- **Reporting threshold.** OEMs below roughly 0.1% (2W) or a small share (PV) are folded into "Others". An OEM listed only in some months is flagged, and its growth and share are not computed. The extractor never fills those months with zeros.
- **'-' entries.** FADA prints '-' for some nil member rows, with a 0.00% share. These are stored as 0 with `reported_as_dash = True`.
- **Presentation changes.** Labels vary across releases: Construction Equipment appears as "CE", "Wheeled CE" and "Wheeled - CE", and Bajaj appears as a single row in some months and as a group with Chetak in others. The parser handles these explicitly, and the mapping documents them.
- **Base effect.** FADA notes that September 2025 was depressed by purchase deferral ahead of GST 2.0, which took effect on 22 September 2025.
- **EV units.** EV units are derived from the 2-decimal EV percentage. The rounding band is reported in `reports/auto_retail/tables/ev_period_summary.csv`.

## Redistribution

The original FADA PDFs are **not included in this repository**. FADA's website terms grant no licence to redistribute its documents, and the site states "All rights reserved" (checked 2026-10-11). The PDFs also contain FADA's published media-contact details.

What the repository does publish:
- **Provenance:** [`source_manifest.csv`](../data/external/fada/source_manifest.csv), with each file's source URL, publication details, retrieval date and SHA-256 checksum.
- **Extracted tables:** the extraction code and the tables extracted from the PDFs (`data/processed/fada/`). These are factual registration figures, attributed to FADA.

To reproduce the extraction from the original documents:

```powershell
.\.venv\Scripts\python python\fetch_fada_sources.py      # downloads from the manifest URLs; keeps a file only if its SHA-256 matches
.\.venv\Scripts\python python\fada_extract.py
```

`data/external/fada/raw/*.pdf` is listed in `.gitignore`. Without the PDFs, the analysis still runs from the committed extract. The two tests that need the PDFs are reported as skipped (blocked), not passed.

## Simulated data

`python/dealership_kpi_simulation.py` generates every file under `data/simulated/dealership/` from `config/simulation.yaml` with a fixed seed.
- Identifiers are synthetic: `DLR01`, `SP-DLR01-01`, `LD000001`, `SIMVIN0000001`.
- Model names are generic descriptions such as "Toyota Compact SUV". They are not product names and don't describe real models.
- Every row carries `data_classification = SIMULATED`.
