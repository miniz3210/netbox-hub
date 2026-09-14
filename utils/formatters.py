import re
from config.constants import NETWORKING_ACRONYMS

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
    Dynamically parse network interface names and convert to standard short form.
    
    Handles various vendor formats including Cisco, Huawei, Arista, etc.
    Supports sub-interfaces and gracefully handles already-shortened inputs.
    
    Examples:
        "XGigabitEthernet0/0/31" -> "XGE0/0/31"
        "TenGigabitEthernet1/0/1" -> "Te1/0/1"
        "GigabitEthernet1/0/24" -> "Gi1/0/24"
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
        'gigabitethernet': 'Gi',
        'gige': 'Gi',
        'fastethernet': 'Fa',
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