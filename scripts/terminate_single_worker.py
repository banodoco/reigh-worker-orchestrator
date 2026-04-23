#!/usr/bin/env python3
"""
Terminate Single Worker and Reset Tasks Script

This script terminates a specific worker and resets its tasks back to Queued status.
"""

import os
import sys
import asyncio
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any
import argparse

# Add the project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from gpu_orchestrator.config import OrchestratorConfig
from gpu_orchestrator.database import DatabaseClient
from gpu_orchestrator.worker_spawner import create_worker_spawner

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def get_worker_tasks(db: DatabaseClient, worker_id: str) -> List[Dict[str, Any]]:
    """Get all tasks currently assigned to a worker."""
    try:
        result = db.supabase.table('tasks').select('*').eq('worker_id', worker_id).eq('status', 'In Progress').execute()
        return result.data or []
    except Exception as e:
        logger.error(f"Failed to get worker tasks: {e}")
        return []


async def reset_tasks(db: DatabaseClient, tasks: List[Dict[str, Any]]) -> int:
    """Reset tasks back to Queued status."""
    if not tasks:
        logger.info("No tasks to reset")
        return 0
    
    reset_count = 0
    for task in tasks:
        try:
            result = db.supabase.table('tasks').update({
                'status': 'Queued',
                'worker_id': None,
                'generation_started_at': None,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }).eq('id', task['id']).execute()
            
            if result.data:
                logger.info(f"✅ Reset task {task['id']} to Queued")
                reset_count += 1
            else:
                logger.error(f"❌ Failed to reset task {task['id']}")
        except Exception as e:
            logger.error(f"❌ Error resetting task {task['id']}: {e}")
    
    return reset_count


async def terminate_worker(worker_id: str, skip_tasks: bool = False):
    """Terminate a worker and optionally reset its tasks."""
    try:
        db = DatabaseClient()
        runpod = create_worker_spawner(OrchestratorConfig.from_env(), db)
        
        logger.info(f"\n🔍 Looking up worker: {worker_id}")
        
        # Get worker info
        worker_result = db.supabase.table('workers').select('*').eq('id', worker_id).execute()
        
        if not worker_result.data or len(worker_result.data) == 0:
            logger.error(f"❌ Worker {worker_id} not found in database")
            return False
        
        worker = worker_result.data[0]
        metadata = worker.get('metadata', {})
        runpod_id = metadata.get('runpod_id')
        
        logger.info(f"   Status: {worker['status']}")
        logger.info(f"   Instance Type: {worker.get('instance_type', 'unknown')}")
        logger.info(f"   RunPod ID: {runpod_id or 'None'}")
        logger.info(f"   Last Heartbeat: {worker.get('last_heartbeat', 'Never')}")
        
        # Get tasks assigned to this worker
        if not skip_tasks:
            logger.info(f"\n📋 Getting tasks for worker {worker_id}...")
            tasks = await get_worker_tasks(db, worker_id)
            logger.info(f"   Found {len(tasks)} active tasks")
            
            if tasks:
                logger.info(f"\n🔄 Resetting {len(tasks)} tasks to Queued status...")
                reset_count = await reset_tasks(db, tasks)
                logger.info(f"   Successfully reset {reset_count}/{len(tasks)} tasks")
        
        # Terminate RunPod instance
        if runpod_id:
            logger.info(f"\n🛑 Terminating RunPod instance {runpod_id}...")
            success = await runpod.terminate_worker(runpod_id)
            
            if success:
                logger.info(f"✅ RunPod instance terminated successfully")
            else:
                logger.warning(f"⚠️ Failed to terminate RunPod instance (may already be terminated)")
        else:
            logger.info(f"\n⚠️ No RunPod ID found, skipping cloud termination")
        
        # Update database status
        logger.info(f"\n📝 Updating worker status to 'terminated'...")
        termination_metadata = {
            **metadata,
            'terminated_at': datetime.now(timezone.utc).isoformat(),
            'terminated_by': 'manual_script'
        }
        
        result = db.supabase.table('workers').update({
            'status': 'terminated',
            'metadata': termination_metadata
        }).eq('id', worker_id).execute()
        
        if result.data:
            logger.info(f"✅ Worker status updated in database")
        else:
            logger.error(f"❌ Failed to update worker status in database")
        
        logger.info(f"\n🎉 Worker {worker_id} terminated successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error terminating worker: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Terminate a specific worker and reset its tasks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Terminate worker and reset its tasks
  python terminate_single_worker.py gpu-20251005_223523-a3f40738
  
  # Terminate worker without resetting tasks
  python terminate_single_worker.py gpu-20251005_223523-a3f40738 --skip-tasks
        """
    )
    
    parser.add_argument('worker_id', help='The worker ID to terminate')
    parser.add_argument('--skip-tasks', action='store_true',
                       help='Skip resetting tasks (only terminate worker)')
    
    args = parser.parse_args()
    
    try:
        success = await terminate_worker(args.worker_id, skip_tasks=args.skip_tasks)
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        logger.info("\n❌ Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
