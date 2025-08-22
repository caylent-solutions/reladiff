#!/usr/bin/env python3
"""
Benchmark Runner Script

This script provides utilities for running benchmarks locally or triggering
GitHub Actions workflows. It can parse benchmark configuration and provide
a unified interface for benchmark execution.
"""

import argparse
import json
import os
import sys
import yaml
import subprocess
from typing import Dict, List, Optional
from pathlib import Path

class BenchmarkRunner:
    def __init__(self, config_path: str = ".github/benchmark-config.yml"):
        self.config_path = Path(config_path)
        self.config = self._load_config()
        
    def _load_config(self) -> Dict:
        """Load benchmark configuration from YAML file"""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"Configuration file not found: {self.config_path}")
            return {}
        except yaml.YAMLError as e:
            print(f"Error parsing configuration file: {e}")
            return {}
    
    def list_scenarios(self) -> None:
        """List available benchmark scenarios"""
        scenarios = self.config.get('scenarios', {})
        
        print("Available benchmark scenarios:")
        print("=" * 50)
        
        for name, config in scenarios.items():
            print(f"\n{name}:")
            print(f"  Description: {config.get('description', 'No description')}")
            print(f"  Dataset: {config.get('dataset_size', 'Unknown')}")
            print(f"  Timeout: {config.get('timeout_minutes', 'Unknown')} minutes")
            
            test_filter = config.get('test_filter', [])
            if test_filter:
                print(f"  Tests: {', '.join(test_filter)}")
            else:
                print("  Tests: All tests")
    
    def run_local_benchmark(self, scenario: str, **kwargs) -> None:
        """Run benchmark locally using the Lambda orchestrator"""
        scenarios = self.config.get('scenarios', {})
        
        if scenario not in scenarios:
            print(f"Unknown scenario: {scenario}")
            print("Available scenarios:", list(scenarios.keys()))
            return
            
        scenario_config = scenarios[scenario]
        defaults = self.config.get('defaults', {})
        
        # Build command arguments
        cmd = ['python', 'benchmark_lambda.py']
        
        # Dataset size
        dataset_size = kwargs.get('dataset_size') or scenario_config.get('dataset_size')
        if dataset_size:
            cmd.extend(['--dataset-size', dataset_size])
            
        # Stack name
        stack_name = kwargs.get('stack_name') or defaults.get('stack_name')
        if stack_name:
            cmd.extend(['--stack-name', stack_name])
            
        # Region
        region = kwargs.get('region') or defaults.get('region')
        if region:
            cmd.extend(['--region', region])
            
        # Test filter
        test_filter = kwargs.get('test_filter') or scenario_config.get('test_filter', [])
        if test_filter:
            for test in test_filter:
                cmd.extend(['--test-filter', test])
                
        # Generate graphs
        if kwargs.get('generate_graphs') or defaults.get('generate_graphs'):
            cmd.append('--generate-graphs')
            
        print(f"Running benchmark scenario: {scenario}")
        print(f"Command: {' '.join(cmd)}")
        
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Benchmark failed with exit code {e.returncode}")
            sys.exit(e.returncode)
    
    def trigger_github_action(self, scenario: str, **kwargs) -> None:
        """Trigger GitHub Actions workflow for benchmark execution"""
        scenarios = self.config.get('scenarios', {})
        
        if scenario not in scenarios:
            print(f"Unknown scenario: {scenario}")
            return
            
        scenario_config = scenarios[scenario]
        defaults = self.config.get('defaults', {})
        
        # Prepare workflow inputs
        inputs = {
            'dataset_size': kwargs.get('dataset_size') or scenario_config.get('dataset_size'),
            'generate_graphs': str(kwargs.get('generate_graphs', defaults.get('generate_graphs', True))).lower(),
            'stack_name': kwargs.get('stack_name') or defaults.get('stack_name')
        }
        
        # Handle test filter
        test_filter = kwargs.get('test_filter') or scenario_config.get('test_filter', [])
        if test_filter:
            inputs['test_filter'] = ','.join(test_filter)
            
        # Build GitHub CLI command
        cmd = ['gh', 'workflow', 'run', 'lambda-benchmarks.yml']
        
        for key, value in inputs.items():
            if value:
                cmd.extend(['-f', f'{key}={value}'])
        
        print(f"Triggering GitHub Actions workflow for scenario: {scenario}")
        print(f"Command: {' '.join(cmd)}")
        
        try:
            subprocess.run(cmd, check=True)
            print("Workflow triggered successfully!")
            print("Check the Actions tab in your GitHub repository for progress.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to trigger workflow: {e}")
            print("Make sure you have the GitHub CLI installed and authenticated.")
    
    def validate_config(self) -> bool:
        """Validate the benchmark configuration file"""
        if not self.config:
            print("❌ Configuration file is empty or invalid")
            return False
            
        required_sections = ['scenarios', 'defaults']
        for section in required_sections:
            if section not in self.config:
                print(f"❌ Missing required section: {section}")
                return False
        
        scenarios = self.config.get('scenarios', {})
        if not scenarios:
            print("❌ No scenarios defined")
            return False
            
        # Validate each scenario
        for name, scenario in scenarios.items():
            required_fields = ['dataset_size', 'description']
            for field in required_fields:
                if field not in scenario:
                    print(f"❌ Scenario '{name}' missing required field: {field}")
                    return False
                    
        print("✅ Configuration file is valid")
        return True
    
    def generate_sample_config(self, output_path: str) -> None:
        """Generate a sample configuration file"""
        sample_config = {
            'scenarios': {
                'quick_test': {
                    'dataset_size': '1m',
                    'test_filter': ['postgresql_int_mysql_int'],
                    'timeout_minutes': 30,
                    'description': 'Quick test with PostgreSQL to MySQL'
                },
                'full_test': {
                    'dataset_size': '1m',
                    'test_filter': [],
                    'timeout_minutes': 60,
                    'description': 'Complete test suite'
                }
            },
            'defaults': {
                'stack_name': 'reladiff-serverless-dev',
                'region': 'us-east-1',
                'generate_graphs': True
            }
        }
        
        with open(output_path, 'w') as f:
            yaml.dump(sample_config, f, default_flow_style=False, indent=2)
            
        print(f"Sample configuration written to: {output_path}")

def main():
    parser = argparse.ArgumentParser(description='Benchmark Runner for Reladiff')
    parser.add_argument('--config', default='.github/benchmark-config.yml',
                       help='Path to benchmark configuration file')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # List scenarios command
    list_parser = subparsers.add_parser('list', help='List available benchmark scenarios')
    
    # Run local benchmark command
    local_parser = subparsers.add_parser('run', help='Run benchmark locally')
    local_parser.add_argument('scenario', help='Scenario name to run')
    local_parser.add_argument('--dataset-size', choices=['1m', '25m'], help='Override dataset size')
    local_parser.add_argument('--stack-name', help='Override stack name')
    local_parser.add_argument('--region', help='Override AWS region')
    local_parser.add_argument('--test-filter', nargs='+', help='Override test filter')
    local_parser.add_argument('--generate-graphs', action='store_true', help='Generate performance graphs')
    
    # Trigger GitHub Actions command
    github_parser = subparsers.add_parser('trigger', help='Trigger GitHub Actions workflow')
    github_parser.add_argument('scenario', help='Scenario name to run')
    github_parser.add_argument('--dataset-size', choices=['1m', '25m'], help='Override dataset size')
    github_parser.add_argument('--stack-name', help='Override stack name')
    github_parser.add_argument('--test-filter', nargs='+', help='Override test filter')
    github_parser.add_argument('--generate-graphs', action='store_true', help='Generate performance graphs')
    
    # Validate config command
    validate_parser = subparsers.add_parser('validate', help='Validate configuration file')
    
    # Generate sample config command
    sample_parser = subparsers.add_parser('sample', help='Generate sample configuration file')
    sample_parser.add_argument('output', help='Output file path')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
        
    runner = BenchmarkRunner(args.config)
    
    if args.command == 'list':
        runner.list_scenarios()
        
    elif args.command == 'run':
        kwargs = {k: v for k, v in vars(args).items() if v is not None and k not in ['command', 'scenario', 'config']}
        runner.run_local_benchmark(args.scenario, **kwargs)
        
    elif args.command == 'trigger':
        kwargs = {k: v for k, v in vars(args).items() if v is not None and k not in ['command', 'scenario', 'config']}
        runner.trigger_github_action(args.scenario, **kwargs)
        
    elif args.command == 'validate':
        valid = runner.validate_config()
        sys.exit(0 if valid else 1)
        
    elif args.command == 'sample':
        runner.generate_sample_config(args.output)

if __name__ == '__main__':
    main()