#!/usr/bin/env python3
"""
Manual GPU spawning script for testing the orchestrator.
Allows spawning and terminating workers manually for debugging.
"""

import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gpu_orchestrator.config import OrchestratorConfig
from gpu_orchestrator.database import DatabaseClient
from gpu_orchestrator.worker_spawner import create_worker_spawner

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def spawn_gpu_worker(worker_id: str = None, register_in_db: bool = True):
    """
    Spawn a GPU worker for testing.
    
    Args:
        worker_id: Specific worker ID, or None to generate one
        register_in_db: Whether to register the worker in the database
    """
    try:
        # Initialize clients
        db = DatabaseClient() if register_in_db else None
        runpod = create_worker_spawner(OrchestratorConfig.from_env(), db)
        
        # Generate worker ID if not provided
        if not worker_id:
            worker_id = f"manual-{runpod.generate_worker_id()}"
        
        print(f"\n🚀 Spawning GPU worker: {worker_id}")
        print("-" * 50)
        
        # Create database record first (if requested)
        if register_in_db:
            print("1. Creating database record...")
            success = await db.create_worker_record(worker_id, runpod.gpu_type)
            if not success:
                print(f"❌ Failed to create database record for {worker_id}")
                return None
            print(f"✅ Database record created")
        
        # Spawn the Runpod instance
        print("2. Creating Runpod instance...")
        result = await runpod.spawn_worker(worker_id)
        
        if not result:
            print(f"❌ Failed to spawn Runpod instance")
            if register_in_db:
                await db.mark_worker_error(worker_id, 'Failed to spawn Runpod instance')
            return None
        
        pod_id = result["runpod_id"]
        print(f"✅ Runpod instance created: {pod_id}")
        
        # Update database with Runpod ID
        if register_in_db:
            print("3. Updating database with Runpod ID...")
            await db.update_worker_status(worker_id, 'spawning', {'runpod_id': pod_id})
            print(f"✅ Database updated")
        
        print(f"\n🎉 Worker {worker_id} spawned successfully!")
        print(f"   Runpod ID: {pod_id}")
        print(f"   GPU Type: {runpod.gpu_type}")
        print(f"   Status: {result['status']}")
        
        # Show SSH connection details if available
        if "ssh_details" in result:
            ssh_details = result["ssh_details"]
            print(f"   SSH: {ssh_details['ip']}:{ssh_details['port']}")
            
            # Test SSH connection if possible
            print("\n4. Testing SSH connection...")
            test_result = await runpod.execute_command_on_worker(
                pod_id,
                "whoami && pwd && nvidia-smi --query-gpu=name --format=csv,noheader,nounits",
                timeout=30,
            )
            
            if test_result:
                exit_code, stdout, stderr = test_result
                if exit_code == 0:
                    print("✅ SSH connection successful!")
                    lines = stdout.strip().split('\n')
                    if len(lines) >= 3:
                        print(f"   User: {lines[0]}")
                        print(f"   Directory: {lines[1]}")
                        print(f"   GPU: {lines[2]}")
                else:
                    print(f"⚠️  SSH command failed (exit code: {exit_code})")
                    if stderr:
                        print(f"   Error: {stderr.strip()}")
            else:
                print("⚠️  Could not establish SSH connection")
        else:
            print("⚠️  No SSH details available")
        
        return {
            'worker_id': worker_id,
            'pod_id': pod_id,
            'status': 'spawned',
            'ssh_details': result.get('ssh_details')
        }
        
    except Exception as e:
        logger.error(f"Error spawning worker: {e}")
        return None

async def list_workers():
    """List all active workers."""
    try:
        db = DatabaseClient()
        workers = await db.get_workers(['spawning', 'active', 'terminating'])
        
        print(f"\n📋 Active Workers ({len(workers)} total):")
        print("-" * 80)
        print(f"{'Worker ID':<20} {'Status':<12} {'GPU Type':<15} {'Runpod ID':<15} {'Age'}")
        print("-" * 80)
        
        for worker in workers:
            worker_id = worker['id']
            status = worker['status']
            instance_type = worker.get('instance_type', 'N/A')
            runpod_id = worker.get('metadata', {}).get('runpod_id', 'N/A')
            age = worker.get('created_at', 'N/A')
            
            print(f"{worker_id:<20} {status:<12} {instance_type:<15} {runpod_id:<15} {age}")
        
        if not workers:
            print("No active workers found.")
            
    except Exception as e:
        logger.error(f"Error listing workers: {e}")

async def terminate_worker(worker_id: str = None, pod_id: str = None):
    """
    Terminate a worker by worker ID or pod ID.
    
    Args:
        worker_id: Database worker ID
        pod_id: Runpod pod ID
    """
    try:
        db = DatabaseClient()
        runpod = create_worker_spawner(OrchestratorConfig.from_env(), db)
        
        # If worker_id provided, look up the pod_id
        if worker_id and not pod_id:
            worker = await db.get_worker_by_id(worker_id)
            if not worker:
                print(f"❌ Worker {worker_id} not found in database")
                return False
            
            pod_id = worker.get('metadata', {}).get('runpod_id')
            if not pod_id:
                print(f"❌ No Runpod ID found for worker {worker_id}")
                return False
        
        if not pod_id:
            print("❌ Must provide either worker_id or pod_id")
            return False
        
        print(f"\n🛑 Terminating worker...")
        print(f"   Worker ID: {worker_id or 'N/A'}")
        print(f"   Pod ID: {pod_id}")
        print("-" * 50)
        
        # Terminate the Runpod instance
        print("1. Terminating Runpod instance...")
        success = await runpod.terminate_worker(pod_id)
        
        if success:
            print(f"✅ Runpod instance terminated")
        else:
            print(f"❌ Failed to terminate Runpod instance")
        
        # Update database if worker_id provided
        if worker_id:
            print("2. Updating database...")
            await db.update_worker_status(worker_id, 'terminated')
            print(f"✅ Database updated")
        
        print(f"\n🎉 Worker terminated!")
        return success
        
    except Exception as e:
        logger.error(f"Error terminating worker: {e}")
        return False

async def get_worker_status(worker_id: str):
    """Get detailed status of a worker."""
    try:
        db = DatabaseClient()
        runpod = create_worker_spawner(OrchestratorConfig.from_env(), db)
        
        # Get database record
        worker = await db.get_worker_by_id(worker_id)
        if not worker:
            print(f"❌ Worker {worker_id} not found in database")
            return
        
        print(f"\n📊 Worker Status: {worker_id}")
        print("-" * 50)
        print(f"Database Status: {worker['status']}")
        print(f"GPU Type: {worker.get('instance_type', 'N/A')}")
        print(f"Created: {worker.get('created_at', 'N/A')}")
        print(f"Last Heartbeat: {worker.get('last_heartbeat', 'N/A')}")
        
        # Get Runpod status if available
        runpod_id = worker.get('metadata', {}).get('runpod_id')
        if runpod_id:
            print(f"Runpod ID: {runpod_id}")
            
            status = await runpod.get_pod_status(runpod_id)
            if status:
                print(f"Runpod Status: {status.get('desired_status', 'N/A')}")
                print(f"IP: {status.get('ip', 'N/A')}")
                ports = status.get('ports', [])
                for port in ports:
                    if port.get('privatePort') == 22:
                        print(f"SSH: {status.get('ip')}:{port.get('publicPort')}")
            else:
                print("❌ Could not get Runpod status")
        else:
            print("No Runpod ID found")
            
    except Exception as e:
        logger.error(f"Error getting worker status: {e}")

def main():
    """Main CLI interface."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Manual GPU worker management")
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Spawn command
    spawn_parser = subparsers.add_parser('spawn', help='Spawn a new GPU worker')
    spawn_parser.add_argument('--worker-id', help='Specific worker ID (optional)')
    spawn_parser.add_argument('--no-db', action='store_true', help='Skip database registration')
    
    # List command
    subparsers.add_parser('list', help='List all workers')
    
    # Terminate command
    term_parser = subparsers.add_parser('terminate', help='Terminate a worker')
    term_parser.add_argument('--worker-id', help='Worker ID to terminate')
    term_parser.add_argument('--pod-id', help='Runpod pod ID to terminate')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Get worker status')
    status_parser.add_argument('worker_id', help='Worker ID to check')
    
    args = parser.parse_args()
    
    if args.command == 'spawn':
        result = asyncio.run(spawn_gpu_worker(
            worker_id=args.worker_id,
            register_in_db=not args.no_db
        ))
        if result:
            print(f"\nSuccess! Worker details:")
            print(f"  Worker ID: {result['worker_id']}")
            print(f"  Pod ID: {result['pod_id']}")
        else:
            sys.exit(1)
            
    elif args.command == 'list':
        asyncio.run(list_workers())
        
    elif args.command == 'terminate':
        if not args.worker_id and not args.pod_id:
            print("Error: Must provide --worker-id or --pod-id")
            sys.exit(1)
        
        success = asyncio.run(terminate_worker(
            worker_id=args.worker_id,
            pod_id=args.pod_id
        ))
        if not success:
            sys.exit(1)
            
    elif args.command == 'status':
        asyncio.run(get_worker_status(args.worker_id))
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main() 
