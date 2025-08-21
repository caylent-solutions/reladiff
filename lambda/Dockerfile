# Multi-stage Docker build for AWS Lambda container images
FROM public.ecr.aws/lambda/python:3.9 as base

# Install system dependencies for database drivers
RUN yum update -y && \
    yum install -y \
    gcc \
    gcc-c++ \
    make \
    unixODBC-devel \
    freetds-devel \
    postgresql-devel \
    mysql-devel \
    openssl-devel \
    libffi-devel \
    curl \
    && yum clean all && rm -rf /var/cache/yum

# Set working directory
WORKDIR ${LAMBDA_TASK_ROOT}

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy reladiff source code
COPY ../reladiff ./reladiff/

# Copy Lambda function code
COPY coordinator.py worker.py aggregator.py config.py ./

# Build stage for coordinator function
FROM base as coordinator
CMD ["coordinator.lambda_handler"]

# Build stage for worker function  
FROM base as worker
CMD ["worker.lambda_handler"]

# Build stage for aggregator function
FROM base as aggregator
CMD ["aggregator.lambda_handler"]