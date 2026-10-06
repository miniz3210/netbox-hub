# Dynamic Network Interface Name Parser

## Overview

The dynamic interface name parser automatically converts full network interface names to their standard abbreviated forms across multiple vendors (Cisco, Huawei, Arista, Juniper, etc.) without hardcoded string replacements.

## Features

✅ **Dynamic Parsing** - Algorithmically extracts abbreviations from interface names
✅ **Multi-Vendor Support** - Works with Cisco, Huawei, Arista, and other vendors
✅ **Sub-Interface Support** - Handles VLAN tags (e.g., `.100`, `.200`)
✅ **Idempotent** - Already-shortened names pass through unchanged
✅ **Case-Insensitive** - Works regardless of input casing
✅ **Whitespace Tolerant** - Automatically removes spaces

## Implementation

### Location
- **File**: `/opt/netbox-hub/utils/formatters.py`
- **Functions**: 
  - `normalize_port_shortname(port_name: str) -> str` - Main function
  - `_get_interface_abbreviation(interface_type: str) -> str` - Helper function

### Algorithm

The parser uses a three-tier approach:

1. **Known Mappings** - Check common interface types first (fast path)
2. **CamelCase Extraction** - Extract capital letters from CamelCase names
3. **Intelligent Fallback** - Parse word components and build abbreviations

## Usage Examples

### Basic Usage

```python
from utils.formatters import normalize_port_shortname

# Huawei XGigabitEthernet
result = normalize_port_shortname("XGigabitEthernet0/0/31")
# Output: "XGE0/0/31"

# Cisco TenGigabitEthernet
result = normalize_port_shortname("TenGigabitEthernet1/0/1")
# Output: "Te1/0/1"

# Cisco GigabitEthernet
result = normalize_port_shortname("GigabitEthernet1/0/24")
# Output: "Gi1/0/24"

# Port-channel (LAG)
result = normalize_port_shortname("Port-channel10")
# Output: "Po10"
```

### Real-World Scenario: Uplink Configuration

```python
from utils.formatters import normalize_port_shortname

# Local device: Huawei switch
local_device = "ESPRWDC-SW01"
local_port = normalize_port_shortname("XGigabitEthernet0/0/31")
# Result: "XGE0/0/31"

# Remote device: Cisco switch
remote_device = "Huawei-Core"
remote_port = normalize_port_shortname("TenGigabitEthernet1/0/1")
# Result: "Te1/0/1"

# Generate uplink descriptions
uplink_local = f"to {remote_device}_{remote_port} [Uplink]"
# Output: "to Huawei-Core_Te1/0/1 [Uplink]"

uplink_remote = f"to {local_device}_{local_port} [Uplink]"
# Output: "to ESPRWDC-SW01_XGE0/0/31 [Uplink]"
```

## Supported Interface Types

### Common Interfaces

| Full Name | Abbreviation | Vendor |
|-----------|--------------|--------|
| XGigabitEthernet | XGE | Huawei |
| TenGigabitEthernet | Te | Cisco |
| GigabitEthernet | Gi | Cisco |
| FastEthernet | Fa | Cisco (legacy) |
| FortyGigabitEthernet | Fo | Cisco |
| TwentyFiveGigE | Twe | Cisco |
| HundredGigE | Hu | Cisco |
| Port-channel | Po | Cisco/Huawei |
| Management | Mgmt | Multiple |
| Ethernet | Eth | Arista/Nexus |

### Sub-Interfaces

Sub-interfaces with VLAN tags are fully supported:

```python
normalize_port_shortname("XGigabitEthernet0/0/31.100")
# Output: "XGE0/0/31.100"

normalize_port_shortname("TenGigabitEthernet1/0/1.200")
# Output: "Te1/0/1.200"
```

### Already-Shortened Names

The parser is idempotent - already-shortened names pass through unchanged:

```python
normalize_port_shortname("XGE0/0/31")  # Output: "XGE0/0/31"
normalize_port_shortname("Te1/0/1")    # Output: "Te1/0/1"
normalize_port_shortname("Po10")       # Output: "Po10"
```

## Edge Cases Handled

### 1. Whitespace
```python
normalize_port_shortname("Gigabit Ethernet 1/0/24")
# Output: "Gi1/0/24"
```

### 2. Case Variations
```python
normalize_port_shortname("tengigabitethernet1/0/1")  # lowercase
# Output: "Te1/0/1"

normalize_port_shortname("TENGIGABITETHERNET1/0/1")  # uppercase
# Output: "Te1/0/1"
```

### 3. Different Port Numbering Schemes
```python
# Single number
normalize_port_shortname("GigabitEthernet1")
# Output: "Gi1"

# Two-level
normalize_port_shortname("GigabitEthernet0/1")
# Output: "Gi0/1"

# Three-level (chassis switches)
normalize_port_shortname("GigabitEthernet1/0/24")
# Output: "Gi1/0/24"
```

### 4. Empty or Invalid Input
```python
normalize_port_shortname("")           # Output: ""
normalize_port_shortname("   ")        # Output: ""
```

## Integration with NetBox Hub

The parser is integrated into the **Naming Tab** for generating standardized interface descriptions:

### Switch Uplink Descriptions

**Old Format:**
```
Uplink from ESPRWDC-SW01_Port1 to Huawei-1_XGE0/0/31
```

**New Format:**
```
to Huawei-1_XGE0/0/31 [Uplink]
```

### LAG/Port-Channel Descriptions

**Old Format:**
```
LAG1 to SWUSNYC02-0_Po1 Trunk VLANs 10,20,30
```

**New Format:**
```
LAG1 to SWUSNYC02-0 VLANs 10,20,30
```

## Testing

### Unit Tests

Comprehensive unit tests are available in:
- **File**: `/opt/netbox-hub/tests/test_interface_parser.py`

Run tests with:
```bash
pytest tests/test_interface_parser.py -v
```

### Manual Testing

A standalone test script is provided:
- **File**: `/opt/netbox-hub/test_interface_manual.py`

Run manual tests:
```bash
python3 test_interface_manual.py
```

## Benefits

### 1. **Scalability**
No need to update code when encountering new interface types - the parser adapts automatically.

### 2. **Consistency**
All interface names are normalized to standard abbreviations across the application.

### 3. **Multi-Vendor Support**
Works seamlessly with mixed-vendor environments (Cisco + Huawei + Arista, etc.).

### 4. **Maintainability**
Single function handles all interface types - no scattered regex replacements.

### 5. **Future-Proof**
Algorithmically handles new interface speeds (e.g., 400G, 800G) without code changes.

## Technical Details

### Regular Expression Pattern

The parser uses this pattern to split interface names:

```python
^([a-zA-Z\-]+)([\d/]+(?:\.\d+)?)$
```

- **Group 1**: Interface type (letters and hyphens)
- **Group 2**: Port path (numbers, slashes, optional sub-interface)

### Abbreviation Extraction Logic

1. **Known mappings**: O(1) lookup in dictionary
2. **CamelCase extraction**: Extract capital letters (e.g., `XGigabitEthernet` → `XGE`)
3. **Word parsing**: Split by camelCase boundaries and apply rules
4. **Fallback**: Use first 2-3 characters

## Configuration Updates

The default naming rules have been updated in `/opt/netbox-hub/config/naming_rules.py`:

```python
DEFAULT_RULES = {
    "switch_uplink_desc": "to <Remote_Device>_<Remote_Port_Short> [Uplink]",
    "switch_port_channel": "<Local_Po> to <Remote_Device> <Trunk_Info>",
    # ... other rules
}
```

## Migration Notes

### Breaking Changes
- Uplink description format changed from `Uplink from X to Y` to `to Y [Uplink]`
- Port-channel format simplified (removed remote port-channel ID)
- Default LAG ID changed from `Po1` to `LAG1`

### Backward Compatibility
Already-shortened interface names work without modification, ensuring existing configurations continue to function.

## Future Enhancements

Potential improvements for future versions:

1. **Vendor-Specific Abbreviations** - Allow custom abbreviation rules per vendor
2. **Module Interfaces** - Support for line cards (e.g., `module 1/port 1`)
3. **Breakout Ports** - Handle breakout cable notation (e.g., `1/1/1:1`)
4. **Speed Detection** - Optionally include speed in output (e.g., `10GE0/0/1`)

## Support

For issues or questions about the interface parser:

1. Check test cases in `tests/test_interface_parser.py` for examples
2. Review algorithm in `utils/formatters.py`
3. Test with `test_interface_manual.py` script

## Changelog

### Version 2.0 (2026-09-14)
- ✨ Implemented dynamic, algorithmic interface parsing
- ✨ Removed hardcoded regex replacements
- ✨ Added comprehensive unit tests
- ✨ Integrated with naming tab uplink/LAG formatters
- 📝 Updated default naming standards

### Version 1.0 (Original)
- Basic regex-based replacements for common interface types
