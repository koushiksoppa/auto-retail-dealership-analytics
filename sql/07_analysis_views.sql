/* 07_analysis_views.sql
   Analysis views in schema rpt. Definitions mirror python/market_metrics.py:
     YoY growth %        = (current / prior - 1) * 100            NULL when prior is 0 or missing
     Share %             = units / own-segment units * 100        weighted over the period (summed units)
     Share change bps    = (current share % - prior share %) * 100
     Constant-share units = current segment units * prior share % / 100
     Registration gap    = constant-share units - actual units
   Ratios are computed in FLOAT to avoid DECIMAL scale truncation, then presented as DECIMAL.
   Period filters use MonthDateKey BETWEEN StartDateKey AND EndDateKey (SARGable on the PK). */

CREATE OR ALTER VIEW rpt.vMarketOEMMonthly AS
SELECT d.YearMonth, d.CalendarDate AS MonthStartDate, s.SegmentCode, o.OEMName, o.PortfolioBrand, o.IsResidual,
       ap.PeriodName, f.Units, t.PublishedTotalUnits AS SegmentUnits,
       CAST(100.0E0 * f.Units / NULLIF(t.PublishedTotalUnits, 0) AS DECIMAL(18, 6)) AS SharePct,
       f.DataClassification
FROM dw.FactMarketRegistrations f
JOIN dw.DimDate d ON d.DateKey = f.MonthDateKey
JOIN dw.DimVehicleSegment s ON s.SegmentKey = f.SegmentKey
JOIN dw.DimOEM o ON o.OEMKey = f.OEMKey
JOIN dw.FactMarketSegmentMonth t ON t.MonthDateKey = f.MonthDateKey AND t.SegmentKey = f.SegmentKey
LEFT JOIN dw.AnalysisPeriod ap ON f.MonthDateKey BETWEEN ap.StartDateKey AND ap.EndDateKey;
GO

CREATE OR ALTER VIEW rpt.vSegmentPeriodSummary AS
WITH p AS (
    SELECT t.SegmentKey, ap.PeriodName, SUM(CAST(t.PublishedTotalUnits AS BIGINT)) AS Units
    FROM dw.FactMarketSegmentMonth t
    JOIN dw.AnalysisPeriod ap ON t.MonthDateKey BETWEEN ap.StartDateKey AND ap.EndDateKey
    GROUP BY t.SegmentKey, ap.PeriodName
)
SELECT s.SegmentCode, s.SegmentName,
       pr.Units AS UnitsPrior, cu.Units AS UnitsCurrent, cu.Units - pr.Units AS IncrementalUnits,
       CAST((CAST(cu.Units AS FLOAT) / NULLIF(pr.Units, 0) - 1) * 100 AS DECIMAL(18, 6)) AS YoYGrowthPct
FROM dw.DimVehicleSegment s
JOIN p pr ON pr.SegmentKey = s.SegmentKey AND pr.PeriodName = N'prior'
JOIN p cu ON cu.SegmentKey = s.SegmentKey AND cu.PeriodName = N'current';
GO

CREATE OR ALTER VIEW rpt.vOEMPeriodShare AS
WITH seg AS (
    SELECT t.SegmentKey, ap.PeriodName, SUM(CAST(t.PublishedTotalUnits AS BIGINT)) AS SegUnits
    FROM dw.FactMarketSegmentMonth t
    JOIN dw.AnalysisPeriod ap ON t.MonthDateKey BETWEEN ap.StartDateKey AND ap.EndDateKey
    GROUP BY t.SegmentKey, ap.PeriodName
), oem AS (
    SELECT f.OEMKey, ap.PeriodName, SUM(CAST(f.Units AS BIGINT)) AS Units, COUNT(*) AS MonthsReported
    FROM dw.FactMarketRegistrations f
    JOIN dw.AnalysisPeriod ap ON f.MonthDateKey BETWEEN ap.StartDateKey AND ap.EndDateKey
    GROUP BY f.OEMKey, ap.PeriodName
), months AS (
    SELECT PeriodName, DATEDIFF(MONTH, CONVERT(DATE, CONVERT(CHAR(8), StartDateKey)), CONVERT(DATE, CONVERT(CHAR(8), EndDateKey))) + 1 AS N
    FROM dw.AnalysisPeriod
), base AS (
    SELECT s.SegmentCode, o.OEMName, o.PortfolioBrand, o.IsResidual,
           ISNULL(pr.Units, 0) AS UnitsPrior, ISNULL(cu.Units, 0) AS UnitsCurrent,
           ISNULL(pr.MonthsReported, 0) AS MonthsReportedPrior, ISNULL(cu.MonthsReported, 0) AS MonthsReportedCurrent,
           CAST(CASE WHEN pr.MonthsReported = mp.N AND cu.MonthsReported = mc.N THEN 1 ELSE 0 END AS BIT) AS IsCoverageComplete,
           sp.SegUnits AS SegUnitsPrior, sc.SegUnits AS SegUnitsCurrent
    FROM dw.DimOEM o
    JOIN dw.DimVehicleSegment s ON s.SegmentKey = o.SegmentKey
    LEFT JOIN oem pr ON pr.OEMKey = o.OEMKey AND pr.PeriodName = N'prior'
    LEFT JOIN oem cu ON cu.OEMKey = o.OEMKey AND cu.PeriodName = N'current'
    JOIN seg sp ON sp.SegmentKey = o.SegmentKey AND sp.PeriodName = N'prior'
    JOIN seg sc ON sc.SegmentKey = o.SegmentKey AND sc.PeriodName = N'current'
    CROSS JOIN (SELECT N FROM months WHERE PeriodName = N'prior') mp
    CROSS JOIN (SELECT N FROM months WHERE PeriodName = N'current') mc
    WHERE pr.OEMKey IS NOT NULL OR cu.OEMKey IS NOT NULL
)
SELECT SegmentCode, OEMName, PortfolioBrand, IsResidual, IsCoverageComplete, MonthsReportedPrior, MonthsReportedCurrent,
       UnitsPrior, UnitsCurrent, SegUnitsPrior, SegUnitsCurrent,
       /* Metrics are NULL when FADA lists the OEM in only some months (it is otherwise inside 'Others'). */
       CAST(CASE WHEN IsCoverageComplete = 1 THEN 100.0E0 * UnitsPrior / NULLIF(SegUnitsPrior, 0) END AS DECIMAL(18, 6)) AS SharePriorPct,
       CAST(CASE WHEN IsCoverageComplete = 1 THEN 100.0E0 * UnitsCurrent / NULLIF(SegUnitsCurrent, 0) END AS DECIMAL(18, 6)) AS ShareCurrentPct,
       CAST(CASE WHEN IsCoverageComplete = 1
                 THEN (CAST(UnitsCurrent AS FLOAT) / NULLIF(SegUnitsCurrent, 0) - CAST(UnitsPrior AS FLOAT) / NULLIF(SegUnitsPrior, 0)) * 10000
            END AS DECIMAL(18, 6)) AS ShareChangeBps,
       CAST(CASE WHEN IsCoverageComplete = 1 THEN (CAST(UnitsCurrent AS FLOAT) / NULLIF(UnitsPrior, 0) - 1) * 100 END AS DECIMAL(18, 6)) AS YoYGrowthPct
FROM base;
GO

CREATE OR ALTER VIEW rpt.vPortfolioBrandPerformance AS
WITH seg AS (
    SELECT t.SegmentKey, ap.PeriodName, SUM(CAST(t.PublishedTotalUnits AS BIGINT)) AS SegUnits
    FROM dw.FactMarketSegmentMonth t
    JOIN dw.AnalysisPeriod ap ON t.MonthDateKey BETWEEN ap.StartDateKey AND ap.EndDateKey
    GROUP BY t.SegmentKey, ap.PeriodName
), brand AS (
    SELECT o.SegmentKey, o.PortfolioBrand AS Brand, CAST(0 AS BIT) AS IsAggregate,
           SUM(CASE WHEN ap.PeriodName = N'prior' THEN CAST(f.Units AS BIGINT) ELSE 0 END) AS UnitsPrior,
           SUM(CASE WHEN ap.PeriodName = N'current' THEN CAST(f.Units AS BIGINT) ELSE 0 END) AS UnitsCurrent
    FROM dw.FactMarketRegistrations f
    JOIN dw.DimOEM o ON o.OEMKey = f.OEMKey
    JOIN dw.AnalysisPeriod ap ON f.MonthDateKey BETWEEN ap.StartDateKey AND ap.EndDateKey
    WHERE o.PortfolioBrand IS NOT NULL
    GROUP BY o.SegmentKey, o.PortfolioBrand
), allrows AS (
    SELECT SegmentKey, Brand, IsAggregate, UnitsPrior, UnitsCurrent FROM brand
    UNION ALL
    SELECT b.SegmentKey, N'Portfolio car brands', CAST(1 AS BIT), SUM(b.UnitsPrior), SUM(b.UnitsCurrent)
    FROM brand b JOIN dw.DimVehicleSegment s ON s.SegmentKey = b.SegmentKey
    WHERE s.SegmentCode = N'PV' GROUP BY b.SegmentKey
), m AS (
    SELECT s.SegmentCode, a.Brand, a.IsAggregate, a.UnitsPrior, a.UnitsCurrent,
           sp.SegUnits AS SegUnitsPrior, sc.SegUnits AS SegUnitsCurrent,
           100.0E0 * a.UnitsPrior / NULLIF(sp.SegUnits, 0) AS SharePrior,
           100.0E0 * a.UnitsCurrent / NULLIF(sc.SegUnits, 0) AS ShareCurrent
    FROM allrows a
    JOIN dw.DimVehicleSegment s ON s.SegmentKey = a.SegmentKey
    JOIN seg sp ON sp.SegmentKey = a.SegmentKey AND sp.PeriodName = N'prior'
    JOIN seg sc ON sc.SegmentKey = a.SegmentKey AND sc.PeriodName = N'current'
)
SELECT SegmentCode, Brand, IsAggregate, UnitsPrior, UnitsCurrent, SegUnitsPrior, SegUnitsCurrent,
       CAST((CAST(UnitsCurrent AS FLOAT) / NULLIF(UnitsPrior, 0) - 1) * 100 AS DECIMAL(18, 6)) AS YoYGrowthPct,
       CAST((CAST(SegUnitsCurrent AS FLOAT) / NULLIF(SegUnitsPrior, 0) - 1) * 100 AS DECIMAL(18, 6)) AS SegmentGrowthPct,
       CAST(((CAST(UnitsCurrent AS FLOAT) / NULLIF(UnitsPrior, 0)) - (CAST(SegUnitsCurrent AS FLOAT) / NULLIF(SegUnitsPrior, 0))) * 100
            AS DECIMAL(18, 6)) AS GrowthGapPp,
       CAST(SharePrior AS DECIMAL(18, 6)) AS SharePriorPct,
       CAST(ShareCurrent AS DECIMAL(18, 6)) AS ShareCurrentPct,
       CAST((ShareCurrent - SharePrior) * 100 AS DECIMAL(18, 6)) AS ShareChangeBps,
       CAST(SegUnitsCurrent * SharePrior / 100 AS DECIMAL(18, 4)) AS ConstantShareUnits,
       CAST(SegUnitsCurrent * SharePrior / 100 - UnitsCurrent AS DECIMAL(18, 4)) AS RegistrationGapUnits
FROM m;
GO

CREATE OR ALTER VIEW rpt.vEVPeriodSummary AS
SELECT s.SegmentCode,
       SUM(CASE WHEN ap.PeriodName = N'prior' THEN CAST(t.PublishedTotalUnits AS BIGINT) END) AS UnitsPrior,
       SUM(CASE WHEN ap.PeriodName = N'current' THEN CAST(t.PublishedTotalUnits AS BIGINT) END) AS UnitsCurrent,
       CAST(SUM(CASE WHEN ap.PeriodName = N'prior' THEN t.EVUnitsEstimated END) AS DECIMAL(18, 4)) AS EVUnitsEstPrior,
       CAST(SUM(CASE WHEN ap.PeriodName = N'current' THEN t.EVUnitsEstimated END) AS DECIMAL(18, 4)) AS EVUnitsEstCurrent,
       CAST(100.0E0 * SUM(CASE WHEN ap.PeriodName = N'prior' THEN t.EVUnitsEstimated END)
            / NULLIF(SUM(CASE WHEN ap.PeriodName = N'prior' THEN t.PublishedTotalUnits END), 0) AS DECIMAL(18, 6)) AS EVSharePriorPct,
       CAST(100.0E0 * SUM(CASE WHEN ap.PeriodName = N'current' THEN t.EVUnitsEstimated END)
            / NULLIF(SUM(CASE WHEN ap.PeriodName = N'current' THEN t.PublishedTotalUnits END), 0) AS DECIMAL(18, 6)) AS EVShareCurrentPct,
       /* Months without a published EV share make the period share undefined rather than understated. */
       SUM(CASE WHEN t.EVSharePct IS NULL THEN 1 ELSE 0 END) AS MonthsMissingEVShare
FROM dw.FactMarketSegmentMonth t
JOIN dw.DimVehicleSegment s ON s.SegmentKey = t.SegmentKey
JOIN dw.AnalysisPeriod ap ON t.MonthDateKey BETWEEN ap.StartDateKey AND ap.EndDateKey
GROUP BY s.SegmentCode;
GO

/* ------------------------------------------------------------------ SIMULATED dealership views */
CREATE OR ALTER VIEW rpt.vDealerFunnel AS
SELECT d.DealershipId, d.DealershipName, d.Brand,
       COUNT(*) AS Leads, SUM(CAST(f.IsQualified AS INT)) AS Qualified, SUM(CAST(f.IsTestDrive AS INT)) AS TestDrives,
       SUM(CAST(f.IsBooked AS INT)) AS Bookings, SUM(CAST(f.IsDelivered AS INT)) AS Deliveries,
       CAST(100.0E0 * SUM(CAST(f.IsQualified AS INT)) / NULLIF(COUNT(*), 0) AS DECIMAL(9, 4)) AS LeadToQualifiedPct,
       CAST(100.0E0 * SUM(CAST(f.IsTestDrive AS INT)) / NULLIF(SUM(CAST(f.IsQualified AS INT)), 0) AS DECIMAL(9, 4)) AS QualifiedToTestDrivePct,
       CAST(100.0E0 * SUM(CAST(f.IsBooked AS INT)) / NULLIF(SUM(CAST(f.IsTestDrive AS INT)), 0) AS DECIMAL(9, 4)) AS TestDriveToBookingPct,
       CAST(100.0E0 * SUM(CAST(f.IsDelivered AS INT)) / NULLIF(SUM(CAST(f.IsBooked AS INT)), 0) AS DECIMAL(9, 4)) AS BookingToDeliveryPct,
       CAST(100.0E0 * SUM(CAST(f.IsDelivered AS INT)) / NULLIF(COUNT(*), 0) AS DECIMAL(9, 4)) AS LeadToDeliveryPct,
       CAST(100.0E0 * SUM(CAST(f.FirstContactWithinSLA AS INT)) / NULLIF(COUNT(*), 0) AS DECIMAL(9, 4)) AS FirstContactSLAPct,
       'SIMULATED' AS DataClassification
FROM dw.FactLeadFunnel f JOIN dw.DimDealership d ON d.DealershipId = f.DealershipId
GROUP BY d.DealershipId, d.DealershipName, d.Brand;
GO

CREATE OR ALTER VIEW rpt.vInventoryCurrentByDealership AS
WITH latest AS (SELECT MAX(SnapshotDateKey) AS SnapshotDateKey FROM dw.FactInventorySnapshot)
SELECT d.DealershipId, d.Brand, l.SnapshotDateKey,
       COUNT(s.Vin) AS StockUnits,
       SUM(CASE WHEN s.AgeingBucket = N'0-30' THEN 1 ELSE 0 END) AS Stock0To30,
       SUM(CASE WHEN s.AgeingBucket = N'31-60' THEN 1 ELSE 0 END) AS Stock31To60,
       SUM(CASE WHEN s.AgeingBucket = N'61-90' THEN 1 ELSE 0 END) AS Stock61To90,
       SUM(CASE WHEN s.AgeingBucket = N'>90' THEN 1 ELSE 0 END) AS StockOver90,
       SUM(CAST(s.IsAged AS INT)) AS AgedUnits,
       CAST(100.0E0 * SUM(CAST(s.IsAged AS INT)) / NULLIF(COUNT(s.Vin), 0) AS DECIMAL(9, 4)) AS AgedInventorySharePct,
       CAST(AVG(CAST(s.AgeDays AS FLOAT)) AS DECIMAL(9, 4)) AS AvgAgeDays,
       SUM(s.PurchaseCostINR) AS StockCostINR,
       'SIMULATED' AS DataClassification
FROM dw.DimDealership d
CROSS JOIN latest l
LEFT JOIN dw.FactInventorySnapshot s ON s.DealershipId = d.DealershipId AND s.SnapshotDateKey = l.SnapshotDateKey
GROUP BY d.DealershipId, d.Brand, l.SnapshotDateKey;
GO
