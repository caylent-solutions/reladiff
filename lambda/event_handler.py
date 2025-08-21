"""
Event Handling for Reladiff Lambda Architecture

Handles EventBridge events, SQS message processing, and S3 event notifications
for seamless integration with AWS services.
"""

import json
import logging
import boto3
from typing import Dict, Any, List
from datetime import datetime

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

# AWS clients
eventbridge = boto3.client('events')
sqs = boto3.client('sqs')
s3 = boto3.client('s3')


class EventBridgeHandler:
    """Handles EventBridge custom events for job orchestration"""
    
    def __init__(self, event_bus_name: str = None):
        self.event_bus_name = event_bus_name or config.event_bus_name
    
    def publish_job_started(self, job_id: str, job_details: Dict[str, Any]) -> bool:
        """Publish job started event"""
        
        event_detail = {
            'job_id': job_id,
            'status': 'STARTED',
            'timestamp': datetime.utcnow().isoformat(),
            'table1': {
                'database': job_details.get('table1', {}).get('database_uri', '').split('://')[0],
                'table_name': job_details.get('table1', {}).get('table_name')
            },
            'table2': {
                'database': job_details.get('table2', {}).get('database_uri', '').split('://')[0],
                'table_name': job_details.get('table2', {}).get('table_name')
            },
            'estimated_segments': job_details.get('analysis', {}).get('estimated_segments', 0)
        }
        
        return self._publish_event('Diff Job Started', event_detail)
    
    def publish_job_progress(self, job_id: str, completed_segments: int, 
                           total_segments: int, failed_segments: int = 0) -> bool:
        """Publish job progress event"""
        
        progress_percent = (completed_segments / total_segments * 100) if total_segments > 0 else 0
        
        event_detail = {
            'job_id': job_id,
            'status': 'IN_PROGRESS',
            'timestamp': datetime.utcnow().isoformat(),
            'completed_segments': completed_segments,
            'total_segments': total_segments,
            'failed_segments': failed_segments,
            'progress_percent': round(progress_percent, 2)
        }
        
        return self._publish_event('Diff Job Progress', event_detail)
    
    def publish_job_completed(self, job_id: str, result_location: str, 
                            stats: Dict[str, Any]) -> bool:
        """Publish job completed event"""
        
        event_detail = {
            'job_id': job_id,
            'status': 'COMPLETED',
            'timestamp': datetime.utcnow().isoformat(),
            'result_location': result_location,
            'statistics': stats
        }
        
        return self._publish_event('Diff Job Completed', event_detail)
    
    def publish_job_failed(self, job_id: str, error_message: str, 
                          failed_segments: int = 0) -> bool:
        """Publish job failed event"""
        
        event_detail = {
            'job_id': job_id,
            'status': 'FAILED',
            'timestamp': datetime.utcnow().isoformat(),
            'error_message': error_message,
            'failed_segments': failed_segments
        }
        
        return self._publish_event('Diff Job Failed', event_detail)
    
    def _publish_event(self, detail_type: str, detail: Dict[str, Any]) -> bool:
        """Publish event to EventBridge"""
        
        try:
            response = eventbridge.put_events(
                Entries=[{
                    'Source': 'reladiff.lambda',
                    'DetailType': detail_type,
                    'Detail': json.dumps(detail),
                    'EventBusName': self.event_bus_name,
                    'Time': datetime.utcnow()
                }]
            )
            
            failed_count = response.get('FailedEntryCount', 0)
            if failed_count > 0:
                logger.error(f"Failed to publish {failed_count} events: {response.get('Entries', [])}")
                return False
            
            logger.debug(f"Successfully published event: {detail_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to publish EventBridge event: {str(e)}")
            return False


class SQSHandler:
    """Enhanced SQS message handling with DLQ and retry logic"""
    
    @staticmethod
    def send_batch_messages(queue_url: str, messages: List[Dict[str, Any]], 
                          group_id: str = None) -> Dict[str, Any]:
        """Send multiple messages to SQS queue in batches"""
        
        results = {
            'successful': 0,
            'failed': 0,
            'failed_messages': []
        }
        
        # SQS batch limit is 10 messages
        batch_size = 10
        
        for i in range(0, len(messages), batch_size):
            batch = messages[i:i + batch_size]
            
            entries = []
            for j, message in enumerate(batch):
                entry = {
                    'Id': f"msg_{i + j}",
                    'MessageBody': json.dumps(message)
                }
                
                # Add MessageGroupId for FIFO queues
                if group_id and queue_url.endswith('.fifo'):
                    entry['MessageGroupId'] = group_id
                    entry['MessageDeduplicationId'] = f"{group_id}_{i + j}_{datetime.utcnow().timestamp()}"
                
                entries.append(entry)
            
            try:
                response = sqs.send_message_batch(
                    QueueUrl=queue_url,
                    Entries=entries
                )
                
                results['successful'] += len(response.get('Successful', []))
                
                # Handle failed messages
                failed = response.get('Failed', [])
                results['failed'] += len(failed)
                results['failed_messages'].extend(failed)
                
                if failed:
                    logger.warning(f"Failed to send {len(failed)} messages in batch")
                
            except Exception as e:
                logger.error(f"Failed to send message batch: {str(e)}")
                results['failed'] += len(entries)
                results['failed_messages'].extend([{'Id': entry['Id'], 'Error': str(e)} for entry in entries])
        
        return results
    
    @staticmethod
    def handle_dead_letter_queue(dlq_url: str, max_messages: int = 10) -> List[Dict[str, Any]]:
        """Process messages from dead letter queue for analysis"""
        
        try:
            response = sqs.receive_message(
                QueueUrl=dlq_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=5,
                MessageAttributeNames=['All'],
                AttributeNames=['All']
            )
            
            messages = response.get('Messages', [])
            processed_messages = []
            
            for message in messages:
                try:
                    # Parse the message body
                    body = json.loads(message['Body'])
                    
                    # Extract failure information
                    failure_info = {
                        'message_id': message['MessageId'],
                        'receipt_handle': message['ReceiptHandle'],
                        'body': body,
                        'attributes': message.get('Attributes', {}),
                        'approximate_receive_count': message.get('Attributes', {}).get('ApproximateReceiveCount', 0),
                        'sent_timestamp': message.get('Attributes', {}).get('SentTimestamp'),
                        'failure_analysis': SQSHandler._analyze_failure(body)
                    }
                    
                    processed_messages.append(failure_info)
                    
                    # Delete the message from DLQ after processing
                    sqs.delete_message(
                        QueueUrl=dlq_url,
                        ReceiptHandle=message['ReceiptHandle']
                    )
                    
                except Exception as e:
                    logger.error(f"Failed to process DLQ message: {str(e)}")
            
            return processed_messages
            
        except Exception as e:
            logger.error(f"Failed to handle dead letter queue: {str(e)}")
            return []
    
    @staticmethod
    def _analyze_failure(message_body: Dict[str, Any]) -> Dict[str, str]:
        """Analyze failed message to determine cause"""
        
        analysis = {
            'likely_cause': 'unknown',
            'recommendation': 'manual_investigation'
        }
        
        # Analyze message content for common failure patterns
        if 'database_uri' in str(message_body):
            if 'timeout' in str(message_body).lower():
                analysis['likely_cause'] = 'database_timeout'
                analysis['recommendation'] = 'increase_timeout_or_reduce_segment_size'
            elif 'connection' in str(message_body).lower():
                analysis['likely_cause'] = 'database_connection_error'
                analysis['recommendation'] = 'check_database_connectivity'
        
        if 'memory' in str(message_body).lower():
            analysis['likely_cause'] = 'memory_exhaustion'
            analysis['recommendation'] = 'increase_lambda_memory_or_reduce_segment_size'
        
        return analysis


class S3EventHandler:
    """Handle S3 events for result processing and cleanup"""
    
    @staticmethod
    def handle_object_created(bucket: str, key: str) -> Dict[str, Any]:
        """Handle S3 object created event"""
        
        result = {
            'processed': False,
            'action': 'none',
            'metadata': {}
        }
        
        try:
            # Get object metadata
            response = s3.head_object(Bucket=bucket, Key=key)
            metadata = response.get('Metadata', {})
            
            result['metadata'] = metadata
            
            # Process based on object type
            if key.endswith('_results.jsonl.gz'):
                # Segment result file
                result['action'] = 'segment_result_stored'
                result['processed'] = True
                
                # Could trigger aggregation check here
                job_id = metadata.get('job_id')
                if job_id:
                    S3EventHandler._check_job_completion(job_id)
            
            elif key.endswith('final_results.json.gz'):
                # Final aggregated result
                result['action'] = 'final_result_stored'
                result['processed'] = True
                
                # Could trigger notification here
                job_id = metadata.get('job_id')
                if job_id:
                    S3EventHandler._notify_job_completion(job_id, key)
            
            elif key.endswith('metadata.json'):
                # Job metadata
                result['action'] = 'metadata_stored'
                result['processed'] = True
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to handle S3 object created event: {str(e)}")
            result['error'] = str(e)
            return result
    
    @staticmethod
    def _check_job_completion(job_id: str) -> None:
        """Check if all segments for a job are complete"""
        
        try:
            # List all segment results for the job
            prefix = f"{job_id}/segments/"
            response = s3.list_objects_v2(
                Bucket=config.results_bucket,
                Prefix=prefix
            )
            
            segment_count = len(response.get('Contents', []))
            
            # Get expected segment count from metadata
            try:
                metadata_response = s3.get_object(
                    Bucket=config.results_bucket,
                    Key=f"{job_id}/metadata.json"
                )
                metadata = json.loads(metadata_response['Body'].read().decode('utf-8'))
                expected_segments = metadata.get('total_segments', 0)
                
                if segment_count >= expected_segments:
                    logger.info(f"All segments complete for job {job_id}, triggering aggregation")
                    # Could send message to aggregator queue here
                
            except Exception as e:
                logger.warning(f"Could not check job completion for {job_id}: {str(e)}")
                
        except Exception as e:
            logger.error(f"Failed to check job completion: {str(e)}")
    
    @staticmethod
    def _notify_job_completion(job_id: str, result_key: str) -> None:
        """Notify about job completion"""
        
        try:
            # Publish completion event
            event_handler = EventBridgeHandler()
            event_handler.publish_job_completed(
                job_id=job_id,
                result_location=f"s3://{config.results_bucket}/{result_key}",
                stats={}  # Would extract from final results
            )
            
        except Exception as e:
            logger.error(f"Failed to notify job completion: {str(e)}")


def lambda_handler(event, context):
    """
    Universal event handler for EventBridge, SQS, and S3 events
    
    Routes events to appropriate handlers based on event source
    """
    
    try:
        event_source = event.get('source')
        
        if event_source == 'aws.s3':
            # S3 event
            results = []
            for record in event.get('Records', []):
                if record.get('eventSource') == 'aws:s3':
                    bucket = record['s3']['bucket']['name']
                    key = record['s3']['object']['key']
                    result = S3EventHandler.handle_object_created(bucket, key)
                    results.append(result)
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'processed': len(results),
                    'results': results
                })
            }
        
        elif 'Records' in event and event['Records'][0].get('eventSource') == 'aws:sqs':
            # SQS event - delegate to specific function handlers
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'SQS events should be handled by specific function handlers'
                })
            }
        
        elif event_source in ['reladiff.lambda', 'custom']:
            # Custom EventBridge event
            detail_type = event.get('detail-type')
            detail = event.get('detail', {})
            
            logger.info(f"Processing custom event: {detail_type}")
            
            # Route to appropriate handler based on detail type
            if 'Job' in detail_type:
                # Job-related event - could trigger notifications, cleanup, etc.
                pass
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'processed': True,
                    'detail_type': detail_type
                })
            }
        
        else:
            logger.warning(f"Unknown event source: {event_source}")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Unknown event source: {event_source}'
                })
            }
    
    except Exception as e:
        logger.error(f"Failed to process event: {str(e)}", exc_info=True)
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }