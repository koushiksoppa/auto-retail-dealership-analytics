# Analytical Methodology

## Layer A: market intelligence (OFFICIAL data)

### Periods
- **Current period:** February–September 2026. **Prior period:** February–September 2025. Both are inclusive calendar months, set in `config/market_analysis.yaml`.
- The configuration is rejected unless both periods have the same number of months and sit exactly 12 months apart, so year-over-year comparisons are always like for like.
- Periods are calendar months, not Indian financial years (April–March). FADA's FY tables in the March release are not used.

### Denominators and segment isolation
- Every share is computed against the **OEM's own segment total**, using FADA's published Total row for that segment and month.
- Car brands (Toyota, Honda Cars, JSW MG Motor, Mercedes-Benz) are measured against **passenger vehicles (PV)**. Ather is measured against **two-wheelers (2W)**.
- PV and 2W are never added into one denominator. The Power BI measures return BLANK if a context spans both segments.
- "Portfolio car brands" is the sum of the four car brands, measured against PV only.

### OEM normalisation
- `config/oem_mapping.csv` maps every raw FADA label, by segment, to a canonical OEM and, where applicable, a portfolio brand.
- The mapping is explicit and auditable. An unknown label stops the pipeline with an error naming the label.
- FADA prints some OEM groups as a group row followed by indented member rows, for example *Mercedes-Benz Group* = Mercedes-Benz India + Mercedes-Benz AG + Daimler AG + "Mercedes Benz". Only the group row is used; member rows are kept for validation and never added again.
- Honda Motorcycle & Scooter India (2W) is a different entity from Honda Cars India (PV). Only Honda Cars is a portfolio brand.

### Formulas

| Metric | Formula | Undefined when |
|---|---|---|
| YoY growth % | (current ÷ prior − 1) × 100 | prior = 0 or missing. The result is NaN or BLANK, never 0 or −100% |
| Market share % | units ÷ own-segment units × 100 | segment total = 0 or missing |
| Share change (pp) | current share % − prior share % | either share undefined |
| Share change (bps) | (current share % − prior share %) × 100. This equals (current share − prior share) × 10,000 when shares are fractions | as above |
| Growth gap (pp) | brand growth % − own-segment growth % | as above |
| Constant-share registrations | current-period segment units × prior-period share % ÷ 100 | prior share undefined |
| Registration gap | constant-share registrations − actual current-period registrations. Positive means registrations forgone; negative means share gained | as above |
| EV units (estimated) | published EV share % × published segment total ÷ 100, for each month | EV share not published |
| EV share % (period) | Σ EV units ÷ Σ segment units | any month missing |

### Aggregation
- **Weighted, not averaged.** Period shares are calculated from summed units ÷ summed segment totals. Monthly percentages are never averaged.
- **Months used.** Period sums cover only the configured months. A missing month stops the pipeline; it is never zero-filled.
- **Partial listings.** OEMs that FADA lists in only some months (they're in "Others" otherwise) are flagged `coverage_complete = False`, and their growth and share metrics are left blank.
- **Low base.** OEM growth is suppressed when the prior-period base is below `min_base_units_for_growth` (1,000 units). This stops results such as +193,133% for a new entrant growing from 6 units.

### Validation before calculation
1. **Extraction** (`fada_extract.py`, 917 checks):
   - top-level plus Others rows equal the published Total for every segment-month;
   - group rows equal the sum of their members;
   - every recomputed share matches FADA's printed share within rounding (0.011 pp);
   - OEM table totals equal the category summary table;
   - category rows add up to the overall Total;
   - fuel mixes sum to 100% (within ±0.1);
   - there are no duplicate OEM rows.
2. **Analysis** (`auto_retail_analysis.py`) re-checks:
   - required columns, types and `YYYY-MM` periods;
   - segment and row-level codes;
   - missing or non-numeric units;
   - duplicates and missing months;
   - unmapped OEMs;
   - reconciliation, group/member equality, and two raw labels mapping to one OEM in the same month.
3. **SQL Server** repeats the staging checks (`05_validate_staging.sql`) and the post-load reconciliation (`08_reconciliation.sql`).
4. **Cross-check.** `sql_server_pipeline.py` compares 380 SQL view values with the Python outputs.

### Rounding
Calculations use full precision. Values are rounded only for presentation: percentages to 2 dp, basis points to 1 dp, units to whole numbers. Python and SQL agree within 1e-4 percentage points and 1e-3 units.

### Management attention rules
Illustrative thresholds in `config/market_analysis.yaml`: growth gap ≤ −5 pp, share change ≤ −10 bps, registration gap ≥ 1,000 units. These are **not industry benchmarks**.

## Layer B: dealership operations (SIMULATED data)

### Generator design (`python/dealership_kpi_simulation.py`)
- **Assumptions** live in `config/simulation.yaml`: seed, period, scale, lead-source mix and qualification rates, stage probabilities, delivery lead times, model mix and prices, stocking factors and action rules. Hidden per-salesperson parameters are written separately to `simulation_latent_parameters.csv`. **Observations** are the generated tables.
- **Determinism:** one NumPy `default_rng(seed)` stream, fixed iteration order and fixed CSV formatting. The same seed and configuration produce byte-identical files; the tests check this.
- **Leads:** Poisson volumes per dealership-month, scaled by `scale`. Each lead then passes through Lead → Qualified → Test drive → Booking in strict order, with dates that never decrease.
  - A failed transition records `lost_stage`, `lost_reason` and `lost_date`.
  - A stage date that would fall after the snapshot date leaves the lead *Open*, not lost.
  - Bookings can be cancelled before delivery.
- **Inventory:**
  - Opening stock is sized from expected demand × `opening_stock_months` × the model's stocking factor, with receipt dates up to 150 days before the start date.
  - Weekly replenishment is Poisson at expected weekly demand × stocking factor.
- **Allocation:** bookings are served in order of requested date, from the oldest vehicle that has been received and prepared (FIFO). If no vehicle is ready, the next arrival is used. A booking that can't be fulfilled by the snapshot date stays *Booked – awaiting delivery*.
  - One VIN is delivered at most once, and one booking has at most one delivery.
  - Delivery never precedes receipt plus the preparation days.
- **Integrity checks** run on every generation and raise an error if any fails: unique keys, foreign keys, salesperson–dealership and model–brand consistency, stage order, date bounds, lost-lead rules, and delivery/booking/VIN consistency.

### KPI definitions
- **Funnel (lead cohort):** counts by lead-creation date. Stage conversion = later stage ÷ earlier stage; lead-to-delivery = deliveries ÷ leads.
- **Follow-up:** share of leads first contacted within `sla_hours` (4, illustrative); median and P90 hours to first contact.
- **Inventory ageing:** age = snapshot date − receipt date. Buckets 0–30, 31–60, 61–90 and >90 days have inclusive upper bounds, so 30 → 0–30 and 31 → 31–60. Aged means more than 60 days.
- **Sell-through** = deliveries in the period ÷ (opening stock + receipts).
- **Turnover (annualised)** = cost of vehicles delivered ÷ average month-end stock cost × 12 ÷ (days ÷ 30.4).
- **Days of supply** = closing stock ÷ (deliveries in the last 30 days ÷ 30).
- **Concentration:** share of stock held by the top model, and the Herfindahl index (Σ share² × 10,000).
- **Action triggers:** each rule in `config/simulation.yaml` gives a KPI, comparator, threshold, responsible role, review cadence, recommended action and threshold basis.
  - All thresholds are illustrative, except the PV days-of-supply rule. That one uses FADA's stated recommendation of a 21-day PV inventory level from the September 2026 release, and is applied to PV dealerships only.
