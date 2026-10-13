# Power BI Report: Build, Refresh and Validation Guide

## 1. Status

| Item | Status | Evidence |
|---|---|---|
| Power BI Project `AutoRetailAnalytics.pbip`: TMDL semantic model and PBIR report | **Built** by `python/build_powerbi_project.py` | `tests/test_powerbi_project.py` (deterministic, up to date, all fields resolve) |
| Opens in Power BI Desktop 2.158.1304.0 (Microsoft Store) | **Verified** | The model loads 16 tables. Page screenshots are in `screenshots/` |
| Refresh from SQL Server (`.\SQLEXPRESS`, `AutoRetailAnalytics`, Windows auth) | **Verified** | Full refresh through Desktop's engine; every partition is Ready |
| 92 measures evaluate in Desktop's engine and match Python and SQL | **Verified: 574 of 574 checks** | `reports/powerbi_validation.csv` |
| `AutoRetailAnalytics.pbix` | **Not created: one manual step** | See section 6. No supported command-line route to *Save As .pbix* is available here |

Nothing in this folder was hand-assembled into a `.pbix`. The project is Power BI's own text format (PBIP), which Power BI Desktop opens directly.

## 2. Files

```
powerbi/
  AutoRetailAnalytics.pbip                  open this in Power BI Desktop
  AutoRetailAnalytics.SemanticModel/        TMDL: tables, partitions (SQL Server), relationships, measures
  AutoRetailAnalytics.Report/               PBIR: 5 pages, one JSON file per visual, custom theme
  measures/measures.dax                     source of all measures (the generator reads this file)
  theme/auto_retail_theme.json              report theme (copied into the report's resources)
  scripts/PbiDesktop.ps1                    launch / refresh / query / screenshot helper (PowerShell 5.1)
  screenshots/                              captured from Power BI Desktop by python/validate_powerbi.py
  MEASURE_CATALOG.md                        definition, numerator/denominator, format, context, data class
```

`AutoRetailAnalytics.SemanticModel` and `AutoRetailAnalytics.Report` are **generated**. To change the model or report, edit `measures/measures.dax`, the theme or `python/build_powerbi_project.py`, then regenerate. If you edit the report in Power BI Desktop and save, those edits go into the PBIP folders and are overwritten the next time the generator runs.

A plain **File > Save** on the open `.pbip` rewrites these folders in Desktop's own format. It doesn't create a `.pbix`. That happened once (2026-10-11); `tests/test_powerbi_project.py` then failed because the folders no longer matched the generator. Running `python python/build_powerbi_project.py` restores the validated version. The model sets Auto date/time **off** (`__PBI_TimeIntelligenceEnabled = 0`), so a save doesn't add hidden `LocalDateTable_*` tables. `DimDate` is the date table.

## 3. Prerequisites and configuration

- Power BI Desktop (Microsoft Store or installer build) on Windows.
- SQL Server with the `AutoRetailAnalytics` database loaded by `python python/sql_server_pipeline.py`.
- **Connection.** The model has two Power Query parameters, `SqlServer` (default `.\SQLEXPRESS`) and `SqlDatabase` (default `AutoRetailAnalytics`). To change them: Transform data > Edit parameters.
- **Authentication.** The SQL Server source uses Windows credentials. No credentials are stored in the project. The first manual refresh may ask for the credential type: choose **Windows > Use my current credentials**.
- **PBIP support.** Desktop 2.158 opened the project without enabling any preview features. On older builds, enable *File > Options > Preview features > Power BI Project (.pbip) save option* and *Store reports using enhanced metadata format (PBIR)*.

## 4. Build, open and refresh

```powershell
.\.venv\Scripts\python python\build_powerbi_project.py         # (re)generate the PBIP from measures/theme/DDL
start powerbi\AutoRetailAnalytics.pbip                         # or: File > Open in Power BI Desktop
```

In Power BI Desktop, select **Refresh** on the Home ribbon. The project is stored without data; the yellow "Some of the tables have incomplete or no data" bar goes away after the refresh.

## 5. Automated runtime validation

```powershell
.\.venv\Scripts\python python\validate_powerbi.py                 # launch, refresh, validate, close
.\.venv\Scripts\python python\validate_powerbi.py --steps --screenshots   # page screenshots only
```

`validate_powerbi.py` opens the project in Power BI Desktop, runs a full refresh through Desktop's local Analysis Services engine (TMSL over `Microsoft.PowerBI.AdomdClient`), queries the engine with DAX and DMVs, and compares the results with the Python outputs.

Results of the last run (2026-10-11): **574 of 574 checks passed.**

| Area | Checks | What is compared |
|---|---|---|
| model | 12 | the table set; date table marked; all partitions Ready; 92 measures with no semantic errors; 19 active relationships; fact row counts |
| separation | 7 | no relationship joins official and simulated tables. With PV and 2W both in context, the share, segment-total and EV-share measures return BLANK, so they never form a mixed denominator |
| segment growth & EV | 14 | PV/2W period units and growth; EV share and EV units for the prior and current period |
| portfolio brands / car aggregate | 59 | units, growth, own-segment growth, growth gap, shares, bps, constant-share units and registration gap for each brand and for the car portfolio |
| OEM share | 252 | units, coverage flag, shares, bps and growth for all 36 OEM rows (blanks where Python leaves values blank) |
| monthly | 96 | monthly units, same-month-last-year units, YoY growth, and published EV share for all 16 months |
| SIMULATED funnel / inventory / buckets / actions | 134 | funnel counts and conversions; SLA; stock, aged stock, average age, days of supply, sell-through, turnover, opening stock, receipts; age-bucket counts; action counts |

Defects found and fixed by this validation (none were visible to the earlier static checks):

1. **Measure parser.** DAX continuation lines starting at column 0 (`CALCULATE ( …, T[c] = TRUE () )`) were read as new measures. This broke 11 measures in the model, and the previously reported count of 89 measures was wrong: the correct count was 79, and it is now 92 with 13 report helpers.
2. **`Latest Snapshot Key`.** It took the latest snapshot inside the visual's filters, so an age bucket that is empty at month-end showed an older month's count. It now ignores dealership, model and bucket filters and respects only the date context.
3. **`Aged Inventory Units`.** It returned BLANK instead of 0 for dealerships whose stock has no aged vehicles.
4. **`Is Coverage Complete`.** It excluded FADA's residual "Others" rows, unlike the Python analysis. Ranking visuals now filter residual rows explicitly instead.
5. **Layout and readability**, found from the screenshots rather than by the engine: cards abbreviated 43,837 to "44K"; axis and legend titles showed raw field names; age buckets sorted ">90" first; long text was clipped in tables. Each is fixed in the generator. The age-bucket order now comes from a `AgeingBucketOrder` sort column added in SQL.

## 6. Remaining manual step: save as .pbix

1. Open `powerbi\AutoRetailAnalytics.pbip` in Power BI Desktop and select **Refresh**.
   Use **Save as**, not Save: Save writes back into the project folders instead of creating a `.pbix`.
2. **File > Save as**, choose the file type **Power BI file (*.pbix)**, and save it as `powerbi\AutoRetailAnalytics.pbix`.
3. Check the file opens again (File > Open). To repeat the engine checks against the open file, run `python python\validate_powerbi.py --steps validate --port <port>`. The port is in `%USERPROFILE%\Microsoft\Power BI Desktop Store App\AnalysisServicesWorkspaces\*\Data\msmdsrv.port.txt`.

A `.pbix` saved after refresh contains the imported data; check its size before deciding whether to commit it. The PBIP is the reviewable source.

## 7. Report pages

All pages are 1280 × 720 and use the `Auto Retail Executive` theme. Official pages carry a blue **OFFICIAL DATA - FADA** badge, and the dealership page an amber **SIMULATED DATA** badge. Every simulated visual on page 1 has an amber background and the title "SIMULATED".

| Page | Management question | Visuals |
|---|---|---|
| 1 Executive Overview | Is the portfolio keeping pace with the market? | **Cards:** PV and 2W market growth; portfolio car growth, share, share change (bps) and registration gap. **Line:** PV registrations by month, 2025 vs 2026. **Bar:** brand growth vs own-segment growth. **Separate amber strip** of SIMULATED cards: lead-to-delivery, SLA, aged stock, actions triggered |
| 2 OEM & Market Share | Who is gaining share in each segment? | **Cards:** PV and 2W volume and growth. **Diverging bars:** share change in bps by OEM for PV and for 2W (blue gain, red loss; only OEMs listed every month; residual rows excluded). **Table:** OEM units, growth and shares |
| 3 EV Analytics | How fast is electrification moving? | **Cards:** PV and 2W EV share for the prior and current period, plus change in bps. **Lines:** monthly EV share by year for each segment. **Table:** estimated EV units and growth against segment growth. A footnote explains how EV units are derived |
| 4 Portfolio Opportunity | What did losing share cost? | **Cards:** car portfolio prior, current and constant-share volume, gap, growth vs PV. **Table:** each brand against its own segment (Ather vs 2W). **Column chart:** registration gap by brand. **Line:** portfolio PV share by month. **Table:** attention flags |
| 5 Dealership Operations (Simulated) | Where do we lose customers; is stock healthy? | **Seven SIMULATED cards; dealership slicer.** **Funnel bars.** **Stacked bar:** stock by age bucket per dealership. **Table:** dealership KPIs. **Table:** breached action rules with owner, cadence, recommended action and threshold basis |

The official and simulated tables are not related, so filters on page 1 never cross between them. Shares and EV measures return BLANK when the context spans PV and 2W.

## 8. Validated KPI values (default configuration)

These match the Python report and the SQL views.

| KPI | Value |
|---|---|
| PV retail Feb–Sep 2025 → Feb–Sep 2026 | 2,703,948 → 3,301,877 (**+22.11%**) |
| 2W retail | 11,991,359 → 14,564,261 (**+21.46%**) |
| Portfolio car registrations | 298,110 → 320,195 (**+7.41%**) |
| Portfolio share of PV | **11.02% → 9.70%** (−132.8 bps) |
| Constant-share registrations / registration gap | 364,032 / **43,837** |
| Toyota share change | −87.1 bps |
| Ather growth / share change | +81.99% / +53.1 bps |
| EV share of PV / 2W | 4.55% → 6.60% / 7.04% → 9.69% |
| SIMULATED: leads, bookings, deliveries (lead cohort) | 10,936 · 1,577 · 1,422 (lead-to-delivery 13.0%) |
| SIMULATED: current stock / aged units | 348 / 56 |
| SIMULATED: actions triggered / rule checks | 22 / 41 |

## 9. Known limitations

- **Report source.** The `.pbix` must be saved manually (section 6). The PBIP is the version-controlled source.
- **Visual checks.** Visual layout was checked by eye from screenshots of each page, captured by `validate_powerbi.py --screenshots`. The script opens a separate copy of the project per page and captures only that Power BI window once two consecutive captures are identical, so don't interact with those windows while it runs. There is no automated pixel or accessibility test. Alt text for visuals is not yet set; add it in Desktop (Format > General > Alt text) before publishing.
- **Slicer default.** The page 5 dealership slicer starts with nothing selected, which means all simulated dealerships.
- **Period changes.** Page subtitles take the period labels from `config/market_analysis.yaml` at generation time. Regenerate after changing the periods.
- **Publishing.** The report hasn't been published to the Power BI Service. Publishing would need a gateway for the on-premises SQL Server.
