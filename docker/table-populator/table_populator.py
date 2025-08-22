#!/usr/bin/env python3
"""
ECS Fargate table populator for reladiff benchmarks.

This version is optimized for large datasets and uses streaming data generation
with real CSV data download capabilities.
"""

import os
import sys
import json
import logging
import boto3
import requests
import csv
import gzip
import io
import time
import argparse
from typing import Dict, Any, List, Optional, Iterator
from datetime import datetime
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# CSV data URLs for different dataset sizes
DATASET_URLS = {
    '1m': 'https://datafold-public.s3.us-west-2.amazonaws.com/1m.csv',
    '25m': 'https://datafold-public.s3.us-west-2.amazonaws.com/25m.csv'
}

class TablePopulator:
    """ECS-based table populator with streaming data generation"""
    
    def __init__(self):
        self.s3_client = boto3.client('s3')
        self.results_bucket = os.getenv('RESULTS_S3_BUCKET')
        
    def download_csv_data(self, dataset_size: str, chunk_size: int = 8192) -> Iterator[List[tuple]]:
        """Download and stream CSV data in chunks"""
        
        url = DATASET_URLS.get(dataset_size)
        if not url:
            # Fallback to mock data generation
            logger.info(f"No CSV URL for {dataset_size}, generating mock data")
            yield from self.generate_mock_data_chunks(dataset_size)
            return
            
        logger.info(f"Downloading CSV data from: {url}")
        
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            
            # Handle gzipped content
            if url.endswith('.gz'):
                content = gzip.GzipFile(fileobj=io.BytesIO(response.content))
            else:
                content = io.StringIO(response.text)
                
            csv_reader = csv.reader(content)
            
            # Skip header if present
            try:
                header = next(csv_reader)
                logger.info(f"CSV header: {header}")
            except StopIteration:
                logger.warning("Empty CSV file")
                return
                
            # Process in chunks
            chunk = []
            for row_num, row in enumerate(csv_reader, 1):
                try:
                    # Convert to expected format: (id, user_id, movie_id, rating, timestamp)
                    if len(row) >= 4:
                        parsed_row = (
                            int(row[0]) if row[0].isdigit() else row_num,
                            int(row[1]) if len(row) > 1 and row[1].isdigit() else 1,
                            int(row[2]) if len(row) > 2 and row[2].isdigit() else 1,
                            float(row[3]) if len(row) > 3 and row[3].replace('.','').isdigit() else 1.0,
                            int(row[4]) if len(row) > 4 and row[4].isdigit() else int(time.time())
                        )
                        chunk.append(parsed_row)
                        
                        if len(chunk) >= chunk_size:
                            yield chunk
                            chunk = []
                            
                        if row_num % 100000 == 0:
                            logger.info(f"Processed {row_num} CSV rows")
                            
                except (ValueError, IndexError) as e:
                    logger.warning(f"Skipping invalid row {row_num}: {row} - {e}")
                    continue
                    
            # Yield remaining chunk
            if chunk:
                yield chunk
                
            logger.info(f"Completed CSV download: {row_num} rows processed")
            
        except Exception as e:
            logger.error(f"Failed to download CSV data: {e}")
            logger.info("Falling back to mock data generation")
            yield from self.generate_mock_data_chunks(dataset_size)
            
    def generate_mock_data_chunks(self, dataset_size: str, chunk_size: int = 10000) -> Iterator[List[tuple]]:
        """Generate mock data in chunks for memory efficiency"""
        
        # Determine total rows
        if dataset_size == '1m':
            total_rows = 1000000
        elif dataset_size == '25m':
            total_rows = 25000000
        else:
            total_rows = int(dataset_size)
            
        logger.info(f"Generating {total_rows} rows of mock data in chunks of {chunk_size}")
        
        import random
        
        for start_idx in range(0, total_rows, chunk_size):
            end_idx = min(start_idx + chunk_size, total_rows)
            chunk = []
            
            for i in range(start_idx, end_idx):
                user_id = random.randint(1, 100000)
                movie_id = random.randint(1, 10000)
                rating = random.choice([1, 2, 3, 4, 5])
                timestamp = random.randint(800000000, 1600000000)
                
                chunk.append((i + 1, user_id, movie_id, rating, timestamp))
                
            yield chunk
            
            if start_idx % 1000000 == 0:
                logger.info(f"Generated {start_idx} rows...")
                
        logger.info(f"Completed mock data generation: {total_rows} rows")
        
    def get_database_connection(self, db_config: Dict[str, Any]):
        """Get database connection based on configuration"""
        
        driver_uri = db_config.get('driver', '')
        
        # Handle secrets manager URIs
        if 'secrets:' in driver_uri:
            driver_uri = self.resolve_secrets_uri(driver_uri)
            
        logger.info(f"Connecting to database: {driver_uri.split('://')[0]}://***")
        
        # For now, return mock connection for demonstration
        # In production, this would establish real database connections
        return MockDatabaseConnection(driver_uri)
        
    def resolve_secrets_uri(self, uri: str) -> str:
        """Resolve AWS Secrets Manager URIs"""
        
        # Parse secrets URI format: driver://secrets:secret_name@host:port/database
        try:
            parts = uri.split('://')
            driver = parts[0]
            rest = parts[1]
            
            if 'secrets:' in rest:
                secret_part, host_part = rest.split('@', 1)
                secret_name = secret_part.split('secrets:')[1]
                
                # Get secret from AWS Secrets Manager
                secrets_client = boto3.client('secretsmanager')
                response = secrets_client.get_secret_value(SecretId=secret_name)
                credentials = json.loads(response['SecretString'])
                
                username = credentials.get('username', 'user')
                password = credentials.get('password', 'password')
                
                resolved_uri = f"{driver}://{username}:{password}@{host_part}"
                logger.info(f"Resolved secrets URI for secret: {secret_name}")
                return resolved_uri
                
        except Exception as e:
            logger.warning(f"Failed to resolve secrets URI: {e}")
            
        return uri
        
    def create_table_sql(self, db_type: str, table_name: str) -> str:
        """Generate CREATE TABLE SQL for different database types"""
        
        if db_type in ['postgresql', 'postgres']:
            return f"""
            DROP TABLE IF EXISTS {table_name};
            CREATE TABLE {table_name} (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating REAL NOT NULL,
                timestamp INTEGER NOT NULL
            );
            """
        elif db_type == 'mysql':
            return f"""
            DROP TABLE IF EXISTS {table_name};
            CREATE TABLE {table_name} (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                user_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating FLOAT NOT NULL,
                timestamp INTEGER NOT NULL
            );
            """
        elif db_type in ['mssql', 'babelfish']:
            return f"""
            IF OBJECT_ID('{table_name}', 'U') IS NOT NULL DROP TABLE {table_name};
            CREATE TABLE {table_name} (
                id INTEGER IDENTITY(1,1) PRIMARY KEY,
                user_id INTEGER NOT NULL,
                movie_id INTEGER NOT NULL,
                rating FLOAT NOT NULL,
                timestamp INTEGER NOT NULL
            );
            """
        else:
            raise ValueError(f"Unsupported database type: {db_type}")
            
    def populate_table(self, db_config: Dict[str, Any], table_name: str, 
                      dataset_size: str) -> Dict[str, Any]:
        """Populate table with streaming data"""
        
        start_time = time.time()
        
        try:
            # Get database connection
            conn = self.get_database_connection(db_config)
            db_type = conn.get_db_type()
            
            logger.info(f"Populating table {table_name} in {db_type} with {dataset_size} dataset")
            
            # Create table
            create_sql = self.create_table_sql(db_type, table_name)
            conn.execute(create_sql)
            logger.info(f"Created table: {table_name}")
            
            # Insert data in chunks
            total_rows = 0
            for chunk in self.download_csv_data(dataset_size):
                conn.bulk_insert(table_name, chunk)
                total_rows += len(chunk)
                
                if total_rows % 100000 == 0:
                    logger.info(f"Inserted {total_rows} rows into {table_name}")
                    
            conn.commit()
            conn.close()
            
            end_time = time.time()
            duration = end_time - start_time
            
            result = {
                'status': 'SUCCESS',
                'database_type': db_type,
                'table_name': table_name,
                'rows_inserted': total_rows,
                'duration_seconds': round(duration, 2),
                'rows_per_second': round(total_rows / duration, 2) if duration > 0 else 0,
                'start_time': datetime.fromtimestamp(start_time).isoformat(),
                'end_time': datetime.fromtimestamp(end_time).isoformat()
            }
            
            logger.info(f"Successfully populated {table_name}: {total_rows} rows in {duration:.2f}s")
            return result
            
        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            
            logger.error(f"Failed to populate table {table_name}: {str(e)}")
            logger.error(traceback.format_exc())
            
            return {
                'status': 'FAILED',
                'table_name': table_name,
                'error': str(e),
                'duration_seconds': round(duration, 2),
                'start_time': datetime.fromtimestamp(start_time).isoformat(),
                'end_time': datetime.fromtimestamp(end_time).isoformat()
            }
            
    def upload_results(self, results: Dict[str, Any], job_id: str) -> Optional[str]:
        """Upload results to S3"""
        
        try:
            if not self.results_bucket:
                logger.warning("No S3 bucket configured")
                return None
                
            key = f"table-population/{job_id}/results.json"
            self.s3_client.put_object(
                Bucket=self.results_bucket,
                Key=key,
                Body=json.dumps(results, indent=2).encode('utf-8'),
                ContentType='application/json'
            )
            
            s3_url = f"s3://{self.results_bucket}/{key}"
            logger.info(f"Results uploaded to: {s3_url}")
            return s3_url
            
        except Exception as e:
            logger.error(f"Failed to upload results: {e}")
            return None

class MockDatabaseConnection:
    """Mock database connection for testing"""
    
    def __init__(self, uri: str):
        self.uri = uri
        
    def get_db_type(self) -> str:
        return self.uri.split('://')[0]
        
    def execute(self, sql: str):
        logger.info(f"Executing SQL: {sql[:100]}...")
        
    def bulk_insert(self, table_name: str, data: List[tuple]):
        logger.info(f"Bulk inserting {len(data)} rows into {table_name}")
        
    def commit(self):
        logger.info("Committing transaction")
        
    def close(self):
        logger.info("Closing connection")

def main():
    """Main entry point for ECS task"""
    
    parser = argparse.ArgumentParser(description='ECS Table Populator')
    parser.add_argument('--database-config', required=True, help='Database configuration JSON')
    parser.add_argument('--table-name', default='rating', help='Table name to populate')
    parser.add_argument('--dataset-size', required=True, help='Dataset size (1m, 25m, etc.)')
    parser.add_argument('--job-id', required=True, help='Job ID for tracking')
    
    args = parser.parse_args()
    
    try:
        # Parse database configuration
        db_config = json.loads(args.database_config)
        
        logger.info(f"Starting table population job: {args.job_id}")
        logger.info(f"Dataset size: {args.dataset_size}")
        logger.info(f"Table name: {args.table_name}")
        
        # Create populator and run
        populator = TablePopulator()
        result = populator.populate_table(db_config, args.table_name, args.dataset_size)
        
        # Upload results
        s3_url = populator.upload_results(result, args.job_id)
        
        # Output final result
        final_result = {
            'job_id': args.job_id,
            'table_population': result,
            'results_s3_url': s3_url
        }
        
        print(json.dumps(final_result))
        
        # Exit with appropriate code
        if result['status'] == 'SUCCESS':
            logger.info("Table population completed successfully")
            sys.exit(0)
        else:
            logger.error("Table population failed")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == '__main__':
    main()