#!/usr/bin/env python3
"""
Real-time Logs Dashboard
========================

Interactive dashboard for viewing system logs in real-time.

Usage:
    python view_logs_dashboard.py
    python view_logs_dashboard.py --source-type orchestrator_gpu
    python view_logs_dashboard.py --level ERROR
    python view_logs_dashboard.py --worker gpu-20250115-abc123
"""

import os
import sys
import asyncio
import time
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from supabase import create_client


def clear_screen():
    """Clear the terminal screen."""
    if os.name == 'nt':
        subprocess.run(["cmd", "/c", "cls"], check=False)
    else:
        subprocess.run(["clear"], check=False)


class LogsDashboard:
    """Real-time logs dashboard."""
    
    def __init__(self):
        load_dotenv()
        
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        
        self.supabase = create_client(supabase_url, supabase_key)
        self.last_log_id = None
    
    def get_recent_logs(
        self,
        minutes: int = 5,
        source_type: str = None,
        worker_id: str = None,
        log_level: str = None,
        limit: int = 50
    ):
        """Get recent logs."""
        start_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        
        query = self.supabase.table('system_logs').select('*')
        query = query.gte('timestamp', start_time.isoformat())
        
        if source_type:
            query = query.eq('source_type', source_type)
        if worker_id:
            query = query.eq('worker_id', worker_id)
        if log_level:
            query = query.eq('log_level', log_level)
        
        query = query.order('timestamp', desc=True)
        query = query.limit(limit)
        
        result = query.execute()
        return result.data or []
    
    def get_new_logs(
        self,
        source_type: str = None,
        worker_id: str = None,
        log_level: str = None,
        limit: int = 20
    ):
        """Get logs newer than last displayed log."""
        query = self.supabase.table('system_logs').select('*')
        
        if self.last_log_id:
            query = query.gt('timestamp', self.last_log_id)
        
        if source_type:
            query = query.eq('source_type', source_type)
        if worker_id:
            query = query.eq('worker_id', worker_id)
        if log_level:
            query = query.eq('log_level', log_level)
        
        query = query.order('timestamp', desc=False)  # Oldest first for new logs
        query = query.limit(limit)
        
        result = query.execute()
        logs = result.data or []
        
        if logs:
            self.last_log_id = logs[-1]['timestamp']
        
        return logs
    
    def get_log_stats(self, minutes: int = 60):
        """Get logging statistics."""
        start_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        
        query = self.supabase.table('system_logs').select('log_level, source_type')
        query = query.gte('timestamp', start_time.isoformat())
        result = query.execute()
        
        logs = result.data or []
        
        by_level = {}
        by_source_type = {}
        
        for log in logs:
            level = log['log_level']
            by_level[level] = by_level.get(level, 0) + 1
            
            source_type = log['source_type']
            by_source_type[source_type] = by_source_type.get(source_type, 0) + 1
        
        return {
            'total': len(logs),
            'by_level': by_level,
            'by_source_type': by_source_type
        }
    
    def display_log_entry(self, log, max_message_len=100):
        """Display a single log entry."""
        timestamp = log['timestamp']
        # Parse and format timestamp
        try:
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            time_str = dt.strftime('%H:%M:%S')
        except:
            time_str = timestamp[-8:]
        
        level = log['log_level']
        source = log['source_id'][:20]  # Truncate long source IDs
        message = log['message']
        
        # Truncate long messages
        if len(message) > max_message_len:
            message = message[:max_message_len-3] + "..."
        
        # Color code by level
        level_colors = {
            'ERROR': '\033[91m',    # Red
            'WARNING': '\033[93m',  # Yellow
            'INFO': '\033[92m',     # Green
            'DEBUG': '\033[94m',    # Blue
            'CRITICAL': '\033[95m', # Magenta
        }
        level_color = level_colors.get(level, '')
        reset_color = '\033[0m' if level_color else ''
        
        print(f"{time_str} | {level_color}{level:8}{reset_color} | {source:20} | {message}")
        
        # Show context if available
        if log.get('worker_id') or log.get('task_id') or log.get('cycle_number'):
            context_parts = []
            if log.get('worker_id'):
                context_parts.append(f"Worker: {log['worker_id'][:20]}")
            if log.get('task_id'):
                context_parts.append(f"Task: {str(log['task_id'])[:20]}")
            if log.get('cycle_number'):
                context_parts.append(f"Cycle: {log['cycle_number']}")
            
            if context_parts:
                print(f"         {'':8}   {'':20}   → {' | '.join(context_parts)}")
    
    def display_dashboard(
        self,
        logs,
        stats,
        source_type=None,
        worker_id=None,
        log_level=None
    ):
        """Display the logs dashboard."""
        clear_screen()
        
        print("📋 System Logs Dashboard")
        print("=" * 120)
        print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Display filters
        filters = []
        if source_type:
            filters.append(f"Source: {source_type}")
        if worker_id:
            filters.append(f"Worker: {worker_id}")
        if log_level:
            filters.append(f"Level: {log_level}")
        
        if filters:
            print(f"🔍 Filters: {' | '.join(filters)}")
        
        # Display stats
        print(f"\n📊 Statistics (Last Hour)")
        print("-" * 120)
        print(f"Total Logs: {stats['total']}")
        
        if stats['by_level']:
            level_str = " | ".join([f"{level}: {count}" for level, count in stats['by_level'].items()])
            print(f"By Level: {level_str}")
        
        if stats['by_source_type']:
            source_str = " | ".join([f"{source}: {count}" for source, count in stats['by_source_type'].items()])
            print(f"By Source: {source_str}")
        
        # Display recent logs
        print(f"\n📜 Recent Logs (Last 5 minutes)")
        print("-" * 120)
        
        if not logs:
            print("No logs found")
        else:
            print(f"{'Time':<8}   {'Level':<8}   {'Source':<20}   {'Message'}")
            print("-" * 120)
            
            for log in reversed(logs[-30:]):  # Show last 30 logs
                self.display_log_entry(log, max_message_len=60)
        
        # Instructions
        print(f"\n💡 Controls")
        print("-" * 120)
        print("Press Ctrl+C to exit | Auto-refresh every 5 seconds")


async def run_dashboard(
    source_type: str = None,
    worker_id: str = None,
    log_level: str = None,
    refresh_interval: int = 5
):
    """Run the logs dashboard."""
    
    try:
        dashboard = LogsDashboard()
        
        print("🚀 Starting Logs Dashboard...")
        print("   Connecting to database...")
        
        # Test connection
        dashboard.get_recent_logs(minutes=1, limit=1)
        print("   ✅ Connected successfully!")
        
        time.sleep(2)
        
        while True:
            try:
                # Get recent logs
                logs = dashboard.get_recent_logs(
                    minutes=5,
                    source_type=source_type,
                    worker_id=worker_id,
                    log_level=log_level,
                    limit=100
                )
                
                # Get stats
                stats = dashboard.get_log_stats(minutes=60)
                
                # Display dashboard
                dashboard.display_dashboard(
                    logs,
                    stats,
                    source_type=source_type,
                    worker_id=worker_id,
                    log_level=log_level
                )
                
                # Wait for next refresh
                await asyncio.sleep(refresh_interval)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"\n❌ Error updating dashboard: {e}")
                await asyncio.sleep(5)
    
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"❌ Failed to start dashboard: {e}")
        print("   💡 Make sure your .env file has the required database settings")
        print("   💡 Make sure the system_logs table exists (run SQL migration)")
        sys.exit(1)
    
    finally:
        clear_screen()
        print("👋 Logs dashboard stopped")


def main():
    """Main function with command line options."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Real-time system logs dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # View all logs
  %(prog)s

  # View only orchestrator logs
  %(prog)s --source-type orchestrator_gpu

  # View only errors
  %(prog)s --level ERROR

  # View logs for specific worker
  %(prog)s --worker gpu-20250115-abc123

  # Combine filters
  %(prog)s --source-type worker --level ERROR
        """
    )
    
    parser.add_argument(
        '--source-type',
        choices=['orchestrator_gpu', 'orchestrator_api', 'worker'],
        help='Filter by source type'
    )
    parser.add_argument(
        '--worker',
        help='Filter by worker ID'
    )
    parser.add_argument(
        '--level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Filter by log level'
    )
    parser.add_argument(
        '--refresh',
        type=int,
        default=5,
        help='Refresh interval in seconds (default: 5)'
    )
    
    args = parser.parse_args()
    
    try:
        asyncio.run(run_dashboard(
            source_type=args.source_type,
            worker_id=args.worker,
            log_level=args.level,
            refresh_interval=args.refresh
        ))
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped by user")
    except Exception as e:
        print(f"❌ Dashboard failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()




