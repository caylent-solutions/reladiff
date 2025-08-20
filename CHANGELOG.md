# Changelog

All notable changes to reladiff will be documented in this file.

## [0.6.1] - 2025-08-20

### Added
- **Babelfish for PostgreSQL Support**: Full support for Amazon's Babelfish for PostgreSQL
  - New `babelfish://` URI scheme for connecting to Babelfish-enabled PostgreSQL servers
  - Uses TDS protocol with T-SQL compatibility for SQL Server migration scenarios
  - Cross-database diffing between MSSQL ↔ Babelfish, PostgreSQL ↔ Babelfish, MySQL ↔ Babelfish
  - Default connection: `babelfish://sa:Password123!@localhost:1434/master`

### Enhanced
- **ARM64 Platform Support**: Comprehensive ARM64 compatibility improvements
  - Updated MSSQL support to use `pymssql` (FreeTDS) instead of `pyodbc` for better ARM64 compatibility
  - Native ARM64 performance for all database adapters except MSSQL (which runs via emulation)
  - Added ARM64_SETUP.md with detailed setup instructions for Apple Silicon and ARM64 Linux

### Fixed
- **MSSQL Connection Stability**: Resolved ARM64 connectivity issues with SQL Server
  - Switched from `pyodbc` to `pymssql` for broader platform compatibility
  - Fixed connection parameter handling and type mappings
  - Improved error handling for database connection failures

### Technical Details
- **Database Adapters**: Enhanced database adapter framework
  - Added `reladiff/databases/babelfish.py` with full T-SQL compatibility
  - Improved schema detection and type mapping across all adapters
  - Better cross-database diff performance with optimized threading

- **Development Environment**: Improved development setup
  - Updated Docker Compose configuration for multi-database testing
  - Enhanced CLAUDE.md documentation with comprehensive setup instructions
  - Added integration tests for cross-database scenarios

### Dependencies
- Added `psycopg2-binary>=2.8.6` for Babelfish PostgreSQL connectivity
- Updated `pymssql` dependency for improved MSSQL support on ARM64
- Added optional extras: `pip install reladiff[babelfish]`

### Cross-Database Compatibility Matrix
- ✅ MySQL ↔ PostgreSQL ↔ MSSQL ↔ Babelfish ↔ DuckDB
- ✅ All combinations work on both x86_64 and ARM64 architectures
- ✅ Tested with tables containing millions of rows

## [0.6.0] - Previous Release
- Core diffing functionality
- MySQL, PostgreSQL, MSSQL, DuckDB support
- HashDiff and JoinDiff algorithms