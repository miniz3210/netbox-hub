"""Persistence and prompt rendering for infrastructure naming conventions.

The public runtime API remains a flat ``Dict[str, str]`` for compatibility with
the naming and standards tabs.  Files and history use a structured YAML shape,
but legacy flat JSON/YAML dictionaries are normalized on read.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Mapping

import yaml

from config.constants import MAX_HISTORY_ENTRIES, RULES_FILE, RULES_HISTORY_FILE

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
DOMAIN_ENV_KEYS = tuple(DOMAIN_DEFAULTS)

# Stable category ordering is also used when rendering the AI prompt.
CATEGORIES = {
    "network_security": "Network & Security Devices",
    "interface_descriptions": "Switch & Firewall Interface Descriptions",
    "hypervisors_virtual_machines": "Hypervisors & Virtual Machines",
    "esxi_network": "ESXi Network Descriptions",
    "netbox_hardware": "NetBox Hardware YAML Schema",
}

PATTERN_KEYS = (
    "branch_switch", "branch_ap", "branch_security",
    "switch_uplink_desc", "switch_uplink_local", "switch_uplink_remote", "switch_lag_member", "switch_port_channel",
    "switch_access_desc", "firewall_interface", "esxi_host", "vm_host",
    "esxi_uplink", "esxi_portgroup", "esxi_portgroup_name", "esxi_portgroup_desc", "esxi_vmkernel", "esxi_vmkernel_name", "esxi_vmkernel_desc", "netbox_server_yaml",
)
_PATTERN_CATEGORIES = {
    **{key: "network_security" for key in ("branch_switch", "branch_ap", "branch_security")},
    **{key: "interface_descriptions" for key in ("switch_uplink_desc", "switch_uplink_local", "switch_uplink_remote", "switch_lag_member", "switch_port_channel", "switch_access_desc", "firewall_interface")},
    **{key: "hypervisors_virtual_machines" for key in ("esxi_host", "vm_host")},
    **{key: "esxi_network" for key in ("esxi_uplink", "esxi_portgroup", "esxi_portgroup_name", "esxi_portgroup_desc", "esxi_vmkernel", "esxi_vmkernel_name", "esxi_vmkernel_desc")},
    "netbox_server_yaml": "netbox_hardware",
}

DEFAULT_RULES = {
    "branch_switch": "SW<Country><State><Site><Zone><Seq>-<StackID> / VS<Country><State><Site><Seq>-<StackID>",
    "branch_ap": "WAP<Country><State><Site><Seq>",
    "branch_security": "FW<Country><State><Site><Vendor><Seq> / ION<Country><State><Site><Seq>",
    "switch_uplink_desc": "Uplink_to_<Remote_Device>_<Remote_Port_Short>",
    "switch_uplink_local": "Uplink_to_<Remote_Device>_<Remote_Port_Short>",
    "switch_uplink_remote": "Uplink_to_<Local_Device>_<Local_Port_Short>",
    "switch_lag_member": "LACP_to_<Remote_Device>_<Remote_Port_Short>",
    "switch_port_channel": "<Local_Po_ID>_to_<Remote_Device>",
    "switch_access_desc": "<VLAN_Name> - <Device>_<Port>",
    "firewall_interface": "<Role_Zone>_<VLAN_ID>",
    "esxi_host": "<site><role/esx><seq>.<domain> (Valid domains: ${CORP_DOMAIN_IT} for IT/Corp, ${CORP_DOMAIN_OT_PRIMARY} or ${CORP_DOMAIN_OT_SECONDARY} for OT/Industrial, ${CORP_DOMAIN_LOCAL} for Branch/Local, or shortname without domain)",
    "vm_host": "<Country><Site><Role><Seq> (e.g. AURFLWOTAPP01, AUGLOSFS01) or <Site><Role><Seq> (e.g. PWSAFS001, ESCPDC01, NYCCVI01)",
    "esxi_uplink": "<vmnic> - <vSwitch> <Purpose> <Status>",
    "esxi_portgroup": "<PortGroup> [<Active_vmnics> Active / <Standby_vmnics> Standby]",
    "esxi_portgroup_name": "PG-<pg_network>",
    "esxi_portgroup_desc": "<vSwitch> (<Active_vmnics> Active / <Standby_vmnics> Standby)",
    "esxi_vmkernel": "<Purpose> Network - <vSwitch> (<Active_vmnics> Active / <Standby_vmnics> Standby)",
    "esxi_vmkernel_name": "<vmk>",
    "esxi_vmkernel_desc": "<Purpose> (<vSwitch>)",
    "netbox_server_yaml": "console-ports: Serial (de-9); module-bays: PSU1, PSU2, OCP3, PCIe1, PCIe2, PCIe3; interfaces: OOB Management ONLY (1000base-t, mgmt_only: true)",
}

# Reference data is intentionally generic and can be consumed by non-UI clients.
_TOKEN_DEFAULTS = {
    "Country": ("Country Code", "e.g. US"), "State": ("State / Region", "e.g. NY"),
    "Site": ("Site Code", "e.g. NYC"), "Zone": ("Zone / Role / Vendor", "e.g. CORE"),
    "Vendor": ("Vendor", "e.g. PA"), "Seq": ("Sequence Number", "e.g. 01"),
    "StackID": ("Stack / Member ID", "e.g. 0"), "Local_Device": ("Local Device Hostname", "e.g. SWUSNYC01-0"),
    "Local_Port": ("Local Port", "e.g. Gi1/0/48"), "Local_Port_Short": ("Local Port (Short)", "e.g. Gi1/0/48"),
    "Remote_Device": ("Remote Device Hostname", "e.g. SWUSNYC02-0"), "Remote_Port": ("Remote Port", "e.g. Te1/0/1"),
    "Remote_Port_Short": ("Remote Port (Short)", "e.g. Te1/0/1"), "Local_Po_ID": ("Local Port-Channel ID", "e.g. Po1"),
    "VLAN_ID": ("VLAN ID", "e.g. 10"), "VLAN_Name": ("VLAN Name", "e.g. Data"),
    "Device": ("Connected Device / Host", "e.g. PC-001"), "Port": ("Endpoint Port", "e.g. eth0"),
    "Role_Zone": ("Role / Zone", "e.g. TRUST"), "site": ("Site Prefix", "e.g. age"),
    "role": ("Host Role", "e.g. esx"), "esx": ("Host Role", "e.g. esx"), "seq": ("Sequence Number", "e.g. 001"),
    "domain": ("Domain Name", "e.g. corp.example.com"), "Role": ("Workload Role", "e.g. app"),
    "vmnic": ("vmnic Name", "e.g. vmnic0"), "vSwitch": ("vSwitch Name", "e.g. vSwitch0"),
    "Purpose": ("Purpose / Service", "e.g. Management"), "Status": ("Status", "e.g. Active Uplink"),
    "PortGroup": ("Port Group", "e.g. vSwitch0"), "pg_network": ("Network", "e.g. VM Network"),
    "vmk": ("vmk Name", "e.g. vmk0"), "Active_vmnics": ("Active vmnics", "e.g. vmnic0"),
    "Standby_vmnics": ("Standby vmnics", "e.g. vmnic1"),
}
_TOKEN_CATEGORIES = {
    token: "network_security" for token in ("Country", "State", "Site", "Zone", "Vendor", "Seq", "StackID")
}
_TOKEN_CATEGORIES.update({token: "interface_descriptions" for token in ("Local_Device", "Local_Port", "Local_Port_Short", "Remote_Device", "Remote_Port", "Remote_Port_Short", "Local_Po_ID", "VLAN_ID", "VLAN_Name", "Device", "Port", "Role_Zone")})
_TOKEN_CATEGORIES.update({token: "hypervisors_virtual_machines" for token in ("site", "role", "esx", "seq", "domain", "Role")})
_TOKEN_CATEGORIES.update({token: "esxi_network" for token in ("vmnic", "vSwitch", "Purpose", "Status", "PortGroup", "Active_vmnics", "Standby_vmnics")})
DEFAULT_PATTERN_VARIABLES = {
    token: {"label": values[0], "placeholder": values[1], "category": _TOKEN_CATEGORIES.get(token, "custom")}
    for token, values in _TOKEN_DEFAULTS.items()
}
# Lowercase aliases make the metadata convenient for callers that mirror the
# YAML keys, while the uppercase constants remain the canonical Python API.
categories = CATEGORIES
pattern_variables = DEFAULT_PATTERN_VARIABLES


def _env_domain(key: str) -> str:
    return os.getenv(key, DOMAIN_DEFAULTS.get(key, "")).strip()


def _substitute_env(value: str) -> str:
    result = os.path.expandvars(value)
    for key in DOMAIN_ENV_KEYS:
        result = result.replace(f"${{{key}}}", _env_domain(key))
    return result


def _flat_rules(raw: Any) -> Dict[str, str]:
    """Normalize structured or legacy data into the runtime flat mapping."""
    if not isinstance(raw, Mapping):
        return {}
    patterns = raw.get("naming_patterns") or raw.get("patterns")
    if isinstance(patterns, Mapping):
        raw = patterns
    elif isinstance(raw.get("categories"), Mapping):
        flattened = {}
        for category in raw["categories"].values():
            if isinstance(category, Mapping):
                flattened.update(category)
        raw = flattened
    result = {str(key): _substitute_env(str(value)) for key, value in raw.items() if str(key) in PATTERN_KEYS}
    # Seed split ESXi patterns when loading legacy files.
    result.setdefault("esxi_portgroup_name", DEFAULT_RULES["esxi_portgroup_name"])
    result.setdefault("esxi_portgroup_desc", result.get("esxi_portgroup", DEFAULT_RULES["esxi_portgroup_desc"]))
    result.setdefault("esxi_vmkernel_name", DEFAULT_RULES["esxi_vmkernel_name"])
    result.setdefault("esxi_vmkernel_desc", result.get("esxi_vmkernel", DEFAULT_RULES["esxi_vmkernel_desc"]))
    return result


def _structured_rules(rules: Mapping[str, Any]) -> Dict[str, Any]:
    flat = _flat_rules(rules)
    # _flat_rules applies environment substitution for runtime use; persistence
    # must retain the caller's placeholders where possible.
    source = rules.get("patterns", rules) if isinstance(rules, Mapping) else rules
    if isinstance(source, Mapping) and any(str(key) in PATTERN_KEYS for key in source):
        flat = {str(key): str(value) for key, value in source.items() if str(key) in PATTERN_KEYS}
    categories = {name: {} for name in CATEGORIES}
    for key, value in flat.items():
        categories[_PATTERN_CATEGORIES[key]][key] = value
    variables = rules.get("pattern_variables") if isinstance(rules, Mapping) else None
    if not isinstance(variables, Mapping):
        variables = DEFAULT_PATTERN_VARIABLES
    return {"categories": categories, "pattern_variables": dict(variables), "naming_patterns": flat, "patterns": flat, **flat}


def _read_yaml(path: str) -> Any:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError):
        return None


def load_naming_rules() -> Dict[str, str]:
    raw = _read_yaml(RULES_FILE)
    rules = _flat_rules(raw)
    if not rules:
        raw = {"patterns": DEFAULT_RULES, "pattern_variables": DEFAULT_PATTERN_VARIABLES}
        rules = {key: _substitute_env(value) for key, value in DEFAULT_RULES.items()}
    return _structured_rules(raw or {"patterns": rules})


def save_naming_rules(rules: Mapping[str, Any], source: str = "Manual Edit") -> None:
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    if os.path.exists(RULES_FILE):
        old = _read_yaml(RULES_FILE)
        if old is not None:
            add_to_history(old, source)
    with open(RULES_FILE, "w", encoding="utf-8") as stream:
        yaml.safe_dump(_structured_rules(rules), stream, sort_keys=False, allow_unicode=True)
        stream.flush()
        os.fsync(stream.fileno())


def add_to_history(rules: Mapping[str, Any], source: str = "Manual Edit") -> None:
    history = load_history()
    history.insert(0, {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "source": source, "rules": rules})
    os.makedirs(os.path.dirname(RULES_HISTORY_FILE), exist_ok=True)
    with open(RULES_HISTORY_FILE, "w", encoding="utf-8") as stream:
        json.dump(history[:MAX_HISTORY_ENTRIES], stream, indent=2, ensure_ascii=False)


def load_history() -> List[Dict[str, Any]]:
    if not os.path.exists(RULES_HISTORY_FILE):
        return []
    try:
        with open(RULES_HISTORY_FILE, "r", encoding="utf-8") as stream:
            value = json.load(stream)
        return value if isinstance(value, list) else []
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def restore_from_history(index: int) -> Dict[str, str]:
    history = load_history()
    if 0 <= index < len(history):
        return _flat_rules(history[index].get("rules", {})) or {key: _substitute_env(value) for key, value in DEFAULT_RULES.items()}
    return load_naming_rules()


def clear_history() -> None:
    if os.path.exists(RULES_HISTORY_FILE):
        os.remove(RULES_HISTORY_FILE)


def export_rules_as_prompt(rules: Mapping[str, Any]) -> str:
    flat = _flat_rules(rules)
    sections = ["# INFRASTRUCTURE & NAMING CONVENTIONS STANDARD (AUTOMATION GRADE)", ""]
    for number, (category, title) in enumerate(CATEGORIES.items(), 1):
        sections.append(f"{number}. {title}:")
        for key in PATTERN_KEYS:
            if _PATTERN_CATEGORIES[key] == category:
                label = key.replace("_", " ").title()
                sections.append(f"- {label}: {flat.get(key, '')}")
        if category == "hypervisors_virtual_machines":
            sections.extend([
                f"  * IT / Corporate ESXi Domain: {_env_domain('CORP_DOMAIN_IT')}",
                f"  * OT / Industrial Cluster Domain: {_env_domain('CORP_DOMAIN_OT_PRIMARY')} / {_env_domain('CORP_DOMAIN_OT_SECONDARY')}",
                f"  * Branch / Standalone: {_env_domain('CORP_DOMAIN_LOCAL')} or shortname (no FQDN)",
            ])
        sections.append("")
    return "\n".join(sections)
