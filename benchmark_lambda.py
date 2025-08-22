#!/usr/bin/env python3
"""
Lambda Benchmark Orchestration Script

This script orchestrates the Lambda-based benchmarking infrastructure,
matching the functionality of dev/benchmark.sh but using distributed Lambda execution.
"""

import argparse
import json
import boto3
import time
import sys
from typing import Dict, Any, List
from datetime import datetime
import tempfile
import subprocess
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

console = Console()

class LambdaBenchmarkOrchestrator:
    """Orchestrates Lambda-based benchmark execution with progress monitoring"""
    
    def __init__(self, stack_name: str = "reladiff-serverless-dev", region: str = "us-east-1"):
        self.stack_name = stack_name
        self.region = region
        self.lambda_client = boto3.client('lambda', region_name=region)
        self.s3_client = boto3.client('s3', region_name=region)
        self.cloudformation = boto3.client('cloudformation', region_name=region)
        
        # Get stack outputs
        self.stack_outputs = self._get_stack_outputs()
        self.results_bucket = self.stack_outputs.get('ResultsBucket')
        
        console.print(f"[blue]Initialized Lambda Benchmark Orchestrator")
        console.print(f"[blue]Stack: {stack_name}, Region: {region}")
        console.print(f"[blue]Results Bucket: {self.results_bucket}")
    
    def _get_stack_outputs(self) -> Dict[str, str]:
        """Get CloudFormation stack outputs"""
        try:
            response = self.cloudformation.describe_stacks(StackName=self.stack_name)
            outputs = {}
            
            for output in response['Stacks'][0].get('Outputs', []):
                outputs[output['OutputKey']] = output['OutputValue']
                
            return outputs
            
        except Exception as e:
            console.print(f"[red]Failed to get stack outputs: {e}")
            return {}
    
    def _get_database_configs(self) -> Dict[str, Dict]:
        """Get database configurations from the polyglot config"""
        # Read the existing polyglot config for database configurations
        try:
            with open('polyglot-config.toml', 'r') as f:
                import toml
                config = toml.load(f)
                
            return config.get('database', {})
            
        except Exception as e:
            console.print(f"[yellow]Warning: Could not load database configs: {e}")
            
            # Fallback to default configuration
            return {
                'postgresql': {'driver': 'postgresql://secrets:postgres@sql-polyglot-main-target-database.cz8cq8icqxtd.us-east-1.rds.amazonaws.com:5432/dbEoUnyfPy'},
                'mysql': {'driver': 'mysql://secrets:mysql@sql-polyglot-main-mysql-database.cz8cq8icqxtd.us-east-1.rds.amazonaws.com:3306/dbQ9jle5DG'},
                'mssql': {'driver': 'mssql://secrets:mssql@sql-polyglot-main-source-sql-server-database.cz8cq8icqxtd.us-east-1.rds.amazonaws.com:1433/dbQ9jle5DG'},
                'babelfish': {'driver': 'babelfish://secrets:babelfish@sql-polyglot-main-babelfish-database.cz8cq8icqxtd.us-east-1.rds.amazonaws.com:1434/master'}
            }
    
    def invoke_lambda_function(self, function_name: str, payload: Dict) -> Dict[str, Any]:
        """Invoke a Lambda function and return the response"""
        try:
            console.print(f"[cyan]Invoking Lambda function: {function_name}")
            
            response = self.lambda_client.invoke(
                FunctionName=function_name,
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            # Parse response
            response_payload = json.loads(response['Payload'].read().decode('utf-8'))
            
            if response.get('StatusCode') == 200:
                if 'body' in response_payload:
                    return json.loads(response_payload['body'])
                else:
                    return response_payload
            else:
                raise Exception(f"Lambda invocation failed: {response_payload}")
                
        except Exception as e:
            console.print(f"[red]Failed to invoke {function_name}: {e}")
            raise
    
    def populate_tables(self, dataset_size: str = '1m') -> Dict[str, Any]:
        """Populate benchmark tables in all databases"""
        console.print(f"\n[bold blue]🗃️  Populating Benchmark Tables ({dataset_size} dataset)")
        console.print("=" * 60)
        
        db_configs = self._get_database_configs()
        results = {}
        
        # Get function name from stack outputs
        table_populator_function = f"reladiff-table-populator-{self.stack_name.split('-')[-1]}"
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            
            for db_type, db_config in db_configs.items():
                task = progress.add_task(f"Populating {db_type}...", total=1)
                
                try:
                    payload = {
                        'database_config': db_config,
                        'dataset_size': dataset_size,
                        'job_id': f"populate-{db_type}-{dataset_size}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
                    }
                    
                    result = self.invoke_lambda_function(table_populator_function, payload)
                    results[db_type] = result
                    
                    if result.get('status') == 'SUCCESS':
                        stats = result.get('statistics', {})
                        duration = result.get('duration_seconds', 0)
                        console.print(f"[green]✓ {db_type}: {stats.get('rating_rows', 0)} rows in {duration:.1f}s")
                    else:
                        console.print(f"[red]✗ {db_type}: {result.get('error', 'Unknown error')}")
                        
                    progress.update(task, completed=1)
                    
                except Exception as e:
                    console.print(f"[red]✗ {db_type}: {e}")
                    results[db_type] = {'status': 'FAILED', 'error': str(e)}
                    progress.update(task, completed=1)
        
        # Summary
        successful = sum(1 for r in results.values() if r.get('status') == 'SUCCESS')
        total = len(results)
        
        console.print(f"\n[bold]Table Population Summary: {successful}/{total} databases successful")
        
        if successful == 0:
            console.print("[red]❌ All table population failed!")
            return results
        elif successful < total:
            console.print("[yellow]⚠️  Some table population failed, but continuing with available databases")
        else:
            console.print("[green]🎉 All tables populated successfully!")
            
        return results
    
    def execute_benchmarks(self, dataset_size: str = '1m', test_filter: List[str] = None) -> Dict[str, Any]:
        """Execute benchmark suite using Lambda"""
        console.print(f"\n[bold blue]🚀 Executing Lambda Benchmarks ({dataset_size} dataset)")
        console.print("=" * 60)
        
        db_configs = self._get_database_configs()
        
        # Get function name from stack outputs
        benchmark_executor_function = f"reladiff-benchmark-executor-{self.stack_name.split('-')[-1]}"
        
        payload = {
            'database_configs': db_configs,
            'test_filter': test_filter,
            'job_id': f"benchmark-{dataset_size}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        }
        
        with Progress(
            SpinnerColumn(), 
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console
        ) as progress:
            
            task = progress.add_task("Executing benchmarks...", total=None)
            
            try:
                result = self.invoke_lambda_function(benchmark_executor_function, payload)
                progress.update(task, completed=1)
                
                return result
                
            except Exception as e:
                console.print(f"[red]Benchmark execution failed: {e}")
                progress.update(task, completed=1)
                return {'status': 'FAILED', 'error': str(e)}
    
    def display_benchmark_results(self, results: Dict[str, Any]):
        """Display benchmark results in a formatted table"""
        console.print(f"\n[bold blue]📊 Benchmark Results")
        console.print("=" * 80)
        
        if results.get('status') != 'COMPLETED':
            console.print(f"[red]❌ Benchmarks failed: {results.get('error', 'Unknown error')}")
            return
        
        # Summary statistics
        summary = results.get('performance_summary', {})
        if summary:
            console.print(f"[green]🎯 Performance Summary:")
            console.print(f"   Average Duration: {summary.get('average_test_duration_seconds', 0):.2f}s")
            console.print(f"   Average Memory: {summary.get('average_peak_memory_mb', 0):.1f}MB")
            console.print(f"   Fastest Test: {summary.get('fastest_test', 'N/A')}")
            console.print(f"   Slowest Test: {summary.get('slowest_test', 'N/A')}")
        
        # Detailed results table
        table = Table(title="Benchmark Test Results")
        table.add_column("Test Name", style="cyan", no_wrap=True)
        table.add_column("Status", style="green")
        table.add_column("Duration (s)", justify="right")
        table.add_column("Rows/sec", justify="right") 
        table.add_column("Memory (MB)", justify="right")
        table.add_column("Diff Ops", justify="right")
        
        test_results = results.get('results', [])
        for test_result in test_results:
            status_style = "green" if test_result.get('status') == 'SUCCESS' else "red"
            status_icon = "✓" if test_result.get('status') == 'SUCCESS' else "✗"
            
            perf_metrics = test_result.get('performance_metrics', {})
            
            table.add_row(
                test_result.get('test_name', 'Unknown'),
                f"[{status_style}]{status_icon} {test_result.get('status', 'UNKNOWN')}[/{status_style}]",
                f"{test_result.get('duration_seconds', 0):.2f}",
                f"{perf_metrics.get('rows_per_second', 0):.0f}",
                f"{test_result.get('peak_memory_mb', 0):.1f}",
                f"{test_result.get('diff_operations', 0)}"
            )
        
        console.print(table)
        
        # Overall summary
        successful_tests = results.get('successful_tests', 0)
        total_tests = results.get('total_tests', 0)
        
        if successful_tests == total_tests:
            console.print(f"[green]🎉 All {total_tests} benchmark tests completed successfully!")
        else:
            console.print(f"[yellow]⚠️  {successful_tests}/{total_tests} benchmark tests completed successfully")
    
    def download_results(self, job_id: str = None) -> str:
        """Download benchmark results from S3"""
        if not self.results_bucket:
            console.print("[red]No results bucket configured")
            return None
            
        try:
            if not job_id:
                # Find the latest benchmark results
                response = self.s3_client.list_objects_v2(
                    Bucket=self.results_bucket,
                    Prefix='benchmarks/',
                    Delimiter='/'
                )
                
                if 'CommonPrefixes' not in response:
                    console.print("[yellow]No benchmark results found")
                    return None
                    
                # Get the most recent job
                job_prefixes = [p['Prefix'] for p in response['CommonPrefixes']]
                job_prefixes.sort(reverse=True)
                job_id = job_prefixes[0].split('/')[-2]
            
            # Download CSV results
            csv_key = f"benchmarks/{job_id}/benchmark_{job_id}.csv"
            temp_file = f"/tmp/benchmark_{job_id}.csv"
            
            self.s3_client.download_file(self.results_bucket, csv_key, temp_file)
            console.print(f"[green]Downloaded results to: {temp_file}")
            
            return temp_file
            
        except Exception as e:
            console.print(f"[red]Failed to download results: {e}")
            return None
    
    def generate_graphs(self, csv_file: str):
        """Generate performance graphs using the existing dev/graph.py script"""
        try:
            console.print("[cyan]Generating performance graphs...")
            
            # Copy CSV to expected location for graph.py
            import shutil
            import os
            
            # Find the benchmark CSV filename pattern
            csv_filename = os.path.basename(csv_file)
            target_file = f"benchmark_{csv_filename.split('_', 1)[1]}"  # Remove duplicate benchmark_ prefix
            
            shutil.copy(csv_file, target_file)
            
            # Run the graph generation script
            result = subprocess.run(
                ['poetry', 'run', 'python3', 'dev/graph.py'],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                console.print("[green]✓ Performance graphs generated successfully")
                console.print(f"[blue]Check for generated graph files in the current directory")
            else:
                console.print(f"[yellow]Graph generation output: {result.stdout}")
                if result.stderr:
                    console.print(f"[red]Graph generation errors: {result.stderr}")
                    
        except Exception as e:
            console.print(f"[red]Failed to generate graphs: {e}")

def main():
    """Main orchestration function"""
    parser = argparse.ArgumentParser(description='Lambda Benchmark Orchestration')
    parser.add_argument('--dataset-size', choices=['1m', '25m'], default='1m',
                       help='Dataset size to use for benchmarks')
    parser.add_argument('--populate-only', action='store_true',
                       help='Only populate tables, do not run benchmarks')
    parser.add_argument('--benchmark-only', action='store_true', 
                       help='Only run benchmarks, skip table population')
    parser.add_argument('--test-filter', nargs='+',
                       help='Filter to specific test names')
    parser.add_argument('--stack-name', default='reladiff-serverless-dev',
                       help='CloudFormation stack name')
    parser.add_argument('--region', default='us-east-1',
                       help='AWS region')
    parser.add_argument('--generate-graphs', action='store_true',
                       help='Generate performance graphs after benchmarks')
    parser.add_argument('--download-results', 
                       help='Download results for a specific job ID')
    
    args = parser.parse_args()
    
    try:
        orchestrator = LambdaBenchmarkOrchestrator(args.stack_name, args.region)
        
        if args.download_results:
            csv_file = orchestrator.download_results(args.download_results)
            if csv_file and args.generate_graphs:
                orchestrator.generate_graphs(csv_file)
            return
        
        # Step 1: Populate tables (unless benchmark-only)
        if not args.benchmark_only:
            populate_results = orchestrator.populate_tables(args.dataset_size)
            
            # Check if population was successful for at least some databases
            successful_populations = sum(1 for r in populate_results.values() if r.get('status') == 'SUCCESS')
            if successful_populations == 0:
                console.print("[red]❌ No databases were populated successfully. Exiting.")
                sys.exit(1)
        
        if args.populate_only:
            console.print("[green]✓ Table population completed")
            return
        
        # Step 2: Execute benchmarks
        benchmark_results = orchestrator.execute_benchmarks(args.dataset_size, args.test_filter)
        
        # Step 3: Display results
        orchestrator.display_benchmark_results(benchmark_results)
        
        # Step 4: Download and generate graphs if requested
        if args.generate_graphs and benchmark_results.get('status') == 'COMPLETED':
            job_id = benchmark_results.get('job_id') 
            if job_id:
                csv_file = orchestrator.download_results(job_id)
                if csv_file:
                    orchestrator.generate_graphs(csv_file)
        
        # Exit with appropriate code
        if benchmark_results.get('status') == 'COMPLETED':
            successful_tests = benchmark_results.get('successful_tests', 0)
            total_tests = benchmark_results.get('total_tests', 0)
            
            if successful_tests == total_tests:
                console.print(f"\n[green]🎉 Lambda benchmark execution completed successfully!")
                sys.exit(0)
            else:
                console.print(f"\n[yellow]⚠️  Benchmark execution completed with {total_tests - successful_tests} failures")
                sys.exit(1)
        else:
            console.print(f"\n[red]❌ Benchmark execution failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Benchmark execution interrupted by user")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]❌ Benchmark orchestration failed: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()