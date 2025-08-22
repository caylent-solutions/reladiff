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

## AWS Lambda Deployment & Execution

### Prerequisites for Lambda Deployment

#### Required Tools
- **AWS CLI v2** with configured credentials (`aws configure`)
- **AWS SAM CLI** for serverless deployment (`brew install aws-sam-cli` or `pip install aws-sam-cli`)
- **Docker** for container builds (optional but recommended)
- **Poetry** for dependency management

#### AWS Permissions Required
Your AWS user/role needs permissions for: CloudFormation, Lambda, S3, SQS, IAM, API Gateway, EventBridge, SNS, CloudWatch, ECR

### Quick Deployment

#### Deploy to Development Environment
```bash
# Make deployment script executable
chmod +x deploy.sh

# Deploy with email notifications
./deploy.sh dev --email your-email@example.com

# Deploy with container images for better performance
./deploy.sh dev --email your-email@example.com --build-containers
```

#### Deploy to Production Environment
```bash
# Production deployment with enhanced settings
./deploy.sh prod --email alerts@yourcompany.com
```

#### Manual SAM Deployment (Advanced)
```bash
# Build the application
sam build

# Deploy with guided prompts
sam deploy --guided

# Deploy with specific parameters
sam deploy \
  --stack-name reladiff-serverless-prod \
  --parameter-overrides \
    Environment=prod \
    NotificationEmail=alerts@yourcompany.com \
    WorkerMemorySize=3008 \
    BisectionThreshold=32000 \
    BisectionFactor=64 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3
```

### Lambda Architecture Overview

```
┌─────────────────┐    ┌──────────────┐    ┌─────────────────┐
│   API Gateway   │───▶│ Coordinator  │───▶│   SQS Queue     │
│                 │    │   Lambda     │    │   (Workers)     │
└─────────────────┘    └──────────────┘    └─────────────────┘
                              │                       │
                              ▼                       ▼
                    ┌──────────────┐         ┌─────────────────┐
                    │      S3      │         │    Worker       │
                    │  (Metadata)  │         │   Lambdas       │
                    └──────────────┘         │  (Parallel)     │
                              ▲              └─────────────────┘
                              │                       │
                              │                       ▼
                    ┌──────────────┐         ┌─────────────────┐
                    │ Aggregator   │◀────────│   SQS Queue     │
                    │   Lambda     │         │ (Aggregation)   │
                    └──────────────┘         └─────────────────┘
                              │
                              ▼
                    ┌──────────────┐
                    │      S3      │
                    │   (Results)  │
                    └──────────────┘
```

**Key Components:**
- **Coordinator Lambda**: Analyzes tables, creates segments, orchestrates jobs
- **Worker Lambdas**: Process individual table segments in parallel
- **Aggregator Lambda**: Combines results from all workers
- **SQS Queues**: Manage message flow between components
- **S3**: Stores results, metadata, and intermediate data

### Using Lambda with Reladiff

#### Command Line with Auto-Discovery
```bash
# Auto-discover deployed Lambda stack
poetry run reladiff mssql_source products postgres_target products --conf config.toml --aws auto

# With additional options
poetry run reladiff db1 table1 db2 table2 --aws auto -k id -c name,email --limit 1000
```

#### Command Line with Explicit Queue URL
```bash
# Use specific SQS queue URL
poetry run reladiff db1 table1 db2 table2 --aws "https://sqs.us-east-1.amazonaws.com/account/reladiff-coordinator-dev.fifo"
```

#### TOML Configuration for Lambda
```toml
# reladiff-config.toml
[database.mssql_source]
driver = "mssql://secrets:mssql@host:port/database"

[database.postgres_target] 
driver = "postgresql://secrets:postgres@host:port/database"

# AWS Lambda configuration
[aws]
auto = true
region = "us-east-1"

# Table comparison with Lambda
[run.compare_products]
1 = { database = "mssql_source", table = "products" }
2 = { database = "postgres_target", table = "products" }
key_columns = ["id"]
columns = ["name", "price", "category"]
bisection_factor = 64
bisection_threshold = 50000
```

### Lambda Configuration Parameters

| Parameter | Description | Default | Environment |
|-----------|-------------|---------|-------------|
| `RESULTS_S3_BUCKET` | S3 bucket for results | Auto-created | All |
| `BISECTION_THRESHOLD` | Min rows before bisection stops | 16000 | All |
| `BISECTION_FACTOR` | Segments per bisection | 32 | All |
| `WORKER_QUEUE_URL` | SQS queue for worker jobs | Auto-created | All |
| `AGGREGATOR_QUEUE_URL` | SQS queue for aggregation | Auto-created | All |
| `LOG_LEVEL` | Logging verbosity | INFO | All |
| `MAX_MEMORY_MB` | Lambda memory limit | 2048 | All |

### Monitoring & Troubleshooting Lambda

#### CloudWatch Dashboard Access
```bash
# Get dashboard URL from stack outputs
aws cloudformation describe-stacks \
  --stack-name reladiff-serverless-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`Dashboard`].OutputValue' \
  --output text
```

#### Real-Time Log Monitoring
```bash
# Coordinator logs
aws logs tail /aws/lambda/reladiff-coordinator-dev --follow

# Worker logs  
aws logs tail /aws/lambda/reladiff-worker-dev --follow

# Aggregator logs
aws logs tail /aws/lambda/reladiff-aggregator-dev --follow
```

#### Job Status Monitoring
```bash
# Get job metadata
aws s3 cp s3://your-results-bucket/your-job-id/metadata.json -

# List segment results
aws s3 ls s3://your-results-bucket/your-job-id/segments/

# Download final results
aws s3 cp s3://your-results-bucket/your-job-id/final_results.jsonl.gz -
```

### Lambda Performance Optimization

#### For Large Tables (1M+ rows)
```bash
# High-performance configuration
sam deploy --parameter-overrides \
  WorkerMemorySize=3008 \
  BisectionFactor=128 \
  BisectionThreshold=50000
```

#### Memory and Timeout Tuning
```bash
# Monitor memory usage
aws logs filter-log-events \
  --log-group-name /aws/lambda/reladiff-worker-dev \
  --filter-pattern '[timestamp, requestId, billedDuration="REPORT", ...]'
```

### Cost Optimization for Lambda

#### S3 Lifecycle Policies (Auto-configured)
- Move to IA storage after 30 days
- Move to Glacier after 90 days  
- Delete after 1 year

#### Lambda Right-Sizing
- Monitor actual memory usage and adjust accordingly
- Use provisioned concurrency for critical workloads
- Optimize package size to reduce cold starts

### Security for Lambda Deployment

#### Database Connection Security
```bash
# Use SSL connections
postgresql://user:pass@host:5432/db?sslmode=require

# Store credentials in AWS Secrets Manager
# Reference as: "mssql://secrets:mssql@host:port/database"
```

#### IAM Role Configuration
The deployment creates least-privilege roles:
- **CoordinatorRole**: S3 read/write, SQS send, EventBridge publish
- **WorkerRole**: S3 read/write, SQS send  
- **AggregatorRole**: S3 read/write, SNS publish, EventBridge publish

#### VPC Configuration for Private Databases
```yaml
# In template.yaml for private database access
VpcConfig:
  SecurityGroupIds:
    - !Ref DatabaseSecurityGroup
  SubnetIds:
    - !Ref PrivateSubnet1
    - !Ref PrivateSubnet2
```

## Contributing Code

### Code Style Requirements
- **All code must be formatted with `black -l 120`** (line length 120 characters)
- **Type checking**: MyPy is configured in pyproject.toml  
- **Linting**: Ruff is configured in pyproject.toml with line length 120
- When in doubt, use existing code as a guideline

### Development Setup

#### 1. Clone and Install Dependencies
```bash
# Clone the repository
git clone https://github.com/erezsh/reladiff
cd reladiff

# Option 1: Poetry (recommended)
poetry install  # Creates virtual env and installs dependencies
poetry run reladiff --help  # Run commands in poetry env

# Option 2: Global installation
pip install -e .  # Install in global context
```

#### 2. Start Test Databases
```bash
# Install docker-compose if needed
# macOS: brew install docker-compose
# Linux: pip install docker-compose

# Start multiple databases for testing
docker-compose up -d mysql postgres mssql babelfish

# For Mac performance optimization, enable in Docker Desktop UI:
# - Use new Virtualization Framework  
# - Enable VirtioFS accelerated directory sharing
```

#### 3. Configure Database Connection Strings
```bash
# Default URIs (when using docker-compose):
# MySQL: mysql://mysql:Password1@localhost/mysql
# PostgreSQL: postgresql://postgres:Password1@localhost/postgres  
# MSSQL: mssql://sa:Password123!@localhost:1433/master
# Babelfish: babelfish://sa:Password123!@localhost:1434/master

# Override defaults by creating tests/local_settings.py:
TEST_MYSQL_CONN_STRING = "mysql://custom:pass@host/db"
TEST_POSTGRESQL_CONN_STRING = "postgresql://custom:pass@host/db"
TEST_MSSQL_CONN_STRING = "mssql://custom:pass@host/db"
TEST_BABELFISH_CONN_STRING = "babelfish://custom:pass@host/db"
```

#### 4. Run Tests
```bash
# Run all tests with parallel execution (recommended for 1000+ tests)
poetry run unittest-parallel -j 16

# Run individual test
poetry run python -m unittest -k test_name

# Run with stop on first failure (debugging)
poetry run python -m unittest -f

# Enable debug output in reladiff commands
poetry run reladiff db1 table1 db2 table2 -d
```

#### 5. Seed Test Databases (Optional)
```bash
# Download test data
curl https://datafold-public.s3.us-west-2.amazonaws.com/1m.csv -o dev/ratings.csv

# Seed databases with test data
poetry run preql -f dev/prepare_db.pql mysql://mysql:Password1@127.0.0.1:3306/mysql
poetry run preql -f dev/prepare_db.pql postgresql://postgres:Password1@127.0.0.1:5432/postgres

# Cloud databases
poetry run preql -f dev/prepare_db.pql snowflake://uri
poetry run preql -f dev/prepare_db.pql bigquery:///project
```

#### 6. Run Benchmarks (Optional)
```bash
# Run benchmarks and save results
dev/benchmark.sh  # Creates benchmark_<sha>.csv

# Create performance graphs
poetry run python3 dev/graph.py

# Benchmark with specific row count
N_SAMPLES=100000000 dev/benchmark.sh  # 100M rows
```

### Testing Reladiff Locally
```bash
# Test basic diff functionality
poetry run reladiff \
  postgresql://postgres:Password1@localhost/postgres rating \
  postgresql://postgres:Password1@localhost/postgres rating_del1 \
  --verbose

# Test with specific options
poetry run reladiff db1 table1 db2 table2 \
  -k id -c name,email,created_at \
  --limit 1000 --stats --verbose
```

### Implementing New Database Support

#### 1. Create Database Module
Add a new module in `reladiff/databases/` following existing patterns:

```python
# reladiff/databases/newdb.py
from sqeleton.databases.base import Database, BaseDialect, ThreadedDatabase
from .base import ReladiffDialect

class NewDBDialect(BaseDialect, ReladiffDialect):
    name = 'NEWDB'
    # Implementation details...

class NewDB(ThreadedDatabase):
    dialect = NewDBDialect()
    CONNECTS_URI_HELP = "newdb://<user>:<password>@<host>:<port>/<database>"
    # Implementation details...
```

#### 2. Register Database
Add to `reladiff/databases/_connect.py`:

```python
from .newdb import NewDB

DATABASE_BY_SCHEME = {
    # ... existing databases
    "newdb": NewDB,
}
```

#### 3. Add Docker Support (Recommended)
Update `docker-compose.yml` with the new database service for testing.

#### 4. Update CI Pipeline
If adding docker-compose support, update `.github/workflows/ci.yml`.

#### 5. Documentation
Follow the guide: https://reladiff.readthedocs.io/en/latest/new-database-driver-guide.html

### Reporting Issues

#### Bug Reports
Please include:
1. **Exact command used** with `-d` flag for debug output
2. **Complete output** (stdout, logs, exceptions)  
3. **Reproduction steps** with environment setup details
4. **Original values** if results are incorrect (false positive/negative)
5. **Redact sensitive information** like passwords

#### Feature Requests & Database Support
- Check existing [issues](https://github.com/erezsh/reladiff/issues) first
- Use 👍 reactions to vote on existing requests  
- For discussions, use [GitHub Discussions](https://github.com/erezsh/reladiff/discussions)

### Development Environment Variables
```bash
# Set in tests/local_settings.py or environment
TEST_MYSQL_CONN_STRING = "mysql://user:pass@host/db"
TEST_POSTGRESQL_CONN_STRING = "postgresql://user:pass@host/db"  
TEST_MSSQL_CONN_STRING = "mssql://user:pass@host/db"
TEST_BABELFISH_CONN_STRING = "babelfish://user:pass@host/db"
TEST_ORACLE_CONN_STRING = "oracle://user:pass@host/service"
TEST_SNOWFLAKE_CONN_STRING = "snowflake://user:pass@account/db"
TEST_BIGQUERY_CONN_STRING = "bigquery:///project"
```
- Use poetry rather than pip and requirements.txt