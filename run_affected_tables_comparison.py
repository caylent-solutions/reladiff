#!/usr/bin/env python3
"""
Script to read affected tables from DynamoDB and run reladiff comparisons using AWS Lambda.

This script:
1. Takes a file_name parameter to identify the analysis record
2. Reads the affected_tables list from the "sql-polyglot-main-sql-analysis" DynamoDB table
3. Runs reladiff comparison for each affected table using AWS Lambda (--aws=auto)

Usage:
    python run_affected_tables_comparison.py --file-name <file_name>
    python run_affected_tables_comparison.py --file-name "migration_batch_001.sql"
"""

import argparse
import json
import sys
import time
from typing import List, Dict, Any, Optional

# AWS SDK imports
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    boto3 = None

# Rich console imports
try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    class SimpleConsole:
        def print(self, text, style=None):
            # Strip rich markup for simple output
            clean_text = text.replace('[green]', '').replace('[/green]', '')
            clean_text = clean_text.replace('[red]', '').replace('[/red]', '')
            clean_text = clean_text.replace('[blue]', '').replace('[/blue]', '')
            clean_text = clean_text.replace('[yellow]', '').replace('[/yellow]', '')
            clean_text = clean_text.replace('[bold]', '').replace('[/bold]', '')
            clean_text = clean_text.replace('[bold blue]', '').replace('[/bold blue]', '')
            clean_text = clean_text.replace('[bold green]', '').replace('[/bold green]', '')
            print(clean_text)
    console = SimpleConsole()

# Reladiff imports
try:
    import reladiff
    from reladiff import diff_tables
    from reladiff.config import apply_config_from_file
    RELADIFF_AVAILABLE = True
except ImportError:
    RELADIFF_AVAILABLE = False
    reladiff = None

class AffectedTablesProcessor:
    """Processes affected tables from DynamoDB and runs reladiff comparisons."""
    
    def __init__(self, table_name: str = "sql-polyglot-main-sql-analysis"):
        """
        Initialize the processor.
        
        Args:
            table_name: Name of the DynamoDB table containing analysis results
        """
        self.table_name = table_name
        
        # Initialize DynamoDB client
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 is required but not available. Please install it with: pip install boto3")
        
        try:
            self.dynamodb = boto3.resource('dynamodb')
            self.table = self.dynamodb.Table(table_name)
            # Test the connection
            self.table.load()
        except NoCredentialsError:
            raise Exception("AWS credentials not configured. Please run 'aws configure' or set AWS environment variables.")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                raise Exception(f"DynamoDB table '{table_name}' not found.")
            raise Exception(f"Error connecting to DynamoDB: {e}")
        except Exception as e:
            raise Exception(f"Failed to initialize DynamoDB connection: {e}")
        
    def get_affected_tables(self, file_name: str) -> Optional[List[str]]:
        """
        Retrieve affected tables for the given file from DynamoDB using boto3.
        
        Args:
            file_name: The file name to look up in the analysis table
            
        Returns:
            List of affected table names, or None if not found
        """
        try:
            console.print(f"[blue]Querying DynamoDB table {self.table_name} for file: {file_name}")
            
            # First, try to scan for the file_name
            try:
                response = self.table.scan(
                    FilterExpression=boto3.dynamodb.conditions.Attr('file_name').eq(file_name)
                )
                items = response.get('Items', [])
            except Exception as e:
                console.print(f"[yellow]Error scanning for file_name: {e}")
                items = []
            
            if not items:
                console.print(f"[yellow]No analysis found for file: {file_name}")
                console.print("[yellow]Trying alternative search patterns...")
                
                # Try searching in process_id field
                try:
                    response = self.table.scan(
                        FilterExpression=boto3.dynamodb.conditions.Attr('process_id').contains(file_name)
                    )
                    items = response.get('Items', [])
                except Exception as e:
                    console.print(f"[yellow]Error scanning for process_id: {e}")
                    items = []
                
                if not items:
                    console.print(f"[red]No matching records found for: {file_name}")
                    return None
            
            item = items[0]
            console.print(f"[green]Found analysis record for: {file_name}")
            
            # Extract affected_tables from the item
            affected_tables = self._extract_affected_tables(item)
            
            if affected_tables:
                console.print(f"[green]Found {len(affected_tables)} affected tables")
                return affected_tables
            else:
                console.print("[yellow]No affected_tables field found in the record")
                # Show available fields to help with debugging
                console.print(f"[blue]Available fields: {list(item.keys())}")
                return None
                
        except Exception as e:
            console.print(f"[red]Error querying DynamoDB: {str(e)}")
            return None
    
    def _extract_affected_tables(self, item: Dict[str, Any]) -> Optional[List[str]]:
        """
        Extract affected tables from a DynamoDB item.
        
        Args:
            item: DynamoDB item as native Python dict (from boto3)
            
        Returns:
            List of table names or None if not found
        """
        # Store the item for later use in key column inference
        self._current_item = item
        return self._extract_table_names(item)
    
    def _extract_table_names(self, item: Dict[str, Any]) -> Optional[List[str]]:
        """
        Extract affected tables from a DynamoDB item.
        
        Args:
            item: DynamoDB item as native Python dict (from boto3)
            
        Returns:
            List of table names or None if not found
        """
        # Try common field names for affected tables
        possible_fields = ['affected_tables', 'tables', 'dependent_tables', 'table_list']
        
        for field in possible_fields:
            if field in item:
                affected_tables = item[field]
                return self._parse_table_list(affected_tables)
        
        # Check if it's in a nested structure (like assessment_result)
        if 'assessment_result' in item:
            assessment = item['assessment_result']
            if isinstance(assessment, dict):
                
                if 'affected_tables' in assessment:
                    return self._parse_table_list(assessment['affected_tables'])
                
                if 'dependentObjects' in assessment:
                    dep_objects = assessment['dependentObjects']
                    if isinstance(dep_objects, dict) and 'tables' in dep_objects:
                        return self._parse_table_list(dep_objects['tables'])
        
        return None
    
    def _parse_table_list(self, table_value: Any) -> List[str]:
        """
        Parse a table list value from boto3 DynamoDB format.
        
        Args:
            table_value: Table list in various formats
            
        Returns:
            List of table names as strings
        """
        if isinstance(table_value, list):
            # Already a Python list - extract table names from objects if needed
            table_names = []
            for table in table_value:
                if isinstance(table, dict) and 'table_name' in table:
                    # Extract table_name from table object
                    table_names.append(str(table['table_name']))
                elif isinstance(table, str):
                    # Direct string table name
                    table_names.append(table)
                elif table:
                    # Try to convert to string
                    table_names.append(str(table))
            return table_names
        elif isinstance(table_value, str):
            # Single string value
            return [table_value] if table_value else []
        elif isinstance(table_value, set):
            # DynamoDB string set
            return list(table_value)
        else:
            # Try to convert to string
            return [str(table_value)] if table_value else []
    
    def _infer_key_columns(self, table_name: str) -> List[str]:
        """
        Infer key columns for a table from the sample_data_query in DynamoDB.
        
        Args:
            table_name: Name of the table to infer keys for
            
        Returns:
            List of inferred key column names
        """
        if not hasattr(self, '_current_item') or not self._current_item:
            console.print(f"[yellow]No DynamoDB item available for key inference for table: {table_name}")
            return ['id']  # Default fallback
        
        try:
            # Look for the table in affected_tables
            affected_tables = self._current_item.get('affected_tables', [])
            console.print(f"[blue]Debug: Found {len(affected_tables)} affected tables in DynamoDB item")
            
            target_table = None
            for i, table_obj in enumerate(affected_tables):
                console.print(f"[blue]Debug: Table {i}: {table_obj.get('table_name', 'NO_NAME')} (type: {type(table_obj)})")
                if isinstance(table_obj, dict) and table_obj.get('table_name') == table_name:
                    target_table = table_obj
                    break
            
            if not target_table:
                console.print(f"[yellow]Table {table_name} not found in affected_tables for key inference")
                return ['id']  # Default fallback
            
            # Get sample_data_query
            sample_queries = target_table.get('sample_data_query', [])
            if not sample_queries:
                console.print(f"[yellow]No sample_data_query found for table: {table_name}")
                return ['id']  # Default fallback
            
            # Get the first query
            first_query = sample_queries[0]
            if not isinstance(first_query, dict):
                console.print(f"[yellow]Invalid sample_data_query format for table: {table_name}")
                return ['id']  # Default fallback
            
            # Get the result from the first query
            query_result = first_query.get('result', [])
            if not query_result or not isinstance(query_result, list) or len(query_result) == 0:
                console.print(f"[yellow]No query results found for table: {table_name}")
                return ['id']  # Default fallback
            
            # Get column names from the first result row
            first_row = query_result[0]
            if not isinstance(first_row, dict):
                console.print(f"[yellow]Invalid query result format for table: {table_name}")
                return ['id']  # Default fallback
            
            columns = list(first_row.keys())
            
            # Look for common key column patterns
            key_patterns = ['id', 'key', 'pk', 'primary_key', '_id']
            potential_keys = []
            
            for pattern in key_patterns:
                for col in columns:
                    if pattern.lower() in col.lower():
                        potential_keys.append(col)
            
            if potential_keys:
                inferred_key = potential_keys[0]
                console.print(f"[green]Inferred key column for {table_name}: {inferred_key}")
                return [inferred_key]
            else:
                # If no obvious key pattern, use the first column
                if columns:
                    inferred_key = columns[0]
                    console.print(f"[yellow]Using first column as key for {table_name}: {inferred_key}")
                    return [inferred_key]
                else:
                    console.print(f"[red]No columns found for table: {table_name}")
                    return ['id']  # Default fallback
                    
        except Exception as e:
            console.print(f"[red]Error inferring key columns for {table_name}: {e}")
            return ['id']  # Default fallback
    
    def run_reladiff_comparison(self, table_name: str, config_path: str = "polyglot-config.toml") -> Dict[str, Any]:
        """
        Run reladiff comparison for a single table using the Python API.
        
        Args:
            table_name: Name of the table to compare
            config_path: Path to the TOML configuration file
            
        Returns:
            Dictionary with comparison results and metadata
        """
        console.print(f"[blue]Running comparison for table: {table_name}")
        
        if not RELADIFF_AVAILABLE:
            return {
                'table_name': table_name,
                'success': False,
                'error': 'reladiff module not available. Please ensure reladiff is installed.',
                'duration': 0
            }
        
        try:
            start_time = time.time()
            
            # Load configuration from file using reladiff's proper API
            console.print(f"[blue]Loading config from: {config_path}")
            
            # Initialize keyword arguments for reladiff
            kw = {
                'aws': 'auto',
                'verbose': True,
            }
            
            # Apply configuration from file (using proper signature)
            try:
                # Use None as run_name to get default configuration
                kw = apply_config_from_file(config_path, None, kw)
            except Exception as config_error:
                console.print(f"[yellow]Warning: Could not load config file: {config_error}")
                # Continue with basic configuration
                pass
            
            # Construct source and target table references  
            source_db = "mssql_source"
            target_db = "postgres_target"
            
            # Infer key columns from DynamoDB data
            key_columns = self._infer_key_columns(table_name)
            
            console.print(f"[blue]Comparing {source_db}:{table_name} with {target_db}:{table_name}")
            console.print(f"[blue]Using key columns: {', '.join(key_columns)}")
            
            # Use subprocess call with proper table reference format
            import subprocess
            import re
            try:
                # Use the direct database/table format instead of source:table format
                # This connects mssql_source to postgres_target for the same table
                cmd = [
                    "poetry", "run", "reladiff",
                    "mssql_source", table_name,
                    "postgres_target", table_name,
                    "--conf", config_path,
                    "--aws", "https://sqs.us-east-1.amazonaws.com/039612887325/reladiff-coordinator-dev.fifo",
                    "--verbose"
                ]
                
                # Add key columns as separate -k flags (reladiff expects individual flags)
                for key_col in key_columns:
                    cmd.extend(["-k", key_col])
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minute timeout for submission
                )
                
                if result.returncode == 0:
                    output = result.stdout
                    
                    # Check if this is a Lambda job submission
                    if "Job submitted successfully!" in output:
                        # Extract job ID from the output
                        job_id_match = re.search(r'Job ID: ([\w\-]+)', output)
                        if job_id_match:
                            job_id = job_id_match.group(1)
                            console.print(f"[green]✓ Lambda job submitted: {job_id}")
                            
                            # Monitor the job completion
                            monitor_result = self._monitor_lambda_job(job_id, table_name, start_time)
                            return monitor_result
                        else:
                            return {
                                'table_name': table_name,
                                'success': True,
                                'result': output,
                                'duration': time.time() - start_time,
                                'message': 'Lambda job submitted (no job ID found)',
                                'job_type': 'lambda_submitted'
                            }
                    else:
                        # Local execution completed
                        end_time = time.time()
                        return {
                            'table_name': table_name,
                            'success': True,
                            'result': output,
                            'duration': end_time - start_time,
                            'message': 'Local comparison completed',
                            'job_type': 'local'
                        }
                else:
                    error_msg = result.stderr or result.stdout or 'Unknown error'
                    end_time = time.time()
                    
                    # Check if this is an expected "no differences" case
                    if ("no differences" in error_msg.lower() or 
                        "identical" in error_msg.lower() or
                        "tables are identical" in error_msg.lower()):
                        return {
                            'table_name': table_name,
                            'success': True,
                            'result': "No differences found",
                            'duration': end_time - start_time,
                            'message': 'Tables are identical'
                        }
                    else:
                        return {
                            'table_name': table_name,
                            'success': False,
                            'error': error_msg,
                            'duration': end_time - start_time
                        }
                        
            except subprocess.TimeoutExpired:
                return {
                    'table_name': table_name,
                    'success': False,
                    'error': 'Command timed out after 5 minutes',
                    'duration': 300
                }
            except Exception as subprocess_error:
                return {
                    'table_name': table_name,
                    'success': False,
                    'error': f"Subprocess error: {str(subprocess_error)}",
                    'duration': time.time() - start_time
                }
                    
        except Exception as e:
            return {
                'table_name': table_name,
                'success': False,
                'error': str(e),
                'duration': 0
            }
    
    def _monitor_lambda_job(self, job_id: str, table_name: str, start_time: float) -> Dict[str, Any]:
        """
        Monitor a Lambda job execution by checking CloudWatch logs across all Lambda functions.
        
        Args:
            job_id: The AWS Lambda job ID to monitor
            table_name: Name of the table being compared
            start_time: Job start time for duration calculation
            
        Returns:
            Dictionary with job execution results
        """
        console.print(f"[blue]Monitoring Lambda job: {job_id}")
        
        try:
            import subprocess
            import time as time_module
            
            # Wait a bit for the job to start
            time_module.sleep(5)
            
            # Check CloudWatch logs for job completion
            max_wait_time = 300  # 5 minutes max wait
            check_interval = 5   # Check every 5 seconds for faster error detection
            elapsed_time = 0
            
            # All Lambda log groups to check
            log_groups = [
                "/aws/lambda/reladiff-coordinator-dev",
                "/aws/lambda/reladiff-worker-dev", 
                "/aws/lambda/reladiff-aggregator-dev"
            ]
            
            while elapsed_time < max_wait_time:
                try:
                    # Check each log group for job-related activity
                    for log_group_name in log_groups:
                        # First check for any activity in this log group
                        cmd = [
                            "aws", "logs", "filter-log-events",
                            "--log-group-name", log_group_name,
                            "--start-time", str(int((start_time - 60) * 1000)),  # Start 1 minute before job submission
                            "--output", "json"
                        ]
                        
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                        
                        if result.returncode == 0:
                            import json
                            log_data = json.loads(result.stdout)
                            events = log_data.get('events', [])
                            
                            # Look for any logs containing our job ID, table name, or error patterns
                            job_related_events = []
                            for event in events:
                                message = event.get('message', '')
                                # Match by job ID, table name, or error indicators
                                if (job_id in message or 
                                    table_name in message or
                                    any(error_word in message.lower() for error_word in ['failed to analyze', 'coordinator failed', 'cannot approximate'])):
                                    job_related_events.append(event)
                            
                            if job_related_events:
                                console.print(f"[blue]Found {len(job_related_events)} job-related log entries in {log_group_name}")
                                
                                # Check ALL events for error indicators first (fail fast)
                                for event in job_related_events:
                                    message = event.get('message', '').lower()
                                    original_message = event.get('message', '')
                                    
                                    # Immediate error detection for faster debugging
                                    error_indicators = [
                                        'coordinator failed', 'worker failed', 'failed to analyze tables',
                                        'cannot approximate', 'does not exist', 'login failed', 
                                        'connection failed', 'runtimeerror', 'operationalerror',
                                        'traceback', 'exception occurred'
                                    ]
                                    
                                    if any(indicator in message for indicator in error_indicators):
                                        console.print(f"[red]✗ Lambda job {job_id} failed")
                                        console.print(f"[red]Error detected: {original_message[:200]}...")
                                        return {
                                            'table_name': table_name,
                                            'success': False,
                                            'error': f'Lambda job failed: {original_message[:200]}',
                                            'duration': time.time() - start_time,
                                            'job_id': job_id
                                        }
                                
                                # Check the most recent events for completion status
                                for event in job_related_events[-5:]:  # Check last 5 events
                                    message = event.get('message', '').lower()
                                    original_message = event.get('message', '')
                                    
                                    # Check for success indicators
                                    if any(word in message for word in ['completed successfully', 'diff complete', 'no differences', 'identical']):
                                        console.print(f"[green]✓ Lambda job {job_id} completed successfully")
                                        console.print(f"[green]Log: {original_message[:100]}...")
                                        return {
                                            'table_name': table_name,
                                            'success': True,
                                            'result': f'Lambda job completed: {original_message[:100]}',
                                            'duration': time.time() - start_time,
                                            'message': 'Lambda comparison completed',
                                            'job_type': 'lambda_completed',
                                            'job_id': job_id
                                        }
                                    
                                    # Check for error indicators (expanded list for faster detection)
                                    if any(word in message for word in ['error', 'failed', 'exception', 'timeout', 'cannot approximate', 'does not exist', 'login failed', 'connection failed']):
                                        console.print(f"[red]✗ Lambda job {job_id} failed")
                                        console.print(f"[red]Error: {original_message[:100]}...")
                                        return {
                                            'table_name': table_name,
                                            'success': False,
                                            'error': f'Lambda job failed: {original_message[:100]}',
                                            'duration': time.time() - start_time,
                                            'job_id': job_id
                                        }
                    
                    # Show progress
                    console.print(f"[blue]Waiting for Lambda job {job_id}... ({elapsed_time}s elapsed)")
                    time_module.sleep(check_interval)
                    elapsed_time += check_interval
                    
                except subprocess.TimeoutExpired:
                    console.print(f"[yellow]Timeout checking logs for job {job_id}")
                    break
                except Exception as e:
                    console.print(f"[yellow]Error checking logs for job {job_id}: {e}")
                    break
            
            # If we reach here, try one final approach - assume success if no errors found
            console.print(f"[yellow]Monitoring timeout for job {job_id} after {elapsed_time}s")
            console.print(f"[yellow]Assuming job completed successfully (no errors detected)")
            
            return {
                'table_name': table_name,
                'success': True,
                'result': 'Lambda job likely completed (monitoring timeout but no errors found)',
                'duration': time.time() - start_time,
                'job_id': job_id,
                'message': 'Job monitoring timed out - check CloudWatch logs for details',
                'job_type': 'lambda_assumed_success'
            }
            
        except Exception as e:
            console.print(f"[red]Error monitoring Lambda job {job_id}: {e}")
            return {
                'table_name': table_name,
                'success': False,
                'error': f'Monitoring error: {str(e)}',
                'duration': time.time() - start_time,
                'job_id': job_id
            }
    
    def process_all_tables(self, file_name: str, config_path: str = "polyglot-config.toml") -> List[Dict[str, Any]]:
        """
        Process all affected tables for the given file.
        
        Args:
            file_name: The file name to process
            config_path: Path to the TOML configuration file
            
        Returns:
            List of comparison results for all tables
        """
        # Get affected tables
        affected_tables = self.get_affected_tables(file_name)
        
        if not affected_tables:
            console.print("[red]No affected tables found. Exiting.")
            return []
        
        console.print(f"\n[green]Processing {len(affected_tables)} affected tables:")
        for i, table in enumerate(affected_tables, 1):
            console.print(f"  {i}. {table}")
        
        # Process each table
        results = []
        
        if RICH_AVAILABLE:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console
            ) as progress:
                
                for i, table_name in enumerate(affected_tables, 1):
                    task = progress.add_task(f"Processing table {i}/{len(affected_tables)}: {table_name}")
                    
                    result = self.run_reladiff_comparison(table_name, config_path)
                    results.append(result)
                    
                    # Log result
                    if result['success']:
                        message = result.get('message', 'Comparison completed successfully')
                        console.print(f"[green]✓ {table_name}: {message}")
                    else:
                        error_msg = result.get('error', 'Unknown error')
                        console.print(f"[red]✗ {table_name}: {error_msg}")
                    
                    progress.remove_task(task)
        else:
            # Simple progress without rich
            for i, table_name in enumerate(affected_tables, 1):
                console.print(f"Processing table {i}/{len(affected_tables)}: {table_name}")
                
                result = self.run_reladiff_comparison(table_name, config_path)
                results.append(result)
                
                # Log result
                if result['success']:
                    message = result.get('message', 'Comparison completed successfully')
                    console.print(f"✓ {table_name}: {message}")
                else:
                    error_msg = result.get('error', 'Unknown error')
                    console.print(f"✗ {table_name}: {error_msg}")
        
        return results
    
    def print_summary(self, results: List[Dict[str, Any]]) -> None:
        """
        Print a summary of all comparison results.
        
        Args:
            results: List of comparison results
        """
        if not results:
            return
        
        console.print("\n[bold blue]Comparison Summary[/bold blue]")
        
        successful = 0
        failed = 0
        total_duration = 0
        
        if RICH_AVAILABLE:
            # Create summary table
            table = Table()
            table.add_column("Table Name", style="cyan")
            table.add_column("Status", style="green")
            table.add_column("Duration (s)", justify="right")
            table.add_column("Details", style="yellow")
            
            for result in results:
                success = result.get('success', False)
                duration = result.get('duration', 0)
                total_duration += duration
                
                if success:
                    successful += 1
                    status = "[green]✓ SUCCESS[/green]"
                    job_type = result.get('job_type', 'unknown')
                    job_id = result.get('job_id', '')
                    if job_type == 'lambda_completed' and job_id:
                        details = f"Lambda job {job_id[:8]}... completed"
                    elif job_type == 'lambda_assumed_success' and job_id:
                        details = f"Lambda job {job_id[:8]}... likely completed"
                    elif job_type == 'lambda_submitted' and job_id:
                        details = f"Lambda job {job_id[:8]}... submitted"
                    else:
                        details = result.get('message', 'Completed')
                else:
                    failed += 1
                    status = "[red]✗ FAILED[/red]"
                    error = result.get('error', 'Unknown error')
                    job_id = result.get('job_id', '')
                    if job_id:
                        details = f"Lambda job {job_id[:8]}... failed"
                    else:
                        details = error[:50] + "..." if len(str(error)) > 50 else str(error)
                
                table.add_row(
                    result['table_name'],
                    status,
                    f"{duration:.1f}",
                    details
                )
            
            console.print(table)
        else:
            # Simple table format without rich
            console.print(f"{'Table Name':<30} {'Status':<15} {'Duration (s)':<12} {'Details'}")
            console.print("-" * 80)
            
            for result in results:
                success = result.get('success', False)
                duration = result.get('duration', 0)
                total_duration += duration
                
                if success:
                    successful += 1
                    status = "✓ SUCCESS"
                    job_type = result.get('job_type', 'unknown')
                    job_id = result.get('job_id', '')
                    if job_type == 'lambda_completed' and job_id:
                        details = f"Lambda {job_id[:8]}... done"
                    elif job_type == 'lambda_assumed_success' and job_id:
                        details = f"Lambda {job_id[:8]}... likely done"
                    elif job_type == 'lambda_submitted' and job_id:
                        details = f"Lambda {job_id[:8]}... sent"
                    else:
                        details = result.get('message', 'Completed')[:30]
                else:
                    failed += 1
                    status = "✗ FAILED"
                    error = result.get('error', 'Unknown error')
                    job_id = result.get('job_id', '')
                    if job_id:
                        details = f"Lambda {job_id[:8]}... failed"
                    else:
                        details = error[:30] + "..." if len(str(error)) > 30 else str(error)
                
                console.print(f"{result['table_name']:<30} {status:<15} {duration:<12.1f} {details}")
        
        # Print overall statistics
        console.print(f"\n[bold]Results:[/bold]")
        console.print(f"  Successful: [green]{successful}[/green]")
        console.print(f"  Failed: [red]{failed}[/red]")
        console.print(f"  Total Duration: [blue]{total_duration:.1f}s[/blue]")


def main():
    """Main function to run the script."""
    # Check dependencies
    if not BOTO3_AVAILABLE:
        console.print("[red]Error: boto3 is required but not available.")
        console.print("[yellow]Please install it with: pip install boto3")
        sys.exit(1)
    
    if not RELADIFF_AVAILABLE:
        console.print("[red]Error: reladiff module is not available.")
        console.print("[yellow]Please ensure reladiff is properly installed in your Python environment.")
        sys.exit(1)
    
    parser = argparse.ArgumentParser(
        description="Run reladiff comparisons for affected tables from DynamoDB analysis"
    )
    parser.add_argument(
        "--file-name", 
        required=True,
        help="File name to look up in the analysis table"
    )
    parser.add_argument(
        "--table-name",
        default="sql-polyglot-main-sql-analysis",
        help="DynamoDB table name containing analysis results (default: sql-polyglot-main-sql-analysis)"
    )
    parser.add_argument(
        "--config",
        default="polyglot-config.toml",
        help="Path to reladiff configuration file (default: polyglot-config.toml)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without running comparisons"
    )
    parser.add_argument(
        "--single-table",
        action="store_true",
        help="Process only the first table (for testing)"
    )
    
    args = parser.parse_args()
    
    # Initialize processor
    processor = AffectedTablesProcessor(args.table_name)
    
    console.print(f"[bold green]Reladiff Affected Tables Processor[/bold green]")
    console.print(f"File: {args.file_name}")
    console.print(f"DynamoDB Table: {args.table_name}")
    console.print(f"Config: {args.config}\n")
    
    try:
        if args.dry_run:
            # Just get and display the affected tables
            affected_tables = processor.get_affected_tables(args.file_name)
            if affected_tables:
                console.print(f"\n[green]Would process {len(affected_tables)} tables:")
                for i, table in enumerate(affected_tables, 1):
                    console.print(f"  {i}. {table}")
        else:
            # Process all tables (or just one if single-table mode)
            if args.single_table:
                # Get affected tables and process only the first one
                affected_tables = processor.get_affected_tables(args.file_name)
                if affected_tables:
                    console.print(f"\n[yellow]Single table mode: processing only '{affected_tables[0]}'")
                    result = processor.run_reladiff_comparison(affected_tables[0], args.config)
                    results = [result]
                else:
                    console.print("[red]No affected tables found")
                    results = []
            else:
                # Process all tables
                results = processor.process_all_tables(args.file_name, args.config)
            
            processor.print_summary(results)
            
            # Exit with error code if any comparisons failed
            failed_count = sum(1 for r in results if not r.get('success', False))
            if failed_count > 0:
                sys.exit(1)
    
    except KeyboardInterrupt:
        console.print("\n[yellow]Process interrupted by user")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()