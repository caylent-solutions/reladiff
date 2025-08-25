# Reladiff Benchmarking & Table Comparison Guide

**A comprehensive guide for new team members to set up, run, and understand Reladiff benchmarks and table comparisons.**

---

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Setting Up Your Environment](#setting-up-your-environment)
4. [Running Benchmarks](#running-benchmarks)
5. [Lambda-Based Benchmarking](#lambda-based-benchmarking)
6. [Understanding Results](#understanding-results)
7. [Database Configurations](#database-configurations)
8. [Troubleshooting](#troubleshooting)
9. [Advanced Usage](#advanced-usage)
10. [Contributing](#contributing)

---

## Overview

Reladiff is a powerful tool for comparing tables across different databases and measuring performance. This guide covers:

- **Local benchmarking** using dev scripts
- **AWS Lambda benchmarking** for scalable cloud execution  
- **Cross-database comparisons** (PostgreSQL ↔ MySQL ↔ MSSQL ↔ Babelfish)
- **Performance analysis** and optimization

### Key Capabilities

✅ **Cross-Database Support**: PostgreSQL, MySQL, MSSQL, Babelfish, Snowflake, BigQuery, and more  
✅ **Scalable Performance**: Handle tables with billions of rows  
✅ **Lambda Integration**: Serverless benchmarking in AWS  
✅ **Rich Metrics**: Timing, memory usage, throughput analysis  
✅ **CI/CD Ready**: Automated benchmarking with GitHub Actions  

---

## Quick Start

### Prerequisites

- We recommend running in a devcontainer.  This will ensure all pre-reqs are already installed for you
- **Docker & Docker Compose** (for test databases)
- **Python 3.11+** with Poetry
- **Git** for version control
- **AWS CLI** (optional, for Lambda deployment)

### 5-Minute Setup

```bash
# 1. Clone and install
git clone https://github.com/erezsh/reladiff
cd reladiff
poetry install

# 2. Start test databases
docker-compose up -d mysql postgres

# 3. Run a simple benchmark
poetry run python run_1m_benchmark.py
```

🎉 **That's it!** You should see benchmark results comparing PostgreSQL and MySQL performance.

---

## Setting Up Your Environment

### 1. Install Dependencies

```bash
# Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install project dependencies
poetry install

# Verify installation
poetry run reladiff --help
```

### 2. Start Test Databases

```bash
# Start all supported databases
docker-compose up -d mysql postgres mssql babelfish

# Or start specific databases
docker-compose up -d mysql postgres

# Check database status
docker-compose ps
```

**Default Connection Strings for local test databases:**
```bash
PostgreSQL: postgresql://postgres:Password1@localhost:5432/postgres
MySQL:      mysql://mysql:Password1@localhost:3306/mysql
MSSQL:      mssql://sa:Password123!@localhost:1433/master
Babelfish:  babelfish://sa:Password123!@localhost:1434/master
```

### 3. Verify Connectivity

```bash
# Test database connections
poetry run python -c "
from reladiff import connect
db = connect('postgresql://postgres:Password1@localhost:5432/postgres')
print('PostgreSQL: ✅')
db.close()

db = connect('mysql://mysql:Password1@localhost:3306/mysql')
print('MySQL: ✅')
db.close()
"
```

---

## Running Benchmarks

### Local Development Benchmarks

#### 1. Basic Table Comparison

```bash
# Compare two tables in the same database
poetry run reladiff postgresql://postgres:Password1@localhost:5432/postgres table1 table2

# Compare tables across databases
poetry run reladiff \
  postgresql://postgres:Password1@localhost:5432/postgres table1 \
  mysql://mysql:Password1@localhost:3306/mysql table2
```

#### 2. Automated Benchmark Suite

```bash
# Run the complete 1M dataset benchmark
poetry run python run_1m_benchmark.py

# Run with specific test filter
poetry run python -c "
from lambda.benchmark_executor import BenchmarkExecutor
executor = BenchmarkExecutor()
result = executor.execute_benchmark_suite({
    'postgresql': {'driver': 'postgresql://postgres:Password1@localhost:5432/postgres'},
    'mysql': {'driver': 'mysql://mysql:Password1@localhost:3306/mysql'}
}, test_filter=['postgresql_int_mysql_int'])
print(result)
"
```

#### 3. Performance Measurement

```bash
# Run traditional benchmarks (matches dev/benchmark.sh)
poetry run python dev/benchmark.sh

# Create performance graphs
poetry run python dev/graph.py
```

### Configuration-Based Benchmarking

Create a `benchmark-config.toml` file:

```toml
[database.postgres_local]
driver = "postgresql://postgres:Password1@localhost:5432/postgres"

[database.mysql_local]  
driver = "mysql://mysql:Password1@localhost:3306/mysql"

[run.compare_ratings]
1 = { database = "postgres_local", table = "rating" }
2 = { database = "mysql_local", table = "rating" }
key_columns = ["userid"]
columns = ["movieid", "rating", "timestamp"]
bisection_threshold = 16000
```

```bash
# Run configuration-based benchmark
poetry run reladiff --conf benchmark-config.toml
```

---

## Lambda-Based Benchmarking

### Prerequisites for Lambda

```bash
# Install AWS CLI and SAM CLI
poetry install awscli aws-sam-cli

# Configure AWS credentials
aws configure

# Make deployment script executable
chmod +x deploy.sh
```

### Deploy Lambda Infrastructure

```bash
# Deploy to development environment
./deploy.sh dev --email your-email@example.com

# Deploy with container builds for better performance
./deploy.sh dev --email your-email@example.com --build-containers

# Deploy to production
./deploy.sh prod --email alerts@company.com
```

### Run Lambda Benchmarks

#### 1. Auto-Discovery Mode

```bash
# Auto-discover deployed Lambda stack
poetry run reladiff \
  postgresql://postgres:Password1@localhost:5432/postgres rating \
  mysql://mysql:Password1@localhost:3306/mysql rating \
  --aws auto
```

#### 2. Explicit Queue Mode

```bash
# Use specific SQS queue URL
poetry run reladiff \
  postgresql://host1:5432/db1 table1 \
  mysql://host2:3306/db2 table2 \
  --aws "https://sqs.us-east-1.amazonaws.com/account/queue-name.fifo"
```

#### 3. Configuration-Based Lambda

```toml
# reladiff-lambda-config.toml
[database.prod_postgres]
driver = "postgresql://secrets:postgres@prod-host:5432/database"

[database.prod_mysql]
driver = "mysql://secrets:mysql@prod-host:3306/database"

[aws]
auto = true
region = "us-east-1"

[run.prod_comparison]
1 = { database = "prod_postgres", table = "products" }
2 = { database = "prod_mysql", table = "products" }
key_columns = ["id"]
bisection_factor = 64
bisection_threshold = 50000
```

### Monitor Lambda Execution

```bash
# Get CloudWatch dashboard URL
aws cloudformation describe-stacks \
  --stack-name reladiff-serverless-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`Dashboard`].OutputValue' \
  --output text

# Monitor logs in real-time
aws logs tail /aws/lambda/reladiff-coordinator-dev --follow

# Check job status
aws s3 ls s3://your-results-bucket/your-job-id/
```

---

## Understanding Results

### Benchmark Output Format

```csv
test_name,status,duration_seconds,database_types,table1_rows,table2_rows,diff_operations,peak_memory_mb,rows_per_second,operations_per_second,memory_efficiency_mb_per_row,threads_used,start_time,end_time
postgresql_int_mysql_int,SUCCESS,0.886,postgresql_to_mysql,1000000,1000000,0,151.0,2257501,0.0,0.000151,16,2025-08-25T15:11:50,2025-08-25T15:11:51
```

### Key Metrics Explained

| Metric | Description |
|--------|-------------|
| **duration_seconds** | Total time for table comparison |
| **table1_rows / table2_rows** | Number of rows in each table |
| **diff_operations** | Number of differences found (+/-/!) |
| **peak_memory_mb** | Maximum memory usage during comparison |
| **rows_per_second** | Throughput (rows processed per second) |
| **memory_efficiency_mb_per_row** | Memory usage per row |

### Performance Interpretation

#### Excellent Performance
- **Rows/sec**: >1,000,000
- **Memory efficiency**: <0.001 MB/row  
- **Duration**: <1s for 1M rows

#### Good Performance  
- **Rows/sec**: 100,000 - 1,000,000
- **Memory efficiency**: 0.001 - 0.01 MB/row
- **Duration**: 1-10s for 1M rows

#### Needs Optimization
- **Rows/sec**: <100,000
- **Memory efficiency**: >0.01 MB/row
- **Duration**: >10s for 1M rows

### Common Result Patterns

```bash
# No differences (perfect match)
diff_operations,0

# Data discrepancies found
diff_operations,156  # 156 rows differ

# Schema or connection issues
status,FAILED
error,"Connection timeout"
```

---

## Database Configurations

### Connection String Formats

```bash
# PostgreSQL
postgresql://user:password@host:port/database
postgresql://user:password@host:port/database?sslmode=require

# MySQL
mysql://user:password@host:port/database
mysql://user:password@host:port/database?charset=utf8mb4

# MSSQL  
mssql://user:password@host:port/database
mssql://sa:Password123!@localhost:1433/master

# Babelfish (SQL Server compatibility on PostgreSQL)
babelfish://sa:Password123!@localhost:1434/master

# Snowflake
snowflake://user:password@account/database?warehouse=COMPUTE_WH&role=ACCOUNTADMIN

# BigQuery  
bigquery:///project-id
```

### Using AWS Secrets Manager

```toml
[database.prod_db]
driver = "postgresql://secrets:prod-db-secret@prod-host:5432/database"
```

The system will automatically retrieve credentials from AWS Secrets Manager using the secret name `prod-db-secret`.

### Connection Parameters

```bash
# With specific key columns
poetry run reladiff db1_uri table1 db2_uri table2 -k id,timestamp

# With column filtering
poetry run reladiff db1_uri table1 db2_uri table2 -c name,email,created_at

# With row limits (for testing)
poetry run reladiff db1_uri table1 db2_uri table2 --limit 1000

# With threading control
poetry run reladiff db1_uri table1 db2_uri table2 --threads 8

# With debug output
poetry run reladiff db1_uri table1 db2_uri table2 -d
```

---

## Troubleshooting

### Common Issues & Solutions

#### Database Connection Issues

**Problem**: `Connection refused` or `Authentication failed`
```bash
# Check database is running
docker-compose ps

# Test connection manually
poetry run python -c "from reladiff import connect; db = connect('your-uri-here'); print('OK')"

# Check network connectivity
ping localhost
telnet localhost 5432
```

**Solution**: Verify connection strings, passwords, and that databases are started.

#### Memory Issues

**Problem**: `Out of memory` or very high memory usage
```bash
# Use bisection for large tables
poetry run reladiff db1 table1 db2 table2 --bisection-threshold 10000

# Reduce thread count
poetry run reladiff db1 table1 db2 table2 --threads 4

# Use column filtering
poetry run reladiff db1 table1 db2 table2 -c essential_column1,essential_column2
```

#### Performance Issues

**Problem**: Very slow comparisons
```bash
# Check table indexes on key columns
# PostgreSQL
SELECT * FROM pg_indexes WHERE tablename = 'your_table';

# MySQL  
SHOW INDEX FROM your_table;

# Add indexes if missing
CREATE INDEX idx_table_key ON your_table(key_column);
```

#### Lambda Issues

**Problem**: Lambda timeout or failure
```bash
# Check CloudWatch logs
aws logs tail /aws/lambda/reladiff-coordinator-dev --follow

# Increase memory and timeout in template.yaml
WorkerMemorySize: 3008
WorkerTimeout: 900

# Redeploy
sam deploy
```

### Debug Mode

Enable detailed logging:
```bash
# Local debugging
poetry run reladiff db1 table1 db2 table2 -d

# Lambda debugging - check CloudWatch logs
LOG_LEVEL=DEBUG poetry run reladiff db1 table1 db2 table2 --aws auto
```

### Test Suite Validation

```bash
# Run all tests
poetry run pytest test_benchmark_lambdas.py test_benchmark_edge_cases.py -v

# Run specific test category
poetry run pytest test_benchmark_lambdas.py::TestTablePopulator -v

# Run integration tests
poetry run pytest -m integration -v
```

---

## Advanced Usage

### Custom Benchmark Scripts

Create your own benchmark scripts:

```python
#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lambda'))

from benchmark_executor import BenchmarkExecutor

# Custom benchmark configuration
executor = BenchmarkExecutor()
databases = {
    'prod_postgres': {'driver': 'postgresql://...'},
    'prod_mysql': {'driver': 'mysql://...'}
}

# Run specific tests
result = executor.execute_benchmark_suite(
    databases, 
    test_filter=['postgresql_int_mysql_int']
)

# Process results
for test in result['results']:
    if test['status'] == 'SUCCESS':
        print(f"✅ {test['test_name']}: {test['duration_seconds']:.2f}s")
    else:
        print(f"❌ {test['test_name']}: {test['error']}")
```

### Performance Regression Detection

```python
# Compare against baseline
baseline = {
    'postgresql_int_mysql_int': {'duration': 2.0, 'rows_per_sec': 500000}
}

for test in current_results:
    if test['test_name'] in baseline:
        current_duration = test['duration_seconds']
        baseline_duration = baseline[test['test_name']]['duration']
        
        change = (current_duration / baseline_duration - 1) * 100
        if change > 20:  # 20% slower
            print(f"⚠️ Performance regression: {test['test_name']} is {change:.1f}% slower")
```

### Data Population for Testing

```python
from lambda.table_populator import TablePopulator

populator = TablePopulator()

# Populate with 1M dataset
result = populator.populate_database(
    {'driver': 'postgresql://postgres:Password1@localhost:5432/postgres'},
    '1m',  # or '25m' for 25M dataset
    'test-job-123'
)

print(f"Populated {result['statistics']['rating_rows']} rows")
```

### CI/CD Integration

```yaml
# .github/workflows/benchmark.yml
name: Performance Benchmarks
on: 
  push:
    branches: [main]
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM

jobs:
  benchmark:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    - uses: actions/setup-python@v4
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        pip install poetry
        poetry install
    
    - name: Start databases
      run: docker-compose up -d mysql postgres
    
    - name: Run benchmarks
      run: poetry run python run_1m_benchmark.py
      
    - name: Upload results
      uses: actions/upload-artifact@v3
      with:
        name: benchmark-results
        path: benchmark-results.csv
```

---

## Support & Resources

### Getting Help

- 📖 **Documentation**: [reladiff.readthedocs.io](https://reladiff.readthedocs.io)
- 💬 **Discussions**: [GitHub Discussions](https://github.com/erezsh/reladiff/discussions)
- 🐛 **Issues**: [GitHub Issues](https://github.com/erezsh/reladiff/issues)
- 📧 **Email**: See repository maintainers

### Useful Commands Reference

```bash
# Quick reference
poetry run reladiff --help                    # Show all options
poetry run reladiff db1 t1 db2 t2 -k id      # Basic comparison
poetry run reladiff db1 t1 db2 t2 --stats    # Show detailed stats  
poetry run reladiff db1 t1 db2 t2 -d         # Debug mode
poetry run reladiff --conf config.toml       # Use config file
poetry run reladiff db1 t1 db2 t2 --aws auto # Use Lambda

# Database management
docker-compose up -d mysql postgres          # Start databases
docker-compose ps                            # Check status
docker-compose logs mysql                    # View logs
docker-compose down                          # Stop all

# Testing and validation
poetry run pytest -v                        # Run all tests
poetry run python run_1m_benchmark.py       # Full benchmark
poetry run python test_benchmark_small.py   # Quick test
```

### Performance Tuning Tips

1. **Add indexes** on key columns for faster comparisons
2. **Use column filtering** to reduce data transfer  
3. **Adjust bisection thresholds** for optimal memory usage
4. **Tune thread counts** based on database connection limits
5. **Use Lambda** for very large datasets or cross-region comparisons

---

**🎉 You're ready to start benchmarking!**

This guide covers everything from basic table comparisons to advanced Lambda-based benchmarking. Start with the Quick Start section and gradually explore more advanced features as you become comfortable with the system.

For questions or contributions, please see the Support section above.