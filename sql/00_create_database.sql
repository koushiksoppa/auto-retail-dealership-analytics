/* 00_create_database.sql
   Creates the project database on the connected SQL Server instance if it does not exist.
   Run with Windows authentication (no passwords are stored in this repository):
     sqlcmd -S .\SQLEXPRESS -E -C -i sql\00_create_database.sql
   The Python pipeline (python/sql_server_pipeline.py) performs the same step. */
IF DB_ID(N'AutoRetailAnalytics') IS NULL
BEGIN
    CREATE DATABASE AutoRetailAnalytics;
END;
GO
