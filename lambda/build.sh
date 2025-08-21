#!/bin/bash

# Build script for Reladiff Lambda container images
# Usage: ./build.sh [ENVIRONMENT] [AWS_ACCOUNT_ID] [AWS_REGION]

set -e

ENVIRONMENT=${1:-dev}
AWS_ACCOUNT_ID=${2:-$(aws sts get-caller-identity --query Account --output text)}
AWS_REGION=${3:-$(aws configure get region)}

if [ -z "$AWS_ACCOUNT_ID" ] || [ -z "$AWS_REGION" ]; then
    echo "Error: AWS account ID and region are required"
    echo "Usage: ./build.sh [ENVIRONMENT] [AWS_ACCOUNT_ID] [AWS_REGION]"
    exit 1
fi

ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE_TAG="${ENVIRONMENT}-$(date +%Y%m%d%H%M%S)"

# Function names
FUNCTIONS=("coordinator" "worker" "aggregator")

echo "Building Reladiff Lambda container images..."
echo "Environment: $ENVIRONMENT"
echo "Registry: $ECR_REGISTRY"
echo "Tag: $IMAGE_TAG"

# Authenticate Docker to ECR
echo "Authenticating to ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY

# Create ECR repositories if they don't exist
for func in "${FUNCTIONS[@]}"; do
    repo_name="reladiff-${func}-${ENVIRONMENT}"
    
    echo "Ensuring ECR repository exists: $repo_name"
    aws ecr describe-repositories --repository-names $repo_name --region $AWS_REGION 2>/dev/null || \
    aws ecr create-repository \
        --repository-name $repo_name \
        --region $AWS_REGION \
        --image-scanning-configuration scanOnPush=true \
        --lifecycle-policy-text '{
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Keep last 10 images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": 10
                    },
                    "action": {
                        "type": "expire"
                    }
                }
            ]
        }'
done

# Build and push images for each function
for func in "${FUNCTIONS[@]}"; do
    repo_name="reladiff-${func}-${ENVIRONMENT}"
    image_uri="${ECR_REGISTRY}/${repo_name}:${IMAGE_TAG}"
    latest_uri="${ECR_REGISTRY}/${repo_name}:latest"
    
    echo "Building image for $func function..."
    
    # Build the image targeting specific function stage
    docker build \
        --target $func \
        --platform linux/amd64 \
        --tag $image_uri \
        --tag $latest_uri \
        .
    
    echo "Pushing image for $func function..."
    docker push $image_uri
    docker push $latest_uri
    
    echo "✓ Successfully built and pushed $func: $image_uri"
done

# Generate SAM template with container image URIs
echo "Generating deployment configuration..."

cat > container-template.yaml << EOF
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Description: 'Reladiff Serverless - Container Image Deployment'

Parameters:
  Environment:
    Type: String
    Default: $ENVIRONMENT
  ImageTag:
    Type: String
    Default: $IMAGE_TAG

Resources:
  CoordinatorFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub 'reladiff-coordinator-\${Environment}'
      PackageType: Image
      ImageUri: ${ECR_REGISTRY}/reladiff-coordinator-${ENVIRONMENT}:\${ImageTag}
      MemorySize: 1024
      Timeout: 300
      Environment:
        Variables:
          WORKER_QUEUE_URL: !Ref WorkerQueue
          AGGREGATOR_QUEUE_URL: !Ref AggregatorQueue
          RESULTS_S3_BUCKET: !Ref ResultsBucket

  WorkerFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub 'reladiff-worker-\${Environment}'
      PackageType: Image
      ImageUri: ${ECR_REGISTRY}/reladiff-worker-${ENVIRONMENT}:\${ImageTag}
      MemorySize: 2048
      Timeout: 900
      ReservedConcurrencyLimit: 100
      Environment:
        Variables:
          RESULTS_S3_BUCKET: !Ref ResultsBucket
          AGGREGATOR_QUEUE_URL: !Ref AggregatorQueue

  AggregatorFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: !Sub 'reladiff-aggregator-\${Environment}'
      PackageType: Image
      ImageUri: ${ECR_REGISTRY}/reladiff-aggregator-${ENVIRONMENT}:\${ImageTag}
      MemorySize: 1024
      Timeout: 300
      Environment:
        Variables:
          RESULTS_S3_BUCKET: !Ref ResultsBucket
          COMPLETION_TOPIC_ARN: !Ref CompletionTopic
          EVENT_BUS_NAME: !Ref ReladiffEventBus

  # Include other resources from main template...
  # (This would normally import or reference the main template)

EOF

echo "✓ Container images built and pushed successfully!"
echo "✓ Generated container-template.yaml"
echo ""
echo "Image URIs:"
for func in "${FUNCTIONS[@]}"; do
    repo_name="reladiff-${func}-${ENVIRONMENT}"
    echo "  ${func}: ${ECR_REGISTRY}/${repo_name}:${IMAGE_TAG}"
done
echo ""
echo "Next steps:"
echo "1. Update your SAM template to use container images"
echo "2. Deploy with: sam deploy --template-file container-template.yaml"
echo "3. Or update function code with: aws lambda update-function-code --function-name reladiff-coordinator-${ENVIRONMENT} --image-uri ${ECR_REGISTRY}/reladiff-coordinator-${ENVIRONMENT}:${IMAGE_TAG}"