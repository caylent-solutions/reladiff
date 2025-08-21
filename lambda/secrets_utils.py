"""
AWS Secrets Manager utility functions for Lambda functions
"""

import json
import logging
import boto3
from botocore.exceptions import ClientError
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Initialize Secrets Manager client
secrets_client = boto3.client('secretsmanager')

# Cache for secrets to avoid repeated API calls
_secrets_cache: Dict[str, Dict] = {}


def get_secret(secret_name: str) -> Optional[Dict]:
    """
    Retrieve a secret from AWS Secrets Manager with caching.
    
    Args:
        secret_name: Name of the secret in Secrets Manager
        
    Returns:
        Dictionary containing the secret values, or None if not found
    """
    # Return from cache if available
    if secret_name in _secrets_cache:
        logger.debug(f"Retrieved secret {secret_name} from cache")
        return _secrets_cache[secret_name]
    
    try:
        logger.info(f"Retrieving secret: {secret_name}")
        response = secrets_client.get_secret_value(SecretId=secret_name)
        
        # Parse the secret string as JSON
        secret_dict = json.loads(response['SecretString'])
        
        # Cache the secret
        _secrets_cache[secret_name] = secret_dict
        logger.debug(f"Cached secret {secret_name}")
        
        return secret_dict
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        logger.error(f"Failed to retrieve secret {secret_name}: {error_code} - {e}")
        
        if error_code == 'DecryptionFailureException':
            logger.error("Secrets Manager can't decrypt the protected secret text using the provided KMS key")
        elif error_code == 'InternalServiceErrorException':
            logger.error("An error occurred on the server side")
        elif error_code == 'InvalidParameterException':
            logger.error("Invalid parameter provided")
        elif error_code == 'InvalidRequestException':
            logger.error("Invalid request parameter provided")
        elif error_code == 'ResourceNotFoundException':
            logger.error("The requested secret was not found")
        
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse secret {secret_name} as JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error retrieving secret {secret_name}: {e}")
        return None


def get_database_credentials(db_type: str) -> Optional[Dict[str, str]]:
    """
    Get database credentials for a specific database type.
    
    Args:
        db_type: Type of database ('mssql' or 'postgres')
        
    Returns:
        Dictionary with database connection parameters
    """
    secret_name_map = {
        'mssql': 'sql-polyglot-main-source-sql-server-db-credentials',
        'postgres': 'sql-polyglot-main-target-db-credentials'
    }
    
    secret_name = secret_name_map.get(db_type)
    if not secret_name:
        logger.error(f"Unknown database type: {db_type}")
        return None
    
    secret = get_secret(secret_name)
    if not secret:
        logger.error(f"Failed to retrieve credentials for {db_type}")
        return None
    
    # Expected secret format:
    # {
    #   "username": "sa" or "root",
    #   "password": "Password123!",
    #   "database": "master" (optional),
    #   "host": "hostname" (optional),
    #   "port": "1433" (optional)
    # }
    
    required_fields = ['username', 'password']
    for field in required_fields:
        if field not in secret:
            logger.error(f"Secret {secret_name} missing required field: {field}")
            return None
    
    # Add default database names if not present
    if 'database' not in secret:
        if db_type == 'mssql':
            secret['database'] = 'master'
        elif db_type == 'postgres':
            secret['database'] = 'postgres'
    
    logger.info(f"Successfully retrieved credentials for {db_type}")
    return secret


def build_connection_uri(db_type: str, host: str, port: int, database: str = None) -> Optional[str]:
    """
    Build a database connection URI using credentials from Secrets Manager.
    
    Args:
        db_type: Type of database ('mssql' or 'postgres')
        host: Database hostname
        port: Database port
        database: Database name (optional, will use default from secrets)
        
    Returns:
        Complete database connection URI
    """
    logger.info(f"Building connection URI for {db_type} at {host}:{port}")
    
    credentials = get_database_credentials(db_type)
    if not credentials:
        logger.error(f"Failed to get credentials for {db_type}")
        return None
    
    username = credentials.get('username')
    password = credentials.get('password')
    
    if not username or not password:
        logger.error(f"Missing username or password in credentials for {db_type}")
        return None
    
    # Use provided database name or default from secrets
    db_name = database or credentials.get('database', '')
    
    if db_type == 'mssql':
        uri = f"mssql://{username}:{password}@{host}:{port}/{db_name}"
    elif db_type == 'postgres':
        uri = f"postgresql://{username}:{password}@{host}:{port}/{db_name}"
    else:
        logger.error(f"Unsupported database type: {db_type}")
        return None
    
    logger.info(f"Successfully built connection URI for {db_type} at {host}:{port}")
    return uri


def clear_secrets_cache():
    """Clear the secrets cache. Useful for testing or when secrets are rotated."""
    global _secrets_cache
    _secrets_cache.clear()
    logger.info("Cleared secrets cache")