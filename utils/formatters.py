import re

def normalize_manufacturer_name(name: str) -> str:
    """Normalizes common manufacturer abbreviations to their canonical vendor names."""
    if not name:
        return ""
    m_clean = name.strip()
    mapping = {
        "hp": "HPE",
        "hpe": "HPE",
        "hewlett packard": "HPE",
        "hewlett packard enterprise": "HPE",
        "cisco": "Cisco",
        "cisco systems": "Cisco",
        "dell": "Dell",
        "dell emc": "Dell",
        "palo alto": "Palo Alto Networks",
        "paloalto": "Palo Alto Networks",
        "pan": "Palo Alto Networks",
        "palo alto networks": "Palo Alto Networks",
        "fortinet": "Fortinet",
        "juniper": "Juniper",
        "juniper networks": "Juniper",
        "arista": "Arista",
        "arista networks": "Arista",
        "checkpoint": "Check Point",
        "check point": "Check Point",
        "ubiquiti": "Ubiquiti",
        "unifi": "Ubiquiti",
        "aruba": "Aruba",
        "mikrotik": "MikroTik",
        "paloalto networks": "Palo Alto Networks",
        "f5": "F5",
        "f5 networks": "F5",
    }
    return mapping.get(m_clean.lower(), m_clean.title() if len(m_clean) > 3 else m_clean.upper())

def compute_suggested_site_code(location_name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z\s]", "", location_name).strip()
    if not cleaned:
        return "SITE"
    words = cleaned.split()
    if len(words) >= 2:
        return (words[0][:2] + words[1][:2]).upper()
    elif len(words) == 1:
        w = words[0]
        return w[:4].upper() if len(w) >= 4 else w.upper()
    return "SITE"

def normalize_port_shortname(port_name: str) -> str:
    p = re.sub(r"\s+", "", port_name.strip())
    replacements = [
        (r"^TenGigabitEthernet", "Te"),
        (r"^TenGigE", "Te"),
        (r"^TenG", "Te"),
        (r"^GigabitEthernet", "Gi"),
        (r"^GigE", "Gi"),
        (r"^FastEthernet", "Fa"),
        (r"^TwentyFiveGigE", "Twe"),
        (r"^TwentyFiveGigabitEthernet", "Twe"),
        (r"^FortyGigabitEthernet", "Fo"),
        (r"^HundredGigE", "Hu"),
        (r"^HundredGigabitEthernet", "Hu"),
        (r"^Ethernet", "Eth"),
        (r"^Port-channel", "Po"),
        (r"^port-channel", "Po"),
        (r"^Management", "Mgmt"),
    ]
    for pattern, replacement in replacements:
        if re.search(pattern, p, re.IGNORECASE):
            return re.sub(pattern, replacement, p, flags=re.IGNORECASE)
    return p

def normalize_vswitch(name: str) -> str:
    """Auto-corrects vswitch naming to VMware standard (vSwitchX or dvSwitchX)."""
    val = name.strip()
    if not val:
        return ""
    val = re.sub(r"^vswitch(\d+)?$", r"vSwitch\1", val, flags=re.IGNORECASE)
    val = re.sub(r"^vswitch\s*(\d+)$", r"vSwitch\1", val, flags=re.IGNORECASE)
    val = re.sub(r"^dvswitch(\d+)?$", r"dvSwitch\1", val, flags=re.IGNORECASE)
    val = re.sub(r"^dvswitch\s*(\d+)$", r"dvSwitch\1", val, flags=re.IGNORECASE)
    return val

def normalize_vmnic(name: str) -> str:
    """Auto-corrects physical hypervisor interface naming (e.g. VMNIC0 -> vmnic0)."""
    val = name.strip()
    if not val:
        return ""
    val = re.sub(r"^(?:vmnic|nic)\s*(\d+)$", r"vmnic\1", val, flags=re.IGNORECASE)
    return val

def normalize_vmnic_list(names: str) -> str:
    """Normalizes a comma-separated list of vmnic identifiers."""
    if not names.strip():
        return ""
    parts = [normalize_vmnic(p.strip()) for p in names.split(",") if p.strip()]
    return ", ".join(parts)