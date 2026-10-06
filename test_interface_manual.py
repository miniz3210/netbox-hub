#!/usr/bin/env python3
"""
Manual test script for the dynamic interface parser.
Run this to verify all functionality without pytest.
"""

import sys
import re

from utils.formatters import apply_auto_corrections


def normalize_port_shortname(port_name: str) -> str:
    """
    Shorten an interface name using only the user-configured ``port_shortening``
    auto-correction regex pipeline (``data/autocorrect_rules.yaml``).

    All legacy, hardcoded interface-abbreviation dictionaries and camelCase guessing
    have been removed. The output is determined purely by the regex replacements the
    user defines in the Standards Tab -> "Port Abbreviation Rules".

    Examples (factory defaults):
        "XGigabitEthernet0/0/31" -> "XGE0/0/31"
        "TenGigabitEthernet1/0/1" -> "Te1/0/1"
        "GigabitEthernet1/0/24" -> "GE1/0/24"
        "XGE0/0/31.100" -> "XGE0/0/31.100" (already short)

    Args:
        port_name: Full or abbreviated interface name

    Returns:
        Shortened interface name as produced by the active regex rules.
    """
    if not port_name:
        return ""
    p = re.sub(r"\s+", "", port_name.strip())
    return apply_auto_corrections(p, "port_shortening")


# Test cases
def run_tests():
    """Run comprehensive tests and report results."""
    tests = [
        # Huawei XGigabitEthernet
        ("XGigabitEthernet0/0/31", "XGE0/0/31"),
        ("XGigabitEthernet0/0/1", "XGE0/0/1"),
        
        # Cisco TenGigabitEthernet
        ("TenGigabitEthernet1/0/1", "Te1/0/1"),
        ("TenGigabitEthernet0/0/48", "Te0/0/48"),
        ("TenGigE1/1/1", "Te1/1/1"),
        
        # Cisco GigabitEthernet
("GigabitEthernet1/0/24", "GE1/0/24"),
        ("GigabitEthernet0/0/1", "GE0/0/1"),

        # Cisco FastEthernet
        ("FastEthernet0/1", "FE0/1"),
        
        # Cisco FortyGigabitEthernet
        ("FortyGigabitEthernet0/1", "Fo0/1"),
        
        # High-speed interfaces
        ("TwentyFiveGigE1/0/1", "Twe1/0/1"),
        ("HundredGigE0/0/1", "Hu0/0/1"),
        
        # Port-channel
        ("Port-channel10", "Po10"),
        ("port-channel1", "Po1"),
        
        # Sub-interfaces
        ("XGigabitEthernet0/0/31.100", "XGE0/0/31.100"),
        ("TenGigabitEthernet1/0/1.200", "Te1/0/1.200"),
        
        # Already shortened
        ("XGE0/0/31", "XGE0/0/31"),
        ("Te1/0/1", "Te1/0/1"),
        ("Gi1/0/24", "Gi1/0/24"),
        
        # Whitespace handling
        ("Gigabit Ethernet 1/0/24", "GE1/0/24"),
        (" XGigabitEthernet0/0/31 ", "XGE0/0/31"),
        
        # Management
        ("Management1", "Mgmt1"),
        
        # Case variations
        ("tengigabitethernet1/0/1", "Te1/0/1"),
        ("TENGIGABITETHERNET1/0/1", "Te1/0/1"),
    ]
    
    passed = 0
    failed = 0
    
    print("=" * 80)
    print("DYNAMIC INTERFACE NAME PARSER - TEST RESULTS")
    print("=" * 80)
    print()
    
    for input_val, expected in tests:
        result = normalize_port_shortname(input_val)
        status = "✓ PASS" if result == expected else "✗ FAIL"
        
        if result == expected:
            passed += 1
            print(f"{status}  {input_val:40} -> {result}")
        else:
            failed += 1
            print(f"{status}  {input_val:40} -> {result} (expected: {expected})")
    
    print()
    print("=" * 80)
    print(f"SUMMARY: {passed} passed, {failed} failed out of {passed + failed} tests")
    print("=" * 80)
    
    # Real-world scenario test
    print()
    print("REAL-WORLD SCENARIO TEST:")
    print("-" * 80)
    print("Uplink: Local Huawei to Remote Cisco")
    
    local_device = "ESPRWDC-SW01"
    local_port_full = "XGigabitEthernet0/0/31"
    local_port_short = normalize_port_shortname(local_port_full)
    
    remote_device = "Huawei-Core"
    remote_port_full = "TenGigabitEthernet1/0/1"
    remote_port_short = normalize_port_shortname(remote_port_full)
    
    uplink_local = f"to {remote_device}_{remote_port_short} [Uplink]"
    uplink_remote = f"to {local_device}_{local_port_short} [Uplink]"
    
    print(f"Local Port:  {local_port_full:40} -> {local_port_short}")
    print(f"Remote Port: {remote_port_full:40} -> {remote_port_short}")
    print()
    print(f"On {local_device}:  {uplink_local}")
    print(f"On {remote_device}:  {uplink_remote}")
    print("-" * 80)
    
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
