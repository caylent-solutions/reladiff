"""
CloudWatch Monitoring and Metrics for Reladiff Lambda Functions

Provides comprehensive monitoring, custom metrics, and alerting
for the distributed diffing architecture.
"""

import json
import logging
import boto3
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from functools import wraps

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

# AWS clients
cloudwatch = boto3.client('cloudwatch')
logs = boto3.client('logs')


@dataclass
class MetricData:
    """Structure for custom metric data"""
    name: str
    value: float
    unit: str = 'Count'
    dimensions: Dict[str, str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()
        if self.dimensions is None:
            self.dimensions = {}


class ReladiffMetrics:
    """Custom CloudWatch metrics for Reladiff operations"""
    
    NAMESPACE = 'Reladiff/Lambda'
    
    def __init__(self):
        self.metrics_buffer: List[MetricData] = []
        self.max_buffer_size = 20  # CloudWatch batch limit
    
    def record_job_started(self, job_id: str, table1_size: int, table2_size: int, 
                          estimated_segments: int) -> None:
        """Record job initiation metrics"""
        
        base_dimensions = {'JobId': job_id}
        
        metrics = [
            MetricData('JobsStarted', 1, 'Count', base_dimensions),
            MetricData('EstimatedSegments', estimated_segments, 'Count', base_dimensions),
            MetricData('Table1Size', table1_size, 'Count', base_dimensions),
            MetricData('Table2Size', table2_size, 'Count', base_dimensions),
            MetricData('TotalTableSize', table1_size + table2_size, 'Count', base_dimensions)
        ]
        
        self._add_metrics(metrics)
    
    def record_segment_processed(self, job_id: str, segment_id: str, 
                               processing_time: float, result_count: int,
                               memory_used_mb: float = None) -> None:
        """Record segment processing metrics"""
        
        dimensions = {'JobId': job_id, 'SegmentId': segment_id}
        
        metrics = [
            MetricData('SegmentsProcessed', 1, 'Count', dimensions),
            MetricData('SegmentProcessingTime', processing_time, 'Seconds', dimensions),
            MetricData('SegmentResultCount', result_count, 'Count', dimensions)
        ]
        
        if memory_used_mb is not None:
            metrics.append(MetricData('SegmentMemoryUsage', memory_used_mb, 'Megabytes', dimensions))
        
        self._add_metrics(metrics)
    
    def record_segment_failed(self, job_id: str, segment_id: str, 
                            error_type: str, processing_time: float = None) -> None:
        """Record segment failure metrics"""
        
        dimensions = {'JobId': job_id, 'SegmentId': segment_id, 'ErrorType': error_type}
        
        metrics = [
            MetricData('SegmentsFailed', 1, 'Count', dimensions)
        ]
        
        if processing_time is not None:
            metrics.append(MetricData('FailedSegmentProcessingTime', processing_time, 'Seconds', dimensions))
        
        self._add_metrics(metrics)
    
    def record_job_completed(self, job_id: str, total_segments: int, successful_segments: int,
                           failed_segments: int, total_processing_time: float,
                           total_diff_operations: int) -> None:
        """Record job completion metrics"""
        
        dimensions = {'JobId': job_id}
        
        metrics = [
            MetricData('JobsCompleted', 1, 'Count', dimensions),
            MetricData('TotalSegments', total_segments, 'Count', dimensions),
            MetricData('SuccessfulSegments', successful_segments, 'Count', dimensions),
            MetricData('FailedSegments', failed_segments, 'Count', dimensions),
            MetricData('JobProcessingTime', total_processing_time, 'Seconds', dimensions),
            MetricData('TotalDiffOperations', total_diff_operations, 'Count', dimensions),
            MetricData('JobSuccessRate', (successful_segments / total_segments * 100) if total_segments > 0 else 0, 'Percent', dimensions)
        ]
        
        self._add_metrics(metrics)
    
    def record_database_performance(self, database_type: str, operation: str,
                                  execution_time: float, row_count: int = None) -> None:
        """Record database operation performance"""
        
        dimensions = {'DatabaseType': database_type, 'Operation': operation}
        
        metrics = [
            MetricData('DatabaseOperations', 1, 'Count', dimensions),
            MetricData('DatabaseOperationTime', execution_time, 'Seconds', dimensions)
        ]
        
        if row_count is not None:
            metrics.append(MetricData('DatabaseRowsProcessed', row_count, 'Count', dimensions))
            if execution_time > 0:
                metrics.append(MetricData('DatabaseRowsPerSecond', row_count / execution_time, 'Count/Second', dimensions))
        
        self._add_metrics(metrics)
    
    def record_s3_operation(self, operation: str, object_size_bytes: int,
                          transfer_time: float, job_id: str = None) -> None:
        """Record S3 operation metrics"""
        
        dimensions = {'Operation': operation}
        if job_id:
            dimensions['JobId'] = job_id
        
        metrics = [
            MetricData('S3Operations', 1, 'Count', dimensions),
            MetricData('S3ObjectSize', object_size_bytes, 'Bytes', dimensions),
            MetricData('S3TransferTime', transfer_time, 'Seconds', dimensions)
        ]
        
        if transfer_time > 0:
            throughput_mbps = (object_size_bytes / (1024 * 1024)) / transfer_time
            metrics.append(MetricData('S3ThroughputMBps', throughput_mbps, 'Megabytes/Second', dimensions))
        
        self._add_metrics(metrics)
    
    def _add_metrics(self, metrics: List[MetricData]) -> None:
        """Add metrics to buffer and flush if necessary"""
        
        self.metrics_buffer.extend(metrics)
        
        if len(self.metrics_buffer) >= self.max_buffer_size:
            self.flush_metrics()
    
    def flush_metrics(self) -> bool:
        """Flush metrics buffer to CloudWatch"""
        
        if not self.metrics_buffer:
            return True
        
        try:
            # Convert to CloudWatch format
            metric_data = []
            for metric in self.metrics_buffer:
                metric_datum = {
                    'MetricName': metric.name,
                    'Value': metric.value,
                    'Unit': metric.unit,
                    'Timestamp': metric.timestamp
                }
                
                if metric.dimensions:
                    metric_datum['Dimensions'] = [
                        {'Name': k, 'Value': v} for k, v in metric.dimensions.items()
                    ]
                
                metric_data.append(metric_datum)
            
            # Send to CloudWatch in batches
            batch_size = 20
            for i in range(0, len(metric_data), batch_size):
                batch = metric_data[i:i + batch_size]
                
                cloudwatch.put_metric_data(
                    Namespace=self.NAMESPACE,
                    MetricData=batch
                )
            
            logger.debug(f"Flushed {len(self.metrics_buffer)} metrics to CloudWatch")
            self.metrics_buffer.clear()
            return True
            
        except Exception as e:
            logger.error(f"Failed to flush metrics to CloudWatch: {str(e)}")
            return False


class PerformanceMonitor:
    """Monitor Lambda function performance and resource usage"""
    
    def __init__(self):
        self.metrics = ReladiffMetrics()
        self.start_time = None
        self.peak_memory_mb = 0
    
    def start_monitoring(self, job_id: str = None, segment_id: str = None) -> None:
        """Start performance monitoring for an operation"""
        
        self.start_time = time.time()
        self.job_id = job_id
        self.segment_id = segment_id
        
        # Record initial memory usage
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            self.initial_memory_mb = memory_info.rss / (1024 * 1024)
            self.peak_memory_mb = self.initial_memory_mb
        except ImportError:
            logger.warning("psutil not available for memory monitoring")
            self.initial_memory_mb = 0
    
    def update_memory_peak(self) -> float:
        """Update peak memory usage and return current usage"""
        
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            current_memory_mb = memory_info.rss / (1024 * 1024)
            self.peak_memory_mb = max(self.peak_memory_mb, current_memory_mb)
            return current_memory_mb
        except ImportError:
            return 0
    
    def end_monitoring(self, success: bool = True, error_type: str = None,
                      result_count: int = None) -> Dict[str, float]:
        """End monitoring and record metrics"""
        
        if self.start_time is None:
            return {}
        
        processing_time = time.time() - self.start_time
        final_memory = self.update_memory_peak()
        
        performance_data = {
            'processing_time': processing_time,
            'peak_memory_mb': self.peak_memory_mb,
            'memory_delta_mb': final_memory - self.initial_memory_mb
        }
        
        # Record appropriate metrics
        if self.job_id and self.segment_id:
            if success:
                self.metrics.record_segment_processed(
                    self.job_id, self.segment_id, processing_time,
                    result_count or 0, self.peak_memory_mb
                )
            else:
                self.metrics.record_segment_failed(
                    self.job_id, self.segment_id, error_type or 'unknown', processing_time
                )
        
        # Reset monitoring state
        self.start_time = None
        
        return performance_data


def performance_monitor(job_id_key: str = None, segment_id_key: str = None):
    """Decorator for automatic performance monitoring of functions"""
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            monitor = PerformanceMonitor()
            
            # Extract job_id and segment_id from kwargs if specified
            job_id = kwargs.get(job_id_key) if job_id_key else None
            segment_id = kwargs.get(segment_id_key) if segment_id_key else None
            
            monitor.start_monitoring(job_id, segment_id)
            
            try:
                result = func(*args, **kwargs)
                
                # Determine result count if result is iterable
                result_count = None
                if hasattr(result, '__len__'):
                    result_count = len(result)
                elif hasattr(result, '__iter__'):
                    # For iterators, we can't get count without consuming
                    pass
                
                monitor.end_monitoring(success=True, result_count=result_count)
                return result
                
            except Exception as e:
                error_type = type(e).__name__
                monitor.end_monitoring(success=False, error_type=error_type)
                raise
            
            finally:
                monitor.metrics.flush_metrics()
        
        return wrapper
    return decorator


class LogAnalyzer:
    """Analyze CloudWatch logs for insights and alerting"""
    
    def __init__(self, log_group_name: str):
        self.log_group_name = log_group_name
    
    def analyze_error_patterns(self, hours_back: int = 24) -> Dict[str, Any]:
        """Analyze error patterns in logs"""
        
        start_time = datetime.utcnow() - timedelta(hours=hours_back)
        end_time = datetime.utcnow()
        
        # Query for error patterns
        query = """
        fields @timestamp, @message, @requestId
        | filter @message like /ERROR/
        | stats count() by bin(5m)
        | sort @timestamp desc
        """
        
        try:
            response = logs.start_query(
                logGroupName=self.log_group_name,
                startTime=int(start_time.timestamp()),
                endTime=int(end_time.timestamp()),
                queryString=query
            )
            
            query_id = response['queryId']
            
            # Wait for query completion
            while True:
                time.sleep(2)
                result = logs.get_query_results(queryId=query_id)
                
                if result['status'] == 'Complete':
                    break
                elif result['status'] == 'Failed':
                    raise Exception("Log query failed")
            
            # Process results
            error_counts = []
            for row in result['results']:
                timestamp = row[0]['value']
                count = int(row[1]['value'])
                error_counts.append({'timestamp': timestamp, 'count': count})
            
            total_errors = sum(item['count'] for item in error_counts)
            
            return {
                'total_errors': total_errors,
                'error_timeline': error_counts,
                'analysis_period_hours': hours_back
            }
            
        except Exception as e:
            logger.error(f"Failed to analyze error patterns: {str(e)}")
            return {'error': str(e)}
    
    def get_performance_insights(self, hours_back: int = 24) -> Dict[str, Any]:
        """Get performance insights from logs"""
        
        start_time = datetime.utcnow() - timedelta(hours=hours_back)
        end_time = datetime.utcnow()
        
        # Query for duration patterns
        query = """
        fields @timestamp, @duration, @billedDuration, @maxMemoryUsed
        | filter @type = "REPORT"
        | stats avg(@duration), max(@duration), min(@duration), avg(@maxMemoryUsed), max(@maxMemoryUsed) by bin(1h)
        | sort @timestamp desc
        """
        
        try:
            response = logs.start_query(
                logGroupName=self.log_group_name,
                startTime=int(start_time.timestamp()),
                endTime=int(end_time.timestamp()),
                queryString=query
            )
            
            query_id = response['queryId']
            
            # Wait for completion
            while True:
                time.sleep(2)
                result = logs.get_query_results(queryId=query_id)
                
                if result['status'] == 'Complete':
                    break
                elif result['status'] == 'Failed':
                    raise Exception("Performance query failed")
            
            # Process results
            performance_data = []
            for row in result['results']:
                if len(row) >= 6:
                    performance_data.append({
                        'timestamp': row[0]['value'],
                        'avg_duration_ms': float(row[1]['value']),
                        'max_duration_ms': float(row[2]['value']),
                        'min_duration_ms': float(row[3]['value']),
                        'avg_memory_mb': float(row[4]['value']),
                        'max_memory_mb': float(row[5]['value'])
                    })
            
            return {
                'performance_timeline': performance_data,
                'analysis_period_hours': hours_back
            }
            
        except Exception as e:
            logger.error(f"Failed to get performance insights: {str(e)}")
            return {'error': str(e)}


class AlertingSystem:
    """Custom alerting system for Reladiff operations"""
    
    def __init__(self):
        self.sns_topic_arn = config.completion_topic_arn if hasattr(config, 'completion_topic_arn') else None
        self.sns = boto3.client('sns') if self.sns_topic_arn else None
    
    def check_job_health(self, job_id: str) -> Dict[str, Any]:
        """Check overall health of a running job"""
        
        health_status = {
            'job_id': job_id,
            'status': 'healthy',
            'warnings': [],
            'errors': [],
            'recommendations': []
        }
        
        try:
            # Check segment failure rate
            metrics = ReladiffMetrics()
            
            # This would query CloudWatch for actual metrics
            # For now, we'll use placeholder logic
            
            # Check processing time trends
            # Check memory usage patterns
            # Check error rates
            # Check queue depths
            
            # Example health checks:
            # if failed_segments / total_segments > 0.1:
            #     health_status['warnings'].append('High segment failure rate detected')
            
            # if avg_processing_time > expected_time * 2:
            #     health_status['warnings'].append('Processing times higher than expected')
            
            return health_status
            
        except Exception as e:
            health_status['status'] = 'error'
            health_status['errors'].append(str(e))
            return health_status
    
    def send_alert(self, alert_type: str, message: str, job_id: str = None) -> bool:
        """Send alert notification"""
        
        if not self.sns_topic_arn or not self.sns:
            logger.warning("SNS not configured for alerts")
            return False
        
        try:
            alert_message = {
                'alert_type': alert_type,
                'message': message,
                'timestamp': datetime.utcnow().isoformat(),
                'service': 'reladiff-lambda'
            }
            
            if job_id:
                alert_message['job_id'] = job_id
            
            subject = f"Reladiff Alert: {alert_type}"
            if job_id:
                subject += f" (Job: {job_id})"
            
            self.sns.publish(
                TopicArn=self.sns_topic_arn,
                Subject=subject,
                Message=json.dumps(alert_message, indent=2)
            )
            
            logger.info(f"Sent alert: {alert_type}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send alert: {str(e)}")
            return False


# Global instances
metrics = ReladiffMetrics()
alerting = AlertingSystem()


def get_metrics() -> ReladiffMetrics:
    """Get global metrics instance"""
    return metrics


def get_alerting() -> AlertingSystem:
    """Get global alerting instance"""
    return alerting