/* 05_validate_staging.sql
   Validates staged data before any typed load. Every check writes a row to
   audit.ValidationResult; the script raises error 50001 if any check fails, so the
   transform (06) never runs on bad input. Missing values are reported, never zero-filled. */
SET NOCOUNT ON;
DECLARE @v TABLE (CheckName NVARCHAR(200) NOT NULL, FailedRows INT NOT NULL, Detail NVARCHAR(4000) NULL);

/* ---------- row counts */
INSERT @v SELECT N'staging table not empty: ' + t.name, CASE WHEN p.rows > 0 THEN 0 ELSE 1 END, CONCAT(p.rows, N' rows')
FROM sys.tables t JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
WHERE t.schema_id = SCHEMA_ID(N'stg');

/* ---------- analysis periods */
DECLARE @curStart DATE = (SELECT TRY_CONVERT(DATE, start_month + '-01') FROM stg.AnalysisPeriod WHERE period_name = 'current'),
        @curEnd   DATE = (SELECT TRY_CONVERT(DATE, end_month + '-01')   FROM stg.AnalysisPeriod WHERE period_name = 'current'),
        @priStart DATE = (SELECT TRY_CONVERT(DATE, start_month + '-01') FROM stg.AnalysisPeriod WHERE period_name = 'prior'),
        @priEnd   DATE = (SELECT TRY_CONVERT(DATE, end_month + '-01')   FROM stg.AnalysisPeriod WHERE period_name = 'prior');
INSERT @v SELECT N'analysis periods present, equal length and 12 months apart',
    CASE WHEN @curStart IS NULL OR @curEnd IS NULL OR @priStart IS NULL OR @priEnd IS NULL
              OR DATEDIFF(MONTH, @priStart, @curStart) <> 12 OR DATEDIFF(MONTH, @priEnd, @curEnd) <> 12
              OR @curEnd < @curStart THEN 1 ELSE 0 END,
    CONCAT(N'current ', @curStart, N'..', @curEnd, N'; prior ', @priStart, N'..', @priEnd);

/* ---------- FADA OEM extract */
INSERT @v SELECT N'FADA OEM: units missing or not an integer', COUNT(*), N'missing values are not treated as zero'
FROM stg.FadaOemMonthly WHERE TRY_CONVERT(INT, units) IS NULL OR TRY_CONVERT(INT, units) < 0;

INSERT @v SELECT N'FADA OEM: invalid period_month (expected YYYY-MM)', COUNT(*), NULL
FROM stg.FadaOemMonthly WHERE period_month NOT LIKE '[12][0-9][0-9][0-9]-[01][0-9]' OR TRY_CONVERT(DATE, period_month + '-01') IS NULL;

INSERT @v SELECT N'FADA OEM: unexpected segment or row_level', COUNT(*), NULL
FROM stg.FadaOemMonthly WHERE segment NOT IN (N'PV', N'2W') OR row_level NOT IN (N'top', N'member', N'others', N'total');

INSERT @v SELECT N'FADA OEM: duplicate source records', COUNT(*), NULL
FROM (SELECT segment, period_month, oem_raw, row_level, ISNULL(parent_oem_raw, N'') AS p
      FROM stg.FadaOemMonthly GROUP BY segment, period_month, oem_raw, row_level, ISNULL(parent_oem_raw, N'')
      HAVING COUNT(*) > 1) d;

INSERT @v SELECT N'FADA OEM: OEM label missing from config/oem_mapping.csv', COUNT(*),
    STRING_AGG(CONVERT(NVARCHAR(MAX), s.segment + N'/' + s.oem_raw), N'; ')
FROM (SELECT DISTINCT segment, oem_raw FROM stg.FadaOemMonthly WHERE row_level IN (N'top', N'others')) s
WHERE NOT EXISTS (SELECT 1 FROM stg.OemMapping m WHERE m.segment = s.segment AND m.oem_raw = s.oem_raw);

INSERT @v SELECT N'FADA OEM: top-level + Others rows do not sum to published Total', COUNT(*),
    STRING_AGG(CONVERT(NVARCHAR(MAX), CONCAT(b.segment, N' ', b.period_month, N': ', b.body_units, N' vs ', t.total_units)), N'; ')
FROM (SELECT segment, period_month, SUM(TRY_CONVERT(BIGINT, units)) AS body_units
      FROM stg.FadaOemMonthly WHERE row_level IN (N'top', N'others') GROUP BY segment, period_month) b
FULL JOIN (SELECT segment, period_month, SUM(TRY_CONVERT(BIGINT, units)) AS total_units, COUNT(*) AS n
           FROM stg.FadaOemMonthly WHERE row_level = N'total' GROUP BY segment, period_month) t
    ON t.segment = b.segment AND t.period_month = b.period_month
WHERE b.body_units IS NULL OR t.total_units IS NULL OR t.n <> 1 OR b.body_units <> t.total_units;

INSERT @v SELECT N'FADA OEM: group row differs from sum of member rows', COUNT(*), NULL
FROM (SELECT m.segment, m.period_month, m.parent_oem_raw, SUM(TRY_CONVERT(BIGINT, m.units)) AS member_units
      FROM stg.FadaOemMonthly m WHERE m.row_level = N'member' GROUP BY m.segment, m.period_month, m.parent_oem_raw) g
LEFT JOIN stg.FadaOemMonthly p ON p.segment = g.segment AND p.period_month = g.period_month
     AND p.oem_raw = g.parent_oem_raw AND p.row_level = N'top'
WHERE p.oem_raw IS NULL OR TRY_CONVERT(BIGINT, p.units) <> g.member_units;

INSERT @v SELECT N'FADA OEM: two raw labels map to one canonical OEM in the same month (double count risk)', COUNT(*), NULL
FROM (SELECT f.segment, f.period_month, m.oem_canonical
      FROM stg.FadaOemMonthly f JOIN stg.OemMapping m ON m.segment = f.segment AND m.oem_raw = f.oem_raw
      WHERE f.row_level IN (N'top', N'others')
      GROUP BY f.segment, f.period_month, m.oem_canonical HAVING COUNT(DISTINCT f.oem_raw) > 1) d;

/* Every analysis month must have a published total for each segment (no gap filling). */
WITH months AS (
    SELECT DISTINCT d.YearMonth FROM dw.DimDate d
    WHERE (d.CalendarDate BETWEEN @curStart AND @curEnd OR d.CalendarDate BETWEEN @priStart AND @priEnd) AND DAY(d.CalendarDate) = 1
), segs AS (SELECT N'PV' AS segment UNION ALL SELECT N'2W')
INSERT @v SELECT N'FADA OEM: analysis month without a published segment total', COUNT(*),
    STRING_AGG(CONVERT(NVARCHAR(MAX), s.segment + N' ' + m.YearMonth), N'; ')
FROM months m CROSS JOIN segs s
WHERE NOT EXISTS (SELECT 1 FROM stg.FadaOemMonthly f WHERE f.segment = s.segment AND f.period_month = m.YearMonth AND f.row_level = N'total');

/* ---------- FADA fuel mix */
INSERT @v SELECT N'FADA fuel: share not a number between 0 and 100', COUNT(*), NULL
FROM stg.FadaFuelMix WHERE TRY_CONVERT(DECIMAL(9, 4), share_pct) IS NULL OR TRY_CONVERT(DECIMAL(9, 4), share_pct) NOT BETWEEN 0 AND 100;

INSERT @v SELECT N'FADA fuel: OEM month without an EV share from the same release', COUNT(*),
    STRING_AGG(CONVERT(NVARCHAR(MAX), t.segment + N' ' + t.period_month), N'; ')
FROM (SELECT DISTINCT segment, period_month, release_month FROM stg.FadaOemMonthly WHERE row_level = N'total') t
WHERE NOT EXISTS (SELECT 1 FROM stg.FadaFuelMix f WHERE f.segment = t.segment AND f.period_month = t.period_month
                  AND f.release_month = t.release_month AND f.fuel = N'EV' AND f.value_role IN (N'current', N'prior_year'));

/* ---------- SIMULATED dealership data */
INSERT @v SELECT N'SIM: rows not labelled SIMULATED', SUM(n), NULL FROM (
    SELECT COUNT(*) AS n FROM stg.SimDealership WHERE data_classification <> N'SIMULATED' UNION ALL
    SELECT COUNT(*) FROM stg.SimSalesperson WHERE data_classification <> N'SIMULATED' UNION ALL
    SELECT COUNT(*) FROM stg.SimVehicleModel WHERE data_classification <> N'SIMULATED' UNION ALL
    SELECT COUNT(*) FROM stg.SimLeadFunnel WHERE data_classification <> N'SIMULATED' UNION ALL
    SELECT COUNT(*) FROM stg.SimVehicleInventory WHERE data_classification <> N'SIMULATED' UNION ALL
    SELECT COUNT(*) FROM stg.SimVehicleDelivery WHERE data_classification <> N'SIMULATED' UNION ALL
    SELECT COUNT(*) FROM stg.SimInventorySnapshot WHERE data_classification <> N'SIMULATED') x;

INSERT @v SELECT N'SIM: lead dates not valid ISO dates', COUNT(*), NULL FROM stg.SimLeadFunnel
WHERE TRY_CONVERT(DATE, lead_date, 23) IS NULL
   OR (NULLIF(qualified_date, N'') IS NOT NULL AND TRY_CONVERT(DATE, qualified_date, 23) IS NULL)
   OR (NULLIF(test_drive_date, N'') IS NOT NULL AND TRY_CONVERT(DATE, test_drive_date, 23) IS NULL)
   OR (NULLIF(booking_date, N'') IS NOT NULL AND TRY_CONVERT(DATE, booking_date, 23) IS NULL)
   OR (NULLIF(delivery_date, N'') IS NOT NULL AND TRY_CONVERT(DATE, delivery_date, 23) IS NULL);

INSERT @v SELECT N'SIM: lead references unknown dealership, salesperson or model', COUNT(*), NULL
FROM stg.SimLeadFunnel l
WHERE NOT EXISTS (SELECT 1 FROM stg.SimDealership d WHERE d.dealership_id = l.dealership_id)
   OR NOT EXISTS (SELECT 1 FROM stg.SimSalesperson s WHERE s.salesperson_id = l.salesperson_id AND s.dealership_id = l.dealership_id)
   OR NOT EXISTS (SELECT 1 FROM stg.SimVehicleModel m WHERE m.model_id = l.model_id);

INSERT @v SELECT N'SIM: delivery references unknown lead or vehicle', COUNT(*), NULL
FROM stg.SimVehicleDelivery x
WHERE NOT EXISTS (SELECT 1 FROM stg.SimLeadFunnel l WHERE l.lead_id = x.lead_id AND l.booking_id = x.booking_id)
   OR NOT EXISTS (SELECT 1 FROM stg.SimVehicleInventory v WHERE v.vin = x.vin AND v.model_id = x.model_id);

INSERT @v SELECT N'SIM: snapshot references unknown vehicle', COUNT(*), NULL
FROM stg.SimInventorySnapshot s WHERE NOT EXISTS (SELECT 1 FROM stg.SimVehicleInventory v WHERE v.vin = s.vin);

/* ---------- persist and stop on failure */
INSERT audit.ValidationResult (Stage, CheckName, FailedRows, Detail)
SELECT N'staging', CheckName, FailedRows, LEFT(Detail, 4000) FROM @v;

SELECT CheckName, FailedRows, CASE WHEN FailedRows = 0 THEN 'PASS' ELSE 'FAIL' END AS Result, Detail FROM @v ORDER BY Result, CheckName;

IF EXISTS (SELECT 1 FROM @v WHERE FailedRows > 0)
    THROW 50001, N'Staging validation failed - see audit.ValidationResult (Stage = staging).', 1;
GO
