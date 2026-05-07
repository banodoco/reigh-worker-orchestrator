#!/usr/bin/env python3
"""
Simple dashboard for monitoring the Runpod GPU Worker Orchestrator.
Displays real-time status, worker health, and system metrics.
"""

from __future__ import annotations

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

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Keep pure export-builder tests independent of local env deps.
    def load_dotenv():
        return False


def _database_client():
    from gpu_orchestrator.database import DatabaseClient

    return DatabaseClient()

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

def build_export_payload(data, *, imported_live_evidence=None, export_timestamp=None):
    """Build a machine-readable dashboard export from already-fetched data."""
    status = data.get('status', {}) if isinstance(data, dict) else {}
    worker_health = data.get('worker_health', []) if isinstance(data, dict) else []
    tasks = data.get('tasks', []) if isinstance(data, dict) else []
    live_evidence = imported_live_evidence or data.get('imported_live_evidence', {}) if isinstance(data, dict) else {}

    route_worker_health = _route_worker_health(worker_health)
    route_totals = _route_totals(tasks, worker_health)
    return {
        'success': 'error' not in data,
        'export_timestamp': export_timestamp or datetime.utcnow().isoformat(),
        'status': status,
        'worker_health': worker_health,
        'recent_metrics': data.get('recent_metrics', {}),
        'canary_panels': {
            'selected_pool_totals': _selected_pool_totals(data, status),
            'route_totals': route_totals,
            'route_worker_health': route_worker_health,
            'claim_suppression': data.get('claim_suppression') or status.get('claim_suppression') or {},
            'quota_alerts': data.get('quota_alerts') or status.get('quota_alerts') or [],
            'preflight_status': data.get('preflight_status') or status.get('preflight_status') or {},
            'warm_cache_status': data.get('warm_cache_status') or status.get('warm_cache_status') or {},
            'non_rayworker_route_health': _non_rayworker_route_health(live_evidence),
        },
    }

def _selected_pool_totals(data, status):
    task_counts = data.get('task_counts') or status.get('task_counts') or {}
    return (
        task_counts.get('selected_pool_totals')
        or data.get('selected_pool_totals')
        or status.get('selected_pool_totals')
        or {}
    )

def _route_totals(tasks, worker_health):
    totals = {}
    for task in tasks:
        route_key = task.get('route_key') or task.get('task_type') or 'unknown'
        entry = totals.setdefault(
            route_key,
            {
                'route_key': route_key,
                'by_status': {},
                'by_backend': {},
                'by_profile': {},
                'by_selector': {},
            },
        )
        _increment(entry['by_status'], task.get('status') or 'unknown')
        _increment(entry['by_backend'], task.get('worker_backend') or task.get('backend') or 'unknown')
        _increment(entry['by_profile'], str(task.get('worker_profile') or task.get('profile') or 'unknown'))
        selector = f"{task.get('selector_namespace') or 'unknown'}/{task.get('selector_version') or 'default'}"
        _increment(entry['by_selector'], selector)

    for worker in worker_health:
        route_key = _worker_route_key(worker)
        if not route_key:
            continue
        entry = totals.setdefault(
            route_key,
            {
                'route_key': route_key,
                'by_status': {},
                'by_backend': {},
                'by_profile': {},
                'by_selector': {},
            },
        )
        _increment(entry['by_backend'], _worker_backend(worker))
        _increment(entry['by_profile'], str(_worker_profile(worker)))
        selector = f"{_worker_selector_namespace(worker)}/{_worker_selector_version(worker)}"
        _increment(entry['by_selector'], selector)
    return dict(sorted(totals.items()))

def _route_worker_health(worker_health):
    routes = {}
    for worker in worker_health:
        route_key = _worker_route_key(worker) or 'unknown'
        entry = routes.setdefault(
            route_key,
            {
                'route_key': route_key,
                'active_workers': 0,
                'stale_workers': 0,
                'workers': [],
            },
        )
        status = str(worker.get('status') or '').lower()
        health = str(worker.get('health_status') or '').lower()
        if status in {'active', 'running', 'ready'}:
            entry['active_workers'] += 1
        if 'stale' in health or worker.get('is_stale') is True:
            entry['stale_workers'] += 1
        entry['workers'].append(
            {
                'id': worker.get('id'),
                'status': worker.get('status'),
                'health_status': worker.get('health_status'),
                'backend': _worker_backend(worker),
                'profile': _worker_profile(worker),
                'selector_namespace': _worker_selector_namespace(worker),
                'selector_version': _worker_selector_version(worker),
            }
        )
    return dict(sorted(routes.items()))

def _non_rayworker_route_health(live_evidence):
    if not live_evidence:
        return {}
    if isinstance(live_evidence, dict) and 'routes' in live_evidence:
        routes = live_evidence['routes']
        if isinstance(routes, list):
            return {
                route.get('route_key'): route
                for route in routes
                if isinstance(route, dict) and route.get('route_key')
            }
        if isinstance(routes, dict):
            return routes
    if isinstance(live_evidence, list):
        return {
            item.get('route_key'): item
            for item in live_evidence
            if isinstance(item, dict) and item.get('route_key')
        }
    return live_evidence if isinstance(live_evidence, dict) else {}

def _increment(mapping, key):
    mapping[key] = mapping.get(key, 0) + 1

def _worker_metadata(worker):
    metadata = worker.get('metadata')
    return metadata if isinstance(metadata, dict) else {}

def _worker_route_key(worker):
    metadata = _worker_metadata(worker)
    return worker.get('route_key') or metadata.get('route_key')

def _worker_backend(worker):
    metadata = _worker_metadata(worker)
    return worker.get('worker_backend') or worker.get('backend') or metadata.get('worker_backend') or metadata.get('backend') or 'unknown'

def _worker_profile(worker):
    metadata = _worker_metadata(worker)
    return worker.get('worker_profile') or worker.get('profile') or metadata.get('worker_profile') or metadata.get('profile') or 'unknown'

def _worker_selector_namespace(worker):
    metadata = _worker_metadata(worker)
    return worker.get('selector_namespace') or metadata.get('selector_namespace') or 'unknown'

def _worker_selector_version(worker):
    metadata = _worker_metadata(worker)
    return worker.get('selector_version') or metadata.get('selector_version') or 'default'

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
        db = _database_client()
        
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
        db = _database_client()
        data = await get_system_status(db)
        data = build_export_payload(data)
        
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
