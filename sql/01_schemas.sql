/* 01_schemas.sql - stg (raw text landing), dw (typed star schema), rpt (analysis views), audit (validation log). */
IF SCHEMA_ID(N'stg') IS NULL EXEC (N'CREATE SCHEMA stg AUTHORIZATION dbo;');
IF SCHEMA_ID(N'dw') IS NULL EXEC (N'CREATE SCHEMA dw AUTHORIZATION dbo;');
IF SCHEMA_ID(N'rpt') IS NULL EXEC (N'CREATE SCHEMA rpt AUTHORIZATION dbo;');
IF SCHEMA_ID(N'audit') IS NULL EXEC (N'CREATE SCHEMA audit AUTHORIZATION dbo;');
GO
