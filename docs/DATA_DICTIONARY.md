# Data Dictionary

Dates use ISO format (`YYYY-MM-DD`); months use `YYYY-MM`. Units are vehicle registrations unless stated otherwise. Money is in Indian rupees (INR). An empty cell means *not applicable or not reached*. It is never a stand-in for zero.

## Official extract: `data/processed/fada/` (DERIVED from OFFICIAL PDFs)

### `fada_oem_monthly.csv`
Grain: release × segment × OEM row × period month.

| Column | Type | Description |
|---|---|---|
| release_month | text `YYYY-MM` | FADA release (the current month of that release) |
| period_month | text `YYYY-MM` | Month the value refers to: the current month, or the same month one year earlier |
| value_role | text | `current` or `prior_year` |
| segment | text | `PV` (passenger vehicles) or `2W` (two-wheelers) |
| oem_raw | text | OEM label exactly as printed by FADA |
| row_level | text | `top` (standalone OEM or group row), `member` (indented member of a group), `others` (FADA residual) or `total` |
| parent_oem_raw | text | Group label for `member` rows; empty otherwise |
| units | integer | Retail registrations |
| reported_as_dash | boolean | True where FADA printed '-' (nil). Units are 0 in that case |
| published_share_pct | decimal | Share as printed by FADA (2 dp) |
| source_file, source_page | text, integer | Provenance |

### `fada_category_monthly.csv`
Grain: release × category × period month × role. `category` takes the values 2W, 3W, PV, TRAC, CE, CV and TOTAL. `value_role` is `current`, `previous_month` or `prior_year`. The other columns are `units`, `reported_as_dash` and the source columns.

### `fada_fuel_mix_monthly.csv`
Grain: release × segment (2W, PV) × fuel × period month × role. `share_pct` is the published fuel share (%).

### Other extract files
- `fada_release_metadata.csv`: collation date and RTO coverage for each release.
- `extraction_checks.csv`: one row per validation check, with `check`, `key`, `expected`, `actual`, `passed` and `note`.
- `category_revisions_across_releases.csv`: category-months whose value differs between releases.

## Configuration: `config/`

| File | Content |
|---|---|
| `oem_mapping.csv` | `segment`, `oem_raw` → `oem_canonical`, `portfolio_brand` (empty if not a portfolio brand), `mapping_note` |
| `market_analysis.yaml` | Periods, segment names, portfolio brands, EV-only brand assumption, minimum base for growth, attention thresholds, previously quoted figures to cross-check |
| `simulation.yaml` | All simulation assumptions and management-action rules |

## Market outputs: `reports/auto_retail/tables/` (DERIVED)

| File | Grain | Key columns |
|---|---|---|
| `oem_monthly_share.csv` | segment × month × canonical OEM | units, segment_units, share_pct, portfolio_brand, is_residual, period |
| `segment_monthly_yoy.csv` | segment × month | units_prior, units_current, yoy_growth_pct, ev_share_pct_prior/current, ev_share_change_bps |
| `segment_period_summary.csv` | segment | units_prior, units_current, yoy_growth_pct, incremental_units |
| `oem_period_share.csv` | segment × canonical OEM | units, months reported, coverage_complete, shares, share_change_pp/bps, yoy_growth_pct, growth_gap_pp, constant_share_units, registration_gap_units, coverage_note |
| `portfolio_brand_performance.csv` | portfolio brand, plus the car aggregate | as above, plus segment_growth_pct and is_aggregate |
| `portfolio_monthly_share.csv` | month | portfolio_units, segment_units, portfolio_share_pct, one share column per car brand |
| `ev_period_summary.csv` | segment | ev_units_est_prior/current, ev_share_prior/current_pct, ev_share_change_bps, ev_units_growth_pct, ev_rounding_band_units_* |
| `ev_brand_share.csv` | EV-only brand | share_of_segment_ev_prior/current_pct (uses the EV-only assumption) |
| `management_attention.csv` | brand | status, triggers, growth, share change, registration gap |
| `competitor_moves.csv` | segment × list × OEM | top 5 share gainers and losers among fully reported OEMs |
| `brief_figure_verification.csv` | figure | brief_value, reproduced, status, note |
| `headline_metrics.csv` | metric | value |

## Simulated dealership data: `data/simulated/dealership/` (SIMULATED)

Every table except `simulation_latent_parameters.csv` has `data_classification = SIMULATED`.

| Table | Grain | Columns |
|---|---|---|
| `dim_dealership` | dealership | dealership_id (PK, `DLRnn`), dealership_name, brand, segment, region, city |
| `dim_salesperson` | salesperson | salesperson_id (PK, `SP-DLRnn-nn`), dealership_id (FK), salesperson_name (synthetic label) |
| `dim_vehicle_model` | model | model_id (PK, `MDLnn`), brand, model_name (generic description), vehicle_category, powertrain (ICE, Hybrid or EV), segment, ex_showroom_price_inr |
| `fact_lead_funnel` | lead | lead_id (PK); dealership_id, salesperson_id, model_id (FKs); lead_source, lead_date, first_contact_hours, first_contact_within_sla; qualified_date, test_drive_date, booking_id, booking_date, requested_delivery_date, booking_cancelled_date; lead_status (Open, Lost, Booked - awaiting delivery, Delivered); lost_stage, lost_reason, lost_date; delivery_id, delivery_date, vin |
| `fact_vehicle_inventory` | vehicle | vin (PK, `SIMVINnnnnnnn`), dealership_id, model_id, receipt_date, purchase_cost_inr, delivery_date, stock_status (In stock or Delivered), days_in_inventory (to delivery, or to the snapshot date if unsold) |
| `fact_vehicle_delivery` | delivery | delivery_id (PK), booking_id (unique), lead_id (unique FK), vin (unique FK), dealership_id, model_id, salesperson_id, delivery_date, days_from_booking, days_in_inventory, ex_showroom_price_inr, discount_pct, invoice_amount_inr, purchase_cost_inr |
| `fact_inventory_snapshot` | month-end × vehicle in stock | snapshot_date, vin, dealership_id, model_id, purchase_cost_inr, age_days, ageing_bucket (0-30, 31-60, 61-90, >90), is_aged (age > 60) |
| `simulation_latent_parameters` | salesperson | conversion_factor and median_first_contact_hours: hidden generator parameters (assumptions, not observations) |
| `simulation_run_metadata.json` | run | seed, period, scale, configuration hash, row counts |

## Dealership KPI outputs: `reports/dealership/tables/` (SIMULATED)

| File | Content |
|---|---|
| `funnel_by_dealership.csv`, `funnel_by_lead_month.csv`, `funnel_by_source.csv`, `funnel_stages.csv` | Stage counts and conversion rates |
| `lost_reasons.csv` | Lost leads by stage and reason |
| `followup_by_dealership.csv`, `salesperson_performance.csv` | SLA compliance, response times, salesperson conversion and revenue |
| `inventory_by_model.csv`, `inventory_by_dealership.csv`, `inventory_trend.csv` | Stock, ageing buckets, aged share, sell-through, turnover, days of supply, concentration (HHI) |
| `dealership_scorecard.csv` | KPIs evaluated by the action rules |
| `action_triggers.csv` | dealership × rule: kpi, actual, comparator, threshold, breached, status, responsible_role, review_cadence, recommended_action, threshold_basis |

## SQL Server objects
Besides the tables above, `dw.FactActionTrigger` (SIMULATED; from `action_triggers.csv`) and `dw.MarketAttention` (DERIVED; from `management_attention.csv`) hold the two report-ready outputs that the Power BI report reads, so the report needs only SQL Server as a source.
The tables are described in `sql/03_dw_tables.sql`, with the grain stated in the header and constraints on every table. Views are described in `sql/07_analysis_views.sql`. See also `docs/SQL_SERVER_SETUP.md`.
