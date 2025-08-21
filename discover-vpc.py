#!/usr/bin/env python3
"""
Script to discover VPC configuration for Lambda functions to access RDS databases.
This script finds the VPC, subnets, and security groups needed for the Lambda functions
to connect to the specified RDS instances.
"""

import boto3
import json
import sys
from typing import Dict, List, Optional


def get_rds_vpc_config(rds_endpoint: str) -> Optional[Dict]:
    """Get VPC configuration for an RDS instance."""
    try:
        rds = boto3.client('rds')
        
        # Extract identifier from endpoint
        # e.g., sql-polyglot-main-source-sql-server-database.cz8cq8icqxtd.us-east-1.rds.amazonaws.com
        identifier = rds_endpoint.split('.')[0]
        
        print(f"Looking for RDS instance: {identifier}")
        
        # Try to find the DB instance
        try:
            response = rds.describe_db_instances(DBInstanceIdentifier=identifier)
            db_instance = response['DBInstances'][0]
        except rds.exceptions.DBInstanceNotFoundFault:
            # Try DB clusters (for Aurora)
            try:
                response = rds.describe_db_clusters(DBClusterIdentifier=identifier)
                db_cluster = response['DBClusters'][0]
                vpc_id = db_cluster['VpcId']
                security_groups = [sg['VpcSecurityGroupId'] for sg in db_cluster['VpcSecurityGroups']]
                subnets = [subnet['SubnetIdentifier'] for subnet in db_cluster['DBSubnetGroup']['Subnets']]
            except rds.exceptions.DBClusterNotFoundFault:
                print(f"❌ Could not find RDS instance or cluster: {identifier}")
                return None
        else:
            vpc_id = db_instance['VpcId']
            security_groups = [sg['VpcSecurityGroupId'] for sg in db_instance['VpcSecurityGroups']]
            subnets = [subnet['SubnetIdentifier'] for subnet in db_instance['DBSubnetGroup']['Subnets']]
        
        return {
            'vpc_id': vpc_id,
            'security_groups': security_groups,
            'subnets': subnets
        }
        
    except Exception as e:
        print(f"❌ Error getting RDS VPC config: {e}")
        return None


def get_lambda_security_group(vpc_id: str, rds_security_groups: List[str]) -> Optional[str]:
    """Create or find a security group for Lambda functions."""
    try:
        ec2 = boto3.client('ec2')
        
        # Check if a reladiff Lambda security group already exists
        sg_name = f"reladiff-lambda-sg"
        
        try:
            response = ec2.describe_security_groups(
                Filters=[
                    {'Name': 'group-name', 'Values': [sg_name]},
                    {'Name': 'vpc-id', 'Values': [vpc_id]}
                ]
            )
            
            if response['SecurityGroups']:
                sg_id = response['SecurityGroups'][0]['GroupId']
                print(f"✅ Found existing Lambda security group: {sg_id}")
                return sg_id
                
        except ec2.exceptions.ClientError:
            pass
        
        # Create new security group
        print(f"Creating Lambda security group in VPC {vpc_id}...")
        
        response = ec2.create_security_group(
            GroupName=sg_name,
            Description='Security group for Reladiff Lambda functions to access RDS',
            VpcId=vpc_id
        )
        
        lambda_sg_id = response['GroupId']
        print(f"✅ Created Lambda security group: {lambda_sg_id}")
        
        # Add rules to allow outbound access to RDS security groups
        for rds_sg_id in rds_security_groups:
            try:
                # Allow outbound HTTPS (for AWS services)
                ec2.authorize_security_group_egress(
                    GroupId=lambda_sg_id,
                    IpPermissions=[
                        {
                            'IpProtocol': 'tcp',
                            'FromPort': 443,
                            'ToPort': 443,
                            'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
                        }
                    ]
                )
                
                # Allow outbound access to RDS (ports 1433, 5432, 3306)
                for port in [1433, 5432, 3306]:  # MSSQL, PostgreSQL, MySQL
                    ec2.authorize_security_group_egress(
                        GroupId=lambda_sg_id,
                        IpPermissions=[
                            {
                                'IpProtocol': 'tcp',
                                'FromPort': port,
                                'ToPort': port,
                                'UserIdGroupPairs': [{'GroupId': rds_sg_id}]
                            }
                        ]
                    )
                
                print(f"✅ Added outbound rules to Lambda SG for RDS SG: {rds_sg_id}")
                
            except ec2.exceptions.ClientError as e:
                if 'Duplicate' not in str(e):
                    print(f"⚠️  Could not add outbound rule: {e}")
        
        return lambda_sg_id
        
    except Exception as e:
        print(f"❌ Error creating Lambda security group: {e}")
        return None


def get_private_subnets(vpc_id: str, existing_subnets: List[str]) -> List[str]:
    """Get private subnets for Lambda functions."""
    try:
        ec2 = boto3.client('ec2')
        
        # Filter for private subnets (no internet gateway route)
        response = ec2.describe_subnets(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'state', 'Values': ['available']}
            ]
        )
        
        private_subnets = []
        
        for subnet in response['Subnets']:
            subnet_id = subnet['SubnetId']
            
            # Check route tables to determine if subnet is private
            rt_response = ec2.describe_route_tables(
                Filters=[
                    {'Name': 'association.subnet-id', 'Values': [subnet_id]}
                ]
            )
            
            is_private = True
            for rt in rt_response['RouteTables']:
                for route in rt['Routes']:
                    if route.get('GatewayId', '').startswith('igw-'):
                        is_private = False
                        break
                if not is_private:
                    break
            
            if is_private:
                private_subnets.append(subnet_id)
        
        # If no private subnets found, use existing RDS subnets
        if not private_subnets:
            print("⚠️  No private subnets found, using RDS subnets")
            return existing_subnets
        
        print(f"✅ Found {len(private_subnets)} private subnets")
        return private_subnets[:3]  # Limit to 3 for cost optimization
        
    except Exception as e:
        print(f"❌ Error getting private subnets: {e}")
        return existing_subnets


def main():
    """Main function to discover VPC configuration."""
    
    # RDS endpoints from the TOML config
    rds_endpoints = [
        "sql-polyglot-main-source-sql-server-database.cz8cq8icqxtd.us-east-1.rds.amazonaws.com",
        "sql-polyglot-main-target-database.cz8cq8icqxtd.us-east-1.rds.amazonaws.com"
    ]
    
    print("🔍 Discovering VPC configuration for RDS databases...")
    print("=" * 60)
    
    all_vpc_configs = []
    
    for endpoint in rds_endpoints:
        print(f"\n📍 Analyzing: {endpoint}")
        config = get_rds_vpc_config(endpoint)
        if config:
            all_vpc_configs.append(config)
            print(f"   VPC ID: {config['vpc_id']}")
            print(f"   Security Groups: {config['security_groups']}")
            print(f"   Subnets: {len(config['subnets'])} found")
        else:
            print(f"   ❌ Could not get configuration")
    
    if not all_vpc_configs:
        print("\n❌ No VPC configurations found. Cannot proceed.")
        sys.exit(1)
    
    # Check if all databases are in the same VPC
    vpc_ids = set(config['vpc_id'] for config in all_vpc_configs)
    if len(vpc_ids) > 1:
        print(f"\n❌ Databases are in different VPCs: {vpc_ids}")
        print("   Lambda functions can only be deployed in one VPC.")
        sys.exit(1)
    
    vpc_id = vpc_ids.pop()
    
    # Collect all security groups and subnets
    all_security_groups = set()
    all_subnets = set()
    
    for config in all_vpc_configs:
        all_security_groups.update(config['security_groups'])
        all_subnets.update(config['subnets'])
    
    print(f"\n✅ All databases are in VPC: {vpc_id}")
    print(f"   RDS Security Groups: {list(all_security_groups)}")
    print(f"   RDS Subnets: {len(all_subnets)} total")
    
    # Create Lambda security group
    lambda_sg = get_lambda_security_group(vpc_id, list(all_security_groups))
    if not lambda_sg:
        print("❌ Could not create Lambda security group")
        sys.exit(1)
    
    # Get appropriate subnets for Lambda
    lambda_subnets = get_private_subnets(vpc_id, list(all_subnets))
    
    # Output configuration
    print("\n🎯 Lambda VPC Configuration:")
    print("=" * 40)
    print(f"VPC ID: {vpc_id}")
    print(f"Security Groups: [{lambda_sg}]")
    print(f"Subnets: {lambda_subnets}")
    
    print("\n📋 Deployment Command:")
    print("=" * 40)
    
    subnet_list = ",".join(lambda_subnets)
    
    deployment_command = f"""./deploy.sh dev \\
  --email your-email@example.com \\
  --vpc-id {vpc_id} \\
  --subnet-ids {subnet_list} \\
  --security-group-ids {lambda_sg}"""
    
    print(deployment_command)
    
    print("\n💾 Configuration saved to vpc-config.json")
    
    # Save configuration to file
    config_data = {
        "vpc_id": vpc_id,
        "subnet_ids": lambda_subnets,
        "security_group_ids": [lambda_sg],
        "deployment_command": deployment_command.replace(" \\\n  ", " ")
    }
    
    with open("vpc-config.json", "w") as f:
        json.dump(config_data, f, indent=2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Aborted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)