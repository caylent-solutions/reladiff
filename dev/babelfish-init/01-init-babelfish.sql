-- Initialize Babelfish compatibility layer for PostgreSQL
-- This script sets up a PostgreSQL database with SQL Server-compatible schema structure
-- Note: This simulates Babelfish behavior using standard PostgreSQL

-- Set up the default schema (SQL Server compatible)
CREATE SCHEMA IF NOT EXISTS dbo;

-- Create a test table to verify functionality
CREATE TABLE IF NOT EXISTS dbo.babelfish_test (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert test data
INSERT INTO dbo.babelfish_test (id, name) VALUES 
(1, 'Babelfish Test') 
ON CONFLICT (id) DO NOTHING;

-- Create SQL Server compatible functions for testing
-- Simulate GETUTCDATE() function
CREATE OR REPLACE FUNCTION dbo.getutcdate()
RETURNS TIMESTAMP AS $$
BEGIN
    RETURN NOW() AT TIME ZONE 'UTC';
END;
$$ LANGUAGE plpgsql;

-- Create compatibility views for INFORMATION_SCHEMA that work with our dialect
CREATE OR REPLACE VIEW dbo.information_schema_columns AS
SELECT 
    table_schema,
    table_name,
    column_name,
    data_type,
    CASE 
        WHEN data_type LIKE '%time%' OR data_type LIKE '%date%' THEN 3
        ELSE NULL 
    END as datetime_precision,
    numeric_precision,
    numeric_scale,
    ordinal_position
FROM information_schema.columns;