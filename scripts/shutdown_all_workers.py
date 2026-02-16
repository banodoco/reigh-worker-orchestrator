#!/usr/bin/env python3
"""
Shutdown All Workers Script

This script provides a standardized way to:
1. Shut down all active workers
2. Mark them as terminated in the database
3. Reset all processing tasks back to queued status
4. Clear worker assignments and timestamps

This is useful for maintenance, emergency shutdowns, or system resets.
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

from gpu_orchestrator.database import DatabaseClient
from gpu_orchestrator.runpod_client import create_runpod_client

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class WorkerShutdownManager:
    """Manages the shutdown process for all workers."""
    
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.db = DatabaseClient()
        self.runpod = create_runpod_client()
        
        if dry_run:
            logger.info("🔍 DRY RUN MODE - No actual changes will be made")
    
    async def get_all_workers(self) -> List[Dict[str, Any]]:
        """Get all workers regardless of status."""
        try:
            # Get all workers from database
            result = self.db.supabase.table('workers').select('*').execute()
            
            workers = []
            for worker in (result.data or []):
                metadata = worker.get('metadata') or {}
                orchestrator_status = metadata.get('orchestrator_status', worker['status'])
                
                mapped_worker = {
                    'id': worker['id'],
                    'instance_type': worker['instance_type'],
                    'status': orchestrator_status,
                    'created_at': worker['created_at'],
                    'last_heartbeat': worker.get('last_heartbeat'),
                    'metadata': metadata
                }
                workers.append(mapped_worker)
            
            return workers
            
        except Exception as e:
            logger.error(f"Failed to get workers: {e}")
            return []
    
    async def get_all_processing_tasks(self) -> List[Dict[str, Any]]:
        """Get all tasks that are currently being processed."""
        try:
            result = self.db.supabase.table('tasks').select('*').eq('status', 'In Progress').execute()
            
            tasks = []
            for task in (result.data or []):
                mapped_task = {
                    'id': task['id'],
                    'status': task['status'],
                    'worker_id': task.get('worker_id'),
                    'generation_started_at': task.get('generation_started_at'),
                    'task_type': task.get('task_type', 'unknown'),
                    'created_at': task['created_at']
                }
                tasks.append(mapped_task)
            
            return tasks
            
        except Exception as e:
            logger.error(f"Failed to get processing tasks: {e}")
            return []
    
    async def terminate_runpod_worker(self, worker: Dict[str, Any]) -> bool:
        """Terminate a worker's RunPod instance."""
        runpod_id = worker.get('metadata', {}).get('runpod_id')
        
        if not runpod_id:
            logger.warning(f"Worker {worker['id']} has no RunPod ID, skipping RunPod termination")
            return True  # Consider it successful since there's nothing to terminate
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Would terminate RunPod instance {runpod_id} for worker {worker['id']}")
            return True
        
        try:
            logger.info(f"Terminating RunPod instance {runpod_id} for worker {worker['id']}")
            success = self.runpod.terminate_worker(runpod_id)
            
            if success:
                logger.info(f"✅ Successfully terminated RunPod instance {runpod_id}")
            else:
                logger.warning(f"⚠️ Failed to terminate RunPod instance {runpod_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Error terminating RunPod instance {runpod_id}: {e}")
            return False
    
    async def mark_worker_terminated(self, worker_id: str) -> bool:
        """Mark a worker as terminated in the database."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would mark worker {worker_id} as terminated")
            return True
        
        try:
            termination_metadata = {
                'terminated_at': datetime.now(timezone.utc).isoformat(),
                'terminated_by': 'shutdown_script'
            }
            
            success = await self.db.update_worker_status(worker_id, 'terminated', termination_metadata)
            
            if success:
                logger.info(f"✅ Marked worker {worker_id} as terminated")
            else:
                logger.error(f"❌ Failed to mark worker {worker_id} as terminated")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Error marking worker {worker_id} as terminated: {e}")
            return False
    
    async def reset_processing_tasks(self, tasks: List[Dict[str, Any]]) -> int:
        """Reset all processing tasks back to queued status."""
        if not tasks:
            logger.info("No processing tasks to reset")
            return 0
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Would reset {len(tasks)} processing tasks to queued status")
            return len(tasks)
        
        reset_count = 0
        
        for task in tasks:
            try:
                # Reset task to queued status and clear worker assignment
                result = self.db.supabase.table('tasks').update({
                    'status': 'Queued',
                    'worker_id': None,
                    'generation_started_at': None,
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }).eq('id', task['id']).execute()
                
                if result.data:
                    logger.info(f"✅ Reset task {task['id']} to queued status")
                    reset_count += 1
                else:
                    logger.error(f"❌ Failed to reset task {task['id']}")
                    
            except Exception as e:
                logger.error(f"❌ Error resetting task {task['id']}: {e}")
        
        logger.info(f"Successfully reset {reset_count} out of {len(tasks)} tasks")
        return reset_count
    
    async def shutdown_all_workers(self) -> Dict[str, Any]:
        """Execute the complete shutdown process."""
        start_time = datetime.now(timezone.utc)
        
        logger.info("🛑 Starting complete worker shutdown process...")
        logger.info("=" * 60)
        
        # Step 1: Get current state
        logger.info("📊 Step 1: Getting current system state...")
        workers = await self.get_all_workers()
        processing_tasks = await self.get_all_processing_tasks()
        
        # Filter workers by status
        active_workers = [w for w in workers if w['status'] in ['spawning', 'active', 'terminating']]
        terminated_workers = [w for w in workers if w['status'] == 'terminated']
        error_workers = [w for w in workers if w['status'] == 'error']
        
        logger.info(f"   Total workers: {len(workers)}")
        logger.info(f"   Active workers: {len(active_workers)}")
        logger.info(f"   Terminated workers: {len(terminated_workers)}")
        logger.info(f"   Error workers: {len(error_workers)}")
        logger.info(f"   Processing tasks: {len(processing_tasks)}")
        
        if not active_workers and not processing_tasks:
            logger.info("✅ No active workers or processing tasks found. System is already clean.")
            return {
                "success": True,
                "workers_terminated": 0,
                "runpod_terminations": 0,
                "tasks_reset": 0,
                "duration_seconds": 0
            }
        
        # Step 2: Terminate RunPod instances
        logger.info(f"\n🔥 Step 2: Terminating {len(active_workers)} RunPod instances...")
        runpod_terminations = 0
        
        for worker in active_workers:
            success = await self.terminate_runpod_worker(worker)
            if success:
                runpod_terminations += 1
        
        # Step 3: Mark workers as terminated in database
        logger.info(f"\n📝 Step 3: Marking {len(active_workers)} workers as terminated...")
        workers_terminated = 0
        
        for worker in active_workers:
            success = await self.mark_worker_terminated(worker['id'])
            if success:
                workers_terminated += 1
        
        # Step 4: Reset processing tasks
        logger.info(f"\n🔄 Step 4: Resetting {len(processing_tasks)} processing tasks...")
        tasks_reset = await self.reset_processing_tasks(processing_tasks)
        
        # Summary
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        logger.info("\n" + "=" * 60)
        logger.info("🎉 Shutdown process completed!")
        logger.info(f"   Duration: {duration:.2f} seconds")
        logger.info(f"   Workers terminated: {workers_terminated}/{len(active_workers)}")
        logger.info(f"   RunPod instances terminated: {runpod_terminations}/{len(active_workers)}")
        logger.info(f"   Tasks reset: {tasks_reset}/{len(processing_tasks)}")
        
        success = (workers_terminated == len(active_workers) and 
                  tasks_reset == len(processing_tasks))
        
        if success:
            logger.info("✅ All operations completed successfully!")
        else:
            logger.warning("⚠️ Some operations failed. Check the logs above for details.")
        
        return {
            "success": success,
            "workers_terminated": workers_terminated,
            "runpod_terminations": runpod_terminations,
            "tasks_reset": tasks_reset,
            "duration_seconds": duration
        }
    
    async def show_current_state(self):
        """Display the current state of workers and tasks."""
        logger.info("📊 Current System State")
        logger.info("=" * 40)
        
        workers = await self.get_all_workers()
        processing_tasks = await self.get_all_processing_tasks()
        
        # Group workers by status
        status_counts = {}
        for worker in workers:
            status = worker['status']
            status_counts[status] = status_counts.get(status, 0) + 1
        
        logger.info("Workers by status:")
        for status, count in status_counts.items():
            logger.info(f"   {status}: {count}")
        
        logger.info(f"\nProcessing tasks: {len(processing_tasks)}")
        
        if processing_tasks:
            logger.info("Processing tasks by worker:")
            worker_task_counts = {}
            for task in processing_tasks:
                worker_id = task.get('worker_id', 'unassigned')
                worker_task_counts[worker_id] = worker_task_counts.get(worker_id, 0) + 1
            
            for worker_id, count in worker_task_counts.items():
                logger.info(f"   {worker_id}: {count} tasks")

async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Shutdown all workers and reset processing tasks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show current state without making changes
  python shutdown_all_workers.py --status
  
  # Perform a dry run to see what would be done
  python shutdown_all_workers.py --dry-run
  
  # Actually shutdown all workers and reset tasks
  python shutdown_all_workers.py --shutdown
  
  # Force shutdown without confirmation
  python shutdown_all_workers.py --shutdown --force
        """
    )
    
    parser.add_argument('--status', action='store_true',
                       help='Show current system state without making changes')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be done without making actual changes')
    parser.add_argument('--shutdown', action='store_true',
                       help='Actually perform the shutdown process')
    parser.add_argument('--force', action='store_true',
                       help='Skip confirmation prompt')
    
    args = parser.parse_args()
    
    # If no action specified, show help
    if not any([args.status, args.dry_run, args.shutdown]):
        parser.print_help()
        return
    
    try:
        manager = WorkerShutdownManager(dry_run=args.dry_run)
        
        if args.status:
            await manager.show_current_state()
            return
        
        if args.shutdown and not args.force and not args.dry_run:
            # Ask for confirmation
            print("\n⚠️  WARNING: This will shut down ALL workers and reset ALL processing tasks!")
            print("This action cannot be undone.")
            response = input("\nAre you sure you want to continue? (type 'yes' to confirm): ")
            
            if response.lower() != 'yes':
                print("❌ Shutdown cancelled by user")
                return
        
        # Perform the shutdown
        result = await manager.shutdown_all_workers()
        
        # Exit with appropriate code
        sys.exit(0 if result["success"] else 1)
        
    except KeyboardInterrupt:
        logger.info("\n❌ Shutdown cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main()) 
