"""
NetBox Backup Endpoint Configuration

Defines all available NetBox API endpoints grouped by category, with markers
for the 64 essential endpoints included in the minimal backup preset.

Used by the custom backup script generator UI to allow users to select which
endpoints to include in their PowerShell export scripts.
"""

from typing import Dict, List, TypedDict


class EndpointInfo(TypedDict):
    """Metadata for a NetBox API endpoint."""
    path: str
    label: str
    essential: bool  # True if part of the 64 essential endpoints
    description: str


# Complete endpoint registry grouped by NetBox API category
NETBOX_ENDPOINTS: Dict[str, List[EndpointInfo]] = {
    "DCIM": [
        {"path": "dcim/regions", "label": "Regions", "essential": True, "description": "Geographic regions"},
        {"path": "dcim/site-groups", "label": "Site Groups", "essential": True, "description": "Site groupings and hierarchies"},
        {"path": "dcim/sites", "label": "Sites", "essential": True, "description": "Physical locations and facilities"},
        {"path": "dcim/locations", "label": "Locations", "essential": True, "description": "Sub-site locations (floors, rooms)"},
        {"path": "dcim/rack-roles", "label": "Rack Roles", "essential": True, "description": "Functional rack classifications"},
        {"path": "dcim/rack-groups", "label": "Rack Groups", "essential": True, "description": "Rack groupings"},
        {"path": "dcim/rack-types", "label": "Rack Types", "essential": True, "description": "Physical rack specifications"},
        {"path": "dcim/racks", "label": "Racks", "essential": True, "description": "Equipment racks"},
        {"path": "dcim/manufacturers", "label": "Manufacturers", "essential": True, "description": "Equipment vendors"},
        {"path": "dcim/platforms", "label": "Platforms", "essential": True, "description": "Operating systems and platforms"},
        {"path": "dcim/device-roles", "label": "Device Roles", "essential": True, "description": "Device functional roles"},
        {"path": "dcim/device-types", "label": "Device Types", "essential": True, "description": "Device models and specifications"},
        {"path": "dcim/devices", "label": "Devices", "essential": True, "description": "Network and infrastructure devices"},
        {"path": "dcim/interfaces", "label": "Interfaces", "essential": True, "description": "Device network interfaces"},
        {"path": "dcim/cables", "label": "Cables", "essential": True, "description": "Physical cabling"},
        {"path": "dcim/cable-terminations", "label": "Cable Terminations", "essential": True, "description": "Cable endpoint connections"},
        {"path": "dcim/console-ports", "label": "Console Ports", "essential": True, "description": "Console access ports"},
        {"path": "dcim/console-server-ports", "label": "Console Server Ports", "essential": True, "description": "Console server ports"},
        {"path": "dcim/power-ports", "label": "Power Ports", "essential": True, "description": "Device power inputs"},
        {"path": "dcim/power-outlets", "label": "Power Outlets", "essential": True, "description": "Device power outputs"},
        {"path": "dcim/power-panels", "label": "Power Panels", "essential": True, "description": "Electrical panels"},
        {"path": "dcim/power-feeds", "label": "Power Feeds", "essential": True, "description": "Power supply feeds"},
        {"path": "dcim/modules", "label": "Modules", "essential": True, "description": "Device modules"},
        {"path": "dcim/module-types", "label": "Module Types", "essential": True, "description": "Module specifications"},
        {"path": "dcim/module-bays", "label": "Module Bays", "essential": True, "description": "Module slots"},
        {"path": "dcim/device-bay-templates", "label": "Device Bay Templates", "essential": False, "description": "Templates for device bays"},
        {"path": "dcim/device-bays", "label": "Device Bays", "essential": False, "description": "Device bay instances"},
        {"path": "dcim/inventory-items", "label": "Inventory Items", "essential": False, "description": "Device inventory components"},
        {"path": "dcim/inventory-item-roles", "label": "Inventory Item Roles", "essential": False, "description": "Inventory item classifications"},
        {"path": "dcim/inventory-item-templates", "label": "Inventory Item Templates", "essential": False, "description": "Templates for inventory items"},
        {"path": "dcim/front-port-templates", "label": "Front Port Templates", "essential": False, "description": "Templates for front ports"},
        {"path": "dcim/front-ports", "label": "Front Ports", "essential": False, "description": "Front-facing ports"},
        {"path": "dcim/rear-port-templates", "label": "Rear Port Templates", "essential": False, "description": "Templates for rear ports"},
        {"path": "dcim/rear-ports", "label": "Rear Ports", "essential": False, "description": "Rear-facing ports"},
        {"path": "dcim/virtual-chassis", "label": "Virtual Chassis", "essential": False, "description": "Virtual chassis configurations"},
        {"path": "dcim/virtual-device-contexts", "label": "Virtual Device Contexts", "essential": False, "description": "Device virtualization contexts"},
    ],
    
    "IPAM": [
        {"path": "ipam/rirs", "label": "RIRs", "essential": True, "description": "Regional Internet Registries"},
        {"path": "ipam/asn-ranges", "label": "ASN Ranges", "essential": True, "description": "Autonomous System Number ranges"},
        {"path": "ipam/asns", "label": "ASNs", "essential": True, "description": "Autonomous System Numbers"},
        {"path": "ipam/aggregates", "label": "Aggregates", "essential": True, "description": "IP address aggregates"},
        {"path": "ipam/roles", "label": "Roles", "essential": True, "description": "IP/VLAN functional roles"},
        {"path": "ipam/vrfs", "label": "VRFs", "essential": True, "description": "Virtual Routing and Forwarding instances"},
        {"path": "ipam/prefixes", "label": "Prefixes", "essential": True, "description": "IP network prefixes"},
        {"path": "ipam/ip-ranges", "label": "IP Ranges", "essential": True, "description": "Continuous IP address ranges"},
        {"path": "ipam/ip-addresses", "label": "IP Addresses", "essential": True, "description": "Individual IP addresses"},
        {"path": "ipam/vlan-groups", "label": "VLAN Groups", "essential": True, "description": "VLAN groupings"},
        {"path": "ipam/vlans", "label": "VLANs", "essential": True, "description": "Virtual LANs"},
        {"path": "ipam/service-templates", "label": "Service Templates", "essential": True, "description": "Service configuration templates"},
        {"path": "ipam/services", "label": "Services", "essential": True, "description": "Network services"},
        {"path": "ipam/fhrp-groups", "label": "FHRP Groups", "essential": True, "description": "First Hop Redundancy Protocol groups"},
        {"path": "ipam/fhrp-group-assignments", "label": "FHRP Group Assignments", "essential": True, "description": "FHRP interface assignments"},
        {"path": "ipam/l2vpns", "label": "L2VPNs", "essential": False, "description": "Layer 2 VPN instances"},
        {"path": "ipam/l2vpn-terminations", "label": "L2VPN Terminations", "essential": False, "description": "L2VPN endpoints"},
    ],
    
    "VIRTUALIZATION": [
        {"path": "virtualization/cluster-types", "label": "Cluster Types", "essential": True, "description": "Virtualization platform types"},
        {"path": "virtualization/cluster-groups", "label": "Cluster Groups", "essential": True, "description": "Cluster groupings"},
        {"path": "virtualization/clusters", "label": "Clusters", "essential": True, "description": "Virtualization clusters"},
        {"path": "virtualization/virtual-machine-types", "label": "VM Types", "essential": True, "description": "Virtual machine classifications"},
        {"path": "virtualization/virtual-machines", "label": "Virtual Machines", "essential": True, "description": "Virtual machine instances"},
        {"path": "virtualization/interfaces", "label": "VM Interfaces", "essential": True, "description": "Virtual machine network interfaces"},
        {"path": "virtualization/virtual-disks", "label": "Virtual Disks", "essential": True, "description": "Virtual machine storage"},
    ],
    
    "TENANCY": [
        {"path": "tenancy/tenant-groups", "label": "Tenant Groups", "essential": True, "description": "Tenant groupings"},
        {"path": "tenancy/tenants", "label": "Tenants", "essential": True, "description": "Organizational tenants"},
        {"path": "tenancy/contact-groups", "label": "Contact Groups", "essential": True, "description": "Contact groupings"},
        {"path": "tenancy/contact-roles", "label": "Contact Roles", "essential": True, "description": "Contact functional roles"},
        {"path": "tenancy/contacts", "label": "Contacts", "essential": True, "description": "Contact records"},
        {"path": "tenancy/contact-assignments", "label": "Contact Assignments", "essential": True, "description": "Object-to-contact associations"},
    ],
    
    "CIRCUITS": [
        {"path": "circuits/providers", "label": "Providers", "essential": True, "description": "Service providers"},
        {"path": "circuits/provider-accounts", "label": "Provider Accounts", "essential": True, "description": "Provider account details"},
        {"path": "circuits/provider-networks", "label": "Provider Networks", "essential": True, "description": "Provider network infrastructure"},
        {"path": "circuits/circuit-types", "label": "Circuit Types", "essential": True, "description": "Circuit classifications"},
        {"path": "circuits/circuits", "label": "Circuits", "essential": True, "description": "Communication circuits"},
        {"path": "circuits/circuit-terminations", "label": "Circuit Terminations", "essential": True, "description": "Circuit endpoints"},
    ],
    
    "VPN": [
        {"path": "vpn/tunnels", "label": "Tunnels", "essential": False, "description": "VPN tunnel instances"},
        {"path": "vpn/tunnel-groups", "label": "Tunnel Groups", "essential": False, "description": "Tunnel groupings"},
        {"path": "vpn/tunnel-terminations", "label": "Tunnel Terminations", "essential": False, "description": "Tunnel endpoints"},
        {"path": "vpn/ike-policies", "label": "IKE Policies", "essential": False, "description": "IKE configuration policies"},
        {"path": "vpn/ike-proposals", "label": "IKE Proposals", "essential": False, "description": "IKE proposal definitions"},
        {"path": "vpn/ipsec-policies", "label": "IPSec Policies", "essential": False, "description": "IPSec configuration policies"},
        {"path": "vpn/ipsec-proposals", "label": "IPSec Proposals", "essential": False, "description": "IPSec proposal definitions"},
        {"path": "vpn/ipsec-profiles", "label": "IPSec Profiles", "essential": False, "description": "IPSec profile configurations"},
    ],
    
    "WIRELESS": [
        {"path": "wireless/wireless-lan-groups", "label": "Wireless LAN Groups", "essential": False, "description": "WLAN groupings"},
        {"path": "wireless/wireless-lans", "label": "Wireless LANs", "essential": False, "description": "Wireless network instances"},
        {"path": "wireless/wireless-links", "label": "Wireless Links", "essential": False, "description": "Point-to-point wireless connections"},
    ],
    
    "EXTRAS": [
        {"path": "extras/tags", "label": "Tags", "essential": True, "description": "Object tags"},
        {"path": "extras/custom-fields", "label": "Custom Fields", "essential": True, "description": "Custom field definitions"},
        {"path": "extras/custom-field-choice-sets", "label": "Custom Field Choice Sets", "essential": True, "description": "Custom field dropdown options"},
        {"path": "extras/config-contexts", "label": "Config Contexts", "essential": True, "description": "Configuration context data"},
        {"path": "extras/config-templates", "label": "Config Templates", "essential": True, "description": "Configuration templates"},
        {"path": "extras/custom-links", "label": "Custom Links", "essential": False, "description": "Custom navigation links"},
        {"path": "extras/export-templates", "label": "Export Templates", "essential": False, "description": "Data export templates"},
        {"path": "extras/saved-filters", "label": "Saved Filters", "essential": False, "description": "User-saved filter sets"},
        {"path": "extras/webhooks", "label": "Webhooks", "essential": False, "description": "Webhook configurations"},
        {"path": "extras/event-rules", "label": "Event Rules", "essential": False, "description": "Event automation rules"},
        {"path": "extras/image-attachments", "label": "Image Attachments", "essential": False, "description": "Attached images"},
        {"path": "extras/journal-entries", "label": "Journal Entries", "essential": False, "description": "Object journal logs"},
    ],
    
    "USERS": [
        {"path": "users/users", "label": "Users", "essential": False, "description": "User accounts"},
        {"path": "users/groups", "label": "Groups", "essential": False, "description": "User groups"},
        {"path": "users/permissions", "label": "Permissions", "essential": False, "description": "Permission definitions"},
        {"path": "users/tokens", "label": "API Tokens", "essential": False, "description": "API authentication tokens"},
    ],
    
    "CORE": [
        {"path": "core/data-sources", "label": "Data Sources", "essential": False, "description": "External data sources"},
        {"path": "core/data-files", "label": "Data Files", "essential": False, "description": "Imported data files"},
        {"path": "core/jobs", "label": "Jobs", "essential": False, "description": "Background job definitions"},
    ],
}


def get_essential_endpoints() -> List[str]:
    """Return list of the 64 essential endpoint paths."""
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
