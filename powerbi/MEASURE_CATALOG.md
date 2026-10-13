# Measure Catalog

DAX source: [`measures/measures.dax`](measures/measures.dax), **92 measures**, all evaluated without errors in Power BI Desktop's engine and checked against Python and SQL (`reports/powerbi_validation.csv`). Every measure is listed with its business definition, numerator and denominator, output type and format, filter-context assumptions, date relationship and data classification.

**Data classification**
- **OFFICIAL** means FADA published data.
- **DERIVED** means computed from official data, such as EV units = published EV % × published total.
- **SIMULATED** means seeded synthetic dealership data.

**Date relationships**
- `FactMarketRegistrations[MonthDateKey]`, `FactMarketSegmentMonth[MonthDateKey]`, `FactLeadFunnel[LeadDateKey]`, `FactVehicleDelivery[DeliveryDateKey]` and `FactInventorySnapshot[SnapshotDateKey]` all relate to `DimDate[DateKey]`. Each is a single-direction, many-to-one relationship.
- Market facts are monthly. Their key is the first day of the month.

The reference values come from `reports/auto_retail/executive_report.md` and `reports/dealership/dealership_kpi_report.md`. Use them to check the built report; see the build guide's validation checklist.

## 0 Period helpers

| Measure | Definition | Type / format | Filter context | Data |
|---|---|---|---|---|
| Current/Prior Period Start Key, End Key | First and last `DateKey` of each comparison period, read from `AnalysisPeriod` | Integer, `0` | Independent of filters; `AnalysisPeriod` is a disconnected table | Config |
| Current/Prior Period Label | Display label such as "Feb-Sep 2026" | Text | Same as above | Config |

## 1 Market (monthly context)

| Measure | Business definition | Numerator / denominator | Type / format | Filter-context assumptions | Data |
|---|---|---|---|---|---|
| Registrations | Retail registrations of the OEMs in context | Σ `FactMarketRegistrations[Units]` | Integer, `#,0` | Additive over months and OEMs. Group rows only, so member rows are never double counted | OFFICIAL |
| Segment Registrations | Published FADA total of the segment of the OEM rows in context | Σ `FactMarketSegmentMonth[PublishedTotalUnits]` for `VALUES(FactMarketRegistrations[SegmentKey])` | Integer, `#,0` | BLANK if the context contains more than one segment, so PV and 2W are never mixed. Ignores OEM filters because the segment fact has no OEM relationship | OFFICIAL |
| Market Registrations | Published total of the selected segment | Σ `PublishedTotalUnits` | Integer, `#,0` | Requires exactly one `DimVehicleSegment[SegmentCode]` | OFFICIAL |
| Market Share % | Share of own-segment retail | Registrations ÷ Segment Registrations | Decimal, `0.00%` | Weighted by units over whatever months are in context, never an average of monthly %s | OFFICIAL |
| Registrations PY, Market Registrations PY | Same months one year earlier | `SAMEPERIODLASTYEAR(DimDate[CalendarDate])` | Integer, `#,0` | Needs `DimDate` marked as the date table | OFFICIAL |
| YoY Growth % (Monthly), Market YoY Growth % (Monthly) | Growth against the same months last year | Registrations ÷ Registrations PY − 1 | Decimal, `+0.0%` | BLANK, not −100%, when the prior value is 0 or missing | OFFICIAL |

## 2 Market periods (configured comparison)

All of these use `REMOVEFILTERS(DimDate)` and then apply the configured period's `DateKey` range, so date slicers don't change them. Segment, OEM and brand filters still apply.

| Measure | Business definition | Numerator / denominator | Type / format | Data |
|---|---|---|---|---|
| Registrations CP / PP | Registrations in the current or prior period | Σ Units over the period's months | Integer, `#,0` | OFFICIAL |
| Segment Registrations CP / PP | Own-segment published total over the period | Σ PublishedTotalUnits | Integer, `#,0` | OFFICIAL |
| Market Registrations CP / PP | Selected segment's total over the period | Σ PublishedTotalUnits | Integer, `#,0` | OFFICIAL |
| YoY Growth % (Period) | Period-on-period growth | Registrations CP ÷ Registrations PP − 1 | Decimal, `+0.0%`. BLANK if PP is 0 | OFFICIAL |
| Segment YoY Growth % (Period) | Growth of the OEM's own segment | Segment Registrations CP ÷ PP − 1 | Decimal, `+0.0%` | OFFICIAL |
| Market YoY Growth % (Period) | Growth of the selected segment | Market Registrations CP ÷ PP − 1 | Decimal, `+0.0%` | OFFICIAL |
| Market Share CP % / PP % | Weighted period share | Registrations ÷ Segment Registrations, both summed over the period | Decimal, `0.00%` | OFFICIAL |
| Share Change pp | Change in share | (CP % − PP %) × 100 | Decimal, `+0.00 "pp"` | OFFICIAL |
| Share Change bps | Change in share | (CP % − PP %) × 10,000, where 1 pp = 100 bps | Decimal, `+0 "bps"` | OFFICIAL |
| Growth Gap vs Segment pp | Brand growth minus own-segment growth | (YoY Growth % − Segment YoY Growth %) × 100 | Decimal, `+0.0 "pp"` | OFFICIAL |
| Constant-Share Registrations | Registrations the brand would have had if it had held its prior-period share | Segment Registrations CP × Market Share PP % | Decimal, `#,0` | DERIVED |
| Registration Gap | Registrations forgone (positive) or gained (negative) compared with constant share | Constant-Share Registrations − Registrations CP | Decimal, `+#,0` | DERIVED |

## 3 Portfolio

| Measure | Business definition | Numerator / denominator | Type / format | Filter context | Data |
|---|---|---|---|---|---|
| Portfolio Car Registrations CP / PP | Toyota + Honda Cars + JSW MG Motor + Mercedes-Benz | Σ Units with `DimOEM[IsPortfolioBrand]` = TRUE and segment = PV | Integer, `#,0` | Forces the PV segment, so Ather is excluded | OFFICIAL |
| Portfolio Car Share CP % / PP % | Combined portfolio share of PV retail | Portfolio units ÷ PV total | Decimal, `0.00%` | Same as above | OFFICIAL |
| Portfolio Car Share Change bps | Change in combined share | (CP − PP) × 10,000 | Decimal, `+0 "bps"` | Same as above | OFFICIAL |
| Portfolio Car YoY Growth % | Combined car-portfolio growth | CP ÷ PP − 1 | Decimal, `+0.0%` | Same as above | OFFICIAL |
| Portfolio Car Growth Gap vs PV pp | Portfolio growth minus PV market growth | (growth − PV growth) × 100 | Decimal, `+0.0 "pp"` | Same as above | OFFICIAL |
| Portfolio Car Constant-Share Registrations / Registration Gap | Opportunity at the prior-period combined share | PV total CP × portfolio share PP; that figure minus actual | Decimal, `#,0` | Same as above | DERIVED |

Ather is analysed with the brand measures from sections 1 and 2 in a two-wheeler context, for example `DimOEM[PortfolioBrand] = "Ather"`. There, Segment Registrations resolves to the 2W total.

## 4 EV

| Measure | Business definition | Numerator / denominator | Type / format | Filter context | Data |
|---|---|---|---|---|---|
| EV Registrations (Est.) | Estimated EV retail | Σ `EVUnitsEstimated`, where each month's value is published EV % (2 dp) × published total | Decimal, `#,0` | Requires one segment. BLANK if any month in context has no published EV share | DERIVED |
| EV Share % | EV share of segment retail | EV Registrations ÷ Market Registrations | Decimal, `0.00%` | Volume-weighted across months | DERIVED |
| EV Share CP % / PP % | EV share over each configured period | Same calculation over the period | Decimal, `0.00%` | Ignores date slicers | DERIVED |
| EV Share Change bps | Change in EV share | (CP − PP) × 10,000 | Decimal, `+0 "bps"` | Same as above | DERIVED |

## 5 Funnel (lead cohort)

The `FactLeadFunnel` measures count stages on the lead row, filtered by lead date through `LeadDateKey`. A lead created in March and delivered in April counts as a March-cohort delivery.

| Measure | Business definition | Numerator / denominator | Type / format | Data |
|---|---|---|---|---|
| Leads | Enquiries received | COUNTROWS(`FactLeadFunnel`) | Integer, `#,0` | SIMULATED |
| Qualified Leads, Test Drives, Bookings, Deliveries (Lead Cohort) | Leads that reached each stage | Leads with the stage flag = TRUE | Integer, `#,0` | SIMULATED |
| Lost Leads, Open Leads | Leads closed as lost, or still open at the snapshot date | Leads by `LeadStatus` | Integer, `#,0` | SIMULATED |
| Deliveries (Delivery Date) | Vehicles delivered within the date context | COUNTROWS(`FactVehicleDelivery`), linked through `DeliveryDateKey` | Integer, `#,0` | SIMULATED |
| Lead to Qualified %, Qualified to Test Drive %, Test Drive to Booking %, Booking to Delivery % | Stage-to-stage conversion | Later stage ÷ earlier stage | Decimal, `0.0%` | SIMULATED |
| Lead to Booking %, Lead to Delivery % | Overall conversion | Bookings or deliveries ÷ leads | Decimal, `0.0%` | SIMULATED |
| Lost Lead Share % | Leakage | Lost leads ÷ leads | Decimal, `0.0%` | SIMULATED |
| First Contact SLA % | Leads contacted within the illustrative SLA (4 hours) | Leads with `FirstContactWithinSLA` ÷ leads | Decimal, `0.0%` | SIMULATED |
| Median First Contact Hours | Typical response time | MEDIAN(`FirstContactHours`) | Decimal, `0.0` | SIMULATED |
| Invoiced Revenue, Average Discount % | Delivery revenue and discount | Σ `InvoiceAmountINR`; AVERAGE(`DiscountPct`) ÷ 100 | Currency `"₹"#,0`, `0.0%` | SIMULATED |

## 6 Inventory

Snapshot measures are **semi-additive**: they return the latest month-end snapshot in context and never add snapshots together.

| Measure | Business definition | Numerator / denominator | Type / format | Data |
|---|---|---|---|---|
| Latest Snapshot Key | Latest month-end snapshot in the date context | MAX(`SnapshotDateKey`), ignoring dealership, model and age-bucket filters, so an empty bucket shows blank rather than an older month | Integer | SIMULATED |
| Current Inventory Units | Vehicles in stock at that snapshot | Rows at the latest snapshot | Integer, `#,0` | SIMULATED |
| Aged Inventory Units / Aged Inventory % | Stock older than 60 days | Aged rows (0, not BLANK, when there is stock but none is aged); aged ÷ current | `#,0` / `0.0%` | SIMULATED |
| Inventory Ageing % | Share of stock in an age bucket (0–30, 31–60, 61–90, >90; upper bounds inclusive) | Bucket units ÷ all-bucket units | Decimal, `0.0%` | SIMULATED |
| Average Stock Age (Days), Current Inventory Cost | Average age and stock value at cost | AVERAGE(`AgeDays`); Σ `PurchaseCostINR` | `0.0`; `"₹"#,0` | SIMULATED |
| Opening Stock Units, Receipts Units | Stock at the start of the date range, and arrivals within it | Rows of `FactVehicleInventory` filtered by receipt and delivery dates against the DimDate range | Integer | SIMULATED |
| Sell-Through % | Share of available stock that was sold | Deliveries (Delivery Date) ÷ (Opening + Receipts) | Decimal, `0.0%` | SIMULATED |
| Inventory Turnover (Annualised) | Stock turns per year | Cost of vehicles delivered ÷ average month-end stock cost × (12 × 30.4 ÷ days in context) | Decimal, `0.00` | SIMULATED |
| Days of Supply | Days the current stock would last at the recent sales rate | Current units ÷ (deliveries in the last 30 days ÷ 30) | Decimal, `0.0` | SIMULATED |

## 8 Report helpers

These measures mirror the Python rules so the report doesn't show values the analysis deliberately leaves blank.

| Measure | Definition | Type / format | Data |
|---|---|---|---|
| Period Months | Number of months in the configured current period | Integer | Config |
| OEM Months Reported CP / PP | Months in each period in which FADA lists the OEM row | Integer | OFFICIAL |
| Is Coverage Complete | 1 if exactly one OEM row is in context and it is listed in every month of both periods, otherwise 0 | Integer | OFFICIAL |
| Market Share CP % / PP % (Complete Coverage), Share Change bps (Complete Coverage) | The period share measures, BLANK unless coverage is complete | `0.00%`, `+#,0` | OFFICIAL |
| Share Gain bps / Share Loss bps (Complete Coverage) | The bps change split by sign, so diverging bars can colour gains and losses | `+#,0` | OFFICIAL |
| YoY Growth % (Complete Coverage) | Period growth when coverage is complete and the prior period has at least 1,000 units | `+0.0%` | OFFICIAL |
| EV Registrations CP / PP (Est.), EV Registrations Growth % (Est.) | Estimated EV volume over each period and its growth | `#,0`, `+0.0%` | DERIVED |

## 7 Actions and 9 Labels

| Measure | Definition | Data |
|---|---|---|
| Actions Triggered / Rule Checks | Rule checks breached, and all rule checks, from `FactActionTrigger`, loaded into SQL Server from `reports/dealership/tables/action_triggers.csv` | SIMULATED. Thresholds are illustrative unless `threshold_basis` cites a source |
| Simulated Data Banner / Official Data Banner | Text for the mandatory labelling cards on each page | n/a |
