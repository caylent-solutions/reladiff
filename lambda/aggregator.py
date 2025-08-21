"""
AWS Lambda Aggregator Function for Reladiff

Monitors worker progress and aggregates results:
1. Tracks completion of segment jobs
2. Aggregates results from S3 when all segments complete
3. Generates final diff output and statistics
4. Publishes completion events
"""

import json
import logging
import os
import boto3
import gzip
from typing import Dict, Any, List, Tuple, Iterator
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS clients
s3 = boto3.client('s3')
eventbridge = boto3.client('events')
sns = boto3.client('sns')


class LambdaAggregator:
    def __init__(self):
        self.results_bucket = os.environ['RESULTS_S3_BUCKET']
        self.completion_topic_arn = os.environ.get('COMPLETION_TOPIC_ARN')
        self.event_bus_name = os.environ.get('EVENT_BUS_NAME', 'default')
        
    def process_completion_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Process segment completion event and check if job is complete"""
        
        action = event['action']
        job_id = event['job_id']
        
        if action == 'start_monitoring':
            return self._initialize_monitoring(job_id, event['total_segments'])
        elif action == 'segment_completed':
            return self._handle_segment_completion(job_id, event['result'])
        else:
            raise ValueError(f"Unknown action: {action}")
    
    def _initialize_monitoring(self, job_id: str, total_segments: int) -> Dict[str, Any]:
        """Initialize monitoring for a new job"""
        
        monitoring_data = {
            'job_id': job_id,
            'total_segments': total_segments,
            'completed_segments': 0,
            'failed_segments': 0,
            'segment_results': {},
            'status': 'MONITORING',
            'started_at': datetime.utcnow().isoformat()
        }
        
        self._store_monitoring_data(job_id, monitoring_data)
        
        logger.info(f"Initialized monitoring for job {job_id} with {total_segments} segments")
        
        return {
            'status': 'MONITORING_INITIALIZED',
            'job_id': job_id,
            'total_segments': total_segments
        }
    
    def _handle_segment_completion(self, job_id: str, segment_result: Dict[str, Any]) -> Dict[str, Any]:
        """Handle completion of a single segment"""
        
        # Load current monitoring data
        monitoring_data = self._load_monitoring_data(job_id)
        
        if not monitoring_data:
            logger.error(f"No monitoring data found for job {job_id}")
            return {'status': 'ERROR', 'message': 'Monitoring data not found'}
        
        # Update monitoring data
        segment_id = segment_result['segment_id']
        monitoring_data['segment_results'][segment_id] = segment_result
        
        if segment_result['status'] == 'SUCCESS':
            monitoring_data['completed_segments'] += 1
        else:
            monitoring_data['failed_segments'] += 1
        
        monitoring_data['last_updated'] = datetime.utcnow().isoformat()
        
        # Store updated monitoring data
        self._store_monitoring_data(job_id, monitoring_data)
        
        # Check if job is complete
        total_processed = monitoring_data['completed_segments'] + monitoring_data['failed_segments']
        
        if total_processed >= monitoring_data['total_segments']:
            return self._finalize_job(job_id, monitoring_data)
        else:
            logger.info(f"Job {job_id}: {total_processed}/{monitoring_data['total_segments']} segments processed")
            return {
                'status': 'SEGMENT_PROCESSED',
                'job_id': job_id,
                'progress': f"{total_processed}/{monitoring_data['total_segments']}"
            }
    
    def _finalize_job(self, job_id: str, monitoring_data: Dict[str, Any]) -> Dict[str, Any]:
        """Finalize job by aggregating all results"""
        
        logger.info(f"Finalizing job {job_id}")
        
        try:
            # Aggregate results from all successful segments
            aggregated_results = self._aggregate_segment_results(job_id, monitoring_data)
            
            # Generate final statistics
            final_stats = self._calculate_final_statistics(monitoring_data, aggregated_results)
            
            # Store final results
            final_location = self._store_final_results(job_id, aggregated_results, final_stats)
            
            # Update monitoring data with final status
            monitoring_data['status'] = 'COMPLETED' if monitoring_data['failed_segments'] == 0 else 'COMPLETED_WITH_FAILURES'
            monitoring_data['completed_at'] = datetime.utcnow().isoformat()
            monitoring_data['final_location'] = final_location
            monitoring_data['final_stats'] = final_stats
            
            self._store_monitoring_data(job_id, monitoring_data)
            
            # Send completion notification
            self._send_completion_notification(job_id, monitoring_data)
            
            return {
                'status': 'JOB_COMPLETED',
                'job_id': job_id,
                'final_location': final_location,
                'stats': final_stats
            }
            
        except Exception as e:
            logger.error(f"Failed to finalize job {job_id}: {str(e)}", exc_info=True)
            
            monitoring_data['status'] = 'FAILED'
            monitoring_data['error'] = str(e)
            monitoring_data['failed_at'] = datetime.utcnow().isoformat()
            self._store_monitoring_data(job_id, monitoring_data)
            
            return {
                'status': 'JOB_FAILED',
                'job_id': job_id,
                'error': str(e)
            }
    
    def _aggregate_segment_results(self, job_id: str, monitoring_data: Dict[str, Any]) -> List[Tuple[str, List]]:
        """Aggregate results from all successful segments"""
        
        aggregated_results = []
        successful_segments = [
            result for result in monitoring_data['segment_results'].values()
            if result['status'] == 'SUCCESS'
        ]
        
        logger.info(f"Aggregating results from {len(successful_segments)} successful segments")
        
        for segment_result in successful_segments:
            result_location = segment_result['result_location']
            
            # Extract S3 key from location
            s3_key = result_location.replace(f"s3://{self.results_bucket}/", "")
            
            try:
                # Download and decompress results
                response = s3.get_object(Bucket=self.results_bucket, Key=s3_key)
                compressed_content = response['Body'].read()
                content = gzip.decompress(compressed_content).decode('utf-8')
                
                # Parse JSON lines
                for line in content.strip().split('\n'):
                    if line:
                        operation, values = json.loads(line)
                        aggregated_results.append((operation, values))
                        
            except Exception as e:
                logger.error(f"Failed to load segment result from {result_location}: {str(e)}")
                continue
        
        logger.info(f"Aggregated {len(aggregated_results)} total diff results")
        return aggregated_results
    
    def _calculate_final_statistics(self, monitoring_data: Dict[str, Any], 
                                  aggregated_results: List[Tuple[str, List]]) -> Dict[str, Any]:
        """Calculate final job statistics"""
        
        # Count diff operations
        diff_counts = defaultdict(int)
        for operation, _ in aggregated_results:
            diff_counts[operation] += 1
        
        # Aggregate segment statistics
        total_processing_time = 0
        segment_stats = []
        
        for segment_result in monitoring_data['segment_results'].values():
            if segment_result['status'] == 'SUCCESS':
                total_processing_time += segment_result.get('processing_time_seconds', 0)
                if 'stats' in segment_result:
                    segment_stats.append(segment_result['stats'])
        
        return {
            'total_segments': monitoring_data['total_segments'],
            'successful_segments': monitoring_data['completed_segments'],
            'failed_segments': monitoring_data['failed_segments'],
            'total_diff_operations': len(aggregated_results),
            'diff_operations_by_type': dict(diff_counts),
            'total_processing_time_seconds': total_processing_time,
            'average_processing_time_per_segment': total_processing_time / max(monitoring_data['completed_segments'], 1),
            'job_start_time': monitoring_data['started_at'],
            'job_end_time': monitoring_data.get('completed_at'),
            'segment_statistics': segment_stats
        }
    
    def _store_final_results(self, job_id: str, aggregated_results: List[Tuple[str, List]], 
                           final_stats: Dict[str, Any]) -> str:
        """Store final aggregated results in S3"""
        
        # Convert to JSON lines format
        json_lines = []
        for operation, values in aggregated_results:
            json_lines.append(json.dumps([operation, values]))
        
        # Create final output
        final_output = {
            'job_id': job_id,
            'statistics': final_stats,
            'results': json_lines
        }
        
        # Compress and store
        json_content = json.dumps(final_output, indent=2)
        compressed_content = gzip.compress(json_content.encode('utf-8'))
        
        s3_key = f"{job_id}/final_results.json.gz"
        
        s3.put_object(
            Bucket=self.results_bucket,
            Key=s3_key,
            Body=compressed_content,
            ContentType='application/gzip',
            ContentEncoding='gzip',
            Metadata={
                'job_id': job_id,
                'result_type': 'final_aggregated',
                'total_operations': str(len(aggregated_results))
            }
        )
        
        return f"s3://{self.results_bucket}/{s3_key}"
    
    def _load_monitoring_data(self, job_id: str) -> Dict[str, Any]:
        """Load monitoring data from S3"""
        try:
            response = s3.get_object(
                Bucket=self.results_bucket,
                Key=f"{job_id}/monitoring.json"
            )
            return json.loads(response['Body'].read().decode('utf-8'))
        except s3.exceptions.NoSuchKey:
            return None
        except Exception as e:
            logger.error(f"Failed to load monitoring data for job {job_id}: {str(e)}")
            return None
    
    def _store_monitoring_data(self, job_id: str, monitoring_data: Dict[str, Any]) -> None:
        """Store monitoring data in S3"""
        s3.put_object(
            Bucket=self.results_bucket,
            Key=f"{job_id}/monitoring.json",
            Body=json.dumps(monitoring_data, indent=2),
            ContentType='application/json'
        )
    
    def _send_completion_notification(self, job_id: str, monitoring_data: Dict[str, Any]) -> None:
        """Send job completion notification"""
        
        notification_data = {
            'job_id': job_id,
            'status': monitoring_data['status'],
            'completed_at': monitoring_data['completed_at'],
            'total_segments': monitoring_data['total_segments'],
            'successful_segments': monitoring_data['completed_segments'],
            'failed_segments': monitoring_data['failed_segments'],
            'final_location': monitoring_data.get('final_location')
        }
        
        # Send to EventBridge
        try:
            eventbridge.put_events(
                Entries=[{
                    'Source': 'reladiff.lambda',
                    'DetailType': 'Diff Job Completed',
                    'Detail': json.dumps(notification_data),
                    'EventBusName': self.event_bus_name
                }]
            )
            logger.info(f"Sent completion event for job {job_id}")
        except Exception as e:
            logger.error(f"Failed to send EventBridge notification: {str(e)}")
        
        # Send to SNS if configured
        if self.completion_topic_arn:
            try:
                sns.publish(
                    TopicArn=self.completion_topic_arn,
                    Subject=f"Reladiff Job {job_id} Completed",
                    Message=json.dumps(notification_data, indent=2)
                )
                logger.info(f"Sent SNS notification for job {job_id}")
            except Exception as e:
                logger.error(f"Failed to send SNS notification: {str(e)}")


def lambda_handler(event, context):
    """
    Lambda handler for aggregator function
    
    Processes completion events from SQS
    """
    
    aggregator = LambdaAggregator()
    
    results = []
    
    for record in event['Records']:
        try:
            message = json.loads(record['body'])
            result = aggregator.process_completion_event(message)
            results.append(result)
            
        except Exception as e:
            logger.error(f"Failed to process completion event: {str(e)}", exc_info=True)
            results.append({
                'status': 'ERROR',
                'error': str(e)
            })
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed_events': len(results),
            'results': results
        })
    }