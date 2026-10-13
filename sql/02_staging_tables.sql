/* 02_staging_tables.sql
   Raw landing tables. Every column is text so that a load never silently coerces a bad
   value; typing, NULL handling and validation happen in 05_validate_staging.sql and
   06_transform_load.sql. Rerunnable: tables are dropped and recreated (staging only).

   Sources (repository-relative):
     stg.FadaOemMonthly      data/processed/fada/fada_oem_monthly.csv        OFFICIAL (FADA)
     stg.FadaFuelMix         data/processed/fada/fada_fuel_mix_monthly.csv   OFFICIAL (FADA)
     stg.OemMapping          config/oem_mapping.csv                          analyst mapping
     stg.AnalysisPeriod      config/market_analysis.yaml (periods)           analyst configuration
     stg.Sim*                data/simulated/dealership/ (one CSV per table)  SIMULATED */

DROP TABLE IF EXISTS stg.FadaOemMonthly, stg.FadaFuelMix, stg.OemMapping, stg.AnalysisPeriod,
    stg.SimDealership, stg.SimSalesperson, stg.SimVehicleModel, stg.SimLeadFunnel,
    stg.SimVehicleInventory, stg.SimVehicleDelivery, stg.SimInventorySnapshot, stg.SimActionTrigger,
    stg.MarketAttention;
GO

CREATE TABLE stg.FadaOemMonthly (
    release_month NVARCHAR(20) NULL, period_month NVARCHAR(20) NULL, value_role NVARCHAR(20) NULL,
    segment NVARCHAR(10) NULL, oem_raw NVARCHAR(200) NULL, row_level NVARCHAR(20) NULL,
    parent_oem_raw NVARCHAR(200) NULL, units NVARCHAR(30) NULL, reported_as_dash NVARCHAR(10) NULL,
    published_share_pct NVARCHAR(30) NULL, source_file NVARCHAR(200) NULL, source_page NVARCHAR(10) NULL);

CREATE TABLE stg.FadaFuelMix (
    release_month NVARCHAR(20) NULL, period_month NVARCHAR(20) NULL, value_role NVARCHAR(20) NULL,
    segment NVARCHAR(10) NULL, fuel NVARCHAR(40) NULL, share_pct NVARCHAR(30) NULL,
    source_file NVARCHAR(200) NULL, source_page NVARCHAR(10) NULL);

CREATE TABLE stg.OemMapping (
    segment NVARCHAR(10) NULL, oem_raw NVARCHAR(200) NULL, oem_canonical NVARCHAR(200) NULL,
    portfolio_brand NVARCHAR(100) NULL, mapping_note NVARCHAR(400) NULL);

CREATE TABLE stg.AnalysisPeriod (
    period_name NVARCHAR(20) NULL, start_month NVARCHAR(20) NULL, end_month NVARCHAR(20) NULL,
    period_label NVARCHAR(50) NULL);

CREATE TABLE stg.SimDealership (
    dealership_id NVARCHAR(20) NULL, dealership_name NVARCHAR(200) NULL, brand NVARCHAR(100) NULL,
    segment NVARCHAR(10) NULL, region NVARCHAR(50) NULL, city NVARCHAR(100) NULL,
    data_classification NVARCHAR(20) NULL);

CREATE TABLE stg.SimSalesperson (
    salesperson_id NVARCHAR(30) NULL, dealership_id NVARCHAR(20) NULL, salesperson_name NVARCHAR(100) NULL,
    data_classification NVARCHAR(20) NULL);

CREATE TABLE stg.SimVehicleModel (
    model_id NVARCHAR(20) NULL, brand NVARCHAR(100) NULL, model_name NVARCHAR(200) NULL,
    vehicle_category NVARCHAR(100) NULL, powertrain NVARCHAR(20) NULL, segment NVARCHAR(10) NULL,
    ex_showroom_price_inr NVARCHAR(30) NULL, data_classification NVARCHAR(20) NULL);

CREATE TABLE stg.SimLeadFunnel (
    lead_id NVARCHAR(20) NULL, dealership_id NVARCHAR(20) NULL, salesperson_id NVARCHAR(30) NULL,
    model_id NVARCHAR(20) NULL, lead_source NVARCHAR(50) NULL, lead_date NVARCHAR(20) NULL,
    first_contact_hours NVARCHAR(20) NULL, first_contact_within_sla NVARCHAR(10) NULL,
    qualified_date NVARCHAR(20) NULL, test_drive_date NVARCHAR(20) NULL, booking_id NVARCHAR(20) NULL,
    booking_date NVARCHAR(20) NULL, requested_delivery_date NVARCHAR(20) NULL,
    booking_cancelled_date NVARCHAR(20) NULL, lead_status NVARCHAR(50) NULL, lost_stage NVARCHAR(30) NULL,
    lost_reason NVARCHAR(100) NULL, lost_date NVARCHAR(20) NULL, delivery_id NVARCHAR(20) NULL,
    delivery_date NVARCHAR(20) NULL, vin NVARCHAR(20) NULL, data_classification NVARCHAR(20) NULL);

CREATE TABLE stg.SimVehicleInventory (
    vin NVARCHAR(20) NULL, dealership_id NVARCHAR(20) NULL, model_id NVARCHAR(20) NULL,
    receipt_date NVARCHAR(20) NULL, purchase_cost_inr NVARCHAR(30) NULL, delivery_date NVARCHAR(20) NULL,
    stock_status NVARCHAR(20) NULL, days_in_inventory NVARCHAR(20) NULL, data_classification NVARCHAR(20) NULL);

CREATE TABLE stg.SimVehicleDelivery (
    delivery_id NVARCHAR(20) NULL, booking_id NVARCHAR(20) NULL, lead_id NVARCHAR(20) NULL,
    vin NVARCHAR(20) NULL, dealership_id NVARCHAR(20) NULL, model_id NVARCHAR(20) NULL,
    salesperson_id NVARCHAR(30) NULL, delivery_date NVARCHAR(20) NULL, days_from_booking NVARCHAR(20) NULL,
    days_in_inventory NVARCHAR(20) NULL, ex_showroom_price_inr NVARCHAR(30) NULL, discount_pct NVARCHAR(20) NULL,
    invoice_amount_inr NVARCHAR(30) NULL, purchase_cost_inr NVARCHAR(30) NULL, data_classification NVARCHAR(20) NULL);

CREATE TABLE stg.SimInventorySnapshot (
    snapshot_date NVARCHAR(20) NULL, vin NVARCHAR(20) NULL, dealership_id NVARCHAR(20) NULL,
    model_id NVARCHAR(20) NULL, purchase_cost_inr NVARCHAR(30) NULL, age_days NVARCHAR(20) NULL,
    ageing_bucket NVARCHAR(20) NULL, is_aged NVARCHAR(10) NULL, data_classification NVARCHAR(20) NULL);

/* Report-ready outputs consumed by Power BI (reports/dealership/tables/action_triggers.csv,
   reports/auto_retail/tables/management_attention.csv). */
CREATE TABLE stg.SimActionTrigger (
    dealership_id NVARCHAR(20) NULL, dealership_name NVARCHAR(200) NULL, brand NVARCHAR(100) NULL,
    kpi NVARCHAR(100) NULL, actual NVARCHAR(40) NULL, comparator NVARCHAR(5) NULL, threshold NVARCHAR(40) NULL,
    breached NVARCHAR(10) NULL, status NVARCHAR(20) NULL, responsible_role NVARCHAR(100) NULL,
    review_cadence NVARCHAR(30) NULL, recommended_action NVARCHAR(400) NULL, threshold_basis NVARCHAR(400) NULL,
    data_classification NVARCHAR(20) NULL);

CREATE TABLE stg.MarketAttention (
    brand NVARCHAR(100) NULL, segment NVARCHAR(10) NULL, status NVARCHAR(50) NULL, triggers NVARCHAR(400) NULL,
    yoy_growth_pct NVARCHAR(40) NULL, segment_growth_pct NVARCHAR(40) NULL, share_change_bps NVARCHAR(40) NULL,
    registration_gap_units NVARCHAR(40) NULL);
GO
