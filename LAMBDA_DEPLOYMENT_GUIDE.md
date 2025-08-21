# Reladiff Lambda Deployment Guide

Complete guide for deploying and managing the Reladiff serverless architecture on AWS Lambda.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Quick Start](#quick-start)
4. [Detailed Deployment](#detailed-deployment)
5. [Configuration](#configuration)
6. [Usage Examples](#usage-examples)
7. [Monitoring & Troubleshooting](#monitoring--troubleshooting)
8. [Cost Optimization](#cost-optimization)
9. [Security](#security)
10. [FAQ](#faq)

## Architecture Overview

The Reladiff Lambda architecture provides horizontally scalable database diffing using AWS serverless services:

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

### Key Components

- **Coordinator Lambda**: Analyzes tables, creates segments, orchestrates jobs
- **Worker Lambdas**: Process individual table segments in parallel
- **Aggregator Lambda**: Combines results from all workers
- **SQS Queues**: Manage message flow between components
- **S3**: Stores results, metadata, and intermediate data
- **EventBridge**: Publishes job status events
- **CloudWatch**: Monitoring, logging, and alerting

## Prerequisites

### Required Tools

- **AWS CLI v2** - Configure with appropriate credentials
- **AWS SAM CLI** - For serverless application deployment
- **Docker** - For container image builds (optional)
- **Python 3.9+** - Runtime environment
- **Poetry** - Dependency management (recommended) or pip

### AWS Permissions

Your AWS user/role needs the following permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "cloudformation:*",
                "lambda:*",
                "s3:*",
                "sqs:*",
                "iam:*",
                "apigateway:*",
                "events:*",
                "sns:*",
                "logs:*",
                "ecr:*"
            ],
            "Resource": "*"
        }
    ]
}
```

### Installation Steps

1. **Install AWS CLI**:
   ```bash
   curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
   unzip awscliv2.zip
   sudo ./aws/install
   ```

2. **Install SAM CLI**:
   ```bash
   # macOS
   brew install aws-sam-cli
   
   # Linux
   pip install aws-sam-cli
   ```

3. **Configure AWS credentials**:
   ```bash
   aws configure
   ```

## Quick Start

### 1. Deploy with Default Settings

```bash
# Clone the repository
git clone <repository-url>
cd reladiff

# Make deployment script executable
chmod +x deploy.sh

# Deploy to development environment
./deploy.sh dev
```

### 2. Get API Credentials

```bash
# Get API endpoint
aws cloudformation describe-stacks \
  --stack-name reladiff-serverless-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' \
  --output text

# Get API key
API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name reladiff-serverless-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiKeyId`].OutputValue' \
  --output text)

aws apigateway get-api-key \
  --api-key $API_KEY_ID \
  --include-value \
  --query 'value' \
  --output text
```

### 3. Test the API

```bash
curl -X POST 'https://YOUR_API_ENDPOINT/diff' \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: YOUR_API_KEY' \
  -d '{
    "job_id": "test-job-001",
    "table1": {
      "database_uri": "postgresql://user:pass@host:5432/db",
      "table_name": "users_old"
    },
    "table2": {
      "database_uri": "postgresql://user:pass@host:5432/db", 
      "table_name": "users_new"
    }
  }'
```

## Detailed Deployment

### Environment-Specific Deployments

#### Development Environment

```bash
./deploy.sh dev --email your-email@example.com
```

Features:
- Lower resource limits
- Debug logging enabled
- 30-day data retention
- Basic monitoring

#### Staging Environment

```bash
./deploy.sh staging --email your-email@example.com
```

Features:
- Production-like configuration
- Enhanced monitoring
- 90-day data retention
- Performance testing enabled

#### Production Environment

```bash
./deploy.sh prod --email alerts@yourcompany.com
```

Features:
- Maximum performance settings
- Enhanced security
- Long-term data retention
- Full monitoring and alerting

### Container-Based Deployment

For better dependency management and faster cold starts:

```bash
./deploy.sh dev --build-containers
```

This will:
1. Build Docker images for each Lambda function
2. Push to Amazon ECR
3. Deploy using container images instead of ZIP packages

### Manual SAM Deployment

For more control over the deployment process:

```bash
# Build the application
sam build

# Deploy with guided prompts
sam deploy --guided

# Or deploy with specific parameters
sam deploy \
  --stack-name reladiff-serverless-dev \
  --parameter-overrides \
    Environment=dev \
    NotificationEmail=your-email@example.com \
    WorkerMemorySize=2048 \
  --capabilities CAPABILITY_IAM \
  --resolve-s3
```

## Configuration

### Environment Variables

Set these in your deployment environment or SAM parameters:

| Variable | Description | Default | Environment |
|----------|-------------|---------|-------------|
| `RESULTS_S3_BUCKET` | S3 bucket for results | Auto-created | All |
| `BISECTION_THRESHOLD` | Min rows before bisection stops | 16000 | All |
| `BISECTION_FACTOR` | Segments per bisection | 32 | All |
| `WORKER_QUEUE_URL` | SQS queue for worker jobs | Auto-created | All |
| `AGGREGATOR_QUEUE_URL` | SQS queue for aggregation | Auto-created | All |
| `LOG_LEVEL` | Logging verbosity | INFO | All |
| `MAX_MEMORY_MB` | Lambda memory limit | 2048 | All |

### SAM Parameters

Customize deployment via SAM parameters:

```yaml
# samconfig.toml
[production.deploy.parameters]
parameter_overrides = [
    "Environment=prod",
    "WorkerMemorySize=3008",
    "WorkerTimeout=900",
    "BisectionThreshold=32000",
    "BisectionFactor=64",
    "NotificationEmail=alerts@yourcompany.com"
]
```

### Database Connection Configuration

#### Using Environment Variables

```bash
export DATABASE1_URI="postgresql://user:pass@host:5432/db1"
export DATABASE2_URI="mysql://user:pass@host:3306/db2"
```

#### Using AWS Secrets Manager

```json
{
  "username": "dbuser",
  "password": "securepassword",
  "host": "db.example.com",
  "port": "5432",
  "database": "production_db"
}
```

Reference in API calls:
```json
{
  "table1": {
    "secret_name": "prod/database1/credentials",
    "database_type": "postgresql",
    "table_name": "users"
  }
}
```

## Usage Examples

### Basic Table Diff

Compare two tables in the same database:

```bash
curl -X POST 'https://api.example.com/diff' \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: your-api-key' \
  -d '{
    "job_id": "basic-diff-001",
    "table1": {
      "database_uri": "postgresql://user:pass@host:5432/db",
      "table_name": "products_v1",
      "key_columns": ["product_id"]
    },
    "table2": {
      "database_uri": "postgresql://user:pass@host:5432/db",
      "table_name": "products_v2",
      "key_columns": ["product_id"]
    },
    "options": {
      "extra_columns": ["name", "price", "description"],
      "where": "category = 'electronics'"
    }
  }'
```

### Cross-Database Diff

Compare tables across different databases:

```bash
curl -X POST 'https://api.example.com/diff' \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: your-api-key' \
  -d '{
    "job_id": "cross-db-diff-001",
    "table1": {
      "database_uri": "postgresql://user:pass@pg-host:5432/source_db",
      "table_name": "customer_data",
      "key_columns": ["customer_id"]
    },
    "table2": {
      "database_uri": "mysql://user:pass@mysql-host:3306/target_db",
      "table_name": "migrated_customers", 
      "key_columns": ["customer_id"]
    },
    "options": {
      "extra_columns": ["email", "registration_date", "status"]
    }
  }'
```

### Large Table Diff with Custom Settings

For very large tables, tune the bisection parameters:

```bash
curl -X POST 'https://api.example.com/diff' \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: your-api-key' \
  -d '{
    "job_id": "large-table-diff-001",
    "table1": {
      "database_uri": "postgresql://user:pass@host:5432/analytics",
      "table_name": "events_2023",
      "key_columns": ["event_id"]
    },
    "table2": {
      "database_uri": "postgresql://user:pass@host:5432/analytics",
      "table_name": "events_2024",
      "key_columns": ["event_id"]
    },
    "options": {
      "bisection_threshold": 50000,
      "bisection_factor": 64,
      "extra_columns": ["timestamp", "user_id", "event_type", "data"]
    }
  }'
```

### Checking Job Status

Monitor job progress via S3 or CloudWatch:

```bash
# Get job metadata
aws s3 cp s3://your-results-bucket/your-job-id/metadata.json -

# List segment results
aws s3 ls s3://your-results-bucket/your-job-id/segments/

# Download final results
aws s3 cp s3://your-results-bucket/your-job-id/final_results.jsonl.gz -
```

## Monitoring & Troubleshooting

### CloudWatch Dashboard

Access the auto-created dashboard:
```bash
# Get dashboard URL from stack outputs
aws cloudformation describe-stacks \
  --stack-name reladiff-serverless-dev \
  --query 'Stacks[0].Outputs[?OutputKey==`Dashboard`].OutputValue' \
  --output text
```

### Key Metrics to Monitor

- **Lambda Invocations**: Number of coordinator, worker, and aggregator invocations
- **Lambda Duration**: Processing time per function
- **Lambda Errors**: Error rates and failure reasons
- **SQS Queue Depth**: Number of pending jobs
- **S3 Storage**: Results storage usage and costs

### Common Issues and Solutions

#### High Memory Usage in Workers

**Symptoms**: Lambda timeout errors, memory exceeded errors

**Solutions**:
1. Increase worker memory size:
   ```bash
   sam deploy --parameter-overrides WorkerMemorySize=3008
   ```

2. Reduce bisection threshold:
   ```bash
   sam deploy --parameter-overrides BisectionThreshold=8000
   ```

#### Slow Processing Times

**Symptoms**: Long job completion times

**Solutions**:
1. Increase bisection factor for more parallelism:
   ```bash
   sam deploy --parameter-overrides BisectionFactor=64
   ```

2. Optimize database queries with appropriate indexes

3. Use faster storage classes (SSD vs HDD)

#### Database Connection Timeouts

**Symptoms**: Connection refused errors in worker logs

**Solutions**:
1. Increase database connection limits
2. Use connection pooling
3. Configure VPC if Lambda needs private database access

#### Queue Message Buildup

**Symptoms**: Messages accumulating in SQS queues

**Solutions**:
1. Increase Lambda concurrency limits
2. Scale worker Lambda reserved concurrency
3. Check for failing workers and fix issues

### Debugging Lambda Functions

#### Enable Debug Logging

```bash
sam deploy --parameter-overrides LogLevel=DEBUG
```

#### View Real-Time Logs

```bash
# Coordinator logs
sam logs --stack-name reladiff-serverless-dev --name CoordinatorFunction --tail

# Worker logs  
sam logs --stack-name reladiff-serverless-dev --name WorkerFunction --tail

# Aggregator logs
sam logs --stack-name reladiff-serverless-dev --name AggregatorFunction --tail
```

#### Local Testing

```bash
# Start API locally
sam local start-api

# Test coordinator function locally
echo '{"job_id": "test"}' | sam local invoke CoordinatorFunction

# Test with debugger
sam local invoke --debug CoordinatorFunction
```

## Cost Optimization

### Understand Pricing Components

1. **Lambda Execution**: Based on requests and execution time
2. **S3 Storage**: Storage costs for results and metadata
3. **SQS Messages**: Message charges for coordination
4. **Data Transfer**: Outbound data transfer costs
5. **CloudWatch**: Log storage and custom metrics

### Optimization Strategies

#### Right-Size Lambda Functions

```bash
# Monitor memory usage and adjust
aws logs filter-log-events \
  --log-group-name /aws/lambda/reladiff-worker-dev \
  --filter-pattern '[timestamp, requestId, billedDuration="REPORT", ...]' \
  --start-time $(date -d '1 day ago' +%s)000
```

#### Optimize S3 Storage

1. **Enable S3 Lifecycle Policies** (auto-configured):
   - Move to IA after 30 days
   - Move to Glacier after 90 days
   - Delete after 1 year

2. **Use S3 Intelligent Tiering**:
   ```bash
   aws s3api put-bucket-intelligent-tiering-configuration \
     --bucket your-results-bucket \
     --id ReladiffIntelligentTiering \
     --intelligent-tiering-configuration '{
       "Id": "ReladiffIntelligentTiering",
       "Status": "Enabled",
       "Filter": {"Prefix": ""},
       "Tierings": [
         {"Days": 1, "AccessTier": "ARCHIVE_ACCESS"},
         {"Days": 90, "AccessTier": "DEEP_ARCHIVE_ACCESS"}
       ]
     }'
   ```

#### Optimize Lambda Performance

1. **Minimize Cold Starts**:
   - Use provisioned concurrency for critical functions
   - Optimize package size
   - Use container images for complex dependencies

2. **Batch Processing**:
   - Increase SQS batch size
   - Process multiple segments per worker invocation

### Cost Monitoring

Set up billing alerts:

```bash
aws budgets create-budget \
  --account-id YOUR_ACCOUNT_ID \
  --budget '{
    "BudgetName": "Reladiff-Monthly-Budget",
    "BudgetLimit": {"Amount": "100", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }'
```

## Security

### IAM Roles and Policies

The deployment creates least-privilege IAM roles:

- **CoordinatorRole**: S3 read/write, SQS send, EventBridge publish
- **WorkerRole**: S3 read/write, SQS send
- **AggregatorRole**: S3 read/write, SNS publish, EventBridge publish

### Network Security

#### VPC Configuration

For private database access:

```yaml
# In template.yaml
VpcConfig:
  SecurityGroupIds:
    - !Ref DatabaseSecurityGroup
  SubnetIds:
    - !Ref PrivateSubnet1
    - !Ref PrivateSubnet2
```

#### Database Security

1. **Use SSL/TLS connections**:
   ```
   postgresql://user:pass@host:5432/db?sslmode=require
   ```

2. **Rotate credentials regularly**
3. **Use AWS Secrets Manager** for credential storage
4. **Limit database user permissions** to read-only when possible

### Data Protection

#### Encryption in Transit

- API Gateway uses TLS 1.2+
- Lambda functions use HTTPS for all AWS service calls
- Database connections should use SSL/TLS

#### Encryption at Rest

- S3 buckets use AES-256 encryption (SSE-S3)
- Lambda environment variables are encrypted with KMS
- SQS messages are encrypted in transit and at rest

#### Data Retention

Configure automatic cleanup:

```bash
# Set up S3 lifecycle policy for automatic deletion
aws s3api put-bucket-lifecycle-configuration \
  --bucket your-results-bucket \
  --lifecycle-configuration file://lifecycle-policy.json
```

### Compliance Considerations

- **GDPR**: Implement data subject deletion procedures
- **SOX**: Maintain audit logs of all diff operations
- **HIPAA**: Use appropriate encryption and access controls
- **PCI DSS**: Ensure no payment card data is processed

## FAQ

### General Questions

**Q: How much data can Reladiff Lambda handle?**
A: There's no hard limit. The architecture scales horizontally. We've tested with tables containing billions of rows.

**Q: What databases are supported?**
A: PostgreSQL, MySQL, SQL Server, Babelfish, Oracle, Snowflake, BigQuery, DuckDB, ClickHouse, Trino, Presto, Vertica, and others supported by the base Reladiff library.

**Q: Can I diff tables with different schemas?**
A: Yes, but only common columns will be compared. Specify the columns explicitly in the `extra_columns` parameter.

### Performance Questions

**Q: How fast is the Lambda version compared to local Reladiff?**
A: For large tables (>1M rows), Lambda is typically faster due to parallelization. For small tables, local execution may be faster due to Lambda cold start overhead.

**Q: How do I optimize for the fastest possible diff?**
A: 
1. Increase `bisection_factor` to 128 or higher
2. Use maximum Lambda memory (3008 MB)
3. Ensure your databases can handle the concurrent connections
4. Use indexes on key columns

**Q: What's the maximum parallelism I can achieve?**
A: By default, Lambda supports 1000 concurrent executions per region. You can request higher limits from AWS support.

### Cost Questions

**Q: How much does it cost to diff large tables?**
A: Costs vary by table size and configuration. For a 10M row table comparison:
- Lambda execution: ~$5-15
- S3 storage: ~$0.50-2.00
- SQS messages: ~$0.10-0.50
- Total: ~$6-18 per diff

**Q: How can I reduce costs?**
A: 
1. Use appropriate Lambda memory sizes (don't over-provision)
2. Clean up old results regularly
3. Use S3 lifecycle policies
4. Optimize bisection settings to reduce overhead

### Troubleshooting Questions

**Q: My job seems stuck. How do I debug?**
A: 
1. Check CloudWatch logs for errors
2. Verify SQS queue depths
3. Check Lambda metrics for failures
4. Look at S3 for partial results

**Q: I'm getting database connection errors. What should I check?**
A: 
1. Verify connection strings are correct
2. Check database connection limits
3. Ensure Lambda has network access to databases
4. Verify credentials and permissions

**Q: How do I handle very wide tables (many columns)?**
A: 
1. Specify only necessary columns in `extra_columns`
2. Increase Lambda memory and timeout
3. Consider splitting the diff into multiple jobs

### Deployment Questions

**Q: Can I deploy to multiple AWS regions?**
A: Yes, deploy the stack to each region separately. Update the `AWS_REGION` environment variable.

**Q: How do I roll back a deployment?**
A: Use CloudFormation rollback:
```bash
aws cloudformation cancel-update-stack --stack-name reladiff-serverless-dev
```

**Q: Can I customize the Lambda runtime or dependencies?**
A: Yes, modify the `lambda/requirements.txt` file or use container images for full control.

---

## Support

For additional support:

1. **GitHub Issues**: Report bugs and feature requests
2. **Documentation**: Check the main Reladiff documentation
3. **Community**: Join discussions in GitHub Discussions
4. **Commercial Support**: Contact for enterprise support options

## Contributing

Contributions are welcome! Please see the contributing guidelines in the main repository.

## License

This project is licensed under the MIT License. See the LICENSE file for details.