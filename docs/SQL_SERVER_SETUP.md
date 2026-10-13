# SQL Server Setup

Target environment: **Microsoft SQL Server 2025 Express** on Windows with Windows authentication. The scripts were run against SQL Server 2025 (RTM) 17.0.1000.7 Express Edition, instance `.\SQLEXPRESS`.

## 1. Prerequisites
- SQL Server Express (`.\SQLEXPRESS`) with Windows authentication.
- *ODBC Driver 17 or 18 for SQL Server*.
- Optionally, `sqlcmd` for running the scripts by hand.
- The Python environment from the README (`pyodbc` is listed in `requirements.txt`).
- The Python pipelines already run, so the CSVs under `data/processed`, `data/simulated` and `reports/` exist.

## 2. Connection configuration
No server names or credentials are hard-coded in committed files.

| Setting | Default | Override |
|---|---|---|
| Server | `.\SQLEXPRESS` | `--server` or env `AUTORETAIL_SQL_SERVER` |
| Database | `AutoRetailAnalytics` | `--database` or env `AUTORETAIL_SQL_DATABASE` |
| Authentication | Windows (`Trusted_Connection=yes`) | A full ODBC string in env `AUTORETAIL_SQL_CONNECTION_STRING` (for example SQL authentication). It is read at run time and never written to disk |

Example in PowerShell, for this session only:

```powershell
$env:AUTORETAIL_SQL_SERVER = "localhost\SQLEXPRESS"
```

## 3. One-command build, load and reconcile

```powershell
.\.venv\Scripts\python python\sql_server_pipeline.py
```

The pipeline runs these steps in order:

| Step | What runs | Purpose |
|---|---|---|
| create | `CREATE DATABASE` if missing | |
| schema | `01_schemas.sql`, `02_staging_tables.sql`, `03_dw_tables.sql`, `04_dim_date.sql` | Schemas, staging tables (recreated), star schema (created if missing), date dimension |
| stage | Python loader | Truncates and loads the CSVs into the `stg.*` text tables. Empty cells become NULL |
| transform | `05_validate_staging.sql` | Stops with error 50001 on any failed check |
| | `06_transform_load.sql` | Full refresh in one transaction |
| | `07_analysis_views.sql` | `rpt.*` views |
| | `08_reconciliation.sql` | Stops with error 50002 on any failure |
| reconcile | Python comparison | Compares the `rpt.*` views with the Python outputs and writes `reports/sql_python_reconciliation.csv` |

Options:
- `--steps reconcile` runs only the named steps.
- `--rebuild` drops and recreates the `dw` tables and `rpt` views. Staging data and audit history are kept.

## 4. Manual execution with sqlcmd (alternative)

```powershell
sqlcmd -S .\SQLEXPRESS -E -C -i sql\00_create_database.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -i sql\01_schemas.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -i sql\02_staging_tables.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -i sql\03_dw_tables.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -i sql\04_dim_date.sql
.\.venv\Scripts\python python\sql_server_pipeline.py --steps stage
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -b -i sql\05_validate_staging.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -b -i sql\06_transform_load.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -i sql\07_analysis_views.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -b -i sql\08_reconciliation.sql
sqlcmd -S .\SQLEXPRESS -E -C -d AutoRetailAnalytics -i sql\09_business_queries.sql
```

Staging uses the Python loader rather than `BULK INSERT`. `BULK INSERT` reads files as the SQL Server service account, which usually can't read a user's project folder on Express.

## 5. Model summary

**Schemas**
- `stg`: raw text landing tables.
- `dw`: typed star schema.
- `rpt`: analysis views.
- `audit`: validation results.

**Official market star**
- `FactMarketRegistrations` (month × OEM) and `FactMarketSegmentMonth` (month × segment; published total and EV share).
- Dimensions: `DimDate`, `DimVehicleSegment`, `DimOEM`.
- Mapping and configuration: `MapOEMSourceName` (raw label → OEM, auditable) and `AnalysisPeriod`.

**Simulated dealership star**
- Facts: `FactLeadFunnel` (lead), `FactVehicleInventory` (VIN), `FactVehicleDelivery` (delivery), `FactInventorySnapshot` (month-end × VIN).
- Dimensions: `DimDealership`, `DimSalesperson`, `DimVehicleModel`.
- `FactActionTrigger` (dealership × action rule), loaded from `reports/dealership/tables/action_triggers.csv` for Power BI.

**Report-ready derived table**
- `MarketAttention` (portfolio brand attention flags), loaded from `reports/auto_retail/tables/management_attention.csv`, `DataClassification = DERIVED`.

**Separation.** Official and simulated facts are in separate tables. Every fact has a `DataClassification` column enforced by a `CHECK` constraint: `OFFICIAL` or `SIMULATED`.

**Design choices**
- Business keys, or `ROW_NUMBER` surrogate keys over business keys, so keys are stable across full reloads.
- `DECIMAL(14,2)` for money, `DECIMAL(5,2)` for published percentages, and `INT` or `BIGINT` for units.
- Nullable columns only where *not applicable* is a real state. Example: `EVSharePct` stays NULL if FADA did not publish it; it is never set to 0.
- `CHECK` constraints enforce:
  - stage order;
  - lost-lead completeness;
  - stock status against delivery date;
  - ageing bucket against age;
  - the classification labels.
- `FactInventorySnapshot.AgeingBucketOrder` is a persisted computed column (1–4) so reports sort buckets 0–30 → >90 rather than alphabetically.
- Ratios in views are computed in FLOAT, which avoids DECIMAL scale truncation, and presented as `DECIMAL(18,6)`.
- Period filters use `MonthDateKey BETWEEN StartDateKey AND EndDateKey` on the clustered primary key, which keeps them SARGable.
- Nonclustered indexes support the dealership/date access paths and the segment/month aggregation.

## 6. Verified results (this environment, 2026-10-09)

| Check | Result |
|---|---|
| Staging validation | 30 of 30 checks passed (re-run 2026-10-11 after adding the report tables) |
| Post-load reconciliation | 9 of 9 checks passed |
| Python vs SQL | 380 of 380 comparisons agree: segment growth, portfolio brands, OEM share, EV, simulated funnel, simulated inventory |
| Rerun (plain and `--rebuild`) | Idempotent |

## 7. Known issue

On one run the Express instance returned error 802 ("insufficient memory available in the buffer pool") during `--rebuild`. The next three runs, including another `--rebuild`, completed normally. This is an environment memory-pressure issue, not a script defect: the development machine has 7.9 GB of RAM, of which about 1 GB was free while browsers were running. The same pressure explains run times between 5 s and 3 min for the full pipeline. If it happens, close other memory-heavy applications and rerun.
