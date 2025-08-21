"""
S3 Utilities for Reladiff Lambda Functions

Provides enhanced S3 operations for result storage, compression,
and efficient data management for large diff operations.
"""

import json
import gzip
import logging
import boto3
import io
from typing import List, Dict, Any, Iterator, Optional, Tuple
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import get_config

logger = logging.getLogger(__name__)
config = get_config()

s3 = boto3.client('s3')


class S3ResultStore:
    """Enhanced S3 storage for diff results with compression and streaming"""
    
    def __init__(self, bucket_name: str = None):
        self.bucket_name = bucket_name or config.results_bucket
        self.compression_enabled = config.enable_compression
        self.batch_size = config.result_batch_size
    
    def store_segment_results(self, job_id: str, segment_id: str, 
                            results: List[Tuple[str, List]], 
                            metadata: Dict[str, Any] = None) -> str:
        """Store segment results with optimal compression and metadata"""
        
        try:
            # Prepare metadata
            object_metadata = {
                'job_id': job_id,
                'segment_id': segment_id,
                'result_count': str(len(results)),
                'timestamp': datetime.utcnow().isoformat(),
                'compressed': str(self.compression_enabled).lower()
            }
            
            if metadata:
                object_metadata.update({k: str(v) for k, v in metadata.items()})
            
            # Convert results to JSON Lines format
            json_lines = []
            for operation, values in results:
                json_lines.append(json.dumps([operation, values], separators=(',', ':')))
            
            content = '\n'.join(json_lines)
            
            # Compress if enabled
            if self.compression_enabled:
                content_bytes = gzip.compress(content.encode('utf-8'))
                content_type = 'application/gzip'
                content_encoding = 'gzip'
                file_extension = '.jsonl.gz'
            else:
                content_bytes = content.encode('utf-8')
                content_type = 'application/json'
                content_encoding = None
                file_extension = '.jsonl'
            
            # Generate S3 key
            s3_key = f"{job_id}/segments/{segment_id}_results{file_extension}"
            
            # Upload to S3
            put_args = {
                'Bucket': self.bucket_name,
                'Key': s3_key,
                'Body': content_bytes,
                'ContentType': content_type,
                'Metadata': object_metadata,
                'ServerSideEncryption': 'AES256'
            }
            
            if content_encoding:
                put_args['ContentEncoding'] = content_encoding
            
            s3.put_object(**put_args)
            
            logger.info(f"Stored {len(results)} results for segment {segment_id}")
            return f"s3://{self.bucket_name}/{s3_key}"
            
        except Exception as e:
            logger.error(f"Failed to store segment results: {str(e)}")
            raise
    
    def stream_segment_results(self, job_id: str, segment_id: str) -> Iterator[Tuple[str, List]]:
        """Stream segment results from S3 without loading everything into memory"""
        
        # Try compressed first, then uncompressed
        for file_extension in ['.jsonl.gz', '.jsonl']:
            s3_key = f"{job_id}/segments/{segment_id}_results{file_extension}"
            
            try:
                response = s3.get_object(Bucket=self.bucket_name, Key=s3_key)
                
                if file_extension == '.jsonl.gz':
                    # Stream decompression
                    with gzip.GzipFile(fileobj=io.BytesIO(response['Body'].read())) as gz_file:
                        for line in gz_file:
                            line = line.decode('utf-8').strip()
                            if line:
                                operation, values = json.loads(line)
                                yield (operation, values)
                else:
                    # Stream uncompressed
                    for line in response['Body'].iter_lines():
                        line = line.decode('utf-8').strip()
                        if line:
                            operation, values = json.loads(line)
                            yield (operation, values)
                
                return  # Success, exit loop
                
            except s3.exceptions.NoSuchKey:
                continue  # Try next format
            except Exception as e:
                logger.error(f"Failed to stream segment results: {str(e)}")
                raise
        
        raise FileNotFoundError(f"No results found for segment {segment_id} in job {job_id}")
    
    def store_final_results(self, job_id: str, aggregated_results: List[Tuple[str, List]], 
                          statistics: Dict[str, Any]) -> str:
        """Store final aggregated results with comprehensive metadata"""
        
        try:
            # Create final output structure
            final_output = {
                'job_id': job_id,
                'completed_at': datetime.utcnow().isoformat(),
                'statistics': statistics,
                'format': 'json_lines',
                'compression': 'gzip' if self.compression_enabled else 'none',
                'total_operations': len(aggregated_results)
            }
            
            # Separate metadata and results for efficient access
            metadata_content = json.dumps(final_output, indent=2)
            
            # Convert results to JSON Lines
            results_lines = []
            for operation, values in aggregated_results:
                results_lines.append(json.dumps([operation, values], separators=(',', ':')))
            
            results_content = '\n'.join(results_lines)
            
            # Store metadata
            metadata_key = f"{job_id}/final_metadata.json"
            s3.put_object(
                Bucket=self.bucket_name,
                Key=metadata_key,
                Body=metadata_content.encode('utf-8'),
                ContentType='application/json',
                ServerSideEncryption='AES256',
                Metadata={
                    'job_id': job_id,
                    'type': 'final_metadata',
                    'total_operations': str(len(aggregated_results))
                }
            )
            
            # Store results
            if self.compression_enabled:
                results_bytes = gzip.compress(results_content.encode('utf-8'))
                results_key = f"{job_id}/final_results.jsonl.gz"
                content_type = 'application/gzip'
                content_encoding = 'gzip'
            else:
                results_bytes = results_content.encode('utf-8')
                results_key = f"{job_id}/final_results.jsonl"
                content_type = 'application/json'
                content_encoding = None
            
            put_args = {
                'Bucket': self.bucket_name,
                'Key': results_key,
                'Body': results_bytes,
                'ContentType': content_type,
                'ServerSideEncryption': 'AES256',
                'Metadata': {
                    'job_id': job_id,
                    'type': 'final_results',
                    'total_operations': str(len(aggregated_results)),
                    'compressed': str(self.compression_enabled).lower()
                }
            }
            
            if content_encoding:
                put_args['ContentEncoding'] = content_encoding
            
            s3.put_object(**put_args)
            
            logger.info(f"Stored final results for job {job_id}: {len(aggregated_results)} operations")
            return f"s3://{self.bucket_name}/{results_key}"
            
        except Exception as e:
            logger.error(f"Failed to store final results: {str(e)}")
            raise
    
    def get_job_progress(self, job_id: str) -> Dict[str, Any]:
        """Get job progress by analyzing stored segments"""
        
        try:
            # Get expected segments from metadata
            try:
                metadata_response = s3.get_object(
                    Bucket=self.bucket_name,
                    Key=f"{job_id}/metadata.json"
                )
                metadata = json.loads(metadata_response['Body'].read().decode('utf-8'))
                total_segments = metadata.get('total_segments', 0)
            except s3.exceptions.NoSuchKey:
                total_segments = 0
            
            # Count completed segments
            completed_segments = 0
            segment_sizes = []
            
            try:
                response = s3.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=f"{job_id}/segments/",
                    MaxKeys=1000
                )
                
                for obj in response.get('Contents', []):
                    if obj['Key'].endswith('_results.jsonl.gz') or obj['Key'].endswith('_results.jsonl'):
                        completed_segments += 1
                        segment_sizes.append(obj['Size'])
                
            except Exception as e:
                logger.warning(f"Failed to list segments for job {job_id}: {str(e)}")
            
            # Calculate progress
            progress_percent = (completed_segments / total_segments * 100) if total_segments > 0 else 0
            
            return {
                'job_id': job_id,
                'total_segments': total_segments,
                'completed_segments': completed_segments,
                'progress_percent': round(progress_percent, 2),
                'average_segment_size': sum(segment_sizes) / len(segment_sizes) if segment_sizes else 0,
                'total_storage_bytes': sum(segment_sizes)
            }
            
        except Exception as e:
            logger.error(f"Failed to get job progress: {str(e)}")
            return {
                'job_id': job_id,
                'error': str(e)
            }
    
    def cleanup_job_data(self, job_id: str, keep_final_results: bool = True) -> Dict[str, Any]:
        """Clean up job data, optionally keeping final results"""
        
        cleanup_stats = {
            'deleted_objects': 0,
            'deleted_bytes': 0,
            'errors': []
        }
        
        try:
            # List all objects for the job
            response = s3.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=f"{job_id}/",
                MaxKeys=1000
            )
            
            objects_to_delete = []
            
            for obj in response.get('Contents', []):
                key = obj['Key']
                
                # Skip final results if requested
                if keep_final_results and ('final_results' in key or 'final_metadata' in key):
                    continue
                
                objects_to_delete.append({'Key': key})
                cleanup_stats['deleted_bytes'] += obj['Size']
            
            # Delete objects in batches
            batch_size = 1000
            for i in range(0, len(objects_to_delete), batch_size):
                batch = objects_to_delete[i:i + batch_size]
                
                try:
                    delete_response = s3.delete_objects(
                        Bucket=self.bucket_name,
                        Delete={'Objects': batch}
                    )
                    
                    cleanup_stats['deleted_objects'] += len(delete_response.get('Deleted', []))
                    
                    # Handle errors
                    for error in delete_response.get('Errors', []):
                        cleanup_stats['errors'].append(error)
                        logger.warning(f"Failed to delete {error['Key']}: {error['Message']}")
                
                except Exception as e:
                    cleanup_stats['errors'].append(str(e))
                    logger.error(f"Failed to delete batch: {str(e)}")
            
            logger.info(f"Cleanup completed for job {job_id}: {cleanup_stats['deleted_objects']} objects deleted")
            return cleanup_stats
            
        except Exception as e:
            logger.error(f"Failed to cleanup job data: {str(e)}")
            cleanup_stats['errors'].append(str(e))
            return cleanup_stats


class S3LifecycleManager:
    """Manage S3 lifecycle policies for automated cleanup"""
    
    def __init__(self, bucket_name: str = None):
        self.bucket_name = bucket_name or config.results_bucket
    
    def setup_lifecycle_policies(self) -> bool:
        """Setup intelligent lifecycle policies for different data types"""
        
        lifecycle_config = {
            'Rules': [
                {
                    'ID': 'ReladiffSegmentResults',
                    'Status': 'Enabled',
                    'Filter': {'Prefix': 'segments/'},
                    'Transitions': [
                        {
                            'Days': 7,
                            'StorageClass': 'STANDARD_IA'
                        },
                        {
                            'Days': 30,
                            'StorageClass': 'GLACIER'
                        }
                    ],
                    'Expiration': {'Days': 90}
                },
                {
                    'ID': 'ReladiffFinalResults',
                    'Status': 'Enabled',
                    'Filter': {'Prefix': 'final_'},
                    'Transitions': [
                        {
                            'Days': 30,
                            'StorageClass': 'STANDARD_IA'
                        },
                        {
                            'Days': 90,
                            'StorageClass': 'GLACIER'
                        }
                    ],
                    'Expiration': {'Days': 365}
                },
                {
                    'ID': 'ReladiffMetadata',
                    'Status': 'Enabled',
                    'Filter': {'Prefix': 'metadata.json'},
                    'Transitions': [
                        {
                            'Days': 90,
                            'StorageClass': 'STANDARD_IA'
                        }
                    ],
                    'Expiration': {'Days': 365}
                }
            ]
        }
        
        try:
            s3.put_bucket_lifecycle_configuration(
                Bucket=self.bucket_name,
                LifecycleConfiguration=lifecycle_config
            )
            
            logger.info(f"Successfully configured lifecycle policies for bucket {self.bucket_name}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup lifecycle policies: {str(e)}")
            return False


class S3MultipartUploader:
    """Handle large file uploads using S3 multipart upload"""
    
    def __init__(self, bucket_name: str = None):
        self.bucket_name = bucket_name or config.results_bucket
        self.part_size = 100 * 1024 * 1024  # 100MB parts
    
    def upload_large_results(self, job_id: str, file_name: str, 
                           data_iterator: Iterator[bytes]) -> str:
        """Upload large result files using multipart upload"""
        
        s3_key = f"{job_id}/{file_name}"
        
        try:
            # Initiate multipart upload
            response = s3.create_multipart_upload(
                Bucket=self.bucket_name,
                Key=s3_key,
                ServerSideEncryption='AES256',
                Metadata={
                    'job_id': job_id,
                    'upload_type': 'multipart',
                    'timestamp': datetime.utcnow().isoformat()
                }
            )
            
            upload_id = response['UploadId']
            parts = []
            part_number = 1
            
            # Upload parts
            current_part = b''
            
            for chunk in data_iterator:
                current_part += chunk
                
                if len(current_part) >= self.part_size:
                    # Upload this part
                    part_response = s3.upload_part(
                        Bucket=self.bucket_name,
                        Key=s3_key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=current_part
                    )
                    
                    parts.append({
                        'ETag': part_response['ETag'],
                        'PartNumber': part_number
                    })
                    
                    part_number += 1
                    current_part = b''
            
            # Upload final part if there's remaining data
            if current_part:
                part_response = s3.upload_part(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=current_part
                )
                
                parts.append({
                    'ETag': part_response['ETag'],
                    'PartNumber': part_number
                })
            
            # Complete multipart upload
            s3.complete_multipart_upload(
                Bucket=self.bucket_name,
                Key=s3_key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
            
            logger.info(f"Successfully uploaded large file: {s3_key}")
            return f"s3://{self.bucket_name}/{s3_key}"
            
        except Exception as e:
            # Abort multipart upload on failure
            try:
                s3.abort_multipart_upload(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    UploadId=upload_id
                )
            except:
                pass
            
            logger.error(f"Failed to upload large file: {str(e)}")
            raise