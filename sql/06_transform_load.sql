/* 06_transform_load.sql
   Full refresh of the dw star schema from validated staging tables, in one transaction.
   Rerunnable: rows are deleted in foreign-key order and reloaded. Surrogate keys are
   assigned with ROW_NUMBER over business keys, so they are stable across reloads.
   Run 05_validate_staging.sql first; this script assumes it passed. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
BEGIN TRANSACTION;

DELETE dw.FactActionTrigger;
DELETE dw.MarketAttention;
DELETE dw.FactInventorySnapshot;
DELETE dw.FactVehicleDelivery;
DELETE dw.FactLeadFunnel;
DELETE dw.FactVehicleInventory;
DELETE dw.DimSalesperson;
DELETE dw.DimVehicleModel;
DELETE dw.DimDealership;
DELETE dw.FactMarketRegistrations;
DELETE dw.FactMarketSegmentMonth;
DELETE dw.MapOEMSourceName;
DELETE dw.DimOEM;
DELETE dw.AnalysisPeriod;
DELETE dw.DimVehicleSegment;

/* ------------------------------------------------------------------ shared dimensions */
INSERT dw.DimVehicleSegment (SegmentKey, SegmentCode, SegmentName)
VALUES (1, N'PV', N'Passenger vehicles (cars and UVs)'),   -- names as in config/market_analysis.yaml
       (2, N'2W', N'Two-wheelers');

INSERT dw.AnalysisPeriod (PeriodName, StartDateKey, EndDateKey, PeriodLabel)
SELECT period_name,
       CONVERT(INT, REPLACE(start_month, '-', '') + '01'),
       CONVERT(INT, CONVERT(CHAR(8), EOMONTH(CONVERT(DATE, end_month + '-01')), 112)),
       period_label
FROM stg.AnalysisPeriod;

/* ------------------------------------------------------------------ OFFICIAL market data */
WITH oem AS (
    SELECT DISTINCT s.SegmentKey, m.oem_canonical, NULLIF(m.portfolio_brand, N'') AS portfolio_brand,
           CASE WHEN EXISTS (SELECT 1 FROM stg.FadaOemMonthly f WHERE f.segment = m.segment AND f.oem_raw = m.oem_raw
                             AND f.row_level = N'others') THEN 1 ELSE 0 END AS is_residual
    FROM stg.OemMapping m JOIN dw.DimVehicleSegment s ON s.SegmentCode = m.segment
)
INSERT dw.DimOEM (OEMKey, SegmentKey, OEMName, PortfolioBrand, IsResidual)
SELECT ROW_NUMBER() OVER (ORDER BY SegmentKey, oem_canonical), SegmentKey, oem_canonical,
       MAX(portfolio_brand), MAX(is_residual)
FROM oem GROUP BY SegmentKey, oem_canonical;

INSERT dw.MapOEMSourceName (SegmentKey, OEMRawName, OEMKey, MappingNote)
SELECT s.SegmentKey, m.oem_raw, o.OEMKey, NULLIF(m.mapping_note, N'')
FROM stg.OemMapping m
JOIN dw.DimVehicleSegment s ON s.SegmentCode = m.segment
JOIN dw.DimOEM o ON o.SegmentKey = s.SegmentKey AND o.OEMName = m.oem_canonical;

/* Top-level and residual rows only: group member rows are already included in their group row. */
INSERT dw.FactMarketRegistrations (MonthDateKey, SegmentKey, OEMKey, Units, ReleaseMonth, SourceFile)
SELECT CONVERT(INT, REPLACE(f.period_month, '-', '') + '01'), s.SegmentKey, mp.OEMKey,
       SUM(CONVERT(INT, f.units)), MAX(f.release_month), MAX(f.source_file)
FROM stg.FadaOemMonthly f
JOIN dw.DimVehicleSegment s ON s.SegmentCode = f.segment
JOIN dw.MapOEMSourceName mp ON mp.SegmentKey = s.SegmentKey AND mp.OEMRawName = f.oem_raw
WHERE f.row_level IN (N'top', N'others')
GROUP BY f.period_month, s.SegmentKey, mp.OEMKey;

INSERT dw.FactMarketSegmentMonth (MonthDateKey, SegmentKey, PublishedTotalUnits, EVSharePct, ReleaseMonth, SourceFile)
SELECT CONVERT(INT, REPLACE(t.period_month, '-', '') + '01'), s.SegmentKey, CONVERT(INT, t.units),
       CONVERT(DECIMAL(5, 2), ev.share_pct),                  -- stays NULL if FADA did not publish it
       t.release_month, t.source_file
FROM stg.FadaOemMonthly t
JOIN dw.DimVehicleSegment s ON s.SegmentCode = t.segment
LEFT JOIN stg.FadaFuelMix ev ON ev.segment = t.segment AND ev.period_month = t.period_month
     AND ev.release_month = t.release_month AND ev.fuel = N'EV' AND ev.value_role IN (N'current', N'prior_year')
WHERE t.row_level = N'total';

/* ------------------------------------------------------------------ SIMULATED dealership data */
INSERT dw.DimDealership (DealershipId, DealershipName, Brand, SegmentKey, Region, City, DataClassification)
SELECT d.dealership_id, d.dealership_name, d.brand, s.SegmentKey, d.region, d.city, d.data_classification
FROM stg.SimDealership d JOIN dw.DimVehicleSegment s ON s.SegmentCode = d.segment;

INSERT dw.DimSalesperson (SalespersonId, DealershipId, SalespersonName, DataClassification)
SELECT salesperson_id, dealership_id, salesperson_name, data_classification FROM stg.SimSalesperson;

INSERT dw.DimVehicleModel (ModelId, Brand, ModelName, VehicleCategory, Powertrain, SegmentKey, ExShowroomPriceINR, DataClassification)
SELECT m.model_id, m.brand, m.model_name, m.vehicle_category, m.powertrain, s.SegmentKey,
       CONVERT(DECIMAL(14, 2), m.ex_showroom_price_inr), m.data_classification
FROM stg.SimVehicleModel m JOIN dw.DimVehicleSegment s ON s.SegmentCode = m.segment;

INSERT dw.FactVehicleInventory (Vin, DealershipId, ModelId, ReceiptDate, PurchaseCostINR, DeliveryDate, StockStatus,
                                DaysInInventory, DataClassification)
SELECT vin, dealership_id, model_id, CONVERT(DATE, receipt_date, 23), CONVERT(DECIMAL(14, 2), purchase_cost_inr),
       TRY_CONVERT(DATE, NULLIF(delivery_date, N''), 23), stock_status, CONVERT(INT, days_in_inventory), data_classification
FROM stg.SimVehicleInventory;

INSERT dw.FactLeadFunnel (LeadId, DealershipId, SalespersonId, ModelId, LeadDateKey, LeadSource, LeadDate, FirstContactHours,
                          FirstContactWithinSLA, QualifiedDate, TestDriveDate, BookingId, BookingDate, RequestedDeliveryDate,
                          BookingCancelledDate, LeadStatus, LostStage, LostReason, LostDate, DeliveryId, DeliveryDate, Vin,
                          DataClassification)
SELECT lead_id, dealership_id, salesperson_id, model_id,
       CONVERT(INT, CONVERT(CHAR(8), CONVERT(DATE, lead_date, 23), 112)), lead_source, CONVERT(DATE, lead_date, 23),
       CONVERT(DECIMAL(7, 1), first_contact_hours),
       CASE first_contact_within_sla WHEN N'True' THEN 1 WHEN N'False' THEN 0 END,      -- NULL fails NOT NULL
       TRY_CONVERT(DATE, NULLIF(qualified_date, N''), 23), TRY_CONVERT(DATE, NULLIF(test_drive_date, N''), 23),
       NULLIF(booking_id, N''), TRY_CONVERT(DATE, NULLIF(booking_date, N''), 23),
       TRY_CONVERT(DATE, NULLIF(requested_delivery_date, N''), 23), TRY_CONVERT(DATE, NULLIF(booking_cancelled_date, N''), 23),
       lead_status, NULLIF(lost_stage, N''), NULLIF(lost_reason, N''), TRY_CONVERT(DATE, NULLIF(lost_date, N''), 23),
       NULLIF(delivery_id, N''), TRY_CONVERT(DATE, NULLIF(delivery_date, N''), 23), NULLIF(vin, N''), data_classification
FROM stg.SimLeadFunnel;

INSERT dw.FactVehicleDelivery (DeliveryId, BookingId, LeadId, Vin, DealershipId, ModelId, SalespersonId, DeliveryDateKey,
                               DeliveryDate, DaysFromBooking, DaysInInventory, ExShowroomPriceINR, DiscountPct,
                               InvoiceAmountINR, PurchaseCostINR, DataClassification)
SELECT delivery_id, booking_id, lead_id, vin, dealership_id, model_id, salesperson_id,
       CONVERT(INT, CONVERT(CHAR(8), CONVERT(DATE, delivery_date, 23), 112)), CONVERT(DATE, delivery_date, 23),
       CONVERT(INT, days_from_booking), CONVERT(INT, days_in_inventory), CONVERT(DECIMAL(14, 2), ex_showroom_price_inr),
       CONVERT(DECIMAL(5, 2), discount_pct), CONVERT(DECIMAL(14, 2), invoice_amount_inr),
       CONVERT(DECIMAL(14, 2), purchase_cost_inr), data_classification
FROM stg.SimVehicleDelivery;

INSERT dw.FactInventorySnapshot (SnapshotDateKey, Vin, DealershipId, ModelId, PurchaseCostINR, AgeDays, AgeingBucket, IsAged,
                                 DataClassification)
SELECT CONVERT(INT, CONVERT(CHAR(8), CONVERT(DATE, snapshot_date, 23), 112)), vin, dealership_id, model_id,
       CONVERT(DECIMAL(14, 2), purchase_cost_inr), CONVERT(INT, age_days), ageing_bucket,
       CASE is_aged WHEN N'True' THEN 1 WHEN N'False' THEN 0 END, data_classification
FROM stg.SimInventorySnapshot;

INSERT dw.FactActionTrigger (DealershipId, Kpi, ActualValue, Comparator, Threshold, Breached, TriggerStatus, ResponsibleRole,
                              ReviewCadence, RecommendedAction, ThresholdBasis, DataClassification)
SELECT dealership_id, kpi, TRY_CONVERT(DECIMAL(18, 4), NULLIF(actual, N'')), comparator, CONVERT(DECIMAL(18, 4), threshold),
       CASE breached WHEN N'True' THEN 1 WHEN N'False' THEN 0 END, status, responsible_role, review_cadence,
       NULLIF(recommended_action, N''), threshold_basis, data_classification
FROM stg.SimActionTrigger;

INSERT dw.MarketAttention (Brand, SegmentCode, AttentionStatus, Triggers, YoYGrowthPct, SegmentGrowthPct, ShareChangeBps,
                           RegistrationGapUnits)
SELECT brand, segment, status, triggers, TRY_CONVERT(DECIMAL(18, 6), NULLIF(yoy_growth_pct, N'')),
       TRY_CONVERT(DECIMAL(18, 6), NULLIF(segment_growth_pct, N'')), TRY_CONVERT(DECIMAL(18, 6), NULLIF(share_change_bps, N'')),
       TRY_CONVERT(DECIMAL(18, 4), NULLIF(registration_gap_units, N''))
FROM stg.MarketAttention;

COMMIT TRANSACTION;

SELECT t.name AS TableName, p.rows AS RowCount_
FROM sys.tables t JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
WHERE t.schema_id = SCHEMA_ID(N'dw') ORDER BY t.name;
GO
