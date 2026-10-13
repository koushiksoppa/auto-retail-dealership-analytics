/* 08_reconciliation.sql
   Post-load reconciliation inside SQL Server. Writes to audit.ValidationResult (Stage = 'dw')
   and raises error 50002 on any failure. Python-vs-SQL reconciliation of analytical results
   is done by python/sql_server_pipeline.py (step 'reconcile'). */
SET NOCOUNT ON;
DECLARE @v TABLE (CheckName NVARCHAR(200) NOT NULL, FailedRows INT NOT NULL, Detail NVARCHAR(4000) NULL);

/* OEM rows (top-level + Others) reconcile to the FADA published total for every segment-month. */
INSERT @v SELECT N'dw: OEM registrations sum to published segment total (every month)', COUNT(*),
    STRING_AGG(CONVERT(NVARCHAR(MAX), CONCAT(t.MonthDateKey, N'/', t.SegmentKey, N': ', f.Units, N' vs ', t.PublishedTotalUnits)), N'; ')
FROM dw.FactMarketSegmentMonth t
LEFT JOIN (SELECT MonthDateKey, SegmentKey, SUM(CAST(Units AS BIGINT)) AS Units
           FROM dw.FactMarketRegistrations GROUP BY MonthDateKey, SegmentKey) f
       ON f.MonthDateKey = t.MonthDateKey AND f.SegmentKey = t.SegmentKey
WHERE f.Units IS NULL OR f.Units <> t.PublishedTotalUnits;

/* Staging-to-dw completeness. */
INSERT @v SELECT N'dw: every staged total row loaded', ABS((SELECT COUNT(*) FROM stg.FadaOemMonthly WHERE row_level = N'total')
                                                         - (SELECT COUNT(*) FROM dw.FactMarketSegmentMonth)), NULL;
INSERT @v SELECT N'dw: staged and loaded unit totals equal',
    CASE WHEN (SELECT SUM(CONVERT(BIGINT, units)) FROM stg.FadaOemMonthly WHERE row_level IN (N'top', N'others'))
            = (SELECT SUM(CAST(Units AS BIGINT)) FROM dw.FactMarketRegistrations) THEN 0 ELSE 1 END, NULL;
INSERT @v SELECT N'dw: EV share present for every segment-month', COUNT(*), NULL
FROM dw.FactMarketSegmentMonth WHERE EVSharePct IS NULL;

/* Segment isolation: no OEM is attached to more than one segment. */
INSERT @v SELECT N'dw: OEM rows only in their own segment', COUNT(*), NULL
FROM dw.FactMarketRegistrations f JOIN dw.DimOEM o ON o.OEMKey = f.OEMKey WHERE o.SegmentKey <> f.SegmentKey;

/* Simulated data: row counts match staging and the funnel never widens. */
INSERT @v SELECT N'dw: simulated row counts equal staging',
    ABS((SELECT COUNT(*) FROM stg.SimLeadFunnel) - (SELECT COUNT(*) FROM dw.FactLeadFunnel))
  + ABS((SELECT COUNT(*) FROM stg.SimVehicleDelivery) - (SELECT COUNT(*) FROM dw.FactVehicleDelivery))
  + ABS((SELECT COUNT(*) FROM stg.SimVehicleInventory) - (SELECT COUNT(*) FROM dw.FactVehicleInventory))
  + ABS((SELECT COUNT(*) FROM stg.SimInventorySnapshot) - (SELECT COUNT(*) FROM dw.FactInventorySnapshot))
  + ABS((SELECT COUNT(*) FROM stg.SimActionTrigger) - (SELECT COUNT(*) FROM dw.FactActionTrigger)), NULL;
INSERT @v SELECT N'dw: market attention rows equal staging',
    ABS((SELECT COUNT(*) FROM stg.MarketAttention) - (SELECT COUNT(*) FROM dw.MarketAttention)), NULL;
INSERT @v SELECT N'dw: funnel stages non-increasing per dealership', COUNT(*), NULL
FROM rpt.vDealerFunnel WHERE NOT (Leads >= Qualified AND Qualified >= TestDrives AND TestDrives >= Bookings AND Bookings >= Deliveries);
INSERT @v SELECT N'dw: delivered vehicles marked Delivered in inventory', COUNT(*), NULL
FROM dw.FactVehicleDelivery x JOIN dw.FactVehicleInventory v ON v.Vin = x.Vin
WHERE v.StockStatus <> N'Delivered' OR v.DeliveryDate <> x.DeliveryDate;

INSERT audit.ValidationResult (Stage, CheckName, FailedRows, Detail)
SELECT N'dw', CheckName, FailedRows, LEFT(Detail, 4000) FROM @v;
SELECT CheckName, FailedRows, CASE WHEN FailedRows = 0 THEN 'PASS' ELSE 'FAIL' END AS Result, Detail FROM @v ORDER BY Result, CheckName;
IF EXISTS (SELECT 1 FROM @v WHERE FailedRows > 0)
    THROW 50002, N'dw reconciliation failed - see audit.ValidationResult (Stage = dw).', 1;
GO
