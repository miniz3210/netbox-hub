import os
import json
import re
from typing import Dict, List
from datetime import datetime
from config.constants import RULES_FILE, RULES_HISTORY_FILE, MAX_HISTORY_ENTRIES

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

DOMAIN_DEFAULTS = {
    "CORP_DOMAIN_IT": ".example.corp",
    "CORP_DOMAIN_OT_PRIMARY": ".example.ot",
    "CORP_DOMAIN_OT_SECONDARY": ".example.ot",
    "CORP_DOMAIN_LOCAL": ".corp.local",
}

def _env_domain(key: str) -> str:
    return os.getenv(key, DOMAIN_DEFAULTS.get(key, "")).strip()

DEFAULT_NAMING_PATTERNS = {
    "branch_switch": "SW<Country><State><Site><Zone><Seq>-<StackID>",
    "branch_stack": "VS<Country><State><Site><Seq>-<StackID>",
    "branch_ap": "WAP<Country><State><Site><Seq>",
    "branch_firewall": "FW<Country><State><Site><Vendor><Seq>",
    "branch_ion": "ION<Country><State><Site><Seq>",
    "branch_router": "RTR<Country><State><Site><Zone><Seq>",
    "branch_va": "VA<Country><State><Site><Zone><Seq>",
    "switch_uplink_desc": "Uplink_to_<Remote_Device>_<Remote_Port>",
    "switch_lag_member": "LACP_to_<Remote_Device>_<Remote_Port>",
    "switch_port_channel": "<Local_Po_ID>_to_<Remote_Device>",
    "switch_access_desc": "<VLAN_Name> - <Device>_<Port>",
    "firewall_interface": "<Role_Zone>_<VLAN_ID>",
    "esxi_host": "<site_prefix><role_esx><host_seq>.<domain>",
    "vm_host": "<Country><Site><Role><Seq>",
    "esxi_uplink": "<vmnic> - <vSwitch> <Purpose> <Status>",
    "esxi_portgroup_name": "PG-<pg_network>",
    "esxi_portgroup": "<PortGroup> [<Active_vmnics> Active / <Standby_vmnics> Standby]",
    "esxi_vmkernel_name": "<vmk>",
    "esxi_vmkernel": "<Purpose> Network - <vSwitch> (<Active_vmnics> Active / <Standby_vmnics> Standby)",
    "netbox_server_yaml": (
        "console-ports: Serial (de-9); "
        "module-bays: PSU1, PSU2, OCP3, PCIe1, PCIe2, PCIe3; "
        "interfaces: OOB Management ONLY (1000base-t, mgmt_only: true)"
    ),
}

PATTERN_VARIABLES = {
    "Country": {"label": "Country Code (2-letter)", "placeholder": "e.g. US, UK, AU, DE, JP"},
    "State": {"label": "State / Region (Optional)", "placeholder": "e.g. NY, CA, TX, NSW"},
    "Site": {"label": "Site Code", "placeholder": "e.g. NYC, LON, SYD, AGE"},
    "Zone": {"label": "Zone / Role / Vendor (Optional)", "placeholder": "e.g. CORE, DIST, EDGE, PA"},
    "Vendor": {"label": "Vendor (Optional)", "placeholder": "e.g. PA, CISCO, HUAWEI"},
    "Seq": {"label": "Sequence Number", "placeholder": "e.g. 01, 02"},
    "StackID": {"label": "Stack / Member ID (Optional)", "placeholder": "e.g. 0, 1"},
    "Local_Device": {"label": "Local Device Hostname", "placeholder": "e.g. SWUSNYC01-0"},
    "Local_Port": {"label": "Local Port", "placeholder": "e.g. Gi1/0/48, Te1/0/1"},
    "Remote_Device": {"label": "Remote Device Hostname", "placeholder": "e.g. SWUSNYC02-0"},
    "Remote_Port": {"label": "Remote Port", "placeholder": "e.g. Gi1/0/48, Te1/0/1"},
    "Local_Po_ID": {"label": "Local Port-Channel ID", "placeholder": "LAG1"},
    "VLAN_Name": {"label": "VLAN Name", "placeholder": "e.g. DATA, VOIP"},
    "VLAN_ID": {"label": "VLAN ID", "placeholder": "e.g. 10, 20"},
    "Device": {"label": "Connected Device", "placeholder": "e.g. WAP01"},
    "Port": {"label": "Connected Port", "placeholder": "e.g. Gi0/1"},
    "Role_Zone": {"label": "Security Zone / Role", "placeholder": "e.g. INSIDE, OUTSIDE"},
    "site": {"label": "Site Prefix", "placeholder": "e.g. age, nyc, lon, syd"},
    "site_prefix": {"label": "Site Prefix", "placeholder": "e.g. age, nyc, lon, syd"},
    "role": {"label": "Host Role (Optional)", "placeholder": "e.g. esx, otinfhost, infhost"},
    "role_esx": {"label": "Host Role (Optional)", "placeholder": "e.g. esx, otinfhost, infhost"},
    "seq": {"label": "Host Sequence Number", "placeholder": "001"},
    "host_seq": {"label": "Host Sequence Number", "placeholder": "001"},
    "Domain": {"label": "Domain Name (FQDN Suffix)", "placeholder": "e.g. corp.example.com, internal.net"},
    "domain": {"label": "Domain Name (FQDN Suffix)", "placeholder": "e.g. corp.example.com, internal.net"},
    "Role": {"label": "Role Code / Workload", "placeholder": "e.g. app, web, db, fs, dc"},
    "vm_site": {"label": "Site Prefix / Country & Site", "placeholder": "e.g. age, usnyc, uklon"},
    "vmnic": {"label": "vmnic Name", "placeholder": "vmnic", "default": "vmnic"},
    "vSwitch": {"label": "vSwitch Name", "placeholder": "vSwitch", "default": "vSwitch"},
    "Purpose": {"label": "Purpose / Service", "placeholder": "e.g. Management, vMotion, Storage"},
    "Status": {"label": "Status", "placeholder": "Active Uplink / Standby Uplink"},
    "pg_network": {"label": "Network", "placeholder": "e.g. VM Network"},
    "PortGroup": {"label": "Port Group / vSwitch", "placeholder": "e.g. vSwitch0", "default": "vSwitch"},
    "Active_vmnics": {"label": "Active vmnics", "placeholder": "e.g. vmnic0, vmnic1", "default": "vmnic"},
    "Standby_vmnics": {"label": "Standby vmnics (Optional)", "placeholder": "e.g. vmnic2"},
    "vmk": {"label": "vmk Name", "placeholder": "vmk", "default": "vmk"},
}

# Structured Device / Interface presets driving the horizontal radio choices in the
# Naming tab. Each item: code (e.g. SW, Uplink), label, pattern_key, description.
# These live in data/naming_rules.yaml so the Standards Tab can fully modify them.
DEVICE_PRESETS = [
    {"code": "SW", "label": "Switch (SW / SWI)", "pattern_key": "branch_switch",
     "description": "Layer 2/3 access/distribution switch"},
    {"code": "VS", "label": "Virtual Chassis / Stack (VS)", "pattern_key": "branch_stack",
     "description": "Virtual chassis or physical switch stack"},
    {"code": "FW", "label": "Firewall / Security (FW)", "pattern_key": "branch_firewall",
     "description": "Firewall / security appliance"},
    {"code": "ION", "label": "SD-WAN / Prisma (ION)", "pattern_key": "branch_ion",
     "description": "SD-WAN / Prisma Access gateway"},
    {"code": "WAP", "label": "Wireless AP (WAP)", "pattern_key": "branch_ap",
     "description": "Wireless access point"},
    {"code": "RTR", "label": "Router (RTR)", "pattern_key": "branch_router",
     "description": "Routing device"},
    {"code": "VA", "label": "Virtual Appliance (VA)", "pattern_key": "branch_va",
     "description": "Virtualised appliance / gateway"},
]

INTERFACE_PRESETS = [
    {"code": "Uplink", "label": "Switch Uplink (Inter-Switch)", "pattern_key": "switch_uplink_desc",
     "description": "Inter-switch uplink description"},
    {"code": "LAG", "label": "Switch LAG Member (LACP)", "pattern_key": "switch_lag_member",
     "description": "LACP aggregate member description"},
    {"code": "Po", "label": "Switch Port-Channel (Logical)", "pattern_key": "switch_port_channel",
     "description": "Logical port-channel / port-group description"},
    {"code": "Access", "label": "Switch Access Port (Endpoint)", "pattern_key": "switch_access_desc",
     "description": "Endpoint access-port description"},
    {"code": "FW Zone", "label": "Firewall Security Zone Interface", "pattern_key": "firewall_interface",
     "description": "Firewall security-zone interface description"},
]

LEGACY_PATTERN_KEYS = list(DEFAULT_NAMING_PATTERNS.keys())

DOMAIN_ENV_KEYS = (
    "CORP_DOMAIN_IT",
    "CORP_DOMAIN_OT_PRIMARY",
    "CORP_DOMAIN_OT_SECONDARY",
    "CORP_DOMAIN_LOCAL",
)

def _is_str(value) -> bool:
    return isinstance(value, str)

def _substitute_env(value: str) -> str:
    """Expand ${VAR} placeholders in a rule value using os.path.expandvars.

    Falls back to the generic placeholder domain when the environment variable is not set,
    so templates always render something usable rather than empty text.
    """
    result = os.path.expandvars(value)
    # expandvars leaves unknown ${NAME} unchanged when the var is undefined; replace any
    # remaining references to our documented domain vars with the generic fallback.
    for key in DOMAIN_ENV_KEYS:
        if f"${{{key}}}" in result:
            result = result.replace(f"${{{key}}}", DOMAIN_DEFAULTS.get(key, ""))
    return result

def _load_rules_dict_from_file() -> dict:
    """Read the rules file as JSON and apply environment variable substitution."""
    if not os.path.exists(RULES_FILE):
        return {}
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    normalized = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            normalized[str(k)] = {
                str(ik): (_substitute_env(str(iv)) if _is_str(iv) else iv)
                for ik, iv in v.items()
            }
        elif _is_str(v):
            normalized[str(k)] = _substitute_env(v)
        else:
            normalized[str(k)] = v
    return normalized


def _split_legacy_device_patterns(patterns: dict) -> dict:
    """Split slash-joined device templates into one canonical pattern per type."""
    out = dict(patterns)
    switch = out.get("branch_switch", "")
    if " / " in switch:
        parts = [p.strip() for p in switch.split(" / ") if p.strip()]
        sw = next((p for p in parts if p.startswith("SW")), parts[0] if parts else "")
        vs = next((p for p in parts if p.startswith("VS")), "")
        if sw:
            out["branch_switch"] = sw
        if vs and not out.get("branch_stack"):
            out["branch_stack"] = vs
    security = out.get("branch_security", "")
    if " / " in security:
        parts = [p.strip() for p in security.split(" / ") if p.strip()]
        fw = next((p for p in parts if p.startswith("FW")), parts[0] if parts else "")
        ion = next((p for p in parts if p.startswith("ION")), "")
        if fw:
            out["branch_security"] = fw
            if not out.get("branch_firewall"):
                out["branch_firewall"] = fw
        if ion and not out.get("branch_ion"):
            out["branch_ion"] = ion
    return out


def extract_tokens(pattern: str) -> List[str]:
    """Extract unique token names from a pattern string.

    Combined/annotated tokens such as ``<role/esx>`` are reduced to their first
    segment (``role``) so they still drive an input widget. This matches the legacy
    behaviour where ``<role>`` and ``<role/esx>`` map to the same variable.
    """
    tokens: List[str] = []
    if not pattern:
        return tokens
    for raw_token in re.findall(r"<([^>]+)>", pattern):
        token = raw_token.split("/")[0].strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _normalize_rules(raw: dict) -> dict:
    """Upgrade any rule dict to the merged schema used by the app.

    The merged schema keeps every pattern key flat at the top level (for backward
    compatibility) and additionally exposes ``naming_patterns`` and ``pattern_variables``
    sub-dictionaries consumed by the dynamic UI.
    """
    raw_patterns = raw.get("naming_patterns")
    if not isinstance(raw_patterns, dict):
        raw_patterns = {
            k: v for k, v in raw.items()
            if _is_str(v) and k not in ("pattern_variables", "naming_patterns")
        }
    patterns = {k: str(v) for k, v in raw_patterns.items()}
    patterns = _split_legacy_device_patterns(patterns)
    patterns = migrate_port_short_tokens(patterns)
    for key in LEGACY_PATTERN_KEYS:
        if key not in patterns:
            patterns[key] = DEFAULT_NAMING_PATTERNS.get(key, "")

    merged = dict(patterns)

    custom = raw.get("custom_patterns")
    if isinstance(custom, list):
        merged["custom_patterns"] = [{"label": str(c.get("label", "")), "key": str(c.get("key", "")), "pattern": str(c.get("pattern", ""))} for c in custom if isinstance(c, dict)]

    merged["device_presets"] = _normalize_presets(raw.get("device_presets"), DEVICE_PRESETS)
    merged["interface_presets"] = _normalize_presets(raw.get("interface_presets"), INTERFACE_PRESETS)

    variables = raw.get("pattern_variables")
    if not isinstance(variables, dict):
        variables = PATTERN_VARIABLES.copy()
    else:
        merged_vars = PATTERN_VARIABLES.copy()
        for k, v in variables.items():
            if isinstance(v, dict) and v:
                existing = merged_vars.get(k, {})
                merged_vars[str(k)] = {**existing, **v}
            elif _is_str(v):
                merged_vars[str(k)] = {"label": v, "placeholder": f"e.g. {k}"}
        merged_vars.pop("Local_Port_Short", None)
        merged_vars.pop("Remote_Port_Short", None)
        variables = merged_vars

    merged["naming_patterns"] = dict(patterns)
    merged["pattern_variables"] = variables
    token_order = raw.get("token_order")
    if isinstance(token_order, dict):
        normalized_to = {}
        for k, v in token_order.items():
            if isinstance(v, (list, tuple)):
                normalized_to[str(k)] = [str(x) for x in v]
            elif _is_str(v):
                normalized_to[str(k)] = [x for x in v.split(",") if x]
        if normalized_to:
            merged["token_order"] = normalized_to
    return merged


def _normalize_presets(raw_presets, defaults):
    """Validate and normalise a device/interface presets list; falls back to defaults."""
    if not isinstance(raw_presets, list):
        return list(defaults)
    normalized = []
    for p in raw_presets:
        if not isinstance(p, dict):
            continue
        code = str(p.get("code", "")).strip()
        label = str(p.get("label", "")).strip()
        pattern_key = str(p.get("pattern_key", "")).strip()
        if code and label and pattern_key:
            normalized.append({
                "code": code,
                "label": label,
                "pattern_key": pattern_key,
                "description": str(p.get("description", "")).strip(),
            })
    return normalized or list(defaults)


def get_device_presets(rules: dict) -> list:
    """Return the device presets list from a rules dict (defaults if missing)."""
    raw = rules.get("device_presets")
    return _normalize_presets(raw, DEVICE_PRESETS)


def get_interface_presets(rules: dict) -> list:
    """Return the interface presets list from a rules dict (defaults if missing)."""
    raw = rules.get("interface_presets")
    return _normalize_presets(raw, INTERFACE_PRESETS)


def make_preset_key(code: str, prefix: str = "branch") -> str:
    """Generate a safe, collision-free pattern key from a preset code."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", code.strip()).strip("_").lower()
    return f"{prefix}_{slug}" if slug else f"{prefix}_custom"


def load_naming_rules() -> Dict[str, str]:
    rules = _load_rules_dict_from_file()
    if not rules:
        return _normalize_rules(DEFAULT_NAMING_PATTERNS.copy())
    return _normalize_rules(rules)


def get_pattern_variables(rules: dict) -> dict:
    """Return the variable metadata dict from a rules structure (defaults if missing)."""
    variables = rules.get("pattern_variables")
    if isinstance(variables, dict) and variables:
        return variables
    return PATTERN_VARIABLES.copy()


def get_custom_patterns(rules: dict) -> List[dict]:
    """Return the list of user-defined custom patterns (label/key/pattern)."""
    custom = rules.get("custom_patterns")
    if isinstance(custom, list):
        return [
            {"label": str(c.get("label", "")), "key": str(c.get("key", "")), "pattern": str(c.get("pattern", ""))}
            for c in custom if isinstance(c, dict)
        ]
    return []


def get_naming_patterns(rules: dict) -> dict:
    """Return the naming patterns dict from a rules structure (defaults if missing).

    Custom patterns (stored under ``custom_patterns``) are merged in under their key so
    they register dynamically across the application (Naming tab, prompt export, etc.).
    """
    patterns = rules.get("naming_patterns")
    if not (isinstance(patterns, dict) and patterns):
        patterns = {k: v for k, v in rules.items() if _is_str(v)}
    merged = dict(patterns)
    for cp in get_custom_patterns(rules):
        if cp.get("key") and cp.get("pattern") is not None:
            merged[cp["key"]] = cp["pattern"]
    return merged


def save_naming_rules(rules: Dict[str, str], source: str = "Manual Edit"):
    """Save naming rules and add to history."""
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)

    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                old_rules = json.load(f)
            add_to_history(old_rules, source)
        except Exception:
            pass

    stored = _normalize_rules(dict(rules))
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

DEFAULT_RULES = _normalize_rules(DEFAULT_NAMING_PATTERNS.copy())

def add_to_history(rules: Dict[str, str], source: str = "Manual Edit"):
    """Add rules to history with timestamp."""
    history = load_history()

    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "rules": rules
    }

    # Add to beginning of list
    history.insert(0, entry)

    # Keep only last MAX_HISTORY_ENTRIES
    history = history[:MAX_HISTORY_ENTRIES]

    # Save history
    os.makedirs(os.path.dirname(RULES_HISTORY_FILE), exist_ok=True)
    with open(RULES_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def load_history() -> List[Dict]:
    """Load history of naming rules changes."""
    if os.path.exists(RULES_HISTORY_FILE):
        try:
            with open(RULES_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def restore_from_history(index: int) -> Dict[str, str]:
    """Restore naming rules from history by index."""
    history = load_history()
    if 0 <= index < len(history):
        raw_rules = history[index]["rules"]
        if isinstance(raw_rules, dict):
            return _normalize_rules(raw_rules)
        return raw_rules
    return DEFAULT_RULES.copy()

def clear_history():
    """Clear all history entries."""
    if os.path.exists(RULES_HISTORY_FILE):
        os.remove(RULES_HISTORY_FILE)

def export_rules_as_prompt(rules: Dict[str, str]) -> str:
    p = get_naming_patterns(rules)
    variables = get_pattern_variables(rules)
    var_lines = "\n".join(
        f"- <{name}>: {meta.get('label', name)}"
        for name, meta in variables.items()
    )
    return f"""# INFRASTRUCTURE & NAMING CONVENTIONS STANDARD (AUTOMATION GRADE)

1. Network & Security Devices:
- Switch Hostname: {p.get('branch_switch', '')}
- Virtual Chassis / Stack Hostname: {p.get('branch_stack', '')}
- Wireless AP Hostname: {p.get('branch_ap', '')}
- Firewall Hostname: {p.get('branch_firewall', p.get('branch_security', ''))}
- SD-WAN / Prisma Hostname: {p.get('branch_ion', '')}
- Router Hostname: {p.get('branch_router', '')}
- Virtual Appliance Hostname: {p.get('branch_va', '')}

2. Switch & Firewall Interface Descriptions:
- Switch Uplink Description: {p.get('switch_uplink_desc', '')}
- Switch LAG Member Description: {p.get('switch_lag_member', '')}
- Switch Port Channel Description: {p.get('switch_port_channel', '')}
- Switch Access Port Description: {p.get('switch_access_desc', '')}
- Firewall Interface Description: {p.get('firewall_interface', '')}

3. Hypervisors & Virtual Machines:
- ESXi Hypervisor Hostname: {p.get('esxi_host', '')}
  * IT / Corporate ESXi Domain: {_env_domain('CORP_DOMAIN_IT')} (e.g. host001{_env_domain('CORP_DOMAIN_IT')})
  * OT / Industrial Cluster Domain: {_env_domain('CORP_DOMAIN_OT_PRIMARY')} / {_env_domain('CORP_DOMAIN_OT_SECONDARY')} (e.g. host001{_env_domain('CORP_DOMAIN_OT_PRIMARY')})
  * Branch / Standalone: {_env_domain('CORP_DOMAIN_LOCAL')} or shortname (no FQDN)
- Virtual Machine (VM) Hostname: {p.get('vm_host', '')}

4. ESXi Network Descriptions:
- ESXi Physical Uplink Description: {p.get('esxi_uplink', '')}
- ESXi Port Group Teaming Description: {p.get('esxi_portgroup', '')}
- ESXi VMkernel Description: {p.get('esxi_vmkernel', '')}

5. Available Pattern Variables:
{var_lines}

6. NetBox Hardware YAML Schema:
- {p.get('netbox_server_yaml', '')}
"""
