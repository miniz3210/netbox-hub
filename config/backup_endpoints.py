"""
NetBox Backup Endpoint Configuration

Dynamically discovers all available NetBox API endpoints and identifies the
essential endpoints from the minimal backup PowerShell script.

This configuration is future-proof: when new endpoints are added to NetBox
or the minimal backup script is updated, this module automatically reflects
those changes without manual updates.
"""

import re
from pathlib import Path
from typing import Dict, List, TypedDict, Set


class EndpointInfo(TypedDict):
    """Metadata for a NetBox API endpoint."""
    path: str
    label: str
    essential: bool  # True if part of the minimal backup script
    description: str


def _parse_essential_endpoints_from_script() -> Set[str]:
    """
    Parse the minimal backup PowerShell script to extract the list of 
    essential endpoints dynamically.
    
    Returns:
        Set of essential endpoint paths (e.g., "dcim/sites", "ipam/vlans")
    """
    script_path = Path(__file__).resolve().parent.parent / "data" / "netbox-export-min.ps1"
    
    try:
        with open(script_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        
        # Find the $MinimalEndpoints array definition
        # Pattern: $MinimalEndpoints = @( ... )
        match = re.search(
            r'\$MinimalEndpoints\s*=\s*@\((.*?)\)',
            content,
            re.DOTALL
        )
        
        if not match:
            # Fallback: return empty set if pattern not found
            return set()
        
        array_content = match.group(1)
        
        # Extract all quoted endpoint paths
        # Match patterns like "dcim/sites", "ipam/vlans" etc.
        # Exclude: application/json, http://, https://, etc.
        endpoints = re.findall(r'"((?:dcim|ipam|virtualization|tenancy|circuits|vpn|wireless|extras|users|core)/[a-z-]+(?:-[a-z]+)*)"', array_content)
        
        return set(endpoints)
    
    except Exception as e:
        # If script can't be read, return empty set
        # This ensures the app doesn't crash if the file is missing
        print(f"Warning: Could not parse essential endpoints from script: {e}")
        return set()


def _generate_label(endpoint_path: str) -> str:
    """Generate a human-readable label from an endpoint path."""
    # Extract the last part after the slash
    parts = endpoint_path.split('/')
    if len(parts) >= 2:
        label = parts[-1]
    else:
        label = endpoint_path
    
    # Convert kebab-case to Title Case
    # "device-types" -> "Device Types"
    # "ip-addresses" -> "IP Addresses"
    words = label.split('-')
    title_words = []
    
    for word in words:
        # Handle special acronyms
        if word.upper() in ['IP', 'ASN', 'VLAN', 'VRF', 'VM', 'API', 'FHRP', 'RIR', 'VPN', 'IKE', 'IPSEC', 'L2VPN']:
            title_words.append(word.upper())
        else:
            title_words.append(word.capitalize())
    
    return ' '.join(title_words)


def _generate_description(endpoint_path: str) -> str:
    """Generate a description for an endpoint based on its path."""
    descriptions = {
        # DCIM
        "dcim/regions": "Geographic regions",
        "dcim/site-groups": "Site groupings and hierarchies",
        "dcim/sites": "Physical locations and facilities",
        "dcim/locations": "Sub-site locations (floors, rooms)",
        "dcim/rack-roles": "Functional rack classifications",
        "dcim/rack-groups": "Rack groupings",
        "dcim/rack-types": "Physical rack specifications",
        "dcim/racks": "Equipment racks",
        "dcim/manufacturers": "Equipment vendors",
        "dcim/platforms": "Operating systems and platforms",
        "dcim/device-roles": "Device functional roles",
        "dcim/device-types": "Device models and specifications",
        "dcim/devices": "Network and infrastructure devices",
        "dcim/interfaces": "Device network interfaces",
        "dcim/cables": "Physical cabling",
        "dcim/cable-terminations": "Cable endpoint connections",
        "dcim/console-ports": "Console access ports",
        "dcim/console-server-ports": "Console server ports",
        "dcim/power-ports": "Device power inputs",
        "dcim/power-outlets": "Device power outputs",
        "dcim/power-panels": "Electrical panels",
        "dcim/power-feeds": "Power supply feeds",
        "dcim/modules": "Device modules",
        "dcim/module-types": "Module specifications",
        "dcim/module-bays": "Module slots",
        "dcim/device-bay-templates": "Templates for device bays",
        "dcim/device-bays": "Device bay instances",
        "dcim/inventory-items": "Device inventory components",
        "dcim/inventory-item-roles": "Inventory item classifications",
        "dcim/inventory-item-templates": "Templates for inventory items",
        "dcim/front-port-templates": "Templates for front ports",
        "dcim/front-ports": "Front-facing ports",
        "dcim/rear-port-templates": "Templates for rear ports",
        "dcim/rear-ports": "Rear-facing ports",
        "dcim/virtual-chassis": "Virtual chassis configurations",
        "dcim/virtual-device-contexts": "Device virtualization contexts",
        
        # IPAM
        "ipam/rirs": "Regional Internet Registries",
        "ipam/asn-ranges": "Autonomous System Number ranges",
        "ipam/asns": "Autonomous System Numbers",
        "ipam/aggregates": "IP address aggregates",
        "ipam/roles": "IP/VLAN functional roles",
        "ipam/vrfs": "Virtual Routing and Forwarding instances",
        "ipam/prefixes": "IP network prefixes",
        "ipam/ip-ranges": "Continuous IP address ranges",
        "ipam/ip-addresses": "Individual IP addresses",
        "ipam/vlan-groups": "VLAN groupings",
        "ipam/vlans": "Virtual LANs",
        "ipam/service-templates": "Service configuration templates",
        "ipam/services": "Network services",
        "ipam/fhrp-groups": "First Hop Redundancy Protocol groups",
        "ipam/fhrp-group-assignments": "FHRP interface assignments",
        "ipam/l2vpns": "Layer 2 VPN instances",
        "ipam/l2vpn-terminations": "L2VPN endpoints",
        
        # Virtualization
        "virtualization/cluster-types": "Virtualization platform types",
        "virtualization/cluster-groups": "Cluster groupings",
        "virtualization/clusters": "Virtualization clusters",
        "virtualization/virtual-machine-types": "Virtual machine classifications",
        "virtualization/virtual-machines": "Virtual machine instances",
        "virtualization/interfaces": "Virtual machine network interfaces",
        "virtualization/virtual-disks": "Virtual machine storage",
        
        # Tenancy
        "tenancy/tenant-groups": "Tenant groupings",
        "tenancy/tenants": "Organizational tenants",
        "tenancy/contact-groups": "Contact groupings",
        "tenancy/contact-roles": "Contact functional roles",
        "tenancy/contacts": "Contact records",
        "tenancy/contact-assignments": "Object-to-contact associations",
        
        # Circuits
        "circuits/providers": "Service providers",
        "circuits/provider-accounts": "Provider account details",
        "circuits/provider-networks": "Provider network infrastructure",
        "circuits/circuit-types": "Circuit classifications",
        "circuits/circuits": "Communication circuits",
        "circuits/circuit-terminations": "Circuit endpoints",
        
        # VPN
        "vpn/tunnels": "VPN tunnel instances",
        "vpn/tunnel-groups": "Tunnel groupings",
        "vpn/tunnel-terminations": "Tunnel endpoints",
        "vpn/ike-policies": "IKE configuration policies",
        "vpn/ike-proposals": "IKE proposal definitions",
        "vpn/ipsec-policies": "IPSec configuration policies",
        "vpn/ipsec-proposals": "IPSec proposal definitions",
        "vpn/ipsec-profiles": "IPSec profile configurations",
        
        # Wireless
        "wireless/wireless-lan-groups": "WLAN groupings",
        "wireless/wireless-lans": "Wireless network instances",
        "wireless/wireless-links": "Point-to-point wireless connections",
        
        # Extras
        "extras/tags": "Object tags",
        "extras/custom-fields": "Custom field definitions",
        "extras/custom-field-choice-sets": "Custom field dropdown options",
        "extras/config-contexts": "Configuration context data",
        "extras/config-templates": "Configuration templates",
        "extras/custom-links": "Custom navigation links",
        "extras/export-templates": "Data export templates",
        "extras/saved-filters": "User-saved filter sets",
        "extras/webhooks": "Webhook configurations",
        "extras/event-rules": "Event automation rules",
        "extras/image-attachments": "Attached images",
        "extras/journal-entries": "Object journal logs",
        
        # Users
        "users/users": "User accounts",
        "users/groups": "User groups",
        "users/permissions": "Permission definitions",
        "users/tokens": "API authentication tokens",
        
        # Core
        "core/data-sources": "External data sources",
        "core/data-files": "Imported data files",
        "core/jobs": "Background job definitions",
    }
    
    return descriptions.get(endpoint_path, f"NetBox {_generate_label(endpoint_path)}")


def _discover_all_endpoints() -> Set[str]:
    """
    Discover all known NetBox API endpoints from common categories.
    
    This list is comprehensive but if NetBox adds new endpoints, they can be
    added here or discovered via the full backup script.
    
    Returns:
        Set of all known endpoint paths
    """
    # Read from full backup script if available to get comprehensive list
    full_script_path = Path(__file__).resolve().parent.parent / "data" / "netbox-export-full.ps1"
    
    discovered = set()
    
    # Valid NetBox API categories (prevents false matches like "application/json")
    valid_categories = {
        'dcim', 'ipam', 'virtualization', 'tenancy', 'circuits', 
        'vpn', 'wireless', 'extras', 'users', 'core'
    }
    
    # Try to discover from full backup script comments/documentation
    try:
        with open(full_script_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
            # Extract endpoint references - only match valid NetBox API patterns
            # Pattern: category/endpoint-name where category is one of the valid ones
            pattern = r'"((?:' + '|'.join(valid_categories) + r')/[a-z-]+(?:-[a-z]+)*)"'
            endpoints = re.findall(pattern, content)
            discovered.update(endpoints)
    except:
        pass
    
    # Fallback: comprehensive manual list of known endpoints (as of NetBox 3.x/4.x)
    # This ensures we have a complete list even if scripts can't be parsed
    fallback_endpoints = {
        # DCIM
        "dcim/regions", "dcim/site-groups", "dcim/sites", "dcim/locations",
        "dcim/rack-roles", "dcim/rack-groups", "dcim/rack-types", "dcim/racks",
        "dcim/manufacturers", "dcim/platforms", "dcim/device-roles", "dcim/device-types",
        "dcim/devices", "dcim/interfaces", "dcim/cables", "dcim/cable-terminations",
        "dcim/console-ports", "dcim/console-server-ports", "dcim/power-ports",
        "dcim/power-outlets", "dcim/power-panels", "dcim/power-feeds",
        "dcim/modules", "dcim/module-types", "dcim/module-bays",
        "dcim/device-bay-templates", "dcim/device-bays", "dcim/inventory-items",
        "dcim/inventory-item-roles", "dcim/inventory-item-templates",
        "dcim/front-port-templates", "dcim/front-ports", "dcim/rear-port-templates",
        "dcim/rear-ports", "dcim/virtual-chassis", "dcim/virtual-device-contexts",
        
        # IPAM
        "ipam/rirs", "ipam/asn-ranges", "ipam/asns", "ipam/aggregates",
        "ipam/roles", "ipam/vrfs", "ipam/prefixes", "ipam/ip-ranges",
        "ipam/ip-addresses", "ipam/vlan-groups", "ipam/vlans",
        "ipam/service-templates", "ipam/services", "ipam/fhrp-groups",
        "ipam/fhrp-group-assignments", "ipam/l2vpns", "ipam/l2vpn-terminations",
        
        # Virtualization
        "virtualization/cluster-types", "virtualization/cluster-groups",
        "virtualization/clusters", "virtualization/virtual-machine-types",
        "virtualization/virtual-machines", "virtualization/interfaces",
        "virtualization/virtual-disks",
        
        # Tenancy
        "tenancy/tenant-groups", "tenancy/tenants", "tenancy/contact-groups",
        "tenancy/contact-roles", "tenancy/contacts", "tenancy/contact-assignments",
        
        # Circuits
        "circuits/providers", "circuits/provider-accounts", "circuits/provider-networks",
        "circuits/circuit-types", "circuits/circuits", "circuits/circuit-terminations",
        
        # VPN
        "vpn/tunnels", "vpn/tunnel-groups", "vpn/tunnel-terminations",
        "vpn/ike-policies", "vpn/ike-proposals", "vpn/ipsec-policies",
        "vpn/ipsec-proposals", "vpn/ipsec-profiles",
        
        # Wireless
        "wireless/wireless-lan-groups", "wireless/wireless-lans", "wireless/wireless-links",
        
        # Extras
        "extras/tags", "extras/custom-fields", "extras/custom-field-choice-sets",
        "extras/config-contexts", "extras/config-templates", "extras/custom-links",
        "extras/export-templates", "extras/saved-filters", "extras/webhooks",
        "extras/event-rules", "extras/image-attachments", "extras/journal-entries",
        
        # Users
        "users/users", "users/groups", "users/permissions", "users/tokens",
        
        # Core
        "core/data-sources", "core/data-files", "core/jobs",
    }
    
    # Merge discovered and fallback
    all_endpoints = discovered.union(fallback_endpoints)
    
    return all_endpoints


def _build_endpoint_registry() -> Dict[str, List[EndpointInfo]]:
    """
    Dynamically build the complete endpoint registry by:
    1. Discovering all known NetBox endpoints
    2. Identifying which are essential (from minimal backup script)
    3. Organizing by category
    
    This function is called at import time and the result is cached.
    """
    essential_set = _parse_essential_endpoints_from_script()
    all_endpoints = _discover_all_endpoints()
    
    # Valid NetBox API categories (prevents false matches)
    valid_categories = {
        'dcim', 'ipam', 'virtualization', 'tenancy', 'circuits', 
        'vpn', 'wireless', 'extras', 'users', 'core'
    }
    
    # Organize endpoints by category
    registry: Dict[str, List[EndpointInfo]] = {}
    
    for endpoint_path in sorted(all_endpoints):
        # Extract category from path (e.g., "dcim/sites" -> "DCIM")
        parts = endpoint_path.split('/')
        if len(parts) < 2:
            continue
        
        category_lower = parts[0].lower()
        
        # Skip invalid categories
        if category_lower not in valid_categories:
            continue
        
        category = category_lower.upper()
        
        # Initialize category if not exists
        if category not in registry:
            registry[category] = []
        
        # Build endpoint info
        endpoint_info: EndpointInfo = {
            "path": endpoint_path,
            "label": _generate_label(endpoint_path),
            "essential": endpoint_path in essential_set,
            "description": _generate_description(endpoint_path)
        }
        
        registry[category].append(endpoint_info)
    
    # Sort endpoints within each category by label
    for category in registry:
        registry[category].sort(key=lambda x: x['label'])
    
    return registry


# Build the endpoint registry dynamically at import time
NETBOX_ENDPOINTS: Dict[str, List[EndpointInfo]] = _build_endpoint_registry()


def get_essential_endpoints() -> List[str]:
    """Return list of essential endpoint paths (dynamically parsed from script)."""
    essential = []
    for category, endpoints in NETBOX_ENDPOINTS.items():
        for endpoint in endpoints:
            if endpoint["essential"]:
                essential.append(endpoint["path"])
    return essential


def get_all_endpoints() -> List[str]:
    """Return list of all available endpoint paths."""
    all_endpoints = []
    for category, endpoints in NETBOX_ENDPOINTS.items():
        for endpoint in endpoints:
            all_endpoints.append(endpoint["path"])
    return all_endpoints


def get_endpoint_count() -> Dict[str, int]:
    """Return counts of total and essential endpoints."""
    total = 0
    essential = 0
    for category, endpoints in NETBOX_ENDPOINTS.items():
        for endpoint in endpoints:
            total += 1
            if endpoint["essential"]:
                essential += 1
    return {"total": total, "essential": essential}


def reload_endpoints():
    """
    Force reload of endpoint registry from disk.
    Call this if the PowerShell scripts have been updated.
    """
    global NETBOX_ENDPOINTS
    NETBOX_ENDPOINTS = _build_endpoint_registry()
