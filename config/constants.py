"""
Application constants for NetBox Hub (v4.8.12)
"""
import os
from pathlib import Path

# Application Metadata
APP_NAME = "NetBox Hub"
APP_VERSION = "4.8.12"
PAGE_TITLE = "NetBox Hub"
PAGE_ICON = "📦"
DEFAULT_PLATFORM = "VMware ESXi"

# Device Catalog & GitHub Sync
GITHUB_REPO = "netbox-community/devicetype-library"
BRANCH = "master"
LOCAL_CACHE_DIR = os.getenv("LOCAL_CACHE_DIR", "data/devicetype-library")

# Storage & Configuration Paths
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = Path("data/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

RULES_FILE = os.getenv("RULES_FILE", "data/naming_rules.yaml")
HISTORY_FILE = os.getenv("HISTORY_FILE", "data/history.json")
BASELINE_FILE = os.getenv("BASELINE_FILE", "data/baseline_rules.yaml")
AUTOCORRECT_RULES_FILE = os.getenv("AUTOCORRECT_RULES_FILE", "data/autocorrect_rules.json")
VAULT_DB_FILE = os.getenv("VAULT_DB_FILE", "data/vault.db")

# IPAM & Naming Defaults
DEFAULT_VLAN_NAME_PATTERN = "<role>"
DEFAULT_PREFIX_PATTERN = "<site> <role> -- VLAN <vid>"

# Networking Acronyms (Preserved during title casing)
NETWORKING_ACRONYMS = {
    "VLAN", "IP", "IPv4", "IPv6", "VIP", "VM", "ESXi", "SAN", "NAS", "DMZ",
    "CCTV", "WAP", "AP", "FW", "RTR", "SW", "OOB", "MGMT", "VPN", "WAN",
    "LAN", "WLAN", "DNS", "DHCP", "NAT", "BGP", "OSPF", "VRF", "HCI",
    "NIC", "PoE", "LACP", "LAG", "MTU", "QoS", "PRW", "HSRP", "VRRP", "VSS", "VDS"
}

# UI Navigation Chrome Noise
UI_NAV_NOISE = [
    "Virtual switches", "Physical Adapters", "Storage Adapters", "Licensing",
    "Host Profile", "Networking", "Storage", "System", "Hardware",
    "Configure", "Summary", "Monitor", "Permissions", "VMs", "Datastores",
    "Networks", "Updates", "ACTIONS", "ADD NETWORKING...", "REFRESH"
]

# Dynamically audited constants
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/netbox_hub.db")
RULES_HISTORY_FILE = os.getenv("HISTORY_FILE", "data/naming_rules_history.json")
MAX_HISTORY_ENTRIES = int(os.getenv("MAX_HISTORY_ENTRIES", "50"))
