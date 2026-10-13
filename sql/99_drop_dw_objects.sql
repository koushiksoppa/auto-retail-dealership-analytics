/* 99_drop_dw_objects.sql - removes rpt views and dw tables (used by the pipeline's --rebuild option).
   Does not drop the database, staging tables or audit history. */
DROP VIEW IF EXISTS rpt.vMarketOEMMonthly, rpt.vSegmentPeriodSummary, rpt.vOEMPeriodShare, rpt.vPortfolioBrandPerformance,
    rpt.vEVPeriodSummary, rpt.vDealerFunnel, rpt.vInventoryCurrentByDealership;
DROP TABLE IF EXISTS dw.FactActionTrigger, dw.MarketAttention, dw.FactInventorySnapshot, dw.FactVehicleDelivery, dw.FactLeadFunnel, dw.FactVehicleInventory,
    dw.DimSalesperson, dw.DimVehicleModel, dw.DimDealership, dw.FactMarketRegistrations, dw.FactMarketSegmentMonth,
    dw.MapOEMSourceName, dw.DimOEM, dw.AnalysisPeriod, dw.DimVehicleSegment, dw.DimDate;
GO
