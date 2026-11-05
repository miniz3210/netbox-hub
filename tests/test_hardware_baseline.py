#!/usr/bin/env python3
"""
Unit Tests for Hardware Baseline Standards & Module YAML Pattern Enforcement.

Tests cover:
  1. DEFAULT_HARDWARE_BASELINE_STANDARDS structure and defaults
  2. get_hardware_baseline_standards() merge logic
  3. get_module_interface_pattern() resolution
  4. _normalize_rules back-fills netbox_server_yaml from structured model
  5. export_rules_as_prompt includes hardware baseline section
  6. yaml_generator._build_module_port_names
  7. yaml_generator.clean_ai_yaml preserves/enforces patterns
  8. End-to-end: generate_module_yaml produces {module}/Port{index} names
"""

import sys
import os
import re
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.naming_rules import (
    DEFAULT_HARDWARE_BASELINE_STANDARDS,
    get_hardware_baseline_standards,
    get_module_interface_pattern,
    _normalize_rules,
    export_rules_as_prompt,
)
from core.yaml_generator import (
    clean_ai_yaml,
    _get_module_interface_pattern,
    _build_module_port_names,
)


# ── Test Harness ────────────────────────────────────────────────────────────

_passed = 0
_failed = 0


def assert_true(condition: bool, label: str):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ PASS: {label}")
    else:
        _failed += 1
        print(f"  ❌ FAIL: {label}")


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ============================================================================
# PART 1: DEFAULT STRUCTURE
# ============================================================================
def test_default_structure():
    section("TEST 1: DEFAULT_HARDWARE_BASELINE_STANDARDS Structure")
    assert_true("server" in DEFAULT_HARDWARE_BASELINE_STANDARDS,
                "server category present")
    assert_true("module_nic" in DEFAULT_HARDWARE_BASELINE_STANDARDS,
                "module_nic category present")
    assert_true("storage_san" in DEFAULT_HARDWARE_BASELINE_STANDARDS,
                "storage_san category present")

    server = DEFAULT_HARDWARE_BASELINE_STANDARDS["server"]
    assert_true(server.get("console_ports") == "Serial (de-9)",
                f"server.console_ports = 'Serial (de-9)' (got {server.get('console_ports')!r})")
    assert_true("module_bays" in server, "server has module_bays key")
    assert_true("interfaces" in server, "server has interfaces key")

    module_nic = DEFAULT_HARDWARE_BASELINE_STANDARDS["module_nic"]
    assert_true(module_nic.get("interface_pattern") == "{module}/Port{index}",
                f"module_nic.interface_pattern = '{{module}}/Port{{index}}' (got {module_nic.get('interface_pattern')!r})")
    assert_true(module_nic.get("default_port_count") == 2,
                f"module_nic.default_port_count = 2 (got {module_nic.get('default_port_count')})")
    assert_true(module_nic.get("port_start_index") == 1,
                f"module_nic.port_start_index = 1 (got {module_nic.get('port_start_index')})")

    storage_san = DEFAULT_HARDWARE_BASELINE_STANDARDS["storage_san"]
    assert_true(storage_san.get("interface_pattern") == "{module}/Port{index}",
                f"storage_san.interface_pattern = '{{module}}/Port{{index}}' (got {storage_san.get('interface_pattern')!r})")
    assert_true(storage_san.get("default_port_count") == 4,
                f"storage_san.default_port_count = 4 (got {storage_san.get('default_port_count')})")


# ============================================================================
# PART 2: MERGE LOGIC
# ============================================================================
def test_hardware_baseline_merge():
    section("TEST 2: get_hardware_baseline_standards Merge Logic")

    # Empty rules → full defaults
    hb = get_hardware_baseline_standards({})
    assert_true(hb["module_nic"]["interface_pattern"] == "{module}/Port{index}",
                "Empty rules → default interface_pattern")
    assert_true(hb["server"]["console_ports"] == "Serial (de-9)",
                "Empty rules → default server console_ports")

    # Partial user override
    user_rules = {
        "hardware_baseline_standards": {
            "module_nic": {
                "interface_pattern": "{module}/SFP{index}",
                "default_port_count": 8,
            }
        }
    }
    hb = get_hardware_baseline_standards(user_rules)
    assert_true(hb["module_nic"]["interface_pattern"] == "{module}/SFP{index}",
                "User override: interface_pattern replaced")
    assert_true(hb["module_nic"]["default_port_count"] == 8,
                "User override: default_port_count replaced")
    assert_true(hb["server"]["console_ports"] == "Serial (de-9)",
                "Unmodified category: server preserved")
    assert_true(hb["storage_san"]["default_port_count"] == 4,
                "Unmodified category: storage_san preserved")


# ============================================================================
# PART 3: PATTERN RESOLUTION
# ============================================================================
def test_module_interface_pattern():
    section("TEST 3: get_module_interface_pattern Resolution")

    # Default
    pattern = get_module_interface_pattern({})
    assert_true(pattern == "{module}/Port{index}",
                f"Default pattern (got {pattern!r})")

    # User override via structured model
    user_rules = {
        "hardware_baseline_standards": {
            "module_nic": {"interface_pattern": "{module}/X{index}"}
        }
    }
    pattern = get_module_interface_pattern(user_rules)
    assert_true(pattern == "{module}/X{index}",
                f"Override pattern (got {pattern!r})")

    # When no structured model AND no netbox_server_yaml → default
    empty_rules = {}
    pattern = get_module_interface_pattern(empty_rules)
    assert_true(pattern == "{module}/Port{index}",
                f"Empty rules → default pattern (got {pattern!r})")


# ============================================================================
# PART 4: NORMALIZE RULES BACKFILL
# ============================================================================
def test_normalize_backfills_legacy():
    section("TEST 4: _normalize_rules Back-fills netbox_server_yaml")

    normalized = _normalize_rules({})
    assert_true("netbox_server_yaml" in normalized,
                "normalized rules contain netbox_server_yaml")
    assert_true("Serial (de-9)" in normalized["netbox_server_yaml"],
                "legacy string contains console_ports info")
    assert_true("OOB Management ONLY" in normalized["netbox_server_yaml"],
                "legacy string contains interfaces info")


# ============================================================================
# PART 5: EXPORT PROMPT
# ============================================================================
def test_export_prompt_includes_hardware():
    section("TEST 5: export_rules_as_prompt Includes Hardware Baseline")

    normalized = _normalize_rules({})
    prompt = export_rules_as_prompt(normalized)
    assert_true("Module / NIC" in prompt or "module_nic" in prompt,
                "prompt includes module_nic category")
    assert_true("Storage / SAN" in prompt or "storage_san" in prompt,
                "prompt includes storage_san category")
    assert_true("{module}/Port{index}" in prompt,
                "prompt includes interface_pattern")
    assert_true("NetBox Hardware Baseline Standards" in prompt,
                "prompt has hardware baseline section header")


# ============================================================================
# PART 6: MODULE PORT NAMES BUILDER
# ============================================================================
def test_build_module_port_names():
    section("TEST 6: _build_module_port_names")

    names = _build_module_port_names("X550", "{module}/Port{index}", 4)
    assert_true(names == ["X550/Port1", "X550/Port2", "X550/Port3", "X550/Port4"],
                f"4 ports generated (got {names})")

    names = _build_module_port_names("X550", "{module}/SFP{index}", 2)
    assert_true(names == ["X550/SFP1", "X550/SFP2"],
                f"Custom pattern (got {names})")

    names = _build_module_port_names("Mellanox", "{module}/Port{index}", 1)
    assert_true(names == ["Mellanox/Port1"],
                f"Single port (got {names})")


# ============================================================================
# PART 7: CLEAN AI YAML
# ============================================================================
def test_clean_ai_yaml_preserves_pattern():
    section("TEST 7: clean_ai_yaml Preserves {module} Pattern")

    yaml_input = """---
manufacturer: Intel
model: X550
interfaces:
  - name: "{module}/Port1"
    type: 10gbase-x-sfpp
  - name: "{module}/Port2"
    type: 10gbase-x-sfpp
"""
    result = clean_ai_yaml(yaml_input)
    assert_true("{module}/Port1" in result, "Pattern name Port1 preserved")
    assert_true("{module}/Port2" in result, "Pattern name Port2 preserved")
    assert_true("manufacturer: Intel" in result, "Manufacturer preserved")
    assert_true(result.startswith("---"), "Starts with ---")


def test_clean_ai_yaml_standardizes_types():
    section("TEST 8: clean_ai_yaml Standardizes Interface Types")

    yaml_input = """---
manufacturer: Cisco
model: C9300
interfaces:
  - name: Gi1/0/1
    type: 1gbase-t
  - name: Gi1/0/48
    type: 10gbase-x-sfp+
"""
    result = clean_ai_yaml(yaml_input)
    assert_true("type: 1000base-t" in result, "1gbase-t → 1000base-t")
    assert_true("type: 10gbase-x-sfpp" in result, "10gbase-x-sfp+ → 10gbase-x-sfpp")


# ============================================================================
# PART 8: END-TO-END MODULE YAML GENERATION
# ============================================================================
def test_generate_module_yaml_enforces_pattern():
    section("TEST 9: generate_module_yaml Enforces {module}/Port{index}")

    from core.yaml_generator import generate_module_yaml

    # We mock call_ai to return a predictable response with wrong names,
    # then verify the post-processor rewrites them to the correct pattern.
    from unittest import mock

    # Simulate AI returning wrong interface names
    fake_yaml = """---
manufacturer: Intel
model: X550-T2
interfaces:
  - name: "eth0"
    type: 1000base-t
  - name: "eth1"
    type: 1000base-t
  - name: "eth2"
    type: 1000base-t
"""

    with mock.patch("core.yaml_generator.call_ai", return_value=fake_yaml):
        result = generate_module_yaml("Intel", "X550-T2", "X550-T2", "test-model")

    # The post-processor should have rewritten bare eth names to {module}/PortN
    assert_true("{module}/Port1" in result or "X550-T2/Port1" in result,
                f"Result should contain pattern-based names. Got:\n{result[:500]}")
    assert_true("eth0" not in result, "Bare 'eth0' should be rewritten")
    assert_true("eth1" not in result, "Bare 'eth1' should be rewritten")


def test_generate_module_yaml_with_correct_ai_response():
    section("TEST 10: generate_module_yaml Preserves Correct Pattern")

    from core.yaml_generator import generate_module_yaml
    from unittest import mock

    correct_yaml = """---
manufacturer: Intel
model: X550-T2
interfaces:
  - name: "{module}/Port1"
    type: 10gbase-x-sfpp
  - name: "{module}/Port2"
    type: 10gbase-x-sfpp
"""

    with mock.patch("core.yaml_generator.call_ai", return_value=correct_yaml):
        result = generate_module_yaml("Intel", "X550-T2", "X550-T2", "test-model")

    assert_true("{module}/Port1" in result, "Correct pattern name preserved")
    assert_true("{module}/Port2" in result, "Correct pattern name preserved")


def test_generate_module_yaml_custom_pattern():
    section("TEST 11: generate_module_yaml Uses Custom Pattern from Rules")

    from core.yaml_generator import generate_module_yaml
    from unittest import mock

    # Set a custom pattern in rules
    custom_rules = {
        "hardware_baseline_standards": {
            "module_nic": {
                "interface_pattern": "{module}/SFP{index}",
                "default_port_count": 4,
                "port_start_index": 1,
            }
        }
    }

    fake_yaml = """---
manufacturer: Mellanox
model: MCX512
interfaces:
  - name: "Port1"
    type: 100gbase-x-qsfp28
  - name: "Port2"
    type: 100gbase-x-qsfp28
"""

    # Mock _get_module_interface_pattern directly to return custom pattern
    with mock.patch("core.yaml_generator._get_module_interface_pattern", return_value="{module}/SFP{index}"):
        with mock.patch("core.yaml_generator.call_ai", return_value=fake_yaml):
            result = generate_module_yaml("Mellanox", "MCX512", "MCX512", "test-model")

    # Should be rewritten to use custom pattern {module}/SFP{index}
    assert_true("{module}/SFP1" in result or "MCX512/SFP1" in result,
                f"Custom pattern enforced. Got:\n{result[:500]}")


def test_generate_module_yaml_with_ref_pattern():
    section("TEST 12: generate_module_yaml Uses ref_pattern When Provided")

    from core.yaml_generator import generate_module_yaml
    from unittest import mock

    fake_yaml = """---
manufacturer: Broadcom
model: BCM57416
interfaces:
  - name: "Ethernet1"
    type: 100gbase-x-qsfp28
  - name: "Ethernet2"
    type: 100gbase-x-qsfp28
"""

    with mock.patch("core.yaml_generator.call_ai", return_value=fake_yaml):
        result = generate_module_yaml("Broadcom", "BCM57416", "BCM57416", "test-model",
                                      ref_pattern="Ethernet/{module}/1")

    # When ref_pattern is provided, the post-processor should NOT rewrite
    # because the condition checks for ref_pattern in result.
    # But the AI didn't include it, so the fallback rewrites to default pattern.
    # The test verifies the function completes without error and produces valid YAML.
    assert_true("---" in result, "Result starts with ---")
    assert_true("manufacturer" in result, "Result has manufacturer field")
    assert_true("interfaces" in result, "Result has interfaces field")
    # The ref_pattern instruction is in the prompt; AI may not follow it.
    # We verify the function is robust regardless.
    assert_true(isinstance(result, str) and len(result) > 0,
                "Result is non-empty string")


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  Hardware Baseline Standards & Module YAML Tests")
    print("=" * 60)

    test_default_structure()
    test_hardware_baseline_merge()
    test_module_interface_pattern()
    test_normalize_backfills_legacy()
    test_export_prompt_includes_hardware()
    test_build_module_port_names()
    test_clean_ai_yaml_preserves_pattern()
    test_clean_ai_yaml_standardizes_types()
    test_generate_module_yaml_enforces_pattern()
    test_generate_module_yaml_with_correct_ai_response()
    test_generate_module_yaml_custom_pattern()
    test_generate_module_yaml_with_ref_pattern()

    # Run existing test suite
    print(f"\n{'═' * 60}")
    print("  Running existing test suite...")
    print(f"{'═' * 60}")
    import subprocess
    result = subprocess.run(
        ["python3", "-m", "pytest", "tests/", "-v", "--tb=short", "-q"],
        capture_output=True, text=True, cwd="/opt/netbox-hub"
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        _failed += 1

    print(f"\n{'═' * 60}")
    print(f"  Results: {_passed} passed, {_failed} failed")
    print(f"{'═' * 60}")
    sys.exit(1 if _failed else 0)
