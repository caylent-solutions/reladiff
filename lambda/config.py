"""
Environment Configuration for Reladiff Lambda Functions

Centralized configuration management using environment variables
with sensible defaults and validation.
"""

import os
import logging
from typing import Optional, Union, Dict, Any
from dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class LambdaConfig:
    """Configuration class for Lambda environment variables"""
    
    # AWS Resources
    results_bucket: str
    worker_queue_url: Optional[str] = None
    aggregator_queue_url: Optional[str] = None
    completion_topic_arn: Optional[str] = None
    event_bus_name: str = 'default'
    
    # Reladiff Configuration
    bisection_threshold: int = 16000
    bisection_factor: int = 32
    max_memory_mb: int = 2048
    
    # Logging
    log_level: str = 'INFO'
    
    # Performance Tuning
    max_threadpool_size: int = 1
    enable_compression: bool = True
    result_batch_size: int = 10000
    
    @classmethod
    def from_environment(cls) -> 'LambdaConfig':
        """Create configuration from environment variables"""
        
        # Required environment variables
        results_bucket = os.environ['RESULTS_S3_BUCKET']
        
        # Optional environment variables with defaults
        config = cls(
            results_bucket=results_bucket,
            worker_queue_url=os.environ.get('WORKER_QUEUE_URL'),
            aggregator_queue_url=os.environ.get('AGGREGATOR_QUEUE_URL'),
            completion_topic_arn=os.environ.get('COMPLETION_TOPIC_ARN'),
            event_bus_name=os.environ.get('EVENT_BUS_NAME', 'default'),
            bisection_threshold=int(os.environ.get('BISECTION_THRESHOLD', 16000)),
            bisection_factor=int(os.environ.get('BISECTION_FACTOR', 32)),
            max_memory_mb=int(os.environ.get('MAX_MEMORY_MB', 2048)),
            log_level=os.environ.get('LOG_LEVEL', 'INFO'),
            max_threadpool_size=int(os.environ.get('MAX_THREADPOOL_SIZE', 1)),
            enable_compression=os.environ.get('ENABLE_COMPRESSION', 'true').lower() == 'true',
            result_batch_size=int(os.environ.get('RESULT_BATCH_SIZE', 10000))
        )
        
        config.validate()
        return config
    
    def validate(self) -> None:
        """Validate configuration values"""
        
        if not self.results_bucket:
            raise ValueError("RESULTS_S3_BUCKET environment variable is required")
        
        if self.bisection_threshold <= 0:
            raise ValueError("BISECTION_THRESHOLD must be positive")
        
        if self.bisection_factor <= 1:
            raise ValueError("BISECTION_FACTOR must be greater than 1")
        
        if self.max_memory_mb < 128:
            raise ValueError("MAX_MEMORY_MB must be at least 128")
        
        if self.log_level not in ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
            raise ValueError(f"Invalid LOG_LEVEL: {self.log_level}")
    
    def setup_logging(self) -> None:
        """Configure logging based on environment"""
        
        log_format = '[%(asctime)s] %(levelname)s - %(name)s - %(message)s'
        
        logging.basicConfig(
            level=getattr(logging, self.log_level),
            format=log_format,
            force=True  # Override any existing configuration
        )
        
        # Set boto3 logging to WARNING to reduce noise
        logging.getLogger('boto3').setLevel(logging.WARNING)
        logging.getLogger('botocore').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)


class DatabaseConfig:
    """Helper class for database connection configuration"""
    
    @staticmethod
    def get_connection_params(database_uri: str) -> Dict[str, Any]:
        """Extract connection parameters from database URI"""
        
        # Add connection pooling and timeout settings optimized for Lambda
        connection_params = {
            'connect_timeout': 30,
            'read_timeout': 300,
            'write_timeout': 300
        }
        
        # Database-specific optimizations
        if 'mysql' in database_uri.lower():
            connection_params.update({
                'use_unicode': True,
                'charset': 'utf8mb4',
                'autocommit': True,
                'sql_mode': 'TRADITIONAL'
            })
        elif 'postgresql' in database_uri.lower() or 'postgres' in database_uri.lower():
            connection_params.update({
                'application_name': 'reladiff-lambda',
                'connect_timeout': 30
            })
        elif 'mssql' in database_uri.lower() or 'babelfish' in database_uri.lower():
            connection_params.update({
                'timeout': 30,
                'login_timeout': 30,
                'appname': 'reladiff-lambda'
            })
        
        return connection_params


class SecretManager:
    """Helper class for managing secrets in Lambda environment"""
    
    @staticmethod
    def get_database_credentials(secret_name: str) -> Dict[str, str]:
        """Retrieve database credentials from AWS Secrets Manager"""
        
        import boto3
        import json
        
        try:
            secrets_client = boto3.client('secretsmanager')
            response = secrets_client.get_secret_value(SecretId=secret_name)
            secret = json.loads(response['SecretString'])
            
            return {
                'username': secret.get('username', ''),
                'password': secret.get('password', ''),
                'host': secret.get('host', ''),
                'port': secret.get('port', ''),
                'database': secret.get('database', '')
            }
            
        except Exception as e:
            logger.error(f"Failed to retrieve secret {secret_name}: {str(e)}")
            raise
    
    @staticmethod
    def build_connection_string(secret_name: str, database_type: str) -> str:
        """Build database connection string from secrets"""
        
        creds = SecretManager.get_database_credentials(secret_name)
        
        if database_type.lower() == 'mysql':
            return f"mysql://{creds['username']}:{creds['password']}@{creds['host']}:{creds['port']}/{creds['database']}"
        elif database_type.lower() in ['postgresql', 'postgres']:
            return f"postgresql://{creds['username']}:{creds['password']}@{creds['host']}:{creds['port']}/{creds['database']}"
        elif database_type.lower() == 'mssql':
            return f"mssql://{creds['username']}:{creds['password']}@{creds['host']}:{creds['port']}/{creds['database']}"
        elif database_type.lower() == 'babelfish':
            return f"babelfish://{creds['username']}:{creds['password']}@{creds['host']}:{creds['port']}/{creds['database']}"
        else:
            raise ValueError(f"Unsupported database type: {database_type}")


class PerformanceConfig:
    """Performance tuning configuration for Lambda environment"""
    
    @staticmethod
    def get_optimal_settings(memory_mb: int, table_size_estimate: int) -> Dict[str, Any]:
        """Calculate optimal settings based on Lambda memory and table size"""
        
        # Calculate optimal bisection factor based on memory
        if memory_mb >= 3008:
            bisection_factor = 64
            bisection_threshold = 32000
        elif memory_mb >= 2048:
            bisection_factor = 32
            bisection_threshold = 16000
        elif memory_mb >= 1024:
            bisection_factor = 16
            bisection_threshold = 8000
        else:
            bisection_factor = 8
            bisection_threshold = 4000
        
        # Adjust based on table size
        if table_size_estimate > 10_000_000:  # 10M+ rows
            bisection_factor = min(bisection_factor * 2, 128)
        elif table_size_estimate < 100_000:  # <100K rows
            bisection_factor = max(bisection_factor // 2, 4)
        
        return {
            'bisection_factor': bisection_factor,
            'bisection_threshold': bisection_threshold,
            'max_threadpool_size': 1,  # Lambda is single-threaded
            'threaded': False
        }


# Global configuration instance
config: Optional[LambdaConfig] = None


def get_config() -> LambdaConfig:
    """Get the global configuration instance"""
    global config
    
    if config is None:
        config = LambdaConfig.from_environment()
        config.setup_logging()
    
    return config


def get_database_config() -> DatabaseConfig:
    """Get database configuration helper"""
    return DatabaseConfig()


def get_secret_manager() -> SecretManager:
    """Get secrets manager helper"""
    return SecretManager()


def get_performance_config() -> PerformanceConfig:
    """Get performance configuration helper"""
    return PerformanceConfig()