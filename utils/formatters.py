import os
import re
from typing import Any, Dict, List, Optional

import yaml

from config.constants import NETWORKING_ACRONYMS, AUTOCORRECT_RULES_FILE

# ── Auto-correction rules (externalized to data/autocorrect_rules.yaml) ──

DEFAULT_AUTO_CORRECTIONS: Dict[str, List[Dict[str, Any]]] = {
    "port_shortening": [
        {
            "pattern": r'(?i)\b(xgigabitethernet|xge)\s*(\d+[\d/:]*)\b',
            "replacement": r"XGE\2",
            "description": "Shorten XGigabitEthernet/XGE ports to XGE",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(tengigabitethernet|te)\s*(\d+[\d/:]*)\b',
            "replacement": r"Te\2",
            "description": "Shorten TenGigabitEthernet/TE ports to Te",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(gigabitethernet|ge)\s*(\d+[\d/:]*)\b',
            "replacement": r"GE\2",
            "description": "Shorten GigabitEthernet/GE ports to GE",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(fastethernet|fe)\s*(\d+[\d/:]*)\b',
            "replacement": r"FE\2",
            "description": "Shorten FastEthernet/FE ports to FE",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(ethernet|eth)\s*(\d+[\d/:]*)\b',
            "replacement": r"Eth\2",
            "description": "Shorten Ethernet/ETH ports to Eth",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(port-channel|po)\s*(\d+)\b',
            "replacement": r"Po\2",
            "description": "Shorten Port-channel/PO ports to Po",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(fortygigabitethernet|fo)\s*(\d+[\d/:]*)\b',
            "replacement": r"Fo\2",
            "description": "Shorten FortyGigabitEthernet/FO ports to Fo",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(twentyfivegigabitethernet|twentyfivegige|twe)\s*(\d+[\d/:]*)\b',
            "replacement": r"Twe\2",
            "description": "Shorten 25GigabitEthernet ports to Twe",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(hundredgigabitethernet|hundredgige|hu)\s*(\d+[\d/:]*)\b',
            "replacement": r"Hu\2",
            "description": "Shorten HundredGigabitEthernet ports to Hu",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(twohundredgige|twohundredgigabitethernet)\s*(\d+[\d/:]*)\b',
            "replacement": r"TwoHu\2",
            "description": "Shorten 200GigabitEthernet ports to TwoHu",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(fourhundredgige|fourhundredgigabitethernet)\s*(\d+[\d/:]*)\b',
            "replacement": r"FourHu\2",
            "description": "Shorten 400GigabitEthernet ports to FourHu",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(management|mgmt)\s*(\d+[\d/:]*)\b',
            "replacement": r"Mgmt\2",
            "description": "Shorten Management/MGMT ports to Mgmt",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(loopback|lo)\s*(\d+)\b',
            "replacement": r"Lo\2",
            "description": "Shorten Loopback/LO ports to Lo",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(vlan)\s*(\d+)\b',
            "replacement": r"Vlan\2",
            "description": "Normalize VLAN interface naming",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(tunnel|tu)\s*(\d+)\b',
            "replacement": r"Tu\2",
            "description": "Shorten Tunnel/TU ports to Tu",
            "enabled": True,
        },
    ],
    "vmware": [
        {
            "pattern": r'(?i)\b(vswitch)(\d+)\b',
            "replacement": r"vSwitch\2",
            "description": "Normalize vSwitch casing (vswitchX -> vSwitchX)",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(dvswitch)(\d+)\b',
            "replacement": r"dvSwitch\2",
            "description": "Normalize dvSwitch casing (dvswitchX -> dvSwitchX)",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(?:VMNIC|VmnIc)\s*(\d+)\b',
            "replacement": r"vmnic\1",
            "description": "Normalize VMNIC casing to lowercase vmnic",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\bnic\s*(\d+)\b',
            "replacement": r"vmnic\1",
            "description": "Standardize nicX to vmnicX",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\beth\s*(\d+)\b',
            "replacement": r"vmnic\1",
            "description": "Standardize ethX to vmnicX",
            "enabled": True,
        },
        {
            "pattern": r'(?i)\b(vmk)(\d+)\b',
            "replacement": r"vmk\2",
            "description": "Normalize VMkernel adapter casing (VMKX -> vmkX)",
            "enabled": True,
        },
    ],
}


def _coerce_rule(raw) -> Optional[Dict[str, Any]]:
    """Normalize a single rule entry from YAML into a usable dict, or None."""
    if not isinstance(raw, dict):
        return None
    pattern = raw.get("pattern")
    replacement = raw.get("replacement")
    if not isinstance(pattern, str) or not pattern:
        return None
    if not isinstance(replacement, str):
        replacement = ""
    return {
        "pattern": pattern,
        "replacement": replacement,
        "description": raw.get("description") if isinstance(raw.get("description"), str) else "",
        "enabled": bool(raw.get("enabled", True)),
    }


def _normalize_auto_corrections(raw) -> Dict[str, List[Dict[str, Any]]]:
    """Validate and normalize the loaded ``auto_corrections`` structure."""
    if not isinstance(raw, dict):
        return {k: list(rules) for k, rules in DEFAULT_AUTO_CORRECTIONS.items()}
    root = raw.get("auto_corrections")
    if not isinstance(root, dict):
        root = raw
    result: Dict[str, List[Dict[str, Any]]] = {}
    default_keys = set(DEFAULT_AUTO_CORRECTIONS.keys())
    for category, entries in root.items():
        if not isinstance(entries, list):
            continue
        coerced = [r for r in ( _coerce_rule(e) for e in entries ) if r is not None]
        result[str(category)] = coerced
    for key in default_keys:
        if key not in result:
            result[key] = list(DEFAULT_AUTO_CORRECTIONS[key])
        elif not result[key]:
            result[key] = list(DEFAULT_AUTO_CORRECTIONS[key])
    return result


def load_auto_corrections() -> Dict[str, List[Dict[str, Any]]]:
    """Load auto-correction rules from ``data/autocorrect_rules.yaml``.

    Falls back to safe, built-in defaults if the file is missing, the key is
    absent, or the YAML is malformed.
    """
    if not os.path.exists(AUTOCORRECT_RULES_FILE):
        return {k: list(rules) for k, rules in DEFAULT_AUTO_CORRECTIONS.items()}
    try:
        with open(AUTOCORRECT_RULES_FILE, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return _normalize_auto_corrections(raw)
    except Exception:
        return {k: list(rules) for k, rules in DEFAULT_AUTO_CORRECTIONS.items()}


def save_auto_corrections(data: Dict[str, List[Dict[str, Any]]], source: str = "Management UI") -> Dict[str, List[Dict[str, Any]]]:
    """Persist auto-correction rules back to YAML, preserving factory defaults for missing keys."""
    normalized = _normalize_auto_corrections({"auto_corrections": data})
    payload = {"auto_corrections": normalized}
    os.makedirs(os.path.dirname(AUTOCORRECT_RULES_FILE) or ".", exist_ok=True)
    with open(AUTOCORRECT_RULES_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
        f.flush()
        os.fsync(f.fileno())
    return normalized


def reset_auto_corrections() -> Dict[str, List[Dict[str, Any]]]:
    """Restore the factory-default auto-correction rules."""
    return save_auto_corrections(
        {k: list(rules) for k, rules in DEFAULT_AUTO_CORRECTIONS.items()},
        source="Reset to Factory Defaults",
    )


def apply_auto_corrections(text: str, category: str = "vmware") -> str:
    """Apply all enabled rules for a category to ``text`` in order."""
    if not text:
        return text
    rules = load_auto_corrections().get(category) or []
    result = text
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        try:
            result = re.sub(rule["pattern"], rule["replacement"], result)
        except re.error:
            continue
    return result


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
    # Remove whitespace so patterns like "Gigabit Ethernet 1/0/24" still match
    p = re.sub(r"\s+", "", port_name.strip())
    return apply_auto_corrections(p, "port_shortening")

def normalize_vswitch(name: str) -> str:
    """Auto-corrects vswitch naming to VMware standard (vSwitchX or dvSwitchX)."""
    val = name.strip()
    if not val:
        return ""
    corrected = apply_auto_corrections(val, "vmware")
    if corrected != val:
        return corrected
    low = val.lower()
    if low.startswith("dv"):
        return "dvSwitch"
    if low in ("vs", "vswitch") or low.startswith("vswitch"):
        return "vSwitch"
    return val

def normalize_vmnic(name: str) -> str:
    """Auto-corrects physical hypervisor interface naming (e.g. VMNIC0 -> vmnic0)."""
    val = name.strip()
    if not val:
        return ""
    return apply_auto_corrections(val, "vmware")

def normalize_vmnic_list(names: str) -> str:
    """Normalizes a comma-separated list of vmnic identifiers."""
    if not names.strip():
        return ""
    parts = [normalize_vmnic(p.strip()) for p in names.split(",") if p.strip()]
    return ", ".join(parts)


def to_title_case_preserve_acronyms(text: str) -> str:
    """
    Convert text to Title Case while preserving standard networking acronyms.
    E.g., 'oob management' -> 'OOB Management', 'iot sensors' -> 'IoT Sensors'
    """
    if not text:
        return ""
    
    # Split by common delimiters while preserving them
    words = re.split(r'(\s+|/|-|_|\.)', text.strip())
    
    result = []
    for word in words:
        if not word or word.isspace() or word in ['/', '-', '_', '.']:
            result.append(word)
            continue
        
        # Check if the word (or word without punctuation) is a known acronym
        word_clean = word.rstrip('.,;:')
        if word_clean in NETWORKING_ACRONYMS:
            result.append(word_clean + word[len(word_clean):])
        elif word_clean.upper() in NETWORKING_ACRONYMS:
            result.append(word_clean.upper() + word[len(word_clean):])
        else:
            # Standard title case
            result.append(word.capitalize())
    
    return "".join(result)


def format_role_description(role: str, description: str = "") -> tuple:
    """
    Format role and description to Title Case while preserving networking acronyms.
    Returns (formatted_role, formatted_description).
    """
    formatted_role = to_title_case_preserve_acronyms(role) if role else ""
    formatted_desc = to_title_case_preserve_acronyms(description) if description else ""
    return formatted_role, formatted_desc


def normalize_network_name(name: str) -> str:
    """
    Normalize network/port group names to proper capitalization.
    Preserves common network naming patterns and acronyms.
    
    Examples:
        "vm network" -> "VM Network"
        "management" -> "Management"
        "vmotion" -> "vMotion"
        "iscsi storage" -> "iSCSI Storage"
        "iSCSI02" -> "iSCSI02"
    """
    if not name:
        return ""
    
    text = name.strip()
    
    # Known patterns for network names (case-insensitive matching)
    known_patterns = {
        'vm network': 'VM Network',
        'vm': 'VM',
        'vmotion': 'vMotion',
        'management': 'Management',
        'iscsi': 'iSCSI',
        'nfs': 'NFS',
        'san': 'SAN',
        'vsan': 'vSAN',
        'ft': 'FT',
        'replication': 'Replication',
        'backup': 'Backup',
        'dmz': 'DMZ',
        'wan': 'WAN',
        'lan': 'LAN',
        'vlan': 'VLAN',
        'prod': 'Prod',
        'production': 'Production',
        'dev': 'Dev',
        'development': 'Development',
        'test': 'Test',
        'qa': 'QA',
        'uat': 'UAT',
        'storage': 'Storage',
    }
    
    # Check exact match first
    text_lower = text.lower()
    if text_lower in known_patterns:
        return known_patterns[text_lower]
    
    # Check if text starts with a known pattern followed by numbers (e.g., "iscsi02")
    for pattern_key, pattern_value in known_patterns.items():
        if text_lower.startswith(pattern_key):
            # Extract the suffix (numbers or additional text)
            suffix = text[len(pattern_key):]
            return f"{pattern_value}{suffix}"
    
    # Check if text contains known patterns (e.g., "iscsi veeam" -> "iSCSI Veeam")
    # Split into words and process each word
    words = text.split()
    normalized_words = []
    
    for word in words:
        word_lower = word.lower()
        
        # Check if word starts with a known pattern followed by numbers
        matched = False
        for pattern_key, pattern_value in known_patterns.items():
            if word_lower.startswith(pattern_key) and len(word_lower) > len(pattern_key):
                # Extract suffix after the pattern
                suffix = word[len(pattern_key):]
                normalized_words.append(f"{pattern_value}{suffix}")
                matched = True
                break
        
        if not matched:
            # Check for exact match
            if word_lower in known_patterns:
                normalized_words.append(known_patterns[word_lower])
            else:
                # Use the existing title case function to preserve acronyms
                normalized_words.append(to_title_case_preserve_acronyms(word))
    
    return ' '.join(normalized_words)