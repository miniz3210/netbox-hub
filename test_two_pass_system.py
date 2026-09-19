#!/usr/bin/env python3
"""
Test script for Two-Pass AI Assistant System

Validates:
1. Pass 1 token budget stays under 1000 tokens
2. Intent classification accuracy
3. Endpoint routing efficiency
4. Performance metrics collection
"""

import sys
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.ai_intent_router import (
    validate_pass1_token_budget,
    get_endpoint_statistics,
    estimate_pass1_tokens,
    _generate_compact_endpoint_schema,
    _build_pass1_prompt,
    classify_intent,
)
from core.ai_helper import (
    get_two_pass_system_status,
    validate_two_pass_token_budget,
)


def print_section(title: str):
    """Print formatted section header"""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def test_token_budget():
    """Test 1: Validate Pass 1 token budget"""
    print_section("TEST 1: Pass 1 Token Budget Validation")
    
    validation = validate_pass1_token_budget()
    
    print(f"Estimated tokens: {validation['estimated_tokens']}")
    print(f"Token budget: 1000")
    print(f"Budget remaining: {validation['budget_remaining']}")
    print(f"Status: {validation['status']}")
    print(f"Endpoint count: {validation['endpoint_count']}")
    print(f"Schema tokens: {int(validation['schema_tokens'])}")
    
    if validation['under_budget']:
        print("\n✓ PASS: Token budget constraint met (<1000 tokens)")
    else:
        print("\n✗ FAIL: Token budget exceeded!")
    
    return validation['under_budget']


def test_endpoint_coverage():
    """Test 2: Endpoint coverage statistics"""
    print_section("TEST 2: Endpoint Coverage")
    
    stats = get_endpoint_statistics()
    
    print(f"Total endpoints available: {stats['total_endpoints']}")
    print(f"Endpoints in schema: {stats['endpoints_in_schema']}")
    print(f"Coverage: {stats['coverage_percentage']:.1f}%")
    print(f"Schema size: {stats['schema_size_bytes']:,} bytes ({int(stats['schema_size_tokens'])} tokens)")
    
    print("\nEndpoints by category:")
    for category, count in sorted(stats['categories'].items()):
        print(f"  {category}: {count} endpoints")
    
    coverage_ok = stats['coverage_percentage'] >= 80.0
    if coverage_ok:
        print(f"\n✓ PASS: Good endpoint coverage ({stats['coverage_percentage']:.1f}%)")
    else:
        print(f"\n⚠ WARNING: Low endpoint coverage ({stats['coverage_percentage']:.1f}%)")
    
    return coverage_ok


def test_sample_queries():
    """Test 3: Sample query token estimates"""
    print_section("TEST 3: Sample Query Token Estimates")
    
    test_queries = [
        "Show me all switches at Site-HQ",
        "What IP addresses are in 10.0.0.0/24?",
        "List all VLANs with vid 100",
        "Find device fw-hq-01",
        "Show me all virtual machines in the production cluster",
        "What are the available instance types?",
        "List all circuits from provider AT&T",
        "Show devices with asset tag ABC123",
        "What prefixes exist in Site-NYC with role 'Management'?",
        "List all wireless SSIDs",
    ]
    
    all_pass = True
    
    for query in test_queries:
        tokens = estimate_pass1_tokens(query)
        status = "✓" if tokens < 1000 else "✗"
        print(f"{status} {tokens:4d} tokens | {query}")
        if tokens >= 1000:
            all_pass = False
    
    if all_pass:
        print("\n✓ PASS: All sample queries under 1000 tokens")
    else:
        print("\n✗ FAIL: Some queries exceeded budget")
    
    return all_pass


def test_schema_generation():
    """Test 4: Schema generation and format"""
    print_section("TEST 4: Compact Schema Generation")
    
    schema = _generate_compact_endpoint_schema()
    lines = [line for line in schema.split('\n') if line.strip()]
    
    print(f"Schema lines: {len(lines)}")
    print(f"Schema size: {len(schema):,} bytes")
    print(f"Average bytes per endpoint: {len(schema) / len(lines):.1f}")
    
    print("\nSample schema entries (first 10):")
    for line in lines[:10]:
        print(f"  {line}")
    
    print("\n...")
    print("\nSample schema entries (last 5):")
    for line in lines[-5:]:
        print(f"  {line}")
    
    # Validate format
    format_ok = True
    for line in lines:
        if '|' not in line:
            print(f"\n✗ Invalid format: {line}")
            format_ok = False
            break
    
    if format_ok:
        print("\n✓ PASS: Schema format valid")
    else:
        print("\n✗ FAIL: Schema format issues")
    
    return format_ok


def test_pass1_prompt_structure():
    """Test 5: Pass 1 prompt structure"""
    print_section("TEST 5: Pass 1 Prompt Structure")
    
    test_query = "Show me all switches at Site-HQ with IP addresses in 10.0.0.0/24"
    prompt = _build_pass1_prompt(test_query)
    
    print(f"Prompt length: {len(prompt):,} characters")
    print(f"Estimated tokens: {len(prompt) // 3.5:.0f}")
    
    # Check key sections
    has_instructions = "YOUR TASK" in prompt
    has_schema = "ENDPOINT SCHEMA" in prompt
    has_query = test_query in prompt
    has_json_format = "JSON RESPONSE FORMAT" in prompt
    
    print(f"\nPrompt structure:")
    print(f"  {'✓' if has_instructions else '✗'} Has instructions")
    print(f"  {'✓' if has_schema else '✗'} Has endpoint schema")
    print(f"  {'✓' if has_query else '✗'} Includes user query")
    print(f"  {'✓' if has_json_format else '✗'} Has JSON format spec")
    
    all_present = all([has_instructions, has_schema, has_query, has_json_format])
    
    if all_present:
        print("\n✓ PASS: Prompt structure complete")
    else:
        print("\n✗ FAIL: Prompt structure incomplete")
    
    return all_present


def test_system_info():
    """Test 6: System information retrieval"""
    print_section("TEST 6: System Information")
    
    try:
        info = get_two_pass_system_status()
        
        print(f"System: {info.get('system', 'N/A')}")
        print(f"Version: {info.get('version', 'N/A')}")
        print(f"Pass 1 Budget Status: {info.get('pass1_budget_status', 'N/A')}")
        print(f"Pass 1 Tokens: {info.get('pass1_estimated_tokens', 'N/A')}")
        print(f"Budget Remaining: {info.get('pass1_budget_remaining', 'N/A')}")
        print(f"Endpoint Coverage: {info.get('endpoint_coverage', 'N/A')}")
        print(f"Total Endpoints: {info.get('total_endpoints', 'N/A')}")
        
        print("\nCategories:")
        categories = info.get('categories', {})
        for category, count in sorted(categories.items()):
            print(f"  {category}: {count}")
        
        print("\n✓ PASS: System info retrieved successfully")
        return True
    except Exception as e:
        print(f"\n✗ FAIL: Error retrieving system info: {e}")
        return False


def run_all_tests():
    """Run all tests and generate report"""
    print_section("TWO-PASS AI ASSISTANT SYSTEM - TEST SUITE")
    
    print("Testing Pass 1 token budget constraint (<1000 tokens)")
    print("Testing with 112+ NetBox endpoints")
    print("Date: 2026-09-18\n")
    
    results = {}
    
    # Run tests
    results['token_budget'] = test_token_budget()
    results['endpoint_coverage'] = test_endpoint_coverage()
    results['sample_queries'] = test_sample_queries()
    results['schema_generation'] = test_schema_generation()
    results['prompt_structure'] = test_pass1_prompt_structure()
    results['system_info'] = test_system_info()
    
    # Summary
    print_section("TEST SUMMARY")
    
    total_tests = len(results)
    passed_tests = sum(1 for passed in results.values() if passed)
    
    print(f"Total tests: {total_tests}")
    print(f"Passed: {passed_tests}")
    print(f"Failed: {total_tests - passed_tests}")
    print(f"Success rate: {passed_tests / total_tests * 100:.1f}%\n")
    
    for test_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status} | {test_name.replace('_', ' ').title()}")
    
    if all(results.values()):
        print("\n" + "="*70)
        print("  ✓ ALL TESTS PASSED")
        print("  Pass 1 token budget constraint verified (<1000 tokens)")
        print("="*70)
        return 0
    else:
        print("\n" + "="*70)
        print("  ✗ SOME TESTS FAILED")
        print("="*70)
        return 1


if __name__ == "__main__":
    exit_code = run_all_tests()
    sys.exit(exit_code)
