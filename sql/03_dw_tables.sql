/* 03_dw_tables.sql
   Typed star schema. Rerunnable: each object is created only if missing.
   (python python/sql_server_pipeline.py --rebuild drops and recreates dw objects.)

   Grain
     dw.DimDate                  one row per calendar day (DateKey = yyyymmdd)
     dw.DimVehicleSegment        one row per FADA segment code used (PV, 2W)
     dw.DimOEM                   one row per canonical OEM within a segment          OFFICIAL source names
     dw.MapOEMSourceName         one row per raw FADA OEM label (auditable mapping)
     dw.AnalysisPeriod           one row per comparison period (current, prior)
     dw.FactMarketRegistrations  segment x canonical OEM x month (FADA top-level + 'Others' rows)  OFFICIAL
     dw.FactMarketSegmentMonth   segment x month: published total and published EV share          OFFICIAL
     dw.DimDealership / DimSalesperson / DimVehicleModel                                           SIMULATED
     dw.FactLeadFunnel           one row per lead (stage dates on the row)                         SIMULATED
     dw.FactVehicleInventory     one row per stocked vehicle (VIN)                                 SIMULATED
     dw.FactVehicleDelivery      one row per delivery (= one booking, one VIN)                     SIMULATED
     dw.FactInventorySnapshot    month-end x VIN in stock                                          SIMULATED

   Official and simulated facts never share a table; each carries DataClassification. */

IF OBJECT_ID(N'dw.DimDate') IS NULL
CREATE TABLE dw.DimDate (
    DateKey          INT          NOT NULL CONSTRAINT PK_DimDate PRIMARY KEY,
    CalendarDate     DATE         NOT NULL CONSTRAINT UQ_DimDate_CalendarDate UNIQUE,
    YearNumber       SMALLINT     NOT NULL,
    QuarterNumber    TINYINT      NOT NULL,
    MonthNumber      TINYINT      NOT NULL,
    MonthName        NVARCHAR(10) NOT NULL,
    MonthShortName   NCHAR(3)     NOT NULL,
    YearMonth        CHAR(7)      NOT NULL,          -- 'YYYY-MM'
    MonthStartDate   DATE         NOT NULL,
    MonthEndDate     DATE         NOT NULL,
    IsMonthEnd       BIT          NOT NULL,
    IndianFiscalYear NVARCHAR(9)  NOT NULL           -- e.g. 'FY2026-27' (April-March)
);
GO

IF OBJECT_ID(N'dw.DimVehicleSegment') IS NULL
CREATE TABLE dw.DimVehicleSegment (
    SegmentKey  TINYINT       NOT NULL CONSTRAINT PK_DimVehicleSegment PRIMARY KEY,
    SegmentCode NVARCHAR(10)  NOT NULL CONSTRAINT UQ_DimVehicleSegment_Code UNIQUE,
    SegmentName NVARCHAR(100) NOT NULL
);
GO

IF OBJECT_ID(N'dw.DimOEM') IS NULL
CREATE TABLE dw.DimOEM (
    OEMKey           INT           NOT NULL CONSTRAINT PK_DimOEM PRIMARY KEY,
    SegmentKey       TINYINT       NOT NULL CONSTRAINT FK_DimOEM_Segment REFERENCES dw.DimVehicleSegment (SegmentKey),
    OEMName          NVARCHAR(200) NOT NULL,
    PortfolioBrand   NVARCHAR(100) NULL,              -- NULL = not a portfolio brand
    IsPortfolioBrand AS CAST(CASE WHEN PortfolioBrand IS NULL THEN 0 ELSE 1 END AS BIT) PERSISTED,
    IsResidual       BIT           NOT NULL,          -- FADA 'Others' row
    CONSTRAINT UQ_DimOEM_Segment_Name UNIQUE (SegmentKey, OEMName)
);
GO

IF OBJECT_ID(N'dw.MapOEMSourceName') IS NULL
CREATE TABLE dw.MapOEMSourceName (
    SegmentKey  TINYINT       NOT NULL CONSTRAINT FK_MapOEM_Segment REFERENCES dw.DimVehicleSegment (SegmentKey),
    OEMRawName  NVARCHAR(200) NOT NULL,
    OEMKey      INT           NOT NULL CONSTRAINT FK_MapOEM_OEM REFERENCES dw.DimOEM (OEMKey),
    MappingNote NVARCHAR(400) NULL,
    CONSTRAINT PK_MapOEMSourceName PRIMARY KEY (SegmentKey, OEMRawName)
);
GO

IF OBJECT_ID(N'dw.AnalysisPeriod') IS NULL
CREATE TABLE dw.AnalysisPeriod (
    PeriodName   NVARCHAR(20) NOT NULL CONSTRAINT PK_AnalysisPeriod PRIMARY KEY,
    StartDateKey INT          NOT NULL CONSTRAINT FK_AnalysisPeriod_Start REFERENCES dw.DimDate (DateKey),
    EndDateKey   INT          NOT NULL CONSTRAINT FK_AnalysisPeriod_End REFERENCES dw.DimDate (DateKey),
    PeriodLabel  NVARCHAR(50) NOT NULL,
    CONSTRAINT CK_AnalysisPeriod_Name CHECK (PeriodName IN (N'current', N'prior')),
    CONSTRAINT CK_AnalysisPeriod_Range CHECK (StartDateKey <= EndDateKey)
);
GO

IF OBJECT_ID(N'dw.FactMarketRegistrations') IS NULL
CREATE TABLE dw.FactMarketRegistrations (
    MonthDateKey       INT           NOT NULL CONSTRAINT FK_FMR_Date REFERENCES dw.DimDate (DateKey),   -- first day of month
    SegmentKey         TINYINT       NOT NULL CONSTRAINT FK_FMR_Segment REFERENCES dw.DimVehicleSegment (SegmentKey),
    OEMKey             INT           NOT NULL CONSTRAINT FK_FMR_OEM REFERENCES dw.DimOEM (OEMKey),
    Units              INT           NOT NULL CONSTRAINT CK_FMR_Units CHECK (Units >= 0),
    ReleaseMonth       CHAR(7)       NOT NULL,
    SourceFile         NVARCHAR(200) NOT NULL,
    DataClassification VARCHAR(20)   NOT NULL CONSTRAINT DF_FMR_Class DEFAULT ('OFFICIAL')
                                     CONSTRAINT CK_FMR_Class CHECK (DataClassification = 'OFFICIAL'),
    CONSTRAINT PK_FactMarketRegistrations PRIMARY KEY (MonthDateKey, OEMKey)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_FMR_Segment_Month')
    CREATE INDEX IX_FMR_Segment_Month ON dw.FactMarketRegistrations (SegmentKey, MonthDateKey) INCLUDE (OEMKey, Units);
GO

IF OBJECT_ID(N'dw.FactMarketSegmentMonth') IS NULL
CREATE TABLE dw.FactMarketSegmentMonth (
    MonthDateKey        INT           NOT NULL CONSTRAINT FK_FMS_Date REFERENCES dw.DimDate (DateKey),
    SegmentKey          TINYINT       NOT NULL CONSTRAINT FK_FMS_Segment REFERENCES dw.DimVehicleSegment (SegmentKey),
    PublishedTotalUnits INT           NOT NULL CONSTRAINT CK_FMS_Units CHECK (PublishedTotalUnits >= 0),
    EVSharePct          DECIMAL(5, 2) NULL CONSTRAINT CK_FMS_EV CHECK (EVSharePct BETWEEN 0 AND 100),  -- NULL = not published
    EVUnitsEstimated    AS CAST(PublishedTotalUnits * EVSharePct / 100.0 AS DECIMAL(14, 4)) PERSISTED,  -- derived
    ReleaseMonth        CHAR(7)       NOT NULL,
    SourceFile          NVARCHAR(200) NOT NULL,
    DataClassification  VARCHAR(20)   NOT NULL CONSTRAINT DF_FMS_Class DEFAULT ('OFFICIAL')
                                      CONSTRAINT CK_FMS_Class CHECK (DataClassification = 'OFFICIAL'),
    CONSTRAINT PK_FactMarketSegmentMonth PRIMARY KEY (MonthDateKey, SegmentKey)
);
GO

/* ------------------------------------------------------------------ SIMULATED dealership model */
IF OBJECT_ID(N'dw.DimDealership') IS NULL
CREATE TABLE dw.DimDealership (
    DealershipId       VARCHAR(10)   NOT NULL CONSTRAINT PK_DimDealership PRIMARY KEY,
    DealershipName     NVARCHAR(200) NOT NULL,
    Brand              NVARCHAR(100) NOT NULL,     -- matches dw.DimOEM.PortfolioBrand
    SegmentKey         TINYINT       NOT NULL CONSTRAINT FK_DimDealership_Segment REFERENCES dw.DimVehicleSegment (SegmentKey),
    Region             NVARCHAR(50)  NOT NULL,
    City               NVARCHAR(100) NOT NULL,
    DataClassification VARCHAR(20)   NOT NULL CONSTRAINT CK_DimDealership_Class CHECK (DataClassification = 'SIMULATED')
);
GO

IF OBJECT_ID(N'dw.DimSalesperson') IS NULL
CREATE TABLE dw.DimSalesperson (
    SalespersonId      VARCHAR(20)   NOT NULL CONSTRAINT PK_DimSalesperson PRIMARY KEY,
    DealershipId       VARCHAR(10)   NOT NULL CONSTRAINT FK_DimSalesperson_Dealer REFERENCES dw.DimDealership (DealershipId),
    SalespersonName    NVARCHAR(100) NOT NULL,     -- synthetic label, not a person
    DataClassification VARCHAR(20)   NOT NULL CONSTRAINT CK_DimSalesperson_Class CHECK (DataClassification = 'SIMULATED')
);
GO

IF OBJECT_ID(N'dw.DimVehicleModel') IS NULL
CREATE TABLE dw.DimVehicleModel (
    ModelId            VARCHAR(10)    NOT NULL CONSTRAINT PK_DimVehicleModel PRIMARY KEY,
    Brand              NVARCHAR(100)  NOT NULL,
    ModelName          NVARCHAR(200)  NOT NULL,
    VehicleCategory    NVARCHAR(100)  NOT NULL,
    Powertrain         NVARCHAR(20)   NOT NULL CONSTRAINT CK_DimVehicleModel_Powertrain CHECK (Powertrain IN (N'ICE', N'Hybrid', N'EV')),
    SegmentKey         TINYINT        NOT NULL CONSTRAINT FK_DimVehicleModel_Segment REFERENCES dw.DimVehicleSegment (SegmentKey),
    ExShowroomPriceINR DECIMAL(14, 2) NOT NULL CONSTRAINT CK_DimVehicleModel_Price CHECK (ExShowroomPriceINR > 0),
    DataClassification VARCHAR(20)    NOT NULL CONSTRAINT CK_DimVehicleModel_Class CHECK (DataClassification = 'SIMULATED')
);
GO

IF OBJECT_ID(N'dw.FactLeadFunnel') IS NULL
CREATE TABLE dw.FactLeadFunnel (
    LeadId                VARCHAR(12)   NOT NULL CONSTRAINT PK_FactLeadFunnel PRIMARY KEY,
    DealershipId          VARCHAR(10)   NOT NULL CONSTRAINT FK_FLF_Dealer REFERENCES dw.DimDealership (DealershipId),
    SalespersonId         VARCHAR(20)   NOT NULL CONSTRAINT FK_FLF_Salesperson REFERENCES dw.DimSalesperson (SalespersonId),
    ModelId               VARCHAR(10)   NOT NULL CONSTRAINT FK_FLF_Model REFERENCES dw.DimVehicleModel (ModelId),
    LeadDateKey           INT           NOT NULL CONSTRAINT FK_FLF_Date REFERENCES dw.DimDate (DateKey),
    LeadSource            NVARCHAR(50)  NOT NULL,
    LeadDate              DATE          NOT NULL,
    FirstContactHours     DECIMAL(7, 1) NOT NULL CONSTRAINT CK_FLF_Contact CHECK (FirstContactHours >= 0),
    FirstContactWithinSLA BIT           NOT NULL,
    QualifiedDate         DATE          NULL,
    TestDriveDate         DATE          NULL,
    BookingId             VARCHAR(12)   NULL,
    BookingDate           DATE          NULL,
    RequestedDeliveryDate DATE          NULL,
    BookingCancelledDate  DATE          NULL,
    LeadStatus            NVARCHAR(40)  NOT NULL,
    LostStage             NVARCHAR(20)  NULL,
    LostReason            NVARCHAR(100) NULL,
    LostDate              DATE          NULL,
    DeliveryId            VARCHAR(12)   NULL,
    DeliveryDate          DATE          NULL,
    Vin                   VARCHAR(20)   NULL,
    IsQualified AS CAST(CASE WHEN QualifiedDate IS NULL THEN 0 ELSE 1 END AS BIT) PERSISTED,
    IsTestDrive AS CAST(CASE WHEN TestDriveDate IS NULL THEN 0 ELSE 1 END AS BIT) PERSISTED,
    IsBooked    AS CAST(CASE WHEN BookingId IS NULL THEN 0 ELSE 1 END AS BIT) PERSISTED,
    IsDelivered AS CAST(CASE WHEN DeliveryId IS NULL THEN 0 ELSE 1 END AS BIT) PERSISTED,
    DataClassification    VARCHAR(20)   NOT NULL CONSTRAINT CK_FLF_Class CHECK (DataClassification = 'SIMULATED'),
    CONSTRAINT CK_FLF_Status CHECK (LeadStatus IN (N'Open', N'Lost', N'Booked - awaiting delivery', N'Delivered')),
    CONSTRAINT CK_FLF_StageOrder CHECK (
        (QualifiedDate IS NULL OR QualifiedDate >= LeadDate)
        AND (TestDriveDate IS NULL OR (QualifiedDate IS NOT NULL AND TestDriveDate >= QualifiedDate))
        AND (BookingDate IS NULL OR (TestDriveDate IS NOT NULL AND BookingDate >= TestDriveDate))
        AND (DeliveryDate IS NULL OR (BookingDate IS NOT NULL AND DeliveryDate >= BookingDate))),
    CONSTRAINT CK_FLF_Lost CHECK (LeadStatus <> N'Lost' OR (LostStage IS NOT NULL AND LostReason IS NOT NULL
                                                           AND LostDate IS NOT NULL AND DeliveryId IS NULL))
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_FLF_Dealer_LeadDate')
    CREATE INDEX IX_FLF_Dealer_LeadDate ON dw.FactLeadFunnel (DealershipId, LeadDate)
        INCLUDE (SalespersonId, ModelId, LeadSource, LeadStatus, FirstContactWithinSLA, QualifiedDate, TestDriveDate, BookingId, DeliveryId);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_FLF_BookingId')
    CREATE UNIQUE INDEX UX_FLF_BookingId ON dw.FactLeadFunnel (BookingId) WHERE BookingId IS NOT NULL;
GO

IF OBJECT_ID(N'dw.FactVehicleInventory') IS NULL
CREATE TABLE dw.FactVehicleInventory (
    Vin                VARCHAR(20)    NOT NULL CONSTRAINT PK_FactVehicleInventory PRIMARY KEY,
    DealershipId       VARCHAR(10)    NOT NULL CONSTRAINT FK_FVI_Dealer REFERENCES dw.DimDealership (DealershipId),
    ModelId            VARCHAR(10)    NOT NULL CONSTRAINT FK_FVI_Model REFERENCES dw.DimVehicleModel (ModelId),
    ReceiptDate        DATE           NOT NULL,
    PurchaseCostINR    DECIMAL(14, 2) NOT NULL CONSTRAINT CK_FVI_Cost CHECK (PurchaseCostINR > 0),
    DeliveryDate       DATE           NULL,
    StockStatus        NVARCHAR(20)   NOT NULL,
    DaysInInventory    INT            NOT NULL CONSTRAINT CK_FVI_Days CHECK (DaysInInventory >= 0),
    DataClassification VARCHAR(20)    NOT NULL CONSTRAINT CK_FVI_Class CHECK (DataClassification = 'SIMULATED'),
    CONSTRAINT CK_FVI_Status CHECK ((StockStatus = N'Delivered' AND DeliveryDate IS NOT NULL)
                                    OR (StockStatus = N'In stock' AND DeliveryDate IS NULL)),
    CONSTRAINT CK_FVI_Dates CHECK (DeliveryDate IS NULL OR DeliveryDate >= ReceiptDate)
);
GO

IF OBJECT_ID(N'dw.FactVehicleDelivery') IS NULL
CREATE TABLE dw.FactVehicleDelivery (
    DeliveryId         VARCHAR(12)    NOT NULL CONSTRAINT PK_FactVehicleDelivery PRIMARY KEY,
    BookingId          VARCHAR(12)    NOT NULL CONSTRAINT UQ_FVD_Booking UNIQUE,
    LeadId             VARCHAR(12)    NOT NULL CONSTRAINT UQ_FVD_Lead UNIQUE
                                      CONSTRAINT FK_FVD_Lead REFERENCES dw.FactLeadFunnel (LeadId),
    Vin                VARCHAR(20)    NOT NULL CONSTRAINT UQ_FVD_Vin UNIQUE
                                      CONSTRAINT FK_FVD_Vin REFERENCES dw.FactVehicleInventory (Vin),
    DealershipId       VARCHAR(10)    NOT NULL CONSTRAINT FK_FVD_Dealer REFERENCES dw.DimDealership (DealershipId),
    ModelId            VARCHAR(10)    NOT NULL CONSTRAINT FK_FVD_Model REFERENCES dw.DimVehicleModel (ModelId),
    SalespersonId      VARCHAR(20)    NOT NULL CONSTRAINT FK_FVD_Salesperson REFERENCES dw.DimSalesperson (SalespersonId),
    DeliveryDateKey    INT            NOT NULL CONSTRAINT FK_FVD_Date REFERENCES dw.DimDate (DateKey),
    DeliveryDate       DATE           NOT NULL,
    DaysFromBooking    INT            NOT NULL CONSTRAINT CK_FVD_DaysBooking CHECK (DaysFromBooking >= 0),
    DaysInInventory    INT            NOT NULL CONSTRAINT CK_FVD_DaysInv CHECK (DaysInInventory >= 0),
    ExShowroomPriceINR DECIMAL(14, 2) NOT NULL,
    DiscountPct        DECIMAL(5, 2)  NOT NULL CONSTRAINT CK_FVD_Discount CHECK (DiscountPct BETWEEN 0 AND 100),
    InvoiceAmountINR   DECIMAL(14, 2) NOT NULL,
    PurchaseCostINR    DECIMAL(14, 2) NOT NULL,
    DataClassification VARCHAR(20)    NOT NULL CONSTRAINT CK_FVD_Class CHECK (DataClassification = 'SIMULATED')
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_FVD_Dealer_Date')
    CREATE INDEX IX_FVD_Dealer_Date ON dw.FactVehicleDelivery (DealershipId, DeliveryDate)
        INCLUDE (ModelId, InvoiceAmountINR, PurchaseCostINR, DaysInInventory);
GO

IF OBJECT_ID(N'dw.FactInventorySnapshot') IS NULL
CREATE TABLE dw.FactInventorySnapshot (
    SnapshotDateKey    INT            NOT NULL CONSTRAINT FK_FIS_Date REFERENCES dw.DimDate (DateKey),
    Vin                VARCHAR(20)    NOT NULL CONSTRAINT FK_FIS_Vin REFERENCES dw.FactVehicleInventory (Vin),
    DealershipId       VARCHAR(10)    NOT NULL CONSTRAINT FK_FIS_Dealer REFERENCES dw.DimDealership (DealershipId),
    ModelId            VARCHAR(10)    NOT NULL CONSTRAINT FK_FIS_Model REFERENCES dw.DimVehicleModel (ModelId),
    PurchaseCostINR    DECIMAL(14, 2) NOT NULL,
    AgeDays            INT            NOT NULL CONSTRAINT CK_FIS_Age CHECK (AgeDays >= 0),
    AgeingBucket       NVARCHAR(10)   NOT NULL CONSTRAINT CK_FIS_Bucket CHECK (AgeingBucket IN (N'0-30', N'31-60', N'61-90', N'>90')),
    IsAged             BIT            NOT NULL,
    AgeingBucketOrder  AS CAST(CASE AgeingBucket WHEN N'0-30' THEN 1 WHEN N'31-60' THEN 2 WHEN N'61-90' THEN 3 ELSE 4 END AS TINYINT) PERSISTED,
    DataClassification VARCHAR(20)    NOT NULL CONSTRAINT CK_FIS_Class CHECK (DataClassification = 'SIMULATED'),
    CONSTRAINT PK_FactInventorySnapshot PRIMARY KEY (SnapshotDateKey, Vin),
    CONSTRAINT CK_FIS_BucketMatchesAge CHECK (
        (AgeingBucket = N'0-30' AND AgeDays <= 30) OR (AgeingBucket = N'31-60' AND AgeDays BETWEEN 31 AND 60)
        OR (AgeingBucket = N'61-90' AND AgeDays BETWEEN 61 AND 90) OR (AgeingBucket = N'>90' AND AgeDays > 90))
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_FIS_Dealer_Snapshot')
    CREATE INDEX IX_FIS_Dealer_Snapshot ON dw.FactInventorySnapshot (DealershipId, SnapshotDateKey)
        INCLUDE (ModelId, AgeDays, AgeingBucket, IsAged, PurchaseCostINR);
GO

/* ------------------------------------------------------------------ report-ready outputs */
IF OBJECT_ID(N'dw.FactActionTrigger') IS NULL
CREATE TABLE dw.FactActionTrigger (                 -- one row per dealership x action rule      SIMULATED
    DealershipId       VARCHAR(10)    NOT NULL CONSTRAINT FK_FAT_Dealer REFERENCES dw.DimDealership (DealershipId),
    Kpi                NVARCHAR(100)  NOT NULL,
    ActualValue        DECIMAL(18, 4) NULL,         -- NULL = KPI undefined (e.g. no deliveries), not zero
    Comparator         NVARCHAR(5)    NOT NULL CONSTRAINT CK_FAT_Comparator CHECK (Comparator IN (N'>', N'<', N'>=', N'<=')),
    Threshold          DECIMAL(18, 4) NOT NULL,
    Breached           BIT            NOT NULL,
    TriggerStatus      NVARCHAR(20)   NOT NULL CONSTRAINT CK_FAT_Status CHECK (TriggerStatus IN (N'ACTION', N'OK', N'NO DATA')),
    ResponsibleRole    NVARCHAR(100)  NOT NULL,
    ReviewCadence      NVARCHAR(30)   NOT NULL,
    RecommendedAction  NVARCHAR(400)  NULL,
    ThresholdBasis     NVARCHAR(400)  NOT NULL,
    DataClassification VARCHAR(20)    NOT NULL CONSTRAINT CK_FAT_Class CHECK (DataClassification = 'SIMULATED'),
    CONSTRAINT PK_FactActionTrigger PRIMARY KEY (DealershipId, Kpi)
);
GO

IF OBJECT_ID(N'dw.MarketAttention') IS NULL
CREATE TABLE dw.MarketAttention (                   -- one row per portfolio brand (+ car aggregate)  DERIVED from OFFICIAL
    Brand                NVARCHAR(100)  NOT NULL CONSTRAINT PK_MarketAttention PRIMARY KEY,
    SegmentCode          NVARCHAR(10)   NOT NULL,
    AttentionStatus      NVARCHAR(50)   NOT NULL,
    Triggers             NVARCHAR(400)  NOT NULL,
    YoYGrowthPct         DECIMAL(18, 6) NULL,
    SegmentGrowthPct     DECIMAL(18, 6) NULL,
    ShareChangeBps       DECIMAL(18, 6) NULL,
    RegistrationGapUnits DECIMAL(18, 4) NULL,
    DataClassification   VARCHAR(20)    NOT NULL CONSTRAINT DF_MA_Class DEFAULT ('DERIVED')
                                        CONSTRAINT CK_MA_Class CHECK (DataClassification = 'DERIVED')
);
GO

IF OBJECT_ID(N'audit.ValidationResult') IS NULL
CREATE TABLE audit.ValidationResult (
    ValidationResultId INT IDENTITY(1, 1) NOT NULL CONSTRAINT PK_ValidationResult PRIMARY KEY,
    RunAtUtc           DATETIME2(0)   NOT NULL CONSTRAINT DF_ValidationResult_RunAt DEFAULT (SYSUTCDATETIME()),
    Stage              NVARCHAR(30)   NOT NULL,
    CheckName          NVARCHAR(200)  NOT NULL,
    FailedRows         INT            NOT NULL,
    Detail             NVARCHAR(4000) NULL
);
GO
