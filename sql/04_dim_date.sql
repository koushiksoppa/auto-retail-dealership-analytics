/* 04_dim_date.sql - populates dw.DimDate for 2024-01-01 .. 2027-12-31. Rerunnable (inserts missing days only). */
SET NOCOUNT ON;
DECLARE @start DATE = '2024-01-01', @end DATE = '2027-12-31';

WITH n AS (
    SELECT TOP (DATEDIFF(DAY, @start, @end) + 1)
           ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
    FROM sys.all_objects a CROSS JOIN sys.all_objects b
), d AS (
    SELECT DATEADD(DAY, i, @start) AS CalendarDate FROM n
)
INSERT dw.DimDate (DateKey, CalendarDate, YearNumber, QuarterNumber, MonthNumber, MonthName, MonthShortName,
                   YearMonth, MonthStartDate, MonthEndDate, IsMonthEnd, IndianFiscalYear)
SELECT CONVERT(INT, CONVERT(CHAR(8), d.CalendarDate, 112)),
       d.CalendarDate,
       YEAR(d.CalendarDate),
       DATEPART(QUARTER, d.CalendarDate),
       MONTH(d.CalendarDate),
       DATENAME(MONTH, d.CalendarDate),
       LEFT(DATENAME(MONTH, d.CalendarDate), 3),
       CONVERT(CHAR(7), d.CalendarDate, 126),
       DATEFROMPARTS(YEAR(d.CalendarDate), MONTH(d.CalendarDate), 1),
       EOMONTH(d.CalendarDate),
       CASE WHEN d.CalendarDate = EOMONTH(d.CalendarDate) THEN 1 ELSE 0 END,
       CONCAT(N'FY', YEAR(d.CalendarDate) - CASE WHEN MONTH(d.CalendarDate) < 4 THEN 1 ELSE 0 END, N'-',
              RIGHT(CONVERT(CHAR(4), YEAR(d.CalendarDate) + CASE WHEN MONTH(d.CalendarDate) < 4 THEN 0 ELSE 1 END), 2))
FROM d
WHERE NOT EXISTS (SELECT 1 FROM dw.DimDate x WHERE x.CalendarDate = d.CalendarDate);
GO
