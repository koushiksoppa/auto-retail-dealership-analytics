/* 09_business_queries.sql - sample management questions answered from the star schema.
   Market queries use OFFICIAL FADA data; dealership queries use SIMULATED data. */

/* Q1. How fast is each segment growing? */
SELECT SegmentName, UnitsPrior, UnitsCurrent, IncrementalUnits, CAST(YoYGrowthPct AS DECIMAL(9, 2)) AS YoYGrowthPct
FROM rpt.vSegmentPeriodSummary ORDER BY SegmentCode DESC;

/* Q2/Q3/Q6. Portfolio brands vs their own segment, share change and constant-share gap. */
SELECT SegmentCode, Brand, UnitsPrior, UnitsCurrent,
       CAST(YoYGrowthPct AS DECIMAL(9, 2)) AS YoYGrowthPct, CAST(SegmentGrowthPct AS DECIMAL(9, 2)) AS SegmentGrowthPct,
       CAST(SharePriorPct AS DECIMAL(9, 2)) AS SharePriorPct, CAST(ShareCurrentPct AS DECIMAL(9, 2)) AS ShareCurrentPct,
       CAST(ShareChangeBps AS DECIMAL(9, 1)) AS ShareChangeBps, CAST(RegistrationGapUnits AS DECIMAL(12, 0)) AS RegistrationGapUnits
FROM rpt.vPortfolioBrandPerformance ORDER BY IsAggregate, SegmentCode DESC, UnitsCurrent DESC;

/* Q4. Which competitors gained or lost share? (OEMs listed by FADA in every month only) */
SELECT TOP (5) WITH TIES SegmentCode, OEMName, CAST(ShareChangeBps AS DECIMAL(9, 1)) AS ShareChangeBps, 'gainer' AS Direction
FROM rpt.vOEMPeriodShare WHERE SegmentCode = N'PV' AND IsCoverageComplete = 1 AND IsResidual = 0 AND ShareChangeBps > 0
ORDER BY ShareChangeBps DESC;
SELECT TOP (5) WITH TIES SegmentCode, OEMName, CAST(ShareChangeBps AS DECIMAL(9, 1)) AS ShareChangeBps, 'loser' AS Direction
FROM rpt.vOEMPeriodShare WHERE SegmentCode = N'PV' AND IsCoverageComplete = 1 AND IsResidual = 0 AND ShareChangeBps < 0
ORDER BY ShareChangeBps ASC;

/* Q5. EV adoption (weighted by monthly segment volume). */
SELECT SegmentCode, CAST(EVSharePriorPct AS DECIMAL(9, 2)) AS EVSharePriorPct, CAST(EVShareCurrentPct AS DECIMAL(9, 2)) AS EVShareCurrentPct,
       CAST((EVShareCurrentPct - EVSharePriorPct) * 100 AS DECIMAL(9, 0)) AS EVShareChangeBps, MonthsMissingEVShare
FROM rpt.vEVPeriodSummary;

/* Monthly portfolio PV share trend; SARGable date-key range on the fact's primary key. */
DECLARE @from INT = 20250201, @to INT = 20260930;
SELECT d.YearMonth,
       SUM(CASE WHEN o.PortfolioBrand IS NOT NULL THEN f.Units ELSE 0 END) AS PortfolioUnits,
       MAX(t.PublishedTotalUnits) AS PVUnits,
       CAST(100.0E0 * SUM(CASE WHEN o.PortfolioBrand IS NOT NULL THEN f.Units ELSE 0 END) / MAX(t.PublishedTotalUnits) AS DECIMAL(9, 2)) AS PortfolioSharePct
FROM dw.FactMarketRegistrations f
JOIN dw.DimOEM o ON o.OEMKey = f.OEMKey
JOIN dw.DimDate d ON d.DateKey = f.MonthDateKey
JOIN dw.FactMarketSegmentMonth t ON t.MonthDateKey = f.MonthDateKey AND t.SegmentKey = f.SegmentKey
WHERE f.MonthDateKey BETWEEN @from AND @to AND f.SegmentKey = 1
GROUP BY d.YearMonth ORDER BY d.YearMonth;

/* SIMULATED: funnel leakage by dealership. */
SELECT DealershipId, Brand, Leads, Qualified, TestDrives, Bookings, Deliveries,
       CAST(LeadToDeliveryPct AS DECIMAL(9, 1)) AS LeadToDeliveryPct, DataClassification
FROM rpt.vDealerFunnel ORDER BY LeadToDeliveryPct;

/* SIMULATED: aged stock list for the latest snapshot (oldest first). */
DECLARE @snap INT = (SELECT MAX(SnapshotDateKey) FROM dw.FactInventorySnapshot);
SELECT TOP (20) s.DealershipId, m.ModelName, s.Vin, s.AgeDays, s.AgeingBucket, s.PurchaseCostINR, s.DataClassification
FROM dw.FactInventorySnapshot s JOIN dw.DimVehicleModel m ON m.ModelId = s.ModelId
WHERE s.SnapshotDateKey = @snap AND s.IsAged = 1
ORDER BY s.AgeDays DESC;

/* SIMULATED: lost-lead reasons by stage. */
SELECT LostStage, LostReason, COUNT(*) AS LostLeads
FROM dw.FactLeadFunnel WHERE LeadStatus = N'Lost'
GROUP BY LostStage, LostReason ORDER BY LostStage, LostLeads DESC;
