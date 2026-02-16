#!/usr/bin/env python3
"""
Test script for qwen_edit_model variants in qwen_image_edit task type.
Tests: qwen-edit (default), qwen-edit-2511, qwen-edit-2509

Usage:
    python scripts/tests/test_qwen_edit_models.py                     # Unit test (no API call)
    python scripts/tests/test_qwen_edit_models.py --live              # Live API test all models
    python scripts/tests/test_qwen_edit_models.py --live qwen-edit-2511  # Test specific model
    python scripts/tests/test_qwen_edit_models.py --live --with-lora  # Test with LoRA
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


# Test image URL (a public image for testing)
TEST_IMAGE_URL = "https://wczysqzxlwdndgxitrvc.supabase.co/storage/v1/object/public/image_uploads/702a2ebf-569e-4f7d-a7df-78e7c1847000/uploads/1766004551919-7rsoekdg.png"


def test_parameter_building():
    """Unit test: Verify qwen_edit_model endpoint mapping logic."""
    print("\n" + "="*60)
    print("UNIT TEST: qwen_edit_model Parameter Mapping")
    print("="*60)

    all_passed = True

    # Test 1: Endpoint mapping without LoRAs
    print("\n📋 Test 1: Endpoint mapping (without LoRAs)")
    test_cases_no_lora = [
        ("qwen-edit", "wavespeed-ai/qwen-image/edit-lora", False),
        ("qwen-edit-2511", "wavespeed-ai/qwen-image/edit-2511", True),
        ("qwen-edit-2509", "wavespeed-ai/qwen-image/edit-plus", True),
        (None, "wavespeed-ai/qwen-image/edit-lora", False),  # Default case
    ]

    for model, expected_endpoint, expected_uses_array in test_cases_no_lora:
        qwen_edit_model = model if model else "qwen-edit"
        has_loras = False

        if qwen_edit_model == "qwen-edit-2511":
            endpoint = "wavespeed-ai/qwen-image/edit-2511-lora" if has_loras else "wavespeed-ai/qwen-image/edit-2511"
            uses_array = True
        elif qwen_edit_model == "qwen-edit-2509":
            endpoint = "wavespeed-ai/qwen-image/edit-plus-lora" if has_loras else "wavespeed-ai/qwen-image/edit-plus"
            uses_array = True
        else:
            endpoint = "wavespeed-ai/qwen-image/edit-lora"
            uses_array = False

        model_display = model if model else "(default/None)"
        if endpoint == expected_endpoint and uses_array == expected_uses_array:
            print(f"   ✅ {model_display} -> {endpoint} (array={uses_array})")
        else:
            print(f"   ❌ {model_display} -> Expected {expected_endpoint}, got {endpoint}")
            all_passed = False

    # Test 2: Endpoint mapping with LoRAs
    print("\n📋 Test 2: Endpoint mapping (with LoRAs)")
    test_cases_with_lora = [
        ("qwen-edit", "wavespeed-ai/qwen-image/edit-lora"),
        ("qwen-edit-2511", "wavespeed-ai/qwen-image/edit-2511-lora"),
        ("qwen-edit-2509", "wavespeed-ai/qwen-image/edit-plus-lora"),
    ]

    for model, expected_endpoint in test_cases_with_lora:
        qwen_edit_model = model
        has_loras = True

        if qwen_edit_model == "qwen-edit-2511":
            endpoint = "wavespeed-ai/qwen-image/edit-2511-lora" if has_loras else "wavespeed-ai/qwen-image/edit-2511"
        elif qwen_edit_model == "qwen-edit-2509":
            endpoint = "wavespeed-ai/qwen-image/edit-plus-lora" if has_loras else "wavespeed-ai/qwen-image/edit-plus"
        else:
            endpoint = "wavespeed-ai/qwen-image/edit-lora"

        if endpoint == expected_endpoint:
            print(f"   ✅ {model} + LoRA -> {endpoint}")
        else:
            print(f"   ❌ {model} + LoRA -> Expected {expected_endpoint}, got {endpoint}")
            all_passed = False

    # Test 3: API format differences
    print("\n📋 Test 3: API format (image vs images array)")
    print("   qwen-edit (default): uses 'image' string")
    print("   qwen-edit-2511: uses 'images' array")
    print("   qwen-edit-2509: uses 'images' array")

    print(f"\n{'='*60}")
    if all_passed:
        print("✅ All unit tests PASSED")
    else:
        print("❌ Some unit tests FAILED")
    print("="*60)

    return all_passed


async def test_qwen_edit_model_live(model: str, with_lora: bool = False):
    """Live test: Call actual Wavespeed API with specified model."""
    import httpx
    from api_orchestrator.main import process_api_task

    print(f"\n{'='*60}")
    print(f"LIVE TEST: qwen_edit_model={model} (LoRA: {with_lora})")
    print("="*60)

    # Build test task
    task = {
        "task_id": f"test-qwen-edit-{model}-{'lora' if with_lora else 'base'}",
        "task_type": "qwen_image_edit",
        "params": {
            "prompt": "A beautiful sunset over a calm ocean",
            "image": TEST_IMAGE_URL,
            "resolution": "1024x576",
            "seed": 42,
            "output_format": "jpeg",
            "qwen_edit_model": model,
        }
    }

    # Add LoRA if requested
    if with_lora:
        task["params"]["loras"] = [
            {
                "path": "https://huggingface.co/peteromallet/ad_motion_loras/resolve/main/style_transfer_qwen_edit_2_000011250.safetensors",
                "scale": 0.8
            }
        ]
        print(f"Using LoRA: style_transfer_qwen_edit_2")

    # Determine expected endpoint for logging
    has_loras = bool(task["params"].get("loras"))
    if model == "qwen-edit-2511":
        expected_endpoint = "wavespeed-ai/qwen-image/edit-2511-lora" if has_loras else "wavespeed-ai/qwen-image/edit-2511"
    elif model == "qwen-edit-2509":
        expected_endpoint = "wavespeed-ai/qwen-image/edit-plus-lora" if has_loras else "wavespeed-ai/qwen-image/edit-plus"
    else:
        expected_endpoint = "wavespeed-ai/qwen-image/edit-lora"

    print(f"Expected endpoint: {expected_endpoint}")
    print(f"Task params: {task['params']}")

    limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
    async with httpx.AsyncClient(limits=limits, timeout=300.0) as client:
        try:
            result = await process_api_task(task, client)
            print(f"\n✅ SUCCESS!")
            print(f"Result keys: {list(result.keys())}")

            if 'output_url' in result:
                print(f"\n🖼️  Output URL: {result['output_url']}")
            if 'output_location' in result:
                print(f"📍 Output Location: {result['output_location']}")
            if 'duration_seconds' in result:
                print(f"⏱️  Duration: {result['duration_seconds']:.2f}s")

            return True, result

        except Exception as e:
            print(f"\n❌ FAILED: {e}")
            import traceback
            traceback.print_exc()
            return False, str(e)


async def main():
    parser = argparse.ArgumentParser(description="Test qwen_edit_model variants")
    parser.add_argument('model', nargs='?', help='Specific model to test (qwen-edit, qwen-edit-2511, qwen-edit-2509)')
    parser.add_argument('--live', action='store_true', help='Run live API tests (needs WAVESPEED_API_KEY)')
    parser.add_argument('--with-lora', action='store_true', help='Test with LoRA (live only)')

    args = parser.parse_args()

    # Always run unit tests first
    unit_tests_passed = test_parameter_building()

    if not args.live:
        print("\n💡 Tip: Run with --live to test against actual Wavespeed API")
        print("   (Requires the live-test environment variable)")
        return 0 if unit_tests_passed else 1

    # Live tests require WAVESPEED_API_KEY
    if not os.getenv('WAVESPEED_API_KEY'):
        print("\n❌ Live-test environment variable is not set!")
        print("   Please set it in your .env file or environment")
        return 1

    print("\n🔑 Live-test credential is set, running live tests...")

    # Determine which models to test
    all_models = ["qwen-edit", "qwen-edit-2511", "qwen-edit-2509"]

    if args.model:
        if args.model not in all_models:
            print(f"❌ Unknown model: {args.model}")
            print(f"   Valid models: {', '.join(all_models)}")
            return 1
        models_to_test = [args.model]
    else:
        models_to_test = all_models

    print(f"\n🧪 Testing models: {', '.join(models_to_test)}")
    print(f"   With LoRA: {args.with_lora}")

    # Run tests
    results = {}
    for model in models_to_test:
        success, result = await test_qwen_edit_model_live(model, with_lora=args.with_lora)
        results[model] = {"success": success, "result": result}

    # Summary
    print(f"\n{'='*60}")
    print("LIVE TEST SUMMARY")
    print("="*60)

    passed = 0
    failed = 0
    for model, data in results.items():
        status = "✅ PASSED" if data["success"] else "❌ FAILED"
        print(f"  {model}: {status}")
        if not data["success"]:
            print(f"     Error: {data['result'][:100]}...")
        if data["success"]:
            passed += 1
        else:
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
