#!/usr/bin/env python3
"""
Test script for Runpod API connectivity and basic operations.
Validates that the Runpod integration is working correctly.
"""

import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runpod_lifecycle import RunPodConfig, find_gpu_type, get_network_volumes

from gpu_orchestrator.config import OrchestratorConfig
from gpu_orchestrator.worker_spawner import create_worker_spawner

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_api_connection():
    """Test basic API connectivity."""
    print("🔗 Testing Runpod API Connection...")
    print("-" * 50)
    
    try:
        config = RunPodConfig.from_env()

        # Test GPU types
        gpu_info = find_gpu_type(config.gpu_type, config.api_key)
        if gpu_info:
            print("✅ API connection successful")
            print(f"   Target GPU: {config.gpu_type}")
            print(f"   GPU ID: {gpu_info.get('id')}")
            if gpu_info.get('lowestPrice'):
                price = gpu_info['lowestPrice'].get('uninterruptablePrice', 'N/A')
                print(f"   Price: ${price}/hr")
            return True
        else:
            print(f"❌ GPU type '{client.gpu_type}' not found")
            return False
            
    except Exception as e:
        print(f"❌ API connection failed: {e}")
        return False

async def check_network_volumes():
    """Test network volume listing and storage name lookup."""
    print("\n📁 Testing Network Volumes...")
    print("-" * 50)
    
    try:
        config = RunPodConfig.from_env()
        volumes = get_network_volumes(config.api_key)
        
        if volumes:
            print(f"✅ Found {len(volumes)} network volumes:")
            for vol in volumes[:5]:  # Show first 5
                dc = vol.get('dataCenter', {})
                print(f"   • {vol.get('name')} (ID: {vol.get('id')}) - {vol.get('size')}GB")
                print(f"     Location: {dc.get('name', 'N/A')} ({dc.get('location', 'N/A')})")
            
            if len(volumes) > 5:
                print(f"   ... and {len(volumes) - 5} more")
            
            # Test storage name lookup (like user's example)
            if config.storage_name:
                print(f"\n📦 Testing storage lookup for: {config.storage_name}")
                storage_id = next(
                    (volume.get("id") for volume in volumes if volume.get("name") == config.storage_name),
                    None,
                )
                if storage_id:
                    print(f"✅ Found storage '{config.storage_name}' → {storage_id}")
                else:
                    print(f"❌ Storage '{config.storage_name}' not found")
            else:
                print("\n⚠️  No storage name configured (RUNPOD_STORAGE_NAME)")
        else:
            print("⚠️  No network volumes found")
        
        return True
        
    except Exception as e:
        print(f"❌ Error listing network volumes: {e}")
        return False

async def check_ssh_configuration():
    """Test SSH key configuration."""
    print("\n🔐 Testing SSH Configuration...")
    print("-" * 50)
    
    try:
        config = RunPodConfig.from_env()
        
        # Check public key
        if config.ssh_public_key_path:
            pub_path = os.path.expanduser(config.ssh_public_key_path)
            if os.path.exists(pub_path):
                print(f"✅ Public key found: {config.ssh_public_key_path}")
                # Show first few chars
                with open(pub_path, 'r') as f:
                    content = f.read().strip()
                    print(f"   Key type: {content.split()[0] if content else 'Unknown'}")
            else:
                print(f"❌ Public key not found: {config.ssh_public_key_path}")
        else:
            print("⚠️  No public key configured")
        
        # Check private key
        if config.ssh_private_key_path:
            priv_path = os.path.expanduser(config.ssh_private_key_path)
            if os.path.exists(priv_path):
                print("✅ File-based SSH material found")
            else:
                print("❌ File-based SSH material not found at configured path")
        else:
            print("⚠️  No private key configured")
        
        return True
        
    except Exception as e:
        print(f"❌ SSH configuration error: {e}")
        return False

async def check_worker_lifecycle():
    """Test complete worker spawn and terminate cycle with SSH and initialization."""
    print("\n🔄 Testing Worker Lifecycle...")
    print("-" * 50)
    
    print("⚠️  WARNING: This test will spawn a real GPU instance and cost money!")
    response = input("Continue? (y/N): ").strip().lower()
    if response != 'y':
        print("Test skipped.")
        return True
    
    try:
        client = create_worker_spawner(OrchestratorConfig.from_env(), None)
        worker_id = f"test-{client.generate_worker_id()}"
        
        print(f"Worker ID: {worker_id}")
        
        # Test spawn with initialization (this now includes repo setup)
        print("\n1. Testing worker spawn with initialization...")
        result = await client.spawn_worker(worker_id)
        
        if not result:
            print("❌ Failed to spawn worker")
            return False
        
        pod_id = result["runpod_id"]
        status = result.get("status", "unknown")
        print("✅ Worker spawned successfully")
        print(f"   Pod ID: {pod_id}")
        print(f"   Status: {status}")
        
        if status == "error":
            print("❌ Worker initialization failed")
            return False
        elif status == "running":
            print("✅ Worker fully initialized and ready")
        else:
            print(f"⚠️  Worker in status: {status}")
        
        # Test SSH connection and show initialization results
        if "ssh_details" in result:
            ssh_details = result["ssh_details"]
            print("\n2. Testing SSH connection and checking setup...")
            print(f"   SSH: {ssh_details['ip']}:{ssh_details['port']}")
            
            # Test commands to verify initialization
            test_commands = [
                "pwd",  # Current directory
                "ls -la",  # List files
                "ls -la /workspace 2>/dev/null || echo 'No workspace'",  # Check storage
                "ls -la worker-repo 2>/dev/null || echo 'No worker repo'",  # Check repo
                "python --version",  # Python version
                "pip list | grep supabase || echo 'Supabase not installed'",  # Check deps
                "nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null || echo 'No GPU'",  # GPU check
            ]
            
            for i, command in enumerate(test_commands, 1):
                print(f"\n   Command {i}: {command}")
                result_cmd = await client.execute_command_on_worker(pod_id, command, timeout=30)
                
                if result_cmd:
                    exit_code, stdout, stderr = result_cmd
                    print(f"     Exit Code: {exit_code}")
                    if stdout.strip():
                        print(f"     Output: {stdout.strip()}")
                    if stderr.strip() and exit_code != 0:
                        print(f"     Error: {stderr.strip()}")
                else:
                    print("     ❌ Command failed")
        else:
            print("\n2. ⚠️  No SSH details available")
        
        # Test starting worker process manually if not auto-started
        if status == "running":
            print("\n3. Testing worker process management...")
            if await client.start_worker_process(pod_id, worker_id):
                print("✅ Worker process started successfully")
            else:
                print("⚠️  Worker process start failed")
        
        # Wait a moment before termination
        print("\n4. Waiting 30 seconds before termination...")
        await asyncio.sleep(30)
        
        # Test terminate
        print("\n5. Testing worker termination...")
        success = await client.terminate_worker(pod_id)
        
        if success:
            print("✅ Worker terminated successfully")
            return True
        else:
            print("❌ Failed to terminate worker")
            return False
        
    except Exception as e:
        print(f"❌ Error in worker lifecycle test: {e}")
        return False

async def check_configuration():
    """Test configuration and environment setup."""
    print("\n⚙️  Testing Configuration...")
    print("-" * 50)
    
    try:
        config = RunPodConfig.from_env()

        print(f"GPU Type: {config.gpu_type}")
        print(f"Worker Image: {config.worker_image}")
        print(f"Disk Size: {config.disk_size_gb}GB")
        print(f"Container Disk: {config.container_disk_gb}GB")

        if config.storage_name:
            print(f"Network Volume: {config.storage_name}")
            print(f"Mount Path: {config.volume_mount_path}")
        else:
            print("Network Volume: None configured")
        
        return True
        
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return False

async def run_all_tests():
    """Run all tests and report results."""
    print("🧪 Runpod Integration Tests")
    print("=" * 50)
    
    tests = [
        ("Configuration", check_configuration),
        ("API Connection", check_api_connection),
        ("Network Volumes", check_network_volumes),
        ("SSH Configuration", check_ssh_configuration),
        ("Worker Lifecycle", check_worker_lifecycle),
    ]
    
    results = {}
    
    for test_name, test_func in tests:
        try:
            result = await test_func()
            results[test_name] = result
        except Exception as e:
            print(f"❌ {test_name} test crashed: {e}")
            results[test_name] = False
    
    # Summary
    print("\n📊 Test Results Summary:")
    print("-" * 50)
    
    passed = 0
    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{test_name:<20} {status}")
        if result:
            passed += 1
    
    print(f"\nPassed: {passed}/{len(tests)} tests")
    
    if passed == len(tests):
        print("🎉 All tests passed! Runpod integration is ready.")
        return True
    else:
        print("⚠️  Some tests failed. Check configuration and API keys.")
        return False

def main():
    """Main CLI interface."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test Runpod API integration")
    parser.add_argument('--test', choices=['config', 'api', 'volumes', 'ssh', 'lifecycle', 'all'], 
                       default='all', help='Specific test to run')
    parser.add_argument('--quick', action='store_true', 
                       help='Skip worker lifecycle test (no actual spawning)')
    
    args = parser.parse_args()
    
    if args.test == 'config':
        success = asyncio.run(check_configuration())
    elif args.test == 'api':
        success = asyncio.run(check_api_connection())
    elif args.test == 'volumes':
        success = asyncio.run(check_network_volumes())
    elif args.test == 'ssh':
        success = asyncio.run(check_ssh_configuration())
    elif args.test == 'lifecycle':
        success = asyncio.run(check_worker_lifecycle())
    elif args.test == 'all':
        if args.quick:
            # Run all except lifecycle
            tests = [
                check_configuration(),
                check_api_connection(),
                check_network_volumes(),
                check_ssh_configuration(),
            ]
            results = asyncio.run(asyncio.gather(*tests, return_exceptions=True))
            success = all(r is True for r in results if not isinstance(r, Exception))
        else:
            success = asyncio.run(run_all_tests())
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main() 
