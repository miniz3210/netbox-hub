"""
Application Constants and Versioning
"""

APP_VERSION = "2.5.1"
APP_NAME = "NetBox Universal Library Hub"

# GitHub Official Device-Type Library Source
OWNER = "netbox-community"
REPO = "devicetype-library"
BRANCH = "master"

GITHUB_REPO = "netbox-community/devicetype-library"
OFFICIAL_GITHUB_OWNER = OWNER
OFFICIAL_GITHUB_REPO = REPO
OFFICIAL_GITHUB_BRANCH = BRANCH

# Data Storage & Rules Paths
DATABASE_PATH = "data/netbox_hub.db"
RULES_FILE = "data/naming_rules.yaml"
LOCAL_CACHE_DIR = "data/catalog_cache"
CUSTOM_TEMPLATES_DIR = "data/custom_templates"

# Networking Acronyms to preserve in Title Case formatting
NETWORKING_ACRONYMS = {
    "OT", "IoT", "AV", "WiFi", "OOB", "IPMI", "iLO", "DMZ", "DB",
    "VIN_Corp", "VIN_Guest", "VIN_Mobi", "VLAN", "VPN", "VRF", "VXLAN",
    "BGP", "OSPF", "ISIS", "MPLS", "LACP", "LAG", "STP", "RSTP", "MSTP",
    "DHCP", "DNS", "NTP", "SNMP", "SSH", "TLS", "SSL", "IPsec", "GRE",
    "VTEP", "VNI", "RD", "RT", "ASN", "BFD", "VRRP", "HSRP", "GLBP",
    "QoS", "CoS", "DSCP", "ToS", "MTU", "TCP", "UDP", "ICMP", "ARP",
    "NDP", "RA", "RS", "NS", "NA", "DHCPv6", "SLAAC", "EUI-64",
    "API", "SDK", "CLI", "GUI", "REST", "JSON", "XML", "YAML", "CSV",
    "HTTP", "HTTPS", "FTP", "SFTP", "SCP", "TFTP", "Syslog", "RADIUS",
    "TACACS", "LDAP", "AD", "AAA", "NAC", "ISE", "PI", "DNAC", "ACI",
    "NSX", "AVS", "DVS", "vDS", "vSS", "VMkernel", "vMotion", "vSAN",
    "ESXi", "vCenter", "vSphere", "NSX-T", "NSX-V", "PKS", "TKG", "TKGS"
}