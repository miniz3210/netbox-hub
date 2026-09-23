#!/usr/bin/env python3
"""
Manual test script for the dynamic interface parser.
Run this to verify all functionality without pytest.
"""

import sys
import re

# Inline the functions for testing
def _get_interface_abbreviation(interface_type: str) -> str:
    """
    Dynamically extract interface abbreviation from full interface type name.
    
    Uses a hybrid approach:
    1. Check known mappings for common/special cases
    2. Extract capital letters for camelCase names
    3. Fall back to intelligent prefix extraction
    
    Args:
        interface_type: Full interface type (e.g., "XGigabitEthernet", "TenGigE")
        
    Returns:
        Standard abbreviation (e.g., "XGE", "Te")
    """
    itype = interface_type.strip()
    itype_lower = itype.lower()
    
    # Known mappings for common interfaces and special cases
    known_mappings = {
        'xgigabitethernet': 'XGE',
        'xgige': 'XGE',
        'tengigabitethernet': 'Te',
        'tengige': 'Te',
        'tengig': 'Te',
        'teng': 'Te',
        'gigabitethernet': 'GE',
        'gige': 'GE',
        'fastethernet': 'FE',
        'twentyfivegige': 'Twe',
        'twentyfivegigabitethernet': 'Twe',
        'fortygigabitethernet': 'Fo',
        'fortygige': 'Fo',
        'hundredgige': 'Hu',
        'hundredgigabitethernet': 'Hu',
        'twohundredgige': 'TwoHu',
        'twohundredgigabitethernet': 'TwoHu',
        'fourhundredgige': 'FourHu',
        'fourhundredgigabitethernet': 'FourHu',
        'ethernet': 'Eth',
        'port-channel': 'Po',
        'portchannel': 'Po',
        'management': 'Mgmt',
        'mgmt': 'Mgmt',
        'loopback': 'Lo',
        'vlan': 'Vlan',
        'tunnel': 'Tu',
    }
    
    # Check known mappings first
    if itype_lower in known_mappings:
        return known_mappings[itype_lower]
    
    # Dynamic extraction for CamelCase or mixed case (e.g., "XGigabitEthernet" -> "XGE")
    # Extract capital letters and initial capital sequences
    capitals = []
    i = 0
    while i < len(itype):
        if itype[i].isupper():
            # Check if this starts a capitalized word
            if i == 0 or not itype[i-1].isupper():
                capitals.append(itype[i])
            i += 1
        else:
            i += 1
    
    # If we found capital letters in CamelCase pattern, use them
    if len(capitals) >= 2:
        abbreviation = ''.join(capitals)
        # Limit to reasonable length (2-4 chars typically)
        if len(abbreviation) <= 5:
            return abbreviation
    
    # Fallback: Extract first letters of major word components
    # Split on common patterns (Gig, Ethernet, etc.)
    words = re.findall(r'[A-Z][a-z]*|[a-z]+', itype)
    
    if words:
        # For speed indicators + type, take more intelligent approach
        speed_keywords = ['ten', 'twenty', 'forty', 'hundred', 'fast', 'gig', 'gigabit', 'x']
        type_keywords = ['ethernet', 'port', 'channel', 'management']
        
        speed_parts = [w for w in words if w.lower() in speed_keywords]
        type_parts = [w for w in words if w.lower() in type_keywords]
        
        if speed_parts and type_parts:
            # Combine first letter of speed + first letter of type
            speed_abbr = ''.join([w[0] for w in speed_parts[:2]]).capitalize()
            type_abbr = type_parts[0][0].lower()
            return f"{speed_abbr}{type_abbr}"
    
    # Final fallback: Use first 2-3 characters
    return itype[:3].capitalize() if len(itype) >= 3 else itype.upper()


def normalize_port_shortname(port_name: str) -> str:
    """
    Dynamically parse network interface names and convert to standard short form.
    
    Handles various vendor formats including Cisco, Huawei, Arista, etc.
    Supports sub-interfaces and gracefully handles already-shortened inputs.
    
    Examples:
        "XGigabitEthernet0/0/31" -> "XGE0/0/31"
        "TenGigabitEthernet1/0/1" -> "Te1/0/1"
        "GigabitEthernet1/0/24" -> "GE1/0/24"
        "FortyGigabitEthernet0/1" -> "Fo0/1"
        "XGE0/0/31.100" -> "XGE0/0/31.100" (already short)
        "Port-channel10" -> "Po10"
    
    Args:
        port_name: Full or abbreviated interface name
        
    Returns:
        Shortened interface name with standard abbreviation
    """
    if not port_name:
        return ""
    
    # Remove whitespace
    p = re.sub(r"\s+", "", port_name.strip())
    
    # Pattern to match: <InterfaceType><PortPath>[.SubInterface]
    # Port path: digits, slashes, dots for sub-interfaces
    match = re.match(r'^([a-zA-Z\-]+)([\d/]+(?:\.\d+)?)$', p, re.IGNORECASE)
    
    if not match:
        return p
    
    interface_type = match.group(1)
    port_path = match.group(2)
    
    # Check if already abbreviated (short form: 2-4 characters typically)
    if len(interface_type) <= 4 and interface_type[0].isupper():
        return p
    
    # Normalize interface type to abbreviation
    abbreviation = _get_interface_abbreviation(interface_type)
    
    return f"{abbreviation}{port_path}"


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
