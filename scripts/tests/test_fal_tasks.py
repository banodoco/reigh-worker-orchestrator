#!/usr/bin/env python3
"""
Test script for fal.ai image generation task types.
Tests: qwen_image, qwen_image_2512, z_image_lightning

Usage:
    python scripts/tests/test_fal_tasks.py                    # Unit test (no API call)
    python scripts/tests/test_fal_tasks.py --live             # Live API test (needs FAL_KEY)
    python scripts/tests/test_fal_tasks.py --live qwen_image  # Test specific model
    python scripts/tests/test_fal_tasks.py --live --with-lora # Test with LoRA
"""

import os
import sys
import asyncio
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()


def run_parameter_building_checks() -> bool:
    """Run parameter-building checks used by both pytest and CLI mode."""
    from api_orchestrator.fal_utils import build_fal_lora_list
    from api_orchestrator.image_utils import normalize_resolution
    
    print("\n" + "="*60)
    print("UNIT TEST: Parameter Building Logic")
    print("="*60)
    
    all_passed = True
    
    # Test 1: build_fal_lora_list with list format
    print("\n📋 Test 1: build_fal_lora_list (list format)")
    params = {
        "loras": [
            {"path": "https://example.com/lora1.safetensors", "scale": 0.8},
            {"url": "https://example.com/lora2.safetensors", "strength": 1.0},
        ]
    }
    loras = build_fal_lora_list(params)
    expected = [
        {"path": "https://example.com/lora1.safetensors", "scale": 0.8},
        {"path": "https://example.com/lora2.safetensors", "scale": 1.0},
    ]
    if loras == expected:
        print(f"   ✅ PASSED: {loras}")
    else:
        print(f"   ❌ FAILED: Expected {expected}, got {loras}")
        all_passed = False
    
    # Test 2: build_fal_lora_list with dict format
    print("\n📋 Test 2: build_fal_lora_list (dict format)")
    params = {
        "additional_loras": {
            "https://example.com/lora1.safetensors": 0.5,
            "https://example.com/lora2.safetensors": 1.2,
        }
    }
    loras = build_fal_lora_list(params)
    if len(loras) == 2 and all(item["path"].startswith("https://") for item in loras):
        print(f"   ✅ PASSED: {loras}")
    else:
        print(f"   ❌ FAILED: {loras}")
        all_passed = False
    
    # Test 3: normalize_resolution
    print("\n📋 Test 3: normalize_resolution")
    test_cases = [
        ("512x512", "512*512"),
        ("400x225", "910*512"),  # Scaled up: shortest side (225) -> 512, ratio 2.28x
        ("1920x1080", "1200*675"),  # Scaled down to max 1200
        ("1200*800", "1200*800"),  # Already within bounds
    ]
    for input_res, expected_res in test_cases:
        result = normalize_resolution(input_res)
        if result == expected_res:
            print(f"   ✅ {input_res} -> {result}")
        else:
            print(f"   ❌ {input_res} -> Expected {expected_res}, got {result}")
            all_passed = False
    
    # Test 4: Endpoint selection logic
    print("\n📋 Test 4: Endpoint mapping")
    FALAI_IMAGE_MODELS = {
        "qwen_image": "fal-ai/qwen-image",
        "qwen_image_2512": "fal-ai/qwen-image-2512",
        "z_image_turbo": "fal-ai/z-image/turbo",
    }
    
    for task_type, endpoint in FALAI_IMAGE_MODELS.items():
        print(f"   {task_type} -> {endpoint}")
    print("   (LoRAs are passed via 'loras' parameter on base endpoint)")
    
    print(f"\n{'='*60}")
    if all_passed:
        print("✅ All unit tests PASSED")
    else:
        print("❌ Some unit tests FAILED")
    print("="*60)
    
    return all_passed


def test_parameter_building():
    """Pytest entrypoint for fal parameter-building checks."""
    assert run_parameter_building_checks()


# Model-specific LoRAs from Huggingface
# Using smaller, compatible LoRAs that work with fal.ai
LORA_FOR_MODEL = {
    "qwen_image": {
        "path": "https://huggingface.co/peteromallet/ad_motion_loras/resolve/main/style_transfer_qwen_edit_2_000011250.safetensors",
        "scale": 0.8,
        "name": "Style Transfer LoRA"
    },
    "qwen_image_2512": {
        "path": "https://huggingface.co/peteromallet/ad_motion_loras/resolve/main/style_transfer_qwen_edit_2_000011250.safetensors",
        "scale": 0.8,
        "name": "Style Transfer LoRA"
    },
    "z_image_turbo": {
        "path": "https://huggingface.co/peteromallet/ad_motion_loras/resolve/main/style_transfer_qwen_edit_2_000011250.safetensors",
        "scale": 0.8,
        "name": "Style Transfer LoRA"
    },
}


async def run_fal_task_live(task_type: str, with_lora: bool = False):
    """Live test: Call actual fal.ai API."""
    import httpx
    from api_orchestrator.main import process_api_task
    
    print(f"\n{'='*60}")
    print(f"LIVE TEST: {task_type} (LoRA: {with_lora})")
    print(f"{'='*60}")
    
    # Build test task
    task = {
        "task_id": f"test-{task_type}-{'lora' if with_lora else 'base'}",
        "task_type": task_type,
        "params": {
            "prompt": "A beautiful sunset over a calm ocean, photorealistic",
            "resolution": "512x512",
            "seed": 42,
            "negative_prompt": "blurry, low quality, distorted",
        }
    }
    
    # Add model-specific LoRA if requested
    if with_lora and task_type in LORA_FOR_MODEL:
        lora_info = LORA_FOR_MODEL[task_type]
        task["params"]["loras"] = [
            {
                "path": lora_info["path"],
                "scale": lora_info["scale"]
            }
        ]
        print(f"Using LoRA: {lora_info['name']}")
        print(f"  URL: {lora_info['path']}")
    
    print(f"Task params: {task['params']}")
    
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(limits=limits, timeout=120.0) as client:
        try:
            result = await process_api_task(task, client)
            print("\n✅ SUCCESS!")
            print(f"Result: {result}")
            
            if 'output_url' in result:
                print(f"\n🖼️  Output URL: {result['output_url']}")
            if 'output_location' in result:
                print(f"📍 Output Location: {result['output_location']}")
            
            return True, result
            
        except Exception as e:
            print(f"\n❌ FAILED: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)


async def main():
    parser = argparse.ArgumentParser(description="Test fal.ai task types")
    parser.add_argument('task_type', nargs='?', help='Specific task type to test')
    parser.add_argument('--live', action='store_true', help='Run live API tests (needs FAL_KEY)')
    parser.add_argument('--with-lora', action='store_true', help='Test with LoRA (live only)')
    
    args = parser.parse_args()
    
    # Always run unit tests first
    unit_tests_passed = run_parameter_building_checks()
    
    if not args.live:
        print("\n💡 Tip: Run with --live to test against actual fal.ai API")
        print("   (Requires FAL_KEY environment variable)")
        return 0 if unit_tests_passed else 1
    
    # Live tests require FAL_KEY
    if not os.getenv('FAL_KEY'):
        print("\n❌ FAL_KEY environment variable not set!")
        print("   Please set it in your .env file or environment")
        return 1
    
    print("\n🔑 FAL_KEY is set, running live tests...")
    
    # Determine which task types to test
    all_task_types = ["qwen_image", "qwen_image_2512", "z_image_turbo"]
    
    if args.task_type:
        if args.task_type not in all_task_types:
            print(f"❌ Unknown task type: {args.task_type}")
            print(f"   Valid types: {', '.join(all_task_types)}")
            return 1
        task_types_to_test = [args.task_type]
    else:
        task_types_to_test = all_task_types
    
    print(f"\n🧪 Testing task types: {', '.join(task_types_to_test)}")
    print(f"   With LoRA: {args.with_lora}")
    
    # Run tests
    results = {}
    for task_type in task_types_to_test:
        success, result = await run_fal_task_live(task_type, with_lora=args.with_lora)
        results[task_type] = {"success": success, "result": result}
    
    # Summary
    print(f"\n{'='*60}")
    print("LIVE TEST SUMMARY")
    print(f"{'='*60}")
    
    passed = 0
    failed = 0
    for task_type, data in results.items():
        status = "✅ PASSED" if data["success"] else "❌ FAILED"
        print(f"  {task_type}: {status}")
        if data["success"]:
            passed += 1
        else:
            failed += 1
    
    print(f"\nTotal: {passed} passed, {failed} failed")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
