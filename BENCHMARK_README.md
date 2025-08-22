# Lambda Benchmark System

This document describes the Lambda-based benchmarking infrastructure for Reladiff, which enables scalable performance testing across multiple database types using AWS Lambda.

## Overview

The benchmark system consists of:

- **Lambda Functions**: Serverless execution of table population and benchmark tests
- **SAM Template**: Infrastructure as Code for AWS resources
- **Orchestration Script**: Python script for managing benchmark execution
- **GitHub Actions**: Automated benchmarking workflows
- **Test Suite**: Comprehensive testing of benchmark functionality

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Table          │    │  Benchmark      │    │  Results        │
│  Populator      │───▶│  Executor       │───▶│  Storage        │
│  Lambda         │    │  Lambda         │    │  (S3)           │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  CSV Data       │    │  Reladiff       │    │  Performance    │
│  (1M/25M rows)  │    │  Core Engine    │    │  Graphs         │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Quick Start

### 1. Deploy Infrastructure

```bash
# Using SAM CLI
sam build
sam deploy --stack-name reladiff-serverless-dev --resolve-s3 --capabilities CAPABILITY_IAM

# Or using GitHub Actions
gh workflow run lambda-infrastructure.yml -f action=deploy
```

### 2. Run Benchmarks

```bash
# Run 1M dataset benchmark
python benchmark_lambda.py --dataset-size 1m

# Run specific tests only
python benchmark_lambda.py --dataset-size 1m --test-filter postgresql_int_mysql_int mysql_int_mysql_int

# Run with graph generation
python benchmark_lambda.py --dataset-size 1m --generate-graphs
```

### 3. Using the Benchmark Runner

```bash
# List available scenarios
python scripts/benchmark-runner.py list

# Run a predefined scenario
python scripts/benchmark-runner.py run smoke_test

# Trigger GitHub Actions workflow
python scripts/benchmark-runner.py trigger full_test
```

## Benchmark Tests

The system runs the following benchmark tests, matching `dev/benchmark.sh`:

| Test Name | Source DB | Target DB | Table 1 | Table 2 | Key Columns |
|-----------|-----------|-----------|---------|---------|-------------|
| `postgresql_int_mysql_int` | PostgreSQL | MySQL | rating | rating | id |
| `mysql_int_mysql_int` | MySQL | MySQL | rating | rating_del1 | id |
| `postgresql_int_postgresql_int` | PostgreSQL | PostgreSQL | rating | rating_update1 | id |
| `postgresql_ts6_n_tz_mysql_ts0` | PostgreSQL | MySQL | rating | rating | timestamp |
| `mssql_int_postgresql_int` | MSSQL | PostgreSQL | rating | rating_update001p | id |
| `babelfish_int_mysql_int` | Babelfish | MySQL | rating | rating_update1p | id |

## Configuration

### Database Configuration

The benchmark system reads database configurations from `polyglot-config.toml`:

```toml
[database.postgresql]
driver = "postgresql://secrets:postgres@host:5432/database"

[database.mysql]
driver = "mysql://secrets:mysql@host:3306/database"

[database.mssql]
driver = "mssql://secrets:mssql@host:1433/database"

[database.babelfish]
driver = "babelfish://secrets:babelfish@host:1434/database"
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ENVIRONMENT` | Deployment environment | `dev` |
| `RESULTS_S3_BUCKET` | S3 bucket for results | Auto-generated |
| `N_SAMPLES` | Number of samples for benchmarking | `1000000` |
| `N_THREADS` | Thread pool size | `16` |
| `LOG_LEVEL` | Logging level | `INFO` |

## GitHub Actions Workflows

### Lambda Benchmarks (`lambda-benchmarks.yml`)

Automated benchmark execution with the following triggers:

- **Manual**: `workflow_dispatch` with configurable parameters
- **Scheduled**: Weekly on Sundays at 2 AM UTC
- **Release**: On published releases
- **Push**: Main branch changes affecting core logic
- **Pull Request**: PR changes affecting core logic

Features:
- Parallel execution of 1M and 25M datasets
- Performance regression detection
- Automatic graph generation
- PR comments with results
- Artifact upload to GitHub and S3

### Infrastructure Management (`lambda-infrastructure.yml`)

Infrastructure deployment and management:

- **Deploy**: Create new infrastructure
- **Update**: Update existing infrastructure
- **Destroy**: Clean up resources
- **Status**: Check infrastructure status

## Performance Metrics

The benchmark system collects comprehensive performance metrics:

### Timing Metrics
- **Duration**: Total test execution time
- **Rows/second**: Throughput measurement
- **Operations/second**: Diff operations throughput

### Memory Metrics
- **Peak Memory**: Maximum memory usage during test
- **Memory Efficiency**: Memory per row processed

### Operational Metrics
- **Diff Operations**: Total number of differences found
- **Success Rate**: Percentage of successful tests
- **Error Rate**: Percentage of failed tests

## Output Formats

### CSV Output

The benchmark system generates CSV files compatible with `dev/benchmark.sh`:

```csv
test_name,status,duration_seconds,database_types,table1_rows,table2_rows,diff_operations,peak_memory_mb,rows_per_second,operations_per_second,memory_efficiency_mb_per_row,threads_used,start_time,end_time
postgresql_int_mysql_int,SUCCESS,2.34,postgresql_to_mysql,1000000,1000000,0,256.5,854700.85,0.0,0.0002565,16,2025-08-21T10:00:00,2025-08-21T10:00:02.34
```

### JSON Output

Detailed JSON results with nested performance metrics:

```json
{
  "status": "COMPLETED",
  "total_tests": 6,
  "successful_tests": 6,
  "failed_tests": 0,
  "results": [
    {
      "test_name": "postgresql_int_mysql_int",
      "status": "SUCCESS",
      "duration_seconds": 2.34,
      "performance_metrics": {
        "rows_per_second": 854700.85,
        "operations_per_second": 0.0,
        "memory_efficiency_mb_per_row": 0.0002565
      }
    }
  ]
}
```

## Performance Regression Detection

The system automatically detects performance regressions by comparing current results with baseline results:

- **Duration Regression**: >20% increase in execution time
- **Memory Regression**: >30% increase in memory usage
- **Throughput Regression**: >15% decrease in rows/second

Regression detection runs automatically on pull requests and reports findings as PR comments.

## Cost Management

### Lambda Costs
- **Table Populator**: ~$0.01 per 1M dataset population
- **Benchmark Executor**: ~$0.05 per full benchmark suite
- **Monthly Estimate**: ~$5-10 for regular development usage

### S3 Storage Costs
- **Results Storage**: ~$0.01 per GB per month
- **Lifecycle Policies**: Automatic transition to cheaper storage classes

### Cost Monitoring
- GitHub Actions workflow tracks monthly costs
- Alerts when costs exceed $50/month threshold
- Automatic cleanup of old results

## Troubleshooting

### Common Issues

1. **Lambda Function Not Found**
   ```bash
   # Redeploy infrastructure
   sam deploy --stack-name reladiff-serverless-dev --resolve-s3 --capabilities CAPABILITY_IAM
   ```

2. **Database Connection Failures**
   ```bash
   # Test connectivity
   aws lambda invoke --function-name reladiff-test-connectivity-dev test-output.json
   ```

3. **S3 Permission Errors**
   ```bash
   # Check IAM roles and policies in CloudFormation template
   aws iam get-role --role-name reladiff-table-populator-dev-role
   ```

4. **Memory/Timeout Issues**
   ```bash
   # Increase Lambda memory/timeout in template.yaml
   MemorySize: 3008
   Timeout: 900
   ```

### Debug Mode

Enable debug logging:

```bash
# Set environment variable
export LOG_LEVEL=DEBUG

# Run with debug output
python benchmark_lambda.py --dataset-size 1m --stack-name reladiff-serverless-dev
```

### CloudWatch Logs

Monitor Lambda execution:

```bash
# View logs
aws logs tail /aws/lambda/reladiff-table-populator-dev --follow

# Filter errors
aws logs filter-log-events --log-group-name /aws/lambda/reladiff-benchmark-executor-dev --filter-pattern "ERROR"
```

## Development

### Local Testing

```bash
# Install dependencies
poetry install

# Run unit tests
poetry run python -m pytest test_benchmark_lambdas.py -v

# Test Lambda functions locally
sam local invoke TablePopulatorFunction -e events/table-population-event.json
```

### Adding New Tests

1. Update `BENCHMARK_TESTS` in `lambda/benchmark_executor.py`
2. Add test validation in `test_benchmark_lambdas.py`
3. Update documentation

### Extending Functionality

1. **New Database Types**: Add to database configurations and test matrix
2. **Additional Metrics**: Extend performance monitoring in `BenchmarkExecutor`
3. **Custom Scenarios**: Add to `.github/benchmark-config.yml`

## Security Considerations

- **Secrets Management**: Database credentials stored in AWS Secrets Manager
- **IAM Permissions**: Least-privilege access for Lambda functions
- **VPC Configuration**: Optional network isolation for database access
- **Encryption**: S3 results encrypted at rest

## Monitoring and Alerting

- **CloudWatch Dashboards**: Real-time monitoring of Lambda metrics
- **Performance Alerts**: Automatic alerts for regressions
- **Cost Alerts**: Budget monitoring and notifications
- **GitHub Notifications**: PR comments and issue creation

## Contributing

1. **Test Changes**: Run full benchmark suite before submitting PRs
2. **Update Documentation**: Keep README and comments current
3. **Performance Impact**: Monitor regression detection results
4. **Cost Impact**: Consider Lambda execution costs for new features

For more details, see [CONTRIBUTING.md](CONTRIBUTING.md) and [CLAUDE.md](CLAUDE.md).