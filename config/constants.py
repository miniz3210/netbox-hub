"""
Application constants for NetBox Hub (v4.8.10)
"""
import os
from pathlib import Path

# Application Meta
APP_NAME = "NetBox Hub"
APP_VERSION = "4.8.10"
PAGE_TITLE = "NetBox Hub"
PAGE_ICON = "📦"
DEFAULT_PLATFORM = "VMware ESXi"

# Device Catalog & GitHub Sync
GITHUB_REPO = "netbox-community/devicetype-library"
BRANCH = "master"
LOCAL_CACHE_DIR = os.getenv("LOCAL_CACHE_DIR", "data/devicetype-library")

# Paths & Rules Configuration
DATA_DIR = Path("data")
RULES_FILE = os.getenv("RULES_FILE", "data/naming_rules.yaml")
RULES_HISTORY_FILE = os.getenv("HISTORY_FILE", "data/naming_rules_history.json")
MAX_HISTORY_ENTRIES = int(os.getenv("MAX_HISTORY_ENTRIES", "50"))
BASELINE_FILE = os.getenv("BASELINE_FILE", "data/baseline_rules.yaml")
CACHE_DIR = Path("data/cache")

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/netbox_hub.db")

# Auto-correction
AUTOCORRECT_RULES_FILE = os.getenv("AUTOCORRECT_RULES_FILE", "data/autocorrect_rules.yaml")

# Networking Acronyms (preserve during title-case conversion)
NETWORKING_ACRONYMS = frozenset({
    "API", "ARP", "AS", "BGP", "CAN", "CIDR", "CLI", "CRC", "CPRI",
    "CPU", "CSMA", "DHCP", "DNS", "DOCSIS", "DSL", "DTE", "DVFS",
    "EIGRP", "ETH", "FCC", "FEC", "FTTx", "GBIC", "GPIO", "GPS",
    "GRE", "HTTP", "HTTPS", "IA-64", "ICMP", "IEEE", "IoT", "IP",
    "IPS", "IPv4", "IPv6", "ISCSI", "ISO", "ITS", "JMX", "LAN",
    "LDAP", "LLDP", "LOS", "LTE", "MAC", "MIB", "MPLS", "MSCHAP",
    "MTU", "NAS", "NBMA", "NIC", "NTP", "OAM", "OOB", "OSI",
    "OSS", "OUI", "PCIE", "PDUs", "PHY", "PoE", "PPP", "POTS",
    "POTS", "PTZ", "QoS", "RAM", "RARP", "RBAC", "RDBMS", "RED",
    "RFS", "RIP", "RMA", "ROM", "RRAS", "RTT", "SAN", "SCSI",
    "SD-WAN", "SIM", "SLA", "SMS", "SNMP", "SONET", "SOX", "SPD",
    "SRAM", "SSD", "SSL", "SSH", "STP", "SYSLOG", "TACACS", "TCP",
    "TEM", "TFTP", "TLS", "TONER", "UDLD", "UDP", "UPI", "UPS",
    "URL", "USB", "VLAN", "VoIP", "VPN", "VTP", "WAN", "WAP",
    "WIFI", "WIO", "WLAN", "WSDL", "XML", "XPath", "Zabbix",
})

# Network & IPAM Defaults
DEFAULT_VLAN_NAME_PATTERN = "<role>"
DEFAULT_PREFIX_PATTERN = "<site> <role> -- VLAN <vid>"
