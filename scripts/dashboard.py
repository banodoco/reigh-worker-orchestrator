#!/usr/bin/env python3
"""
Simple dashboard for monitoring the Runpod GPU Worker Orchestrator.
Displays real-time status, worker health, and system metrics.
"""

import os
import sys
import asyncio
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from gpu_orchestrator.database import DatabaseClient

def clear_screen():
    """Clear the terminal screen."""
    if os.name == 'nt':
        subprocess.run(["cmd", "/c", "cls"], check=False)
    else:
        subprocess.run(["clear"], check=False)

def format_duration(seconds):
    """Format duration in seconds to human readable format."""
    if seconds is None:
        return "N/A"
    
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"

def format_cost(runtime_hours, hourly_rate):
    """Format cost calculation."""
    if runtime_hours is None or hourly_rate is None:
        return "N/A"
    
    cost = runtime_hours * hourly_rate
    return f"${cost:.2f}"

async def get_system_status(db: DatabaseClient):
    """Get comprehensive system status."""
    try:
        # Get overall status
        status = await db.get_orchestrator_status()
        
        # Get worker health
        worker_health = await db.get_active_workers_health()
        
        # Get recent task activity
        tasks = await db.get_tasks(['Queued', 'Running', 'Complete', 'Error', 'Failed'])
        
        # Calculate additional metrics
        now = datetime.utcnow()
        recent_tasks = [t for t in tasks if t.get('created_at') and 
                       (now - datetime.fromisoformat(t['created_at'].replace('Z', ''))).total_seconds() < 3600]
        
        completed_last_hour = len([t for t in recent_tasks if t['status'] == 'Complete'])
        failed_last_hour = len([t for t in recent_tasks if t['status'] in ['Error', 'Failed']])
        
        return {
            'status': status,
            'worker_health': worker_health,
            'recent_metrics': {
                'completed_last_hour': completed_last_hour,
                'failed_last_hour': failed_last_hour,
                'success_rate': (completed_last_hour / max(1, completed_last_hour + failed_last_hour)) * 100
            }
        }
        
    except Exception as e:
        return {'error': str(e)}

def display_dashboard(data):
    """Display the dashboard."""
    clear_screen()
    
    print("🤖 Runpod GPU Worker Orchestrator Dashboard")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if 'error' in data:
        print(f"\n❌ Error: {data['error']}")
        return
    
    status = data.get('status', {})
    worker_health = data.get('worker_health', [])
    recent_metrics = data.get('recent_metrics', {})
    
    # Overall Status
    print(f"\n📊 System Status")
    print("-" * 30)
    print(f"Queued Tasks:      {status.get('queued_tasks', 0):>6}")
    print(f"Running Tasks:     {status.get('running_tasks', 0):>6}")
    print(f"Completed Tasks:   {status.get('completed_tasks', 0):>6}")
    print(f"Error Tasks:       {status.get('error_tasks', 0):>6}")
    print(f"Failed Tasks:      {status.get('failed_tasks', 0):>6}")
    
    print(f"\n👷 Worker Status")
    print("-" * 30)
    print(f"Spawning Workers:  {status.get('spawning_workers', 0):>6}")
    print(f"Active Workers:    {status.get('active_workers', 0):>6}")
    print(f"Terminating:       {status.get('terminating_workers', 0):>6}")
    print(f"Error Workers:     {status.get('error_workers', 0):>6}")
    print(f"Terminated:        {status.get('terminated_workers', 0):>6}")
    
    print(f"\n🚨 Health Alerts")
    print("-" * 30)
    print(f"Stale Workers:     {status.get('stale_workers', 0):>6}")
    print(f"Stuck Tasks:       {status.get('stuck_tasks', 0):>6}")
    
    print(f"\n📈 Recent Performance (Last Hour)")
    print("-" * 30)
    print(f"Completed:         {recent_metrics.get('completed_last_hour', 0):>6}")
    print(f"Failed:            {recent_metrics.get('failed_last_hour', 0):>6}")
    print(f"Success Rate:      {recent_metrics.get('success_rate', 0):>5.1f}%")
    
    # Worker Details
    if worker_health:
        print(f"\n🔍 Worker Details")
        print("-" * 60)
        print(f"{'Worker ID':<25} {'Status':<12} {'Health':<15} {'Task':<8}")
        print("-" * 60)
        
        for worker in worker_health[:10]:  # Show first 10 workers
            worker_id = worker['id'][:24]  # Truncate long IDs
            status = worker['status']
            health = worker.get('health_status', 'UNKNOWN')
            
            # Task info
            task_info = "Idle"
            if worker.get('current_task_id'):
                runtime = worker.get('task_runtime_seconds', 0)
                task_info = f"{format_duration(runtime)}"
            
            # Health status emoji
            health_emoji = "✅" if health == "HEALTHY" else "⚠️" if health in ["STALE_HEARTBEAT"] else "❌"
            
            print(f"{worker_id:<25} {status:<12} {health_emoji} {health:<13} {task_info:<8}")
            
            # VRAM info if available
            if worker.get('vram_usage_percent'):
                vram_pct = worker['vram_usage_percent']
                vram_used = worker.get('vram_used_mb', 0)
                vram_total = worker.get('vram_total_mb', 0)
                print(f"{'':>25} VRAM: {vram_pct:>3.0f}% ({vram_used}/{vram_total} MB)")
    
    # Cost Estimation (if we had cost data)
    print(f"\n💰 Cost Estimation")
    print("-" * 30)
    active_workers = status.get('active_workers', 0)
    spawning_workers = status.get('spawning_workers', 0)
    total_running = active_workers + spawning_workers
    
    # Rough estimation - you should customize these rates
    estimated_hourly_rate = 0.6  # $/hour per GPU - update based on your instance types
    estimated_hourly_cost = total_running * estimated_hourly_rate
    estimated_daily_cost = estimated_hourly_cost * 24
    
    print(f"Running Workers:   {total_running:>6}")
    print(f"Est. Hourly Cost:  ${estimated_hourly_cost:>5.2f}")
    print(f"Est. Daily Cost:   ${estimated_daily_cost:>5.2f}")
    
    # Instructions
    print(f"\n💡 Controls")
    print("-" * 30)
    print("Press Ctrl+C to exit")
    print("Refresh every 10 seconds")

async def run_dashboard(refresh_interval=10):
    """Run the dashboard with auto-refresh."""
    
    load_dotenv()
    
    try:
        db = DatabaseClient()
        
        print("🚀 Starting Orchestrator Dashboard...")
        print("   Connecting to database...")
        
        # Test connection
        await db.get_orchestrator_status()
        print("   ✅ Connected successfully!")
        
        time.sleep(2)  # Brief pause
        
        while True:
            try:
                # Get system status
                data = await get_system_status(db)
                
                # Display dashboard
                display_dashboard(data)
                
                # Wait for next refresh
                await asyncio.sleep(refresh_interval)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"\n❌ Error updating dashboard: {e}")
                await asyncio.sleep(5)  # Brief pause before retry
    
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"❌ Failed to start dashboard: {e}")
        print("   💡 Make sure your .env file has the required database settings")
        sys.exit(1)
    
    finally:
        clear_screen()
        print("👋 Dashboard stopped")

async def export_status():
    """Export current status to JSON for monitoring integrations."""
    
    load_dotenv()
    
    try:
        db = DatabaseClient()
        data = await get_system_status(db)
        
        # Add timestamp
        data['export_timestamp'] = datetime.utcnow().isoformat()
        
        # Print JSON for consumption by monitoring tools
        print(json.dumps(data, indent=2, default=str))
        
    except Exception as e:
        error_data = {
            'error': str(e),
            'export_timestamp': datetime.utcnow().isoformat(),
            'success': False
        }
        print(json.dumps(error_data, indent=2))
        sys.exit(1)

def main():
    """Main function with command line options."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Orchestrator Dashboard")
    parser.add_argument(
        "--export",
        action="store_true",
        help="Export status as JSON instead of running interactive dashboard"
    )
    parser.add_argument(
        "--refresh",
        type=int,
        default=10,
        help="Refresh interval in seconds (default: 10)"
    )
    
    args = parser.parse_args()
    
    try:
        if args.export:
            asyncio.run(export_status())
        else:
            asyncio.run(run_dashboard(args.refresh))
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped by user")
    except Exception as e:
        print(f"❌ Dashboard failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 
