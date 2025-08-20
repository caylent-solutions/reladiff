# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Package Management
- **Poetry**: This project uses Poetry for dependency management
- **Install dependencies**: `poetry install` (creates virtual env and installs all dependencies)
- **Run commands in poetry env**: `poetry run <command>`
- **Install in global context**: `pip install -e .` (alternative to poetry for development)

### Testing
- **Minimum requirement**: MySQL. Create with `docker-compose up mysql`. URI: `mysql://mysql:Password1@localhost/mysql`
- **MSSQL Support**: Available for all platforms including ARM64 (Apple Silicon)
  - Start with: `docker-compose up mssql`  
  - URI: `mssql://sa:Password123!@localhost:1433/master`
  - Uses pymssql (FreeTDS) for ARM64 compatibility instead of pyodbc
- **Babelfish Support**: SQL Server compatibility layer on PostgreSQL
  - Start with: `docker-compose up babelfish`
  - URI: `babelfish://sa:Password123!@localhost:1434/master`
  - Uses TDS protocol with full T-SQL compatibility
- **Multiple databases**: `docker-compose up mysql postgres mssql babelfish` to test cross-database functionality
- **Custom connections**: Override `TEST_*_CONN_STRING` in `tests/local_settings.py`
- **Run all tests**: `poetry run unittest-parallel -j 16` (recommended for 1000+ tests)
- **Run individual test**: `poetry run python -m unittest -k <test_name>`
- **Run with stop on first failure**: `poetry run python -m unittest -f`
- **Enable debug mode**: Add `-d` flag to reladiff commands for debug output

### Code Quality
- **Format code**: `black -l 120` (line length 120, required for all code)
- **Type checking**: MyPy is configured in pyproject.toml
- **Linting**: Ruff is configured in pyproject.toml with line length 120

### Database Testing Setup
- **Start test databases**: `docker-compose up mysql postgres` (or specific databases)
- **Connection strings**: Configure in `tests/common.py` or override in `tests/local_settings.py`
- **Seed test data**: Download CSV and run `poetry run preql -f dev/prepare_db.pql <database_uri>`
- **Benchmark**: `dev/benchmark.sh` and `poetry run python3 dev/graph.py`

### Running Reladiff
- **CLI**: `poetry run reladiff <args>` or `poetry run python -m reladiff <args>`
- **Basic syntax**: `reladiff DB1_URI TABLE1 DB2_URI TABLE2 [OPTIONS]`
- **Same DB**: `reladiff DB_URI TABLE1 TABLE2 [OPTIONS]`

## Architecture Overview

### Core Diffing Architecture
Reladiff implements two main algorithms for table diffing:

1. **HashDiff** (`hashdiff_tables.py`): Cross-database diffing using divide-and-conquer with hash-based segment comparison. Used when tables are in different databases.

2. **JoinDiff** (`joindiff_tables.py`): Same-database diffing using SQL joins. Used when both tables are in the same database for better performance.

The main entry point is `diff_tables()` in `__init__.py` which automatically selects the appropriate algorithm based on whether tables share the same database connection.

### Key Components
- **TableSegment** (`table_segment.py`): Represents a table or portion of a table with key columns, extra columns, and filtering constraints
- **Database Adapters** (`databases/`): Database-specific implementations for 10+ databases (PostgreSQL, MySQL, MSSQL, Babelfish, Snowflake, BigQuery, etc.)
- **Thread Management** (`thread_utils.py`): Utilities for parallel processing across database connections
- **CLI Interface** (`__main__.py`): Click-based command line interface with rich output formatting

### Database Connection System
- **Connection Factory** (`databases/_connect.py`): Central connection management
- **Base Classes** (`databases/base.py`): Abstract base classes for database implementations
- **Per-Database Modules**: Each supported database has its own module (e.g., `postgresql.py`, `mysql.py`, `mssql.py`, `babelfish.py`)

### Configuration System
- **TOML Config** (`config.py`): Support for configuration files to manage complex connection settings
- **CLI Options**: Extensive command-line options for key columns, filtering, threading, output formats

### Performance Features
- **Bisection Strategy**: HashDiff uses configurable bisection factor (default 32) and threshold (default 16K rows)
- **Threading**: Configurable thread pools for parallel database operations
- **Materialization**: JoinDiff can materialize results to tables for large diffs
- **Streaming**: Results are yielded as iterators to handle large datasets efficiently

### Testing Framework
- **Multi-Database Testing**: Tests run against multiple database types using docker-compose
- **Cross-Database Support**: Full MySQL ↔ PostgreSQL ↔ MSSQL ↔ Babelfish ↔ DuckDB compatibility testing
- **Parameterized Tests**: Extensive use of parameterized tests for database compatibility
- **Integration Tests**: End-to-end testing with real database connections
- **Performance Benchmarks**: Dedicated benchmarking tools for performance regression testing

### Platform Compatibility
- **ARM64 Support**: Full compatibility with Apple Silicon (M1/M2/M3) and ARM64 Linux
- **MSSQL on ARM64**: Uses pymssql (FreeTDS) instead of pyodbc for native ARM64 support
- **Docker Emulation**: x86_64 containers run via emulation on ARM64 when native images unavailable
- **Cross-Platform Testing**: All database combinations work across x86_64 and ARM64 architectures

This architecture enables efficient diffing of tables with billions of rows across different database systems while maintaining high performance and reliability on all platforms.