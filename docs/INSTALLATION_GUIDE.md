# Reladiff Installation Guide

Complete installation guide for reladiff v0.6.1 with Babelfish support.

## Quick Install

### Core Package
```bash
pip install reladiff
```

### With Database Support
```bash
# Babelfish support
pip install reladiff[babelfish]

# All major databases
pip install reladiff[mysql,postgresql,babelfish,mssql,duckdb]
```

## Platform-Specific Installation

### macOS (Apple Silicon - M1/M2/M3)

```bash
# Install reladiff with ARM64 optimizations
pip install reladiff[babelfish,mysql,postgresql]

# For MSSQL support (uses emulation)
pip install reladiff[mssql]

# Verify installation
reladiff --version
```

### macOS (Intel x86_64)

```bash
# Standard installation
pip install reladiff[babelfish,mysql,postgresql,mssql]

# Verify installation
reladiff --version
```

### Linux (ARM64)

```bash
# Native ARM64 support
pip install reladiff[babelfish,mysql,postgresql,mssql]

# For Ubuntu/Debian, you might need:
sudo apt-get update
sudo apt-get install python3-dev libpq-dev default-libmysqlclient-dev freetds-dev

# Then install reladiff
pip install reladiff[babelfish,mysql,postgresql,mssql]
```

### Linux (x86_64)

```bash
# Standard installation
pip install reladiff[babelfish,mysql,postgresql,mssql]

# For Ubuntu/Debian dependencies:
sudo apt-get install python3-dev libpq-dev default-libmysqlclient-dev

# For RHEL/CentOS:
sudo yum install python3-devel postgresql-devel mysql-devel freetds-devel
```

### Windows

```bash
# Standard installation
pip install reladiff[babelfish,mysql,postgresql,mssql]

# Note: MSSQL support on Windows uses native drivers
```

## Database-Specific Setup

### Babelfish for PostgreSQL

```bash
# Install Babelfish support
pip install reladiff[babelfish]

# Test connection
reladiff babelfish://sa:password@localhost:1434/master --help
```

**Connection Requirements:**
- Babelfish-enabled PostgreSQL server
- TDS endpoint listening on port 1433 (or custom port)
- SQL Server compatible authentication

### Microsoft SQL Server

```bash
# Install MSSQL support
pip install reladiff[mssql]

# ARM64 note: Uses pymssql (FreeTDS) for better compatibility
```

**Connection Requirements:**
- SQL Server 2012 or later
- TCP/IP protocol enabled
- SQL Server authentication or Windows authentication

### MySQL

```bash
# Install MySQL support
pip install reladiff[mysql]
```

**Connection Requirements:**
- MySQL 5.7 or later (including MariaDB)
- User with SELECT permissions on target tables

### PostgreSQL

```bash
# Install PostgreSQL support
pip install reladiff[postgresql]
```

**Connection Requirements:**
- PostgreSQL 10 or later
- User with SELECT permissions on target tables

## Development Installation

### From Source

```bash
# Clone repository
git clone https://github.com/erezsh/reladiff.git
cd reladiff

# Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Install with all database support
poetry install --extras "mysql postgresql mssql babelfish duckdb"

# Run tests
poetry run python -m pytest
```

### Using Docker

```bash
# Pull latest image
docker pull reladiff/reladiff:latest

# Run with volume mount
docker run -v $(pwd):/data reladiff/reladiff:latest \
  mysql://user:pass@host/db table1 table2
```

## Verification Steps

### Basic Installation Test

```bash
# Check version
reladiff --version

# Should output: reladiff v0.6.1

# Check available database adapters
python3 -c "
from reladiff.databases._connect import DATABASE_BY_SCHEME
print('Available databases:', list(DATABASE_BY_SCHEME.keys()))
"
```

### Database Connection Tests

```bash
# Test Babelfish (adjust connection details)
reladiff babelfish://sa:password@localhost:1434/master --help

# Test MSSQL
reladiff mssql://sa:password@localhost:1433/master --help

# Test MySQL  
reladiff mysql://user:password@localhost:3306/database --help

# Test PostgreSQL
reladiff postgresql://user:password@localhost:5432/database --help
```

## Troubleshooting

### Common Issues

#### ARM64 MSSQL Connection Issues
```bash
# Ensure using pymssql instead of pyodbc
pip uninstall pyodbc
pip install pymssql

# Verify installation
python3 -c "import pymssql; print('pymssql OK')"
```

#### PostgreSQL/Babelfish psycopg2 Issues
```bash
# Install binary version
pip install psycopg2-binary

# Or compile from source (requires dev packages)
sudo apt-get install libpq-dev
pip install psycopg2
```

#### MySQL Connection Issues
```bash
# Install mysql-connector-python
pip install mysql-connector-python

# Alternative: PyMySQL
pip install PyMySQL
```

### Performance Optimization

#### For Large Tables (>1M rows)
```bash
# Increase bisection threshold
reladiff db1 table1 db2 table2 --bisection-threshold 100000

# Limit result set
reladiff db1 table1 db2 table2 --limit 1000

# Use materialization for very large diffs
reladiff db1 table1 db2 table2 --materialize-to-table temp_diff_results
```

#### Multi-threading Configuration
```bash
# Set thread count via environment
export RELADIFF_THREAD_COUNT=8

# Or use configuration file
reladiff --conf config.toml
```

## Configuration Files

### Example config.toml
```toml
[databases.prod_mysql]
uri = "mysql://user:password@prod-server:3306/database"

[databases.prod_babelfish]  
uri = "babelfish://sa:password@babelfish-server:1434/master"

[run.compare_orders]
database_a = "prod_mysql"
table_a = "orders"
database_b = "prod_babelfish"
table_b = "orders"
key_columns = ["order_id"]
limit = 1000
```

### Usage with config
```bash
reladiff --conf config.toml --run compare_orders
```

## Environment Setup

### Docker Compose for Development

```yaml
version: '3.8'
services:
  mysql:
    image: mysql:8.0
    environment:
      MYSQL_ROOT_PASSWORD: password
    ports:
      - "3306:3306"
      
  postgres:
    image: postgres:14
    environment:
      POSTGRES_PASSWORD: password
    ports:
      - "5432:5432"
      
  mssql:
    image: mcr.microsoft.com/mssql/server:2022-latest
    environment:
      SA_PASSWORD: Password123!
      ACCEPT_EULA: Y
    ports:
      - "1433:1433"
      
  babelfish:
    image: babelfishpg/babelfish:latest
    environment:
      POSTGRES_PASSWORD: password
      MSSQL_SA_PASSWORD: Password123!
    ports:
      - "1434:1433"  # TDS port
      - "5433:5432"  # PostgreSQL port
```

## Support

- **Documentation**: https://reladiff.readthedocs.io/
- **Issues**: https://github.com/erezsh/reladiff/issues
- **Discussions**: https://github.com/erezsh/reladiff/discussions

## Next Steps

1. **Verify Installation**: Run `reladiff --version`
2. **Test Connection**: Connect to your databases
3. **Run First Diff**: Compare two small tables
4. **Explore Advanced Features**: Large table diffing, cross-database comparisons
5. **Configure for Production**: Set up configuration files and environment variables