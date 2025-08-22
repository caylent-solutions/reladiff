#!/usr/bin/env python3
"""
ECS-based Lambda Benchmark Orchestrator

This script orchestrates reladiff benchmarks using:
- ECS Fargate for table population (handles large datasets efficiently)  
- Lambda for benchmark execution (fast parallel processing)
- S3 for result storage and coordination
"""

import os
import sys
import time
import json
import boto3
import argparse
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import toml
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.live import Live
from rich.panel import Panel
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Rich console
console = Console()

class ECSBenchmarkOrchestrator:
    """Orchestrates benchmark execution using ECS for table population"""
    
    def __init__(self, stack_name: str, region: str = 'us-east-1'):
        self.stack_name = stack_name
        self.region = region
        
        # Initialize AWS clients
        self.ecs_client = boto3.client('ecs', region_name=region)
        self.lambda_client = boto3.client('lambda', region_name=region)
        self.s3_client = boto3.client('s3', region_name=region)
        self.cloudformation = boto3.client('cloudformation', region_name=region)
        
        # Get stack outputs
        self.stack_outputs = self._get_stack_outputs()
        self.results_bucket = self.stack_outputs.get('ResultsBucket')
        self.ecs_cluster = self.stack_outputs.get('ECSClusterName')
        self.task_definition = self.stack_outputs.get('TablePopulatorTaskDefinition')
        self.benchmark_function = self.stack_outputs.get('BenchmarkExecutorFunctionName')
        
        console.print(f"Initialized ECS Benchmark Orchestrator")
        console.print(f"Stack: {stack_name}, Region: {region}")
        console.print(f"Results Bucket: {self.results_bucket}")
        console.print(f"ECS Cluster: {self.ecs_cluster}")
        
    def _get_stack_outputs(self) -> Dict[str, str]:
        """Get CloudFormation stack outputs"""
        try:
            response = self.cloudformation.describe_stacks(StackName=self.stack_name)
            outputs = {}
            for output in response['Stacks'][0].get('Outputs', []):
                outputs[output['OutputKey']] = output['OutputValue']
            return outputs
        except Exception as e:
            console.print(f"[red]Error getting stack outputs: {e}[/red]")
            return {}
            
    def _get_database_configs(self) -> Dict[str, Dict[str, Any]]:
        """Load database configurations from polyglot-config.toml"""
        
        config_paths = ['polyglot-config.toml', 'polyglot-config-local.toml']
        
        for config_path in config_paths:
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r') as f:
                        config = toml.load(f)
                    
                    databases = {}
                    for db_name, db_config in config.get('database', {}).items():
                        databases[db_name] = db_config
                        
                    console.print(f"Loaded database configs from {config_path}: {list(databases.keys())}")
                    return databases
                    
                except Exception as e:
                    console.print(f"[yellow]Warning: Failed to load {config_path}: {e}[/yellow]")
                    
        # Fallback to mock configurations
        console.print("[yellow]Using mock database configurations[/yellow]")
        return {
            'mssql_source': {'driver': 'mssql://secrets:mssql@host:1433/database'},
            'postgres_target': {'driver': 'postgresql://secrets:postgres@host:5432/database'}
        }
        
    def _run_ecs_task(self, database_name: str, database_config: Dict[str, Any], 
                     dataset_size: str, job_id: str) -> str:
        """Run ECS Fargate task for table population"""
        
        task_name = f"populate-{database_name}-{dataset_size}-{job_id}"
        
        # Prepare container overrides
        container_overrides = [{
            'name': 'table-populator',
            'command': [
                'python', 'table_populator.py',
                '--database-config', json.dumps(database_config),
                '--table-name', 'rating',
                '--dataset-size', dataset_size,
                '--job-id', task_name
            ]
        }]
        
        try:
            # Run ECS task
            response = self.ecs_client.run_task(
                cluster=self.ecs_cluster,
                taskDefinition=self.task_definition,
                launchType='FARGATE',
                networkConfiguration={
                    'awsvpcConfiguration': {
                        'subnets': self._get_default_subnets(),
                        'assignPublicIp': 'ENABLED'
                    }
                },
                overrides={
                    'containerOverrides': container_overrides
                },
                tags=[
                    {'key': 'JobId', 'value': job_id},
                    {'key': 'Database', 'value': database_name},
                    {'key': 'DatasetSize', 'value': dataset_size}
                ]
            )
            
            task_arn = response['tasks'][0]['taskArn']
            task_id = task_arn.split('/')[-1]
            
            logger.info(f"Started ECS task for {database_name}: {task_id}")
            return task_id
            
        except Exception as e:
            logger.error(f"Failed to start ECS task for {database_name}: {e}")
            raise
            
    def _get_default_subnets(self) -> List[str]:
        """Get default VPC subnets"""
        try:
            ec2 = boto3.client('ec2', region_name=self.region)
            
            # Get default VPC
            vpcs = ec2.describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}])
            if not vpcs['Vpcs']:
                raise Exception("No default VPC found")
                
            vpc_id = vpcs['Vpcs'][0]['VpcId']
            
            # Get subnets in default VPC
            subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
            return [subnet['SubnetId'] for subnet in subnets['Subnets'][:2]]  # Use first 2 subnets
            
        except Exception as e:
            logger.warning(f"Failed to get default subnets: {e}")
            # Return empty list - will use default networking
            return []
            
    def _wait_for_ecs_tasks(self, task_ids: List[str], database_names: List[str]) -> Dict[str, Any]:
        """Wait for ECS tasks to complete and return results"""
        
        results = {}
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), 
                     BarColumn(), TimeElapsedColumn()) as progress:
            
            # Create progress tasks
            task_progress = {}
            for i, (task_id, db_name) in enumerate(zip(task_ids, database_names)):
                task_progress[task_id] = progress.add_task(f"Populating {db_name}...", total=100)
            
            start_time = time.time()
            completed_tasks = set()
            
            while len(completed_tasks) < len(task_ids):
                try:
                    # Check task status
                    response = self.ecs_client.describe_tasks(
                        cluster=self.ecs_cluster,
                        tasks=task_ids
                    )
                    
                    for task in response['tasks']:
                        task_id = task['taskArn'].split('/')[-1]
                        status = task['lastStatus']
                        
                        if task_id in completed_tasks:
                            continue
                            
                        # Update progress based on status
                        if status == 'RUNNING':
                            progress.update(task_progress[task_id], completed=50)
                        elif status in ['STOPPED', 'DEACTIVATING']:
                            # Task completed - get exit code
                            exit_code = None
                            for container in task.get('containers', []):
                                if container.get('name') == 'table-populator':
                                    exit_code = container.get('exitCode')
                                    break
                                    
                            # Determine success/failure
                            if exit_code == 0:
                                progress.update(task_progress[task_id], completed=100)
                                results[database_names[task_ids.index(task_id)]] = {
                                    'status': 'SUCCESS',
                                    'task_id': task_id
                                }
                            else:
                                progress.update(task_progress[task_id], completed=100)
                                results[database_names[task_ids.index(task_id)]] = {
                                    'status': 'FAILED',
                                    'task_id': task_id,
                                    'exit_code': exit_code
                                }
                                
                            completed_tasks.add(task_id)
                            
                    # Sleep before next check
                    time.sleep(10)
                    
                    # Timeout after 30 minutes
                    if time.time() - start_time > 1800:
                        console.print("[red]Timeout waiting for ECS tasks[/red]")
                        break
                        
                except Exception as e:
                    logger.error(f"Error checking ECS task status: {e}")
                    time.sleep(5)
                    
        return results
        
    def populate_tables(self, dataset_size: str) -> Dict[str, Any]:
        """Populate benchmark tables using ECS Fargate"""
        
        console.print(f"\n🗃️  Populating Benchmark Tables ({dataset_size} dataset)")
        console.print("=" * 60)
        
        databases = self._get_database_configs()
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        
        # Start ECS tasks for each database
        task_ids = []
        database_names = []
        
        for db_name, db_config in databases.items():
            try:
                task_id = self._run_ecs_task(db_name, db_config, dataset_size, job_id)
                task_ids.append(task_id)
                database_names.append(db_name)
                console.print(f"Started ECS task for {db_name}: {task_id}")
            except Exception as e:
                console.print(f"[red]Failed to start task for {db_name}: {e}[/red]")
                
        if not task_ids:
            console.print("[red]No ECS tasks started![/red]")
            return {}
            
        # Wait for tasks to complete
        results = self._wait_for_ecs_tasks(task_ids, database_names)
        
        # Print summary
        successful = [db for db, result in results.items() if result.get('status') == 'SUCCESS']
        failed = [db for db, result in results.items() if result.get('status') == 'FAILED']
        
        console.print(f"\nTable Population Summary: {len(successful)}/{len(results)} databases successful")
        
        if successful:
            console.print(f"[green]✓ Successful: {', '.join(successful)}[/green]")
        if failed:
            console.print(f"[red]✗ Failed: {', '.join(failed)}[/red]")
            
        if len(successful) == len(results):
            console.print("🎉 All tables populated successfully!")
            return results
        elif successful:
            console.print("⚠️  Some tables populated successfully")
            return results
        else:
            console.print("❌ All table population failed!")
            return {}
            
    def execute_benchmarks(self, dataset_size: str) -> Optional[Dict[str, Any]]:
        """Execute benchmarks using Lambda"""
        
        console.print(f"\n🚀 Executing Lambda Benchmarks ({dataset_size} dataset)")
        console.print("=" * 60)
        
        databases = self._get_database_configs()
        job_id = f"benchmark-{dataset_size}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        # Prepare Lambda payload
        payload = {
            'database_configs': databases,
            'job_id': job_id
        }
        
        try:
            with Progress(SpinnerColumn(), TextColumn("Executing benchmarks..."), 
                         TimeElapsedColumn()) as progress:
                task = progress.add_task("Executing benchmarks...", total=None)
                
                # Invoke benchmark Lambda
                response = self.lambda_client.invoke(
                    FunctionName=self.benchmark_function,
                    Payload=json.dumps(payload)
                )
                
                # Parse response
                result = json.loads(response['Payload'].read())
                
                if response['StatusCode'] == 200:
                    benchmark_results = json.loads(result['body'])
                    
                    console.print("\n📊 Benchmark Results")
                    console.print("=" * 80)
                    
                    # Display results table
                    self._display_benchmark_results(benchmark_results.get('results', []))
                    
                    # Summary
                    total_tests = benchmark_results.get('total_tests', 0)
                    successful_tests = benchmark_results.get('successful_tests', 0)
                    
                    if successful_tests == total_tests:
                        console.print(f"🎉 All {total_tests} benchmark tests completed successfully!")
                    else:
                        console.print(f"⚠️  {successful_tests}/{total_tests} benchmark tests completed successfully")
                        
                    return benchmark_results
                    
                else:
                    console.print(f"[red]❌ Benchmarks failed: {result.get('errorMessage', 'Unknown error')}[/red]")
                    return None
                    
        except Exception as e:
            console.print(f"[red]❌ Benchmark execution failed: {e}[/red]")
            return None
            
    def _display_benchmark_results(self, results: List[Dict[str, Any]]):
        """Display benchmark results in a formatted table"""
        
        table = Table(title="Benchmark Test Results")
        table.add_column("Test Name", style="cyan", width=30)
        table.add_column("Status", justify="center", width=8)
        table.add_column("Duration (s)", justify="right", width=8)
        table.add_column("Rows/sec", justify="right", width=9)
        table.add_column("Memory (MB)", justify="right", width=8)
        table.add_column("Diff Ops", justify="right", width=9)
        
        for result in results:
            status_color = "green" if result.get('status') == 'SUCCESS' else "red"
            status_symbol = "✓" if result.get('status') == 'SUCCESS' else "✗"
            
            table.add_row(
                result.get('test_name', 'Unknown'),
                f"[{status_color}]{status_symbol}[/{status_color}]\n[{status_color}]{result.get('status', 'UNKNOWN')}[/{status_color}]",
                f"{result.get('duration_seconds', 0):.2f}",
                f"{result.get('rows_per_second', 0):,.0f}" if result.get('rows_per_second') else "0",
                f"{result.get('peak_memory_mb', 0):.1f}",
                f"{result.get('diff_operations', 0):,}"
            )
            
        console.print(table)
        
    def run_benchmark(self, dataset_size: str) -> bool:
        """Run complete benchmark workflow"""
        
        console.print(Panel(f"🚀 ECS-based Reladiff Benchmark Execution ({dataset_size} dataset)", 
                           style="bold blue"))
        
        # Step 1: Populate tables using ECS
        population_results = self.populate_tables(dataset_size)
        
        if not population_results:
            console.print("[red]❌ Table population failed - aborting benchmark[/red]")
            return False
            
        # Check if all required databases were populated
        successful_dbs = [db for db, result in population_results.items() 
                         if result.get('status') == 'SUCCESS']
                         
        if len(successful_dbs) < 2:
            console.print("[red]❌ Insufficient databases populated - need at least 2 for benchmarks[/red]")
            return False
            
        # Step 2: Execute benchmarks using Lambda
        benchmark_results = self.execute_benchmarks(dataset_size)
        
        if benchmark_results:
            console.print("\n🎉 ECS benchmark execution completed successfully!")
            return True
        else:
            console.print("\n❌ ECS benchmark execution failed")
            return False


def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(description='ECS-based Reladiff Benchmark Orchestrator')
    parser.add_argument('--dataset-size', required=True, choices=['1m', '25m'], 
                       help='Dataset size to benchmark')
    parser.add_argument('--stack-name', required=True, help='CloudFormation stack name')
    parser.add_argument('--region', default='us-east-1', help='AWS region')
    
    args = parser.parse_args()
    
    try:
        orchestrator = ECSBenchmarkOrchestrator(args.stack_name, args.region)
        success = orchestrator.run_benchmark(args.dataset_size)
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        console.print("\n[yellow]Benchmark interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Fatal error: {e}[/red]")
        sys.exit(1)


if __name__ == '__main__':
    main()