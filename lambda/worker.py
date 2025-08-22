"""
AWS Lambda Worker Function for Reladiff

Processes individual table segments:
1. Receives segment job from SQS
2. Executes diff operation on the segment 
3. Stores results in S3
4. Reports completion status
"""

import json
import logging
import os
import boto3
import gzip
from typing import Dict, Any, List, Iterator, Tuple
from datetime import datetime
import traceback

from reladiff import connect_to_table, diff_tables, Algorithm
from reladiff.table_segment import TableSegment
from reladiff.utils import Vector
from secrets_utils import build_connection_uri

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS clients
s3 = boto3.client('s3')
sqs = boto3.client('sqs')


class LambdaWorker:
    def __init__(self):
        self.results_bucket = os.environ['RESULTS_S3_BUCKET']
        self.aggregator_queue_url = os.environ.get('AGGREGATOR_QUEUE_URL')
        self.max_memory_mb = int(os.environ.get('MAX_MEMORY_MB', 2048))
    
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
        
        import re
        from secrets_utils import build_connection_uri
        
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
        
        logger.info(f"Resolved database URI for {db_type} at {host}:{port}")
        return resolved_uri
        
    def process_segment(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single table segment diff"""
        
        job_id = job['job_id']
        segment_id = job['segment_id']
        
        logger.info(f"Processing segment {segment_id} for job {job_id}")
        
        try:
            # Convert list fields to tuples (JSON serialization converts tuples to lists)
            key_columns = job['key_columns']
            if isinstance(key_columns, list):
                key_columns = tuple(key_columns)
                
            extra_columns = job.get('extra_columns', [])
            if isinstance(extra_columns, list):
                extra_columns = tuple(extra_columns)
            
            # Connect to tables
            table1 = self._create_table_segment(job['table1'], key_columns)
            table2 = self._create_table_segment(job['table2'], key_columns)
            
            # Configure diff options
            diff_options = {
                'algorithm': Algorithm(job['algorithm']),
                'key_columns': key_columns,
                'extra_columns': extra_columns,
                'threaded': job['options'].get('threaded', False),
                'max_threadpool_size': job['options'].get('max_threadpool_size', 1),
                'bisection_threshold': job['options'].get('bisection_threshold', 16000),
                'allow_empty_tables': True
            }
            
            # Execute diff
            start_time = datetime.utcnow()
            diff_result = diff_tables(table1, table2, **diff_options)
            
            # Collect results
            results = []
            result_count = 0
            
            for operation, values in diff_result:
                results.append((operation, list(values)))
                result_count += 1
                
                # Memory management - flush to S3 if results get too large
                if result_count % 10000 == 0:
                    self._check_memory_usage(results, segment_id)
            
            end_time = datetime.utcnow()
            processing_time = (end_time - start_time).total_seconds()
            
            # Store results in S3
            result_location = self._store_results(job_id, segment_id, results)
            
            # Get statistics
            stats = diff_result.get_stats_dict() if hasattr(diff_result, 'get_stats_dict') else {}
            
            return {
                'status': 'SUCCESS',
                'segment_id': segment_id,
                'result_count': result_count,
                'result_location': result_location,
                'processing_time_seconds': processing_time,
                'stats': stats,
                'completed_at': end_time.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to process segment {segment_id}: {str(e)}", exc_info=True)
            return {
                'status': 'FAILED',
                'segment_id': segment_id,
                'error': str(e),
                'traceback': traceback.format_exc(),
                'failed_at': datetime.utcnow().isoformat()
            }
    
    def _create_table_segment(self, table_config: Dict, key_columns: List[str]) -> TableSegment:
        """Create a TableSegment from configuration"""
        
        # Resolve database URI with credentials from Secrets Manager
        # Handle case where database_uri might be a dict with 'driver' key
        raw_uri = table_config['database_uri']
        if isinstance(raw_uri, dict):
            raw_uri = raw_uri['driver']
        
        database_uri = self.resolve_database_uri(raw_uri)
        
        table = connect_to_table(
            database_uri,
            table_config['table_name'],
            key_columns,
            thread_count=1
        )
        
        # Apply segment bounds if specified
        if 'min_key' in table_config and 'max_key' in table_config:
            # Convert list/tuple data from JSON back to Vector objects
            min_key = table_config['min_key']
            max_key = table_config['max_key']
            
            if isinstance(min_key, (list, tuple)):
                min_key = Vector(min_key)
            if isinstance(max_key, (list, tuple)):
                max_key = Vector(max_key)
                
            table = table.new_key_bounds(
                min_key=min_key,
                max_key=max_key
            )
        
        # Apply where clause if specified
        if table_config.get('where'):
            table = table.new(where=table_config['where'])
            
        return table.with_schema()
    
    def _check_memory_usage(self, results: List, segment_id: str) -> None:
        """Monitor memory usage and implement backpressure if needed"""
        import psutil
        
        memory_percent = psutil.virtual_memory().percent
        
        if memory_percent > 80:
            logger.warning(f"High memory usage ({memory_percent}%) in segment {segment_id}")
            
        if memory_percent > 90:
            logger.error(f"Critical memory usage ({memory_percent}%) in segment {segment_id}")
            # Could implement emergency result flushing here
    
    def _store_results(self, job_id: str, segment_id: str, results: List[Tuple]) -> str:
        """Store segment results in S3 with compression"""
        
        try:
            # Convert results to JSON lines format
            json_lines = []
            for operation, values in results:
                json_lines.append(json.dumps([operation, values]))
            
            json_content = '\n'.join(json_lines)
            
            # Compress the content
            compressed_content = gzip.compress(json_content.encode('utf-8'))
            
            # Upload to S3
            s3_key = f"{job_id}/segments/{segment_id}_results.jsonl.gz"
            
            s3.put_object(
                Bucket=self.results_bucket,
                Key=s3_key,
                Body=compressed_content,
                ContentType='application/gzip',
                ContentEncoding='gzip',
                Metadata={
                    'job_id': job_id,
                    'segment_id': segment_id,
                    'result_count': str(len(results)),
                    'compressed': 'true'
                }
            )
            
            logger.info(f"Stored {len(results)} results for segment {segment_id} at s3://{self.results_bucket}/{s3_key}")
            return f"s3://{self.results_bucket}/{s3_key}"
            
        except Exception as e:
            logger.error(f"Failed to store results for segment {segment_id}: {str(e)}")
            raise
    
    def report_completion(self, job_id: str, result: Dict[str, Any]) -> None:
        """Report segment completion to aggregator"""
        
        if not self.aggregator_queue_url:
            return
            
        try:
            message = {
                'job_id': job_id,
                'action': 'segment_completed',
                'result': result
            }
            
            sqs.send_message(
                QueueUrl=self.aggregator_queue_url,
                MessageBody=json.dumps(message),
                MessageGroupId=job_id
            )
            
            logger.debug(f"Reported completion for segment {result['segment_id']}")
            
        except Exception as e:
            logger.error(f"Failed to report completion: {str(e)}")


def lambda_handler(event, context):
    """
    Lambda handler for worker function
    
    Processes SQS messages containing segment jobs
    """
    
    worker = LambdaWorker()
    
    results = []
    
    # Process each SQS message
    for record in event['Records']:
        try:
            # Parse the job from SQS message
            job = json.loads(record['body'])
            job_id = job['job_id']
            
            logger.info(f"Processing job {job_id}, segment {job['segment_id']}")
            
            # Process the segment
            result = worker.process_segment(job)
            
            # Report completion to aggregator
            worker.report_completion(job_id, result)
            
            results.append(result)
            
        except Exception as e:
            logger.error(f"Failed to process SQS record: {str(e)}", exc_info=True)
            results.append({
                'status': 'FAILED',
                'error': str(e),
                'record': record.get('messageId', 'unknown')
            })
    
    # Return batch results
    successful = len([r for r in results if r.get('status') == 'SUCCESS'])
    failed = len([r for r in results if r.get('status') == 'FAILED'])
    
    return {
        'statusCode': 200 if failed == 0 else 207,  # 207 = Multi-Status
        'body': json.dumps({
            'processed': len(results),
            'successful': successful,
            'failed': failed,
            'results': results
        })
    }