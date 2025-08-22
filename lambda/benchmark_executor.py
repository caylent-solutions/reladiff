#!/usr/bin/env python3
"""
Lambda function for executing reladiff benchmarks.

This Lambda replicates the functionality of dev/benchmark.sh,
running performance tests and capturing timing/memory metrics.
"""

import json
import logging
import os
import boto3
import time
import psutil
import traceback
from typing import Dict, Any, List, Tuple
from datetime import datetime
import subprocess
import tempfile
import csv
import io

from reladiff import connect_to_table, diff_tables, Algorithm
from secrets_utils import build_connection_uri

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Benchmark test combinations matching dev/benchmark.sh
BENCHMARK_TESTS = [
    {
        'name': 'postgresql_int_mysql_int',
        'db1_type': 'postgresql',
        'db2_type': 'mysql',
        'table1': 'rating',
        'table2': 'rating',
        'key_columns': ['id']
    },
    {
        'name': 'mysql_int_mysql_int', 
        'db1_type': 'mysql',
        'db2_type': 'mysql',
        'table1': 'rating',
        'table2': 'rating_del1',
        'key_columns': ['id']
    },
    {
        'name': 'postgresql_int_postgresql_int',
        'db1_type': 'postgresql', 
        'db2_type': 'postgresql',
        'table1': 'rating',
        'table2': 'rating_update1',
        'key_columns': ['id']
    },
    {
        'name': 'postgresql_ts6_n_tz_mysql_ts0',
        'db1_type': 'postgresql',
        'db2_type': 'mysql', 
        'table1': 'rating',
        'table2': 'rating',
        'key_columns': ['timestamp']
    },
    {
        'name': 'mssql_int_postgresql_int',
        'db1_type': 'mssql',
        'db2_type': 'postgresql',
        'table1': 'rating',
        'table2': 'rating_update001p', 
        'key_columns': ['id']
    },
    {
        'name': 'babelfish_int_mysql_int',
        'db1_type': 'babelfish',
        'db2_type': 'mysql',
        'table1': 'rating',
        'table2': 'rating_update1p',
        'key_columns': ['id']
    }
]

class BenchmarkExecutor:
    """Handles execution of reladiff benchmarks with performance monitoring"""
    
    def __init__(self):
        self.s3_client = boto3.client('s3')
        self.environment = os.environ.get('ENVIRONMENT', 'dev')
        self.results_bucket = os.environ.get('RESULTS_S3_BUCKET')
        self.n_samples = int(os.environ.get('N_SAMPLES', '1000000'))
        self.n_threads = int(os.environ.get('N_THREADS', '16'))
        
    def resolve_database_uri(self, db_config: Dict) -> str:
        """Resolve database URI with credentials from Secrets Manager"""
        try:
            if isinstance(db_config, dict) and 'driver' in db_config:
                driver_url = db_config['driver']
            else:
                driver_url = str(db_config)
                
            # Parse the driver URL
            if '://' not in driver_url:
                raise ValueError(f"Invalid driver URL format: {driver_url}")
                
            scheme, rest = driver_url.split('://', 1)
            
            if rest.startswith('secrets:'):
                # Extract connection details for secret resolution
                secrets_part, host_part = rest.split('@', 1)
                db_type = secrets_part.split(':')[1]  # Extract db type from secrets:dbtype
                host, port_and_db = host_part.split(':', 1)
                port, database = port_and_db.split('/', 1)
                
                # Build connection URI with credentials from Secrets Manager
                resolved_uri = build_connection_uri(db_type, host, port, database)
                if not resolved_uri:
                    raise ValueError(f"Failed to resolve credentials for {db_type} database")
                    
                logger.info(f"Successfully resolved database URI for {db_type} at {host}:{port}")
                return resolved_uri
            else:
                # Direct connection string
                return driver_url
                
        except Exception as e:
            logger.error(f"Failed to resolve database URI: {e}")
            raise
    
    def get_table_stats(self, db_uri: str, table_name: str) -> Dict[str, Any]:
        """Get basic statistics about a table"""
        try:
            table = connect_to_table(db_uri, table_name, ["id"])
            connection = table.db.db
            
            # Get row count
            count_result = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
            row_count = count_result[0] if count_result else 0
            
            # Get sample of key columns for range estimation
            try:
                min_result = connection.execute(f"SELECT MIN(id) FROM {table_name}").fetchone()
                max_result = connection.execute(f"SELECT MAX(id) FROM {table_name}").fetchone()
                min_key = min_result[0] if min_result else None
                max_key = max_result[0] if max_result else None
            except:
                min_key = max_key = None
            
            connection.close()
            
            return {
                'row_count': row_count,
                'min_key': min_key,
                'max_key': max_key
            }
            
        except Exception as e:
            logger.warning(f"Could not get stats for table {table_name}: {e}")
            return {'row_count': 0, 'min_key': None, 'max_key': None}
    
    def monitor_system_resources(self) -> Dict[str, Any]:
        """Capture current system resource usage"""
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            
            return {
                'memory_rss_mb': memory_info.rss / 1024 / 1024,
                'memory_vms_mb': memory_info.vms / 1024 / 1024,
                'cpu_percent': process.cpu_percent(),
                'num_threads': process.num_threads(),
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.warning(f"Could not capture system resources: {e}")
            return {}
    
    def execute_benchmark_test(self, test_config: Dict, db1_config: Dict, db2_config: Dict) -> Dict[str, Any]:
        """Execute a single benchmark test with performance monitoring"""
        test_name = test_config['name']
        logger.info(f"Starting benchmark test: {test_name}")
        
        start_time = datetime.utcnow()
        start_resources = self.monitor_system_resources()
        
        try:
            # Resolve database URIs
            db1_uri = self.resolve_database_uri(db1_config)
            db2_uri = self.resolve_database_uri(db2_config)
            
            # Get table statistics
            table1_stats = self.get_table_stats(db1_uri, test_config['table1'])
            table2_stats = self.get_table_stats(db2_uri, test_config['table2'])
            
            logger.info(f"Table1 ({test_config['table1']}): {table1_stats['row_count']} rows")
            logger.info(f"Table2 ({test_config['table2']}): {table2_stats['row_count']} rows")
            
            # Connect to tables
            table1 = connect_to_table(db1_uri, test_config['table1'], test_config['key_columns'])
            table2 = connect_to_table(db2_uri, test_config['table2'], test_config['key_columns'])
            
            # Configure diff options for benchmarking
            diff_options = {
                'algorithm': Algorithm.AUTO,
                'threaded': True,
                'max_threadpool_size': self.n_threads,
                'bisection_threshold': 16000,
                'stats': True
            }
            
            # Execute the diff with timing
            diff_start = time.time()
            mid_resources = self.monitor_system_resources()
            
            diff_result = diff_tables(table1, table2, **diff_options)
            
            # Process results to get counts
            diff_operations = []
            operation_counts = {'+': 0, '-': 0, '!': 0}
            
            for operation, values in diff_result:
                diff_operations.append((operation, values))
                if operation in operation_counts:
                    operation_counts[operation] += 1
            
            diff_end = time.time()
            end_resources = self.monitor_system_resources()
            
            # Calculate metrics
            diff_duration = diff_end - diff_start
            total_duration = (datetime.utcnow() - start_time).total_seconds()
            
            total_operations = sum(operation_counts.values())
            
            # Memory usage calculation
            peak_memory_mb = max(
                start_resources.get('memory_rss_mb', 0),
                mid_resources.get('memory_rss_mb', 0), 
                end_resources.get('memory_rss_mb', 0)
            )
            
            result = {
                'test_name': test_name,
                'status': 'SUCCESS',
                'duration_seconds': diff_duration,
                'total_duration_seconds': total_duration,
                'start_time': start_time.isoformat(),
                'end_time': datetime.utcnow().isoformat(),
                'database_types': f"{test_config['db1_type']}_to_{test_config['db2_type']}",
                'table1_rows': table1_stats['row_count'],
                'table2_rows': table2_stats['row_count'],
                'diff_operations': total_operations,
                'operation_counts': operation_counts,
                'peak_memory_mb': peak_memory_mb,
                'threads_used': self.n_threads,
                'performance_metrics': {
                    'rows_per_second': (table1_stats['row_count'] + table2_stats['row_count']) / diff_duration if diff_duration > 0 else 0,
                    'operations_per_second': total_operations / diff_duration if diff_duration > 0 else 0,
                    'memory_efficiency_mb_per_row': peak_memory_mb / max(table1_stats['row_count'], 1)
                },
                'resource_monitoring': {
                    'start': start_resources,
                    'mid': mid_resources,
                    'end': end_resources
                }
            }
            
            logger.info(f"Benchmark test {test_name} completed successfully in {diff_duration:.2f}s")
            logger.info(f"Found {total_operations} differences, peak memory: {peak_memory_mb:.1f}MB")
            
            return result
            
        except Exception as e:
            logger.error(f"Benchmark test {test_name} failed: {e}")
            return {
                'test_name': test_name,
                'status': 'FAILED', 
                'error': str(e),
                'traceback': traceback.format_exc(),
                'duration_seconds': (datetime.utcnow() - start_time).total_seconds(),
                'start_time': start_time.isoformat(),
                'end_time': datetime.utcnow().isoformat()
            }
    
    def format_benchmark_csv(self, results: List[Dict]) -> str:
        """Format results as CSV matching benchmark.sh output format"""
        output = io.StringIO()
        
        # CSV header matching benchmark.sh format
        fieldnames = [
            'test_name', 'status', 'duration_seconds', 'database_types',
            'table1_rows', 'table2_rows', 'diff_operations', 'peak_memory_mb',
            'rows_per_second', 'operations_per_second', 'memory_efficiency_mb_per_row',
            'threads_used', 'start_time', 'end_time'
        ]
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        for result in results:
            # Flatten nested metrics for CSV
            csv_row = {
                'test_name': result.get('test_name', ''),
                'status': result.get('status', 'UNKNOWN'),
                'duration_seconds': result.get('duration_seconds', 0),
                'database_types': result.get('database_types', ''),
                'table1_rows': result.get('table1_rows', 0),
                'table2_rows': result.get('table2_rows', 0),
                'diff_operations': result.get('diff_operations', 0),
                'peak_memory_mb': result.get('peak_memory_mb', 0),
                'threads_used': result.get('threads_used', 0),
                'start_time': result.get('start_time', ''),
                'end_time': result.get('end_time', '')
            }
            
            # Add performance metrics
            perf_metrics = result.get('performance_metrics', {})
            csv_row.update({
                'rows_per_second': perf_metrics.get('rows_per_second', 0),
                'operations_per_second': perf_metrics.get('operations_per_second', 0),
                'memory_efficiency_mb_per_row': perf_metrics.get('memory_efficiency_mb_per_row', 0)
            })
            
            writer.writerow(csv_row)
        
        return output.getvalue()
    
    def execute_benchmark_suite(self, db_configs: Dict, test_filter: List[str] = None) -> Dict[str, Any]:
        """Execute a suite of benchmark tests"""
        logger.info(f"Starting benchmark suite with {len(BENCHMARK_TESTS)} tests")
        
        start_time = datetime.utcnow()
        results = []
        
        # Filter tests if specified
        tests_to_run = BENCHMARK_TESTS
        if test_filter:
            tests_to_run = [t for t in BENCHMARK_TESTS if t['name'] in test_filter]
            logger.info(f"Filtering to {len(tests_to_run)} tests: {test_filter}")
        
        for test_config in tests_to_run:
            # Get database configs for this test
            db1_config = db_configs.get(test_config['db1_type'])
            db2_config = db_configs.get(test_config['db2_type'])
            
            if not db1_config or not db2_config:
                logger.error(f"Missing database config for test {test_config['name']}")
                results.append({
                    'test_name': test_config['name'],
                    'status': 'FAILED',
                    'error': f"Missing database config for {test_config['db1_type']} or {test_config['db2_type']}"
                })
                continue
            
            # Execute individual test
            test_result = self.execute_benchmark_test(test_config, db1_config, db2_config)
            results.append(test_result)
            
            # Brief pause between tests
            time.sleep(1)
        
        end_time = datetime.utcnow()
        total_duration = (end_time - start_time).total_seconds()
        
        # Calculate summary statistics
        successful_tests = [r for r in results if r.get('status') == 'SUCCESS']
        failed_tests = [r for r in results if r.get('status') == 'FAILED']
        
        summary = {
            'status': 'COMPLETED',
            'total_tests': len(results),
            'successful_tests': len(successful_tests),
            'failed_tests': len(failed_tests),
            'total_duration_seconds': total_duration,
            'start_time': start_time.isoformat(),
            'end_time': end_time.isoformat(),
            'results': results,
            'csv_output': self.format_benchmark_csv(results)
        }
        
        if successful_tests:
            avg_duration = sum(r.get('duration_seconds', 0) for r in successful_tests) / len(successful_tests)
            avg_memory = sum(r.get('peak_memory_mb', 0) for r in successful_tests) / len(successful_tests)
            
            summary['performance_summary'] = {
                'average_test_duration_seconds': avg_duration,
                'average_peak_memory_mb': avg_memory,
                'fastest_test': min(successful_tests, key=lambda x: x.get('duration_seconds', float('inf')))['test_name'],
                'slowest_test': max(successful_tests, key=lambda x: x.get('duration_seconds', 0))['test_name']
            }
        
        logger.info(f"Benchmark suite completed: {len(successful_tests)}/{len(results)} tests successful")
        return summary

def lambda_handler(event, context):
    """Lambda entry point for benchmark execution"""
    logger.info(f"Starting benchmark execution Lambda with event: {json.dumps(event, default=str)}")
    
    try:
        executor = BenchmarkExecutor()
        
        # Extract parameters from event
        db_configs = event.get('database_configs', {})
        test_filter = event.get('test_filter', None)
        
        if not db_configs:
            raise ValueError("database_configs is required")
        
        # Execute benchmark suite
        result = executor.execute_benchmark_suite(db_configs, test_filter)
        
        # Store results in S3 if bucket configured
        if executor.results_bucket:
            try:
                job_id = event.get('job_id', f"benchmark-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}")
                
                # Store detailed JSON results
                json_key = f"benchmarks/{job_id}/results.json"
                executor.s3_client.put_object(
                    Bucket=executor.results_bucket,
                    Key=json_key,
                    Body=json.dumps(result, indent=2, default=str),
                    ContentType='application/json'
                )
                
                # Store CSV results (matching benchmark.sh format)
                csv_key = f"benchmarks/{job_id}/benchmark_{job_id}.csv"
                executor.s3_client.put_object(
                    Bucket=executor.results_bucket,
                    Key=csv_key,
                    Body=result['csv_output'],
                    ContentType='text/csv'
                )
                
                result['s3_locations'] = {
                    'json': f"s3://{executor.results_bucket}/{json_key}",
                    'csv': f"s3://{executor.results_bucket}/{csv_key}"
                }
                
                logger.info(f"Results stored in S3: {result['s3_locations']}")
                
            except Exception as e:
                logger.warning(f"Failed to store results in S3: {e}")
        
        return {
            'statusCode': 200,
            'body': json.dumps(result, default=str)
        }
        
    except Exception as e:
        logger.error(f"Lambda execution failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'status': 'FAILED',
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }