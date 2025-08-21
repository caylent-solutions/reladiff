#!/bin/bash

# Reladiff Serverless Deployment Script
# Deploys the complete AWS Lambda architecture for scalable database diffing
# Usage: ./deploy.sh [ENVIRONMENT] [OPTIONS]

set -e

# Default values
ENVIRONMENT="dev"
AWS_REGION=${AWS_REGION:-us-east-1}
STACK_NAME=""  # Will be set after parsing arguments
BUILD_CONTAINERS=${BUILD_CONTAINERS:-false}
RUN_TESTS=${RUN_TESTS:-true}
NOTIFICATION_EMAIL=${NOTIFICATION_EMAIL:-}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check prerequisites
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        log_error "AWS CLI is not installed. Please install it first."
        exit 1
    fi
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        log_error "AWS credentials not configured. Please run 'aws configure'."
        exit 1
    fi
    
    # Check SAM CLI
    if ! command -v sam &> /dev/null; then
        log_error "AWS SAM CLI is not installed. Please install it first."
        log_info "Installation: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/serverless-sam-cli-install.html"
        exit 1
    fi
    
    # Check Docker (if building containers)
    if [ "$BUILD_CONTAINERS" == "true" ]; then
        if ! command -v docker &> /dev/null; then
            log_error "Docker is not installed. Required for container builds."
            exit 1
        fi
        
        if ! docker info &> /dev/null; then
            log_error "Docker daemon is not running."
            exit 1
        fi
    fi
    
    # Check Python and Poetry
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is not installed."
        exit 1
    fi
    
    if ! command -v poetry &> /dev/null; then
        log_warning "Poetry is not installed. Using pip instead."
    fi
    
    log_success "All prerequisites satisfied"
}

# Function to validate environment
validate_environment() {
    log_info "Validating deployment environment: $ENVIRONMENT"
    
    case $ENVIRONMENT in
        dev|staging|prod)
            log_success "Valid environment: $ENVIRONMENT"
            ;;
        *)
            log_error "Invalid environment: $ENVIRONMENT. Must be one of: dev, staging, prod"
            exit 1
            ;;
    esac
    
    # Check if stack already exists
    if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" &> /dev/null; then
        log_warning "Stack $STACK_NAME already exists. This will update the existing stack."
        read -p "Continue with update? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Deployment cancelled."
            exit 0
        fi
    fi
}

# Function to run tests
run_tests() {
    if [ "$RUN_TESTS" != "true" ]; then
        log_info "Skipping tests (RUN_TESTS=false)"
        return
    fi
    
    log_info "Running tests before deployment..."
    
    # Install test dependencies
    if command -v poetry &> /dev/null; then
        poetry install
        poetry run python -m pytest tests/test_lambda_functions.py -v
        poetry run python -m pytest tests/test_lambda_integration.py -v
    else
        pip install -r requirements.txt
        pip install pytest moto boto3
        python -m pytest tests/test_lambda_functions.py -v
        python -m pytest tests/test_lambda_integration.py -v
    fi
    
    log_success "All tests passed"
}

# Function to build Lambda packages
build_lambda_packages() {
    log_info "Building Lambda deployment packages..."
    
    # Create build directory
    mkdir -p build/lambda
    
    # Copy Lambda functions
    cp -r lambda/* build/lambda/
    
    # Copy reladiff source
    cp -r reladiff build/lambda/
    
    # Install dependencies
    cd build/lambda
    
    if [ -f requirements.txt ]; then
        pip install -r requirements.txt -t .
    fi
    
    cd ../..
    
    log_success "Lambda packages built"
}

# Function to build container images
build_container_images() {
    if [ "$BUILD_CONTAINERS" != "true" ]; then
        log_info "Skipping container builds (BUILD_CONTAINERS=false)"
        return
    fi
    
    log_info "Building container images..."
    
    cd lambda
    
    # Get AWS account ID
    AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    
    # Build and push container images
    ./build.sh "$ENVIRONMENT" "$AWS_ACCOUNT_ID" "$AWS_REGION"
    
    cd ..
    
    log_success "Container images built and pushed"
}

# Function to deploy infrastructure
deploy_infrastructure() {
    log_info "Deploying infrastructure with SAM..."
    
    # Build SAM application
    sam build
    
    # Create parameter overrides
    PARAMETERS="Environment=$ENVIRONMENT"
    
    if [ -n "$NOTIFICATION_EMAIL" ]; then
        PARAMETERS="$PARAMETERS NotificationEmail=$NOTIFICATION_EMAIL"
    fi
    
    if [ -n "$VPC_ID" ]; then
        PARAMETERS="$PARAMETERS VpcId=$VPC_ID"
    fi
    
    if [ -n "$SUBNET_IDS" ]; then
        PARAMETERS="$PARAMETERS SubnetIds=$SUBNET_IDS"
    fi
    
    if [ -n "$SECURITY_GROUP_IDS" ]; then
        PARAMETERS="$PARAMETERS SecurityGroupIds=$SECURITY_GROUP_IDS"
    fi
    
    # Deploy with SAM
    sam deploy \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION" \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides $PARAMETERS \
        --no-confirm-changeset \
        --resolve-s3
    
    log_success "Infrastructure deployed successfully"
}

# Function to run post-deployment tasks
post_deployment_tasks() {
    log_info "Running post-deployment tasks..."
    
    # Get stack outputs
    STACK_OUTPUTS=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --region "$AWS_REGION" \
        --query 'Stacks[0].Outputs' \
        --output json)
    
    # Extract important values
    API_ENDPOINT=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ApiEndpoint") | .OutputValue')
    API_KEY_ID=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ApiKeyId") | .OutputValue')
    RESULTS_BUCKET=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="ResultsBucket") | .OutputValue')
    DASHBOARD_URL=$(echo "$STACK_OUTPUTS" | jq -r '.[] | select(.OutputKey=="Dashboard") | .OutputValue')
    
    # Get API key value
    if [ "$API_KEY_ID" != "null" ] && [ -n "$API_KEY_ID" ]; then
        API_KEY=$(aws apigateway get-api-key \
            --api-key "$API_KEY_ID" \
            --include-value \
            --region "$AWS_REGION" \
            --query 'value' \
            --output text)
    fi
    
    # Setup S3 lifecycle policies
    cd lambda
    python3 -c "
from s3_utils import S3LifecycleManager
import os
os.environ['RESULTS_S3_BUCKET'] = '$RESULTS_BUCKET'
lifecycle_manager = S3LifecycleManager('$RESULTS_BUCKET')
lifecycle_manager.setup_lifecycle_policies()
print('S3 lifecycle policies configured')
    "
    cd ..
    
    # Create deployment summary
    cat > "deployment-summary-${ENVIRONMENT}.json" << EOF
{
    "environment": "$ENVIRONMENT",
    "stack_name": "$STACK_NAME",
    "region": "$AWS_REGION",
    "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "api_endpoint": "$API_ENDPOINT",
    "api_key_id": "$API_KEY_ID",
    "results_bucket": "$RESULTS_BUCKET",
    "dashboard_url": "$DASHBOARD_URL"
}
EOF
    
    log_success "Post-deployment tasks completed"
}

# Function to display deployment info
display_deployment_info() {
    log_success "🎉 Deployment completed successfully!"
    echo
    echo "📋 Deployment Summary:"
    echo "  Environment: $ENVIRONMENT"
    echo "  Stack Name: $STACK_NAME"
    echo "  Region: $AWS_REGION"
    echo
    
    if [ -f "deployment-summary-${ENVIRONMENT}.json" ]; then
        API_ENDPOINT=$(jq -r '.api_endpoint' "deployment-summary-${ENVIRONMENT}.json")
        RESULTS_BUCKET=$(jq -r '.results_bucket' "deployment-summary-${ENVIRONMENT}.json")
        DASHBOARD_URL=$(jq -r '.dashboard_url' "deployment-summary-${ENVIRONMENT}.json")
        
        echo "🔗 Important URLs:"
        echo "  API Endpoint: $API_ENDPOINT"
        echo "  CloudWatch Dashboard: $DASHBOARD_URL"
        echo
        echo "📦 Resources:"
        echo "  Results S3 Bucket: $RESULTS_BUCKET"
        echo
        echo "🔑 API Usage:"
        echo "  Get your API key: aws apigateway get-api-key --api-key \$(aws cloudformation describe-stacks --stack-name $STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==\`ApiKeyId\`].OutputValue' --output text) --include-value --query 'value' --output text"
        echo
        echo "📚 Example Usage:"
        echo "  curl -X POST '$API_ENDPOINT/diff' \\"
        echo "    -H 'Content-Type: application/json' \\"
        echo "    -H 'x-api-key: YOUR_API_KEY' \\"
        echo "    -d '{"
        echo "      \"job_id\": \"test-job-$(date +%s)\","
        echo "      \"table1\": {"
        echo "        \"database_uri\": \"postgresql://user:pass@host:5432/db\","
        echo "        \"table_name\": \"table1\""
        echo "      },"
        echo "      \"table2\": {"
        echo "        \"database_uri\": \"postgresql://user:pass@host:5432/db\","
        echo "        \"table_name\": \"table2\""
        echo "      }"
        echo "    }'"
    fi
    echo
    echo "💾 Deployment details saved to: deployment-summary-${ENVIRONMENT}.json"
}

# Function to clean up on failure
cleanup_on_failure() {
    log_error "Deployment failed. Cleaning up..."
    
    # Remove build directory
    rm -rf build
    
    # Optionally rollback stack (uncomment if desired)
    # aws cloudformation cancel-update-stack --stack-name "$STACK_NAME" --region "$AWS_REGION" 2>/dev/null || true
}

# Main deployment function
main() {
    echo "🚀 Reladiff Serverless Deployment"
    echo "=================================="
    echo
    
    # Parse command line arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --build-containers)
                BUILD_CONTAINERS=true
                shift
                ;;
            --skip-tests)
                RUN_TESTS=false
                shift
                ;;
            --email)
                NOTIFICATION_EMAIL="$2"
                shift 2
                ;;
            --region)
                AWS_REGION="$2"
                shift 2
                ;;
            --vpc-id)
                VPC_ID="$2"
                shift 2
                ;;
            --subnet-ids)
                SUBNET_IDS="$2"
                shift 2
                ;;
            --security-group-ids)
                SECURITY_GROUP_IDS="$2"
                shift 2
                ;;
            -h|--help)
                echo "Usage: $0 [ENVIRONMENT] [OPTIONS]"
                echo
                echo "Arguments:"
                echo "  ENVIRONMENT     Deployment environment (dev, staging, prod)"
                echo
                echo "Options:"
                echo "  --build-containers  Build and push Docker container images"
                echo "  --skip-tests       Skip running tests before deployment"
                echo "  --email EMAIL      Email address for notifications"
                echo "  --region REGION    AWS region (default: us-east-1)"
                echo "  --vpc-id VPC_ID    VPC ID for Lambda functions to access RDS databases"
                echo "  --subnet-ids SUBNETS Comma-separated subnet IDs for Lambda functions"
                echo "  --security-group-ids SG_IDS Comma-separated security group IDs"
                echo "  -h, --help         Show this help message"
                echo
                echo "Environment Variables:"
                echo "  AWS_REGION         AWS region (default: us-east-1)"
                echo "  BUILD_CONTAINERS   Build container images (true/false)"
                echo "  RUN_TESTS         Run tests before deployment (true/false)"
                echo "  NOTIFICATION_EMAIL Email for notifications"
                exit 0
                ;;
            *)
                # Check if this looks like an environment name
                case $1 in
                    dev|staging|prod)
                        ENVIRONMENT="$1"
                        ;;
                    *)
                        log_error "Unknown option: $1"
                        exit 1
                        ;;
                esac
                shift
                ;;
        esac
    done
    
    # Set stack name after parsing arguments
    STACK_NAME="reladiff-serverless-${ENVIRONMENT}"
    
    # Setup error handling
    trap cleanup_on_failure ERR
    
    # Run deployment steps
    check_prerequisites
    validate_environment
    run_tests
    build_lambda_packages
    build_container_images
    deploy_infrastructure
    post_deployment_tasks
    display_deployment_info
    
    # Cleanup build directory
    rm -rf build
    
    log_success "🎉 Deployment completed successfully!"
}

# Run main function
main "$@"