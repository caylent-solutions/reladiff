"""
AWS Lambda Coordinator Function for Reladiff

Orchestrates table diffing by:
1. Analyzing input tables to determine optimal segmentation
2. Creating segment jobs and publishing to SQS
3. Monitoring worker progress and coordinating result aggregation
"""

import json
import logging
import os
import re
import boto3
from typing import Dict, Any, List, Tuple
from datetime import datetime

from reladiff import connect_to_table, diff_tables, Algorithm
from reladiff.table_segment import TableSegment
from reladiff.hashdiff_tables import DEFAULT_BISECTION_FACTOR, DEFAULT_BISECTION_THRESHOLD
from reladiff.utils import Vector, safezip
from secrets_utils import build_connection_uri


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS clients
sqs = boto3.client('sqs')
s3 = boto3.client('s3')
eventbridge = boto3.client('events')


class LambdaCoordinator:
    def __init__(self):
        self.worker_queue_url = os.environ['WORKER_QUEUE_URL']
        self.results_bucket = os.environ['RESULTS_S3_BUCKET']
        self.aggregator_queue_url = os.environ.get('AGGREGATOR_QUEUE_URL')
    
    def resolve_database_uri(self, uri: str) -> str:
        """
        Resolve database URI by replacing credentials with values from Secrets Manager.
        
        Expected URI format with placeholders:
        - mssql://secrets:mssql@host:port/database
        - postgresql://secrets:postgres@host:port/database
        
        The 'secrets:dbtype' part will be replaced with actual credentials.
        """
        if 'secrets:' not in uri:
            # URI already has explicit credentials, return as-is
            return uri
        
        # Parse URI to extract database type and connection details
        # Pattern: protocol://secrets:dbtype@host:port/database
        pattern = r'(\w+)://secrets:(\w+)@([^:]+):(\d+)/(.+)'
        match = re.match(pattern, uri)
        
        if not match:
            logger.error(f"Invalid URI format with secrets placeholder: {uri}")
            raise ValueError(f"Invalid URI format with secrets placeholder: {uri}")
        
        protocol, db_type, host, port, database = match.groups()
        port = int(port)
        
        # Build connection URI with credentials from Secrets Manager
        resolved_uri = build_connection_uri(db_type, host, port, database)
        
        if not resolved_uri:
            raise ValueError(f"Failed to resolve credentials for {db_type} database")
        
        logger.info(f"Successfully resolved database URI for {db_type} at {host}:{port}")
        # Don't log the actual URI with credentials for security
        return resolved_uri
    
    def _parse_key_range_result(self, key_types, key_range) -> Tuple[Vector, Vector]:
        """Convert query_key_range result to Vector objects, similar to diff_tables._parse_key_range_result"""
        if isinstance(key_range, Exception):
            raise key_range

        min_key_values, max_key_values = key_range

        # Convert to Vector objects using key types
        try:
            min_key = Vector(key_type.make_value(mn) for key_type, mn in safezip(key_types, min_key_values))
            max_key = Vector(key_type.make_value(mx) + 1 for key_type, mx in safezip(key_types, max_key_values))
        except (TypeError, ValueError) as e:
            raise type(e)(f"Cannot apply {key_types} to '{min_key_values}', '{max_key_values}'.") from e

        return min_key, max_key
        
    def analyze_tables(self, table1_config: Dict, table2_config: Dict) -> Dict[str, Any]:
        """Analyze tables to determine optimal segmentation strategy"""
        try:
            # Resolve database URIs with credentials from Secrets Manager
            logger.info(f"Original table1 URI: {table1_config['database_uri']}")
            logger.info(f"Original table2 URI: {table2_config['database_uri']}")
            
            # Handle case where database_uri might be a dict with 'driver' key
            table1_raw_uri = table1_config['database_uri']
            if isinstance(table1_raw_uri, dict):
                table1_raw_uri = table1_raw_uri['driver']
            
            table2_raw_uri = table2_config['database_uri'] 
            if isinstance(table2_raw_uri, dict):
                table2_raw_uri = table2_raw_uri['driver']
            
            try:
                table1_uri = self.resolve_database_uri(table1_raw_uri)
                logger.info("Table1 URI resolved successfully")
            except Exception as e:
                logger.error(f"Failed to resolve table1 URI: {e}")
                raise
            
            try:
                table2_uri = self.resolve_database_uri(table2_raw_uri)
                logger.info("Table2 URI resolved successfully")
            except Exception as e:
                logger.error(f"Failed to resolve table2 URI: {e}")
                raise
            
            logger.info("All URI resolutions completed successfully")
            
            table1 = connect_to_table(
                table1_uri,
                table1_config['table_name'],
                table1_config.get('key_columns', ('id',)),
                thread_count=1
            )
            
            table2 = connect_to_table(
                table2_uri,
                table2_config['table_name'], 
                table2_config.get('key_columns', ('id',)),
                thread_count=1
            )
            
            # Get table schemas and key types
            table1 = table1.with_schema()
            table2 = table2.with_schema()
            key_types1 = table1.key_types
            key_types2 = table2.key_types
            
            # Query key ranges to determine bounds (needed for approximate_size)
            try:
                key_range1 = table1.query_key_range()
                min_key1, max_key1 = self._parse_key_range_result(key_types1, key_range1)
                logger.info(f"Table1 key range: {min_key1} to {max_key1}")
            except Exception as e:
                # Handle empty table case
                logger.warning(f"Could not determine key range for table1: {e}")
                if "empty" in str(e).lower():
                    logger.info("Table1 appears to be empty, using table2 bounds")
                    key_range2 = table2.query_key_range()
                    min_key1, max_key1 = self._parse_key_range_result(key_types2, key_range2)
                else:
                    raise
            
            try:
                key_range2 = table2.query_key_range()
                min_key2, max_key2 = self._parse_key_range_result(key_types2, key_range2)
                logger.info(f"Table2 key range: {min_key2} to {max_key2}")
            except Exception as e:
                # Handle empty table case  
                logger.warning(f"Could not determine key range for table2: {e}")
                if "empty" in str(e).lower():
                    logger.info("Table2 appears to be empty, using table1 bounds")
                    min_key2, max_key2 = min_key1, max_key1
                else:
                    raise
            
            # Use the wider bounds from both tables (Vector objects support comparison)
            min_key = Vector(min(k1, k2) for k1, k2 in zip(min_key1, min_key2))
            max_key = Vector(max(k1, k2) for k1, k2 in zip(max_key1, max_key2))
            logger.info(f"Combined key range: {min_key} to {max_key}")
            
            # Create bounded tables for size calculation
            btable1 = table1.new_key_bounds(min_key=min_key, max_key=max_key)
            btable2 = table2.new_key_bounds(min_key=min_key, max_key=max_key)
            
            size1 = btable1.approximate_size()
            size2 = btable2.approximate_size()
            max_size = max(size1, size2)
            
            # Determine algorithm
            algorithm = Algorithm.JOINDIFF if table1.database is table2.database else Algorithm.HASHDIFF
            
            # Calculate optimal segmentation
            bisection_threshold = int(os.environ.get('BISECTION_THRESHOLD', DEFAULT_BISECTION_THRESHOLD))
            bisection_factor = int(os.environ.get('BISECTION_FACTOR', DEFAULT_BISECTION_FACTOR))
            
            # Estimate number of segments needed
            estimated_segments = max(1, max_size // bisection_threshold)
            
            return {
                'algorithm': algorithm.value,
                'table1_size': size1,
                'table2_size': size2,
                'estimated_segments': estimated_segments,
                'bisection_factor': bisection_factor,
                'bisection_threshold': bisection_threshold,
                'key_columns': table1.key_columns,
                'extra_columns': list(set(table1.relevant_columns) - set(table1.key_columns))
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze tables: {str(e)}")
            raise

    def create_segment_jobs(self, job_id: str, table1_config: Dict, table2_config: Dict, 
                           analysis: Dict) -> List[Dict]:
        """Create segment jobs for parallel processing"""
        
        # Resolve database URIs with credentials from Secrets Manager
        # Handle case where database_uri might be a dict with 'driver' key
        table1_raw_uri = table1_config['database_uri']
        if isinstance(table1_raw_uri, dict):
            table1_raw_uri = table1_raw_uri['driver']
            
        table2_raw_uri = table2_config['database_uri']
        if isinstance(table2_raw_uri, dict):
            table2_raw_uri = table2_raw_uri['driver']
            
        table1_uri = self.resolve_database_uri(table1_raw_uri)
        table2_uri = self.resolve_database_uri(table2_raw_uri)
        
        table1 = connect_to_table(
            table1_uri,
            table1_config['table_name'],
            analysis['key_columns'],
            thread_count=1
        ).with_schema()
        
        table2 = connect_to_table(
            table2_uri, 
            table2_config['table_name'],
            analysis['key_columns'],
            thread_count=1
        ).with_schema()
        
        # Get key ranges and convert to Vector objects
        key_types1 = table1.key_types
        key_types2 = table2.key_types
        
        key_range1 = table1.query_key_range()
        key_range2 = table2.query_key_range()
        
        min_key1, max_key1 = self._parse_key_range_result(key_types1, key_range1)
        min_key2, max_key2 = self._parse_key_range_result(key_types2, key_range2)
        
        # Use wider bounds from both tables
        min_key = Vector(min(k1, k2) for k1, k2 in zip(min_key1, min_key2))
        max_key = Vector(max(k1, k2) for k1, k2 in zip(max_key1, max_key2))
        
        # Create bounded tables
        bounded_table1 = table1.new_key_bounds(min_key=min_key, max_key=max_key)
        bounded_table2 = table2.new_key_bounds(min_key=min_key, max_key=max_key)
        
        # Generate checkpoints for segmentation
        biggest_table = bounded_table1 if bounded_table1.approximate_size() > bounded_table2.approximate_size() else bounded_table2
        checkpoints = biggest_table.choose_checkpoints(analysis['bisection_factor'] - 1)
        
        # Create segments
        segments1 = bounded_table1.segment_by_checkpoints(checkpoints)
        segments2 = bounded_table2.segment_by_checkpoints(checkpoints)
        
        jobs = []
        for i, (seg1, seg2) in enumerate(zip(segments1, segments2)):
            job = {
                'job_id': job_id,
                'segment_id': f"{job_id}_segment_{i:04d}",
                'segment_index': i,
                'total_segments': len(segments1),
                'table1': {
                    **table1_config,
                    'min_key': seg1.min_key,
                    'max_key': seg1.max_key,
                    'where': seg1.where
                },
                'table2': {
                    **table2_config,
                    'min_key': seg2.min_key,
                    'max_key': seg2.max_key,
                    'where': seg2.where
                },
                'algorithm': analysis['algorithm'],
                'key_columns': analysis['key_columns'],
                'extra_columns': analysis['extra_columns'],
                'options': {
                    'bisection_threshold': analysis['bisection_threshold'],
                    'threaded': False,  # Single-threaded in Lambda
                    'max_threadpool_size': 1
                }
            }
            jobs.append(job)
            
        logger.info(f"Created {len(jobs)} segment jobs for job_id: {job_id}")
        return jobs

    def publish_jobs_to_sqs(self, jobs: List[Dict]) -> bool:
        """Publish segment jobs to SQS worker queue"""
        try:
            for job in jobs:
                response = sqs.send_message(
                    QueueUrl=self.worker_queue_url,
                    MessageBody=json.dumps(job),
                    MessageGroupId=job['job_id'],  # For FIFO queues
                    MessageDeduplicationId=job['segment_id']
                )
                logger.debug(f"Published job {job['segment_id']} to SQS: {response['MessageId']}")
            
            logger.info(f"Successfully published {len(jobs)} jobs to SQS")
            return True
            
        except Exception as e:
            logger.error(f"Failed to publish jobs to SQS: {str(e)}")
            return False

    def create_job_metadata(self, job_id: str, jobs: List[Dict], analysis: Dict) -> Dict:
        """Create job metadata for tracking and aggregation"""
        return {
            'job_id': job_id,
            'created_at': datetime.utcnow().isoformat(),
            'total_segments': len(jobs),
            'completed_segments': 0,
            'failed_segments': 0,
            'analysis': analysis,
            'status': 'RUNNING',
            'segments': [job['segment_id'] for job in jobs]
        }

    def store_metadata(self, job_id: str, metadata: Dict) -> bool:
        """Store job metadata in S3"""
        try:
            s3.put_object(
                Bucket=self.results_bucket,
                Key=f"{job_id}/metadata.json",
                Body=json.dumps(metadata, indent=2),
                ContentType='application/json'
            )
            logger.info(f"Stored metadata for job {job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to store metadata: {str(e)}")
            return False


def lambda_handler(event, context):
    """
    Lambda handler for coordinator function
    
    Event format:
    {
        "job_id": "unique_job_identifier",
        "table1": {
            "database_uri": "connection_string",
            "table_name": "table_name",
            "key_columns": ["id"]
        },
        "table2": {
            "database_uri": "connection_string", 
            "table_name": "table_name",
            "key_columns": ["id"]
        },
        "options": {
            "extra_columns": ["column1", "column2"],
            "where": "optional_where_clause"
        }
    }
    """
    
    try:
        coordinator = LambdaCoordinator()
        
        # Parse SQS event or direct invocation
        if 'Records' in event:
            # SQS event - extract message body
            record = event['Records'][0]
            message_body = json.loads(record['body'])
            job_id = message_body['job_id']
            table1_config = message_body['table1']
            table2_config = message_body['table2']
            options = message_body.get('options', {})
        else:
            # Direct invocation - for testing
            job_id = event['job_id']
            table1_config = event['table1']
            table2_config = event['table2']
            options = event.get('options', {})
        
        logger.info(f"Starting coordination for job {job_id}")
        logger.info(f"Table1 config: {table1_config}")
        logger.info(f"Table2 config: {table2_config}")
        logger.info(f"Options: {options}")
        
        # Analyze tables
        analysis = coordinator.analyze_tables(table1_config, table2_config)
        logger.info(f"Table analysis complete: {analysis}")
        
        # Create segment jobs
        jobs = coordinator.create_segment_jobs(job_id, table1_config, table2_config, analysis)
        
        # Create and store job metadata
        metadata = coordinator.create_job_metadata(job_id, jobs, analysis)
        coordinator.store_metadata(job_id, metadata)
        
        # Publish jobs to worker queue
        success = coordinator.publish_jobs_to_sqs(jobs)
        
        if success:
            # Send aggregation job if configured
            if coordinator.aggregator_queue_url:
                aggregation_job = {
                    'job_id': job_id,
                    'action': 'start_monitoring',
                    'total_segments': len(jobs)
                }
                sqs.send_message(
                    QueueUrl=coordinator.aggregator_queue_url,
                    MessageBody=json.dumps(aggregation_job),
                    MessageGroupId=job_id,
                    MessageDeduplicationId=f"{job_id}_start_monitoring"
                )
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'job_id': job_id,
                    'status': 'INITIATED',
                    'total_segments': len(jobs),
                    'analysis': analysis
                })
            }
        else:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'error': 'Failed to publish jobs to worker queue'
                })
            }
            
    except Exception as e:
        logger.error(f"Coordinator failed: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }