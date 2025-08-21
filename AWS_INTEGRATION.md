# AWS Lambda Integration for Reladiff

This document explains how to use Reladiff with AWS Lambda for scalable database diffing.

## Overview

Reladiff now supports submitting diff jobs to AWS Lambda infrastructure instead of running locally. This enables:

- **Horizontal scaling** across hundreds of Lambda workers
- **Cost-effective** pay-per-use model
- **No infrastructure management** - fully serverless
- **Massive table support** - handle billions of rows efficiently

## Prerequisites

1. **Deploy the Lambda infrastructure** (see deployment guide)
2. **Install boto3**: `pip install boto3`
3. **Configure AWS credentials** (AWS CLI, IAM roles, or environment variables)

## Usage

### Command Line

#### Auto-discovery (Recommended)
```bash
# Automatically discover deployed reladiff stack
reladiff postgres://user:pass@host/db table1 table2 --aws auto

# With additional options
reladiff db1 table1 db2 table2 --aws auto -k id -c name,email --limit 1000
```

#### Explicit Queue URL
```bash
# Specify exact SQS queue URL
reladiff db1 table1 db2 table2 --aws "https://sqs.us-east-1.amazonaws.com/account/reladiff-workers-dev.fifo"
```

### TOML Configuration

Create a configuration file with AWS settings:

```toml
# config.toml
[database.prod]
driver = "postgresql://user:${DB_PASSWORD}@prod-host:5432/db"

[database.staging]
driver = "postgresql://user:${DB_PASSWORD}@staging-host:5432/db"

# AWS Lambda configuration
[aws]
auto = true
region = "us-east-1"
# OR specify explicit queue:
# queue_url = "https://sqs.us-east-1.amazonaws.com/account/queue.fifo"

[run.compare_users]
1 = { database = "prod", table = "users" }
2 = { database = "staging", table = "users" }
key_columns = ["id"]
columns = ["email", "created_at"]
```

Then run:
```bash
reladiff --conf config.toml --run compare_users
```

## Configuration Options

### AWS Section in TOML

```toml
[aws]
# Option 1: Auto-discovery (recommended)
auto = true
region = "us-east-1"  # Optional, defaults to AWS_DEFAULT_REGION

# Option 2: Explicit queue URL
queue_url = "https://sqs.us-east-1.amazonaws.com/account/queue.fifo"
```

### Override AWS in Specific Runs

```toml
[run.local_only]
1 = { database = "db1", table = "table1" }
2 = { database = "db2", table = "table2" }
# Disable AWS for this run
aws = false

[run.custom_queue]
1 = { database = "db1", table = "table1" }
2 = { database = "db2", table = "table2" }
# Use different queue
aws = "https://sqs.us-west-2.amazonaws.com/account/custom-queue.fifo"
```

## Job Submission Flow

1. **Job Creation**: Reladiff generates a unique job ID and packages all parameters
2. **Queue Submission**: Job is sent to the configured SQS FIFO queue
3. **Lambda Processing**: Coordinator Lambda picks up the job and creates segments
4. **Parallel Execution**: Worker Lambdas process segments in parallel
5. **Result Aggregation**: Aggregator Lambda combines results and stores in S3
6. **Notifications**: Completion notifications sent via SNS/email

## Monitoring

### CloudWatch Logs
```bash
# Monitor coordinator
aws logs tail /aws/lambda/reladiff-coordinator-dev --follow

# Monitor workers
aws logs tail /aws/lambda/reladiff-worker-dev --follow

# Monitor aggregator
aws logs tail /aws/lambda/reladiff-aggregator-dev --follow
```

### CloudWatch Dashboard
Visit the dashboard URL provided in deployment outputs to monitor:
- Lambda function metrics (invocations, duration, errors)
- SQS queue depth and message processing
- S3 storage usage
- Custom performance metrics

### Job Status
```bash
# Check SQS queue for pending jobs
aws sqs get-queue-attributes \
  --queue-url "YOUR_QUEUE_URL" \
  --attribute-names ApproximateNumberOfMessages

# Check S3 for results
aws s3 ls s3://reladiff-results-dev-ACCOUNT/ --recursive
```

## Examples

### Basic Cross-Database Diff
```bash
reladiff \
  "postgresql://user:pass@prod:5432/db" users \
  "postgresql://user:pass@staging:5432/db" users \
  --aws auto \
  -k user_id \
  -c email,created_at,status
```

### Large Table with Custom Settings
```bash
reladiff \
  "mysql://user:pass@host:3306/db" events \
  "postgresql://user:pass@host:5432/db" events \
  --aws auto \
  -k event_id \
  --bisection-factor 64 \
  --bisection-threshold 50000 \
  --limit 10000
```

### Using Configuration File
```bash
# Create config file
cat > reladiff.toml << EOF
[database.mysql_prod]
driver = "mysql://user:${MYSQL_PASSWORD}@prod-mysql:3306/analytics"

[database.postgres_staging]
driver = "postgresql://user:${PG_PASSWORD}@staging-pg:5432/analytics"

[aws]
auto = true
region = "us-east-1"

[run.daily_sync_check]
1 = { database = "mysql_prod", table = "user_events" }
2 = { database = "postgres_staging", table = "user_events" }
key_columns = ["event_id"]
columns = ["user_id", "event_type", "timestamp", "data"]
stats = true
EOF

# Run the diff
reladiff --conf reladiff.toml --run daily_sync_check
```

## Performance Considerations

### When to Use AWS Lambda
- **Large tables** (>1M rows)
- **Cross-database diffs** requiring network transfers
- **Batch processing** for multiple table comparisons
- **Infrequent usage** where serverless cost model is beneficial

### When to Use Local Processing
- **Small tables** (<100K rows) 
- **Same-database diffs** with fast local connection
- **Interactive exploration** requiring immediate results
- **High-frequency usage** where Lambda cold starts impact performance

### Optimization Tips
1. **Tune bisection settings** for your table sizes
2. **Use appropriate key columns** for efficient segmentation  
3. **Monitor costs** and adjust concurrency limits if needed
4. **Consider VPC configuration** for database access security

## Troubleshooting

### Common Issues

**"No deployed reladiff-serverless stacks found"**
- Ensure you've deployed the Lambda infrastructure
- Check AWS region configuration
- Verify AWS credentials and permissions

**"Failed to submit job to AWS"**
- Check AWS credentials and permissions
- Verify SQS queue URL is correct
- Ensure boto3 is installed

**Job submitted but no results**
- Check CloudWatch logs for errors
- Verify database connectivity from Lambda
- Check IAM permissions for Lambda functions

### Debug Mode
```bash
reladiff db1 table1 db2 table2 --aws auto --debug
```

This will show the exact job payload being submitted and any errors during submission.

## Cost Estimation

Approximate costs for AWS Lambda diffing:

| Table Size | Duration | Lambda Cost | S3 Cost | SQS Cost | Total |
|------------|----------|-------------|---------|----------|-------|
| 1M rows    | 2-5 min  | $1-3        | $0.10   | $0.05    | $1-4  |
| 10M rows   | 10-30 min| $5-15       | $0.50   | $0.25    | $6-16 |
| 100M rows  | 1-3 hours| $15-50      | $2-5    | $1-3     | $18-58|

Costs vary based on:
- Table complexity and diff size
- Database response times
- Number of segments created
- Result data volume

## Security

- Database credentials are passed as connection strings (use environment variables)
- Lambda functions run with minimal IAM permissions
- VPC configuration available for secure database access
- Results stored in encrypted S3 buckets
- All network traffic uses TLS encryption

For production deployments, consider:
- Using AWS Secrets Manager for database credentials
- Configuring VPC endpoints for AWS services
- Setting up CloudTrail for audit logging
- Implementing resource-based policies for additional security