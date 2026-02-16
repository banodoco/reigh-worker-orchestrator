"""Railway deployment status and logs command."""

import subprocess
import json
from datetime import datetime


def run_railway_cmd(args: list[str], cwd: str = None) -> tuple[bool, str]:
    """Run a railway CLI command and return (success, output)."""
    try:
        result = subprocess.run(
            ['railway'] + args,
            capture_output=True,
            text=True,
            cwd=cwd or '/Users/peteromalley/Headless_WGP_Orchestrator',
            timeout=30
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except FileNotFoundError:
        return False, "Railway CLI not found. Install with: brew install railway"
    except Exception as e:
        return False, str(e)


def get_project_status() -> dict:
    """Get overall project status."""
    success, output = run_railway_cmd(['status', '--json'])
    if success:
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return {'error': 'Failed to parse status JSON'}
    return {'error': output}


def get_deployments(service: str, limit: int = 5) -> list[dict]:
    """Get recent deployments for a service."""
    success, output = run_railway_cmd(['deployment', 'list', '--service', service, '--limit', str(limit), '--json'])
    if success:
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return []
    return []


def get_logs(service: str, lines: int = 50, build: bool = False) -> str:
    """Get logs for a service."""
    args = ['logs', '--service', service, '--lines', str(lines)]
    if build:
        args.append('--build')
    success, output = run_railway_cmd(args)
    return output if success else f"Error: {output}"


def format_deployment(dep: dict) -> str:
    """Format a single deployment for display."""
    status = dep.get('status', 'UNKNOWN')
    created = dep.get('createdAt', '')
    dep_id = dep.get('id', 'unknown')[:8]
    reason = dep.get('meta', {}).get('reason', 'unknown')
    
    # Parse and format timestamp
    if created:
        try:
            dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            created = dt.strftime('%Y-%m-%d %H:%M:%S UTC')
        except:
            pass
    
    # Status emoji
    status_emoji = {
        'SUCCESS': '✅',
        'BUILDING': '🔨',
        'DEPLOYING': '🚀',
        'FAILED': '❌',
        'CRASHED': '💥',
        'REMOVED': '🗑️',
        'SLEEPING': '😴',
    }.get(status, '❓')
    
    return f"  {status_emoji} {status:12} | {dep_id} | {reason:10} | {created}"


def run(client, options: dict):
    """Handle 'debug.py railway' command."""
    service = options.get('service', 'gpu-orchestrator')
    show_build = options.get('build', False)
    show_deploy = options.get('deploy', False)
    lines = options.get('lines', 30)
    
    print("🚂 Railway Status")
    print("=" * 60)
    
    # Get project status
    status = get_project_status()
    if 'error' in status:
        print(f"\n❌ Failed to get status: {status['error']}")
        return
    
    project_name = status.get('name', 'Unknown')
    print(f"\n📦 Project: {project_name}")
    
    # Get services
    services = status.get('services', {}).get('edges', [])
    if not services:
        print("   No services found")
        return
    
    for svc_edge in services:
        svc = svc_edge.get('node', {})
        svc_name = svc.get('name', 'Unknown')
        
        # Get latest deployment info
        instances = svc.get('serviceInstances', {}).get('edges', [])
        if instances:
            instance = instances[0].get('node', {})
            latest = instance.get('latestDeployment', {})
            latest_status = latest.get('status', 'UNKNOWN')
            created = latest.get('createdAt', '')
            
            status_emoji = {
                'SUCCESS': '✅',
                'BUILDING': '🔨', 
                'DEPLOYING': '🚀',
                'FAILED': '❌',
                'CRASHED': '💥',
            }.get(latest_status, '❓')
            
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    age = datetime.now(dt.tzinfo) - dt
                    age_str = f"{age.seconds // 3600}h {(age.seconds % 3600) // 60}m ago" if age.days == 0 else f"{age.days}d ago"
                except:
                    age_str = created
            else:
                age_str = "unknown"
                
            print(f"\n🔧 Service: {svc_name}")
            print(f"   Status: {status_emoji} {latest_status} ({age_str})")
    
    # Get recent deployments
    print(f"\n📋 Recent Deployments ({service}):")
    print("-" * 60)
    deployments = get_deployments(service, limit=5)
    if deployments:
        for dep in deployments:
            print(format_deployment(dep))
    else:
        print("   No deployments found")
    
    # Show logs if requested
    if show_build:
        print(f"\n📜 Build Logs (last {lines} lines):")
        print("-" * 60)
        logs = get_logs(service, lines=lines, build=True)
        print(logs)
    
    if show_deploy:
        print(f"\n📜 Deploy Logs (last {lines} lines):")
        print("-" * 60)
        logs = get_logs(service, lines=lines, build=False)
        print(logs)
    
    # If neither logs requested, show a hint
    if not show_build and not show_deploy:
        print("\n💡 Add --build or --deploy to see logs")
        print("   Example: debug.py railway --build --lines 50")









