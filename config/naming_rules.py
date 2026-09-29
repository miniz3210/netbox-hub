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
    "branch_switch": "SW<country><state><site><zone><seq>-<stack_id>",
    "branch_stack": "VS<country><state><site><seq>-<stack_id>",
    "branch_ap": "WAP<country><state><site><seq>",
    "branch_firewall": "FW<country><state><site><vendor><seq>",
    "branch_ion": "ION<country><state><site><seq>",
    "branch_router": "RTR<country><state><site><zone><seq>",
    "branch_va": "VA<country><state><site><zone><seq>",
    "switch_uplink_desc": "Uplink_to_<remote_device>_<remote_port>",
    "switch_lag_member": "LACP_to_<remote_device>_<remote_port>",
    "switch_port_channel": "<local_po_id>_to_<remote_device>",
    "switch_access_desc": "<vlan_name> - <device>_<port>",
    "firewall_interface": "<role_zone>_<vlan_id>",
    "esxi_host": "<site_prefix><role_esx><seq>.<domain>",
    "host_vm_proxmox": "<site_prefix>pve<seq>.<domain>",
    "vm_host": "<country><site><role><seq>",
    "esxi_uplink": "<vmnic> - <v_switch> <purpose> <status>",
    "esxi_portgroup_name": "PG-<pg_network>",
    "esxi_portgroup": "<port_group> [<active_vmnics> Active / <standby_vmnics> Standby]",
    "esxi_vmkernel_name": "<vmk>",
    "esxi_vmkernel": "<purpose> Network - <v_switch> (<active_vmnics> Active / <standby_vmnics> Standby)",
    "netbox_server_yaml": (
        "console-ports: Serial (de-9); "
        "module-bays: PSU1, PSU2, OCP3, PCIe1, PCIe2, PCIe3; "
        "interfaces: OOB Management ONLY (1000base-t, mgmt_only: true)"
    ),
    "vlan_name_pattern": "<role>",
}

# Canonical hypervisor-scoped variable names used for seeding and re-scoping.
HYPERVISOR_VARIABLE_NAMES = [
    "vmnic", "v_switch", "purpose", "pg_network", "port_group",
    "active_vmnics", "standby_vmnics", "vmk", "switch_zone",
]

def ensure_hypervisor_variables(rules: dict) -> None:
    """Ensure the 9 canonical hypervisor-scoped pattern variables exist.

    - Variables from older files still keyed under ``scope == 'esxi'`` are
      migrated to ``'hypervisor'`` so the standards UI buckets stay consistent.
    - Any of the canonical 9 tokens missing from the registry is backfilled
      with its factory-default metadata from ``PATTERN_VARIABLES`` (label,
      placeholder, optional flag). Existing user values that don't conflict
      (default / placeholder) are preserved.
    """
    variables = rules.get("pattern_variables")
    if not isinstance(variables, dict):
        return
    # 1) Migrate legacy 'esxi' scope entries to 'hypervisor'.
    for meta in variables.values():
        if isinstance(meta, dict) and meta.get("scope") == "esxi":
            meta["scope"] = "hypervisor"
    # 2) Ensure every canonical hypervisor token is present & scoped.
    for name in HYPERVISOR_VARIABLE_NAMES:
        default = PATTERN_VARIABLES.get(name)
        if default is None:
            continue
        existing = variables.get(name)
        if not isinstance(existing, dict):
            variables[name] = dict(default)
            continue
        existing["scope"] = "hypervisor"
        for key, val in default.items():
            if key not in existing or not existing[key]:
                existing[key] = val


def _needs_hypervisor_migration(raw: dict) -> bool:
    """Detect whether the on-disk rule set needs an automatic scope migration."""
    pvars = raw.get("pattern_variables")
    if not isinstance(pvars, dict):
        return False
    for meta in pvars.values():
        if isinstance(meta, dict) and meta.get("scope") == "esxi":
            return True
    for name in HYPERVISOR_VARIABLE_NAMES:
        if name not in pvars:
            return True
    return False


PATTERN_VARIABLES = {
    # 1. Global & Shared Scope
    "site": {"label": "Site / Branch Name", "placeholder": "e.g. Bristol, AGE, NYC", "scope": "shared"},
    "site_slug": {"label": "Site URL Slug", "placeholder": "e.g. bristol, age, nyc", "scope": "shared"},
    "country": {"label": "Country Code (2-letter)", "placeholder": "e.g. US, UK, AU, DE, JP", "scope": "shared"},
    "state": {"label": "State / Region (Optional)", "placeholder": "e.g. NY, CA, TX, NSW", "scope": "shared"},
    "domain": {"label": "Domain Name (FQDN Suffix)", "placeholder": "e.g. corp.example.com, internal.net", "scope": "shared"},
    "status": {"label": "Object Status", "placeholder": "active / planned", "default": "active", "scope": "shared"},

    # 2. IPAM & Subnet Scope
    "vid": {"label": "VLAN ID", "placeholder": "e.g. 100, 300", "scope": "ipam"},
    "vlan_id": {"label": "VLAN ID (Alias)", "placeholder": "e.g. 100, 300", "scope": "ipam"},
    "role": {"label": "VLAN / Subnet Role", "placeholder": "e.g. Corporate WiFi, Workstations", "scope": "ipam"},
    "vlan_name": {"label": "NetBox VLAN Name", "placeholder": "e.g. Corporate WiFi", "scope": "ipam"},
    "vlan_desc": {"label": "VLAN Description", "placeholder": "e.g. VIN_Corp", "scope": "ipam"},
    "subnet": {"label": "Subnet (CIDR)", "placeholder": "e.g. 10.113.64.0/24", "scope": "ipam"},
    "prefix": {"label": "Prefix (CIDR)", "placeholder": "e.g. 10.113.64.0/24", "scope": "ipam"},
    "prefix_desc": {"label": "Prefix Description", "placeholder": "e.g. Bristol Corporate WiFi -- VLAN 300", "scope": "ipam"},
    "scope_id": {"label": "NetBox Scope ID (Site ID)", "placeholder": "e.g. 30", "scope": "ipam"},
    "scope_type": {"label": "NetBox Scope Type", "placeholder": "dcim.site", "default": "dcim.site", "scope": "ipam"},
    "site_supernet": {"label": "Site Supernet (CIDR)", "placeholder": "e.g. 10.113.64.0/21", "scope": "ipam"},
    "supernet_desc": {"label": "Supernet Description", "placeholder": "e.g. Site Subnet - 10.113.64.1 - 10.113.71.254", "scope": "ipam"},
    "vlan_group": {"label": "VLAN Group Name", "placeholder": "e.g. Bristol VLAN Group", "scope": "ipam"},
    "vlan_group_slug": {"label": "VLAN Group Slug", "placeholder": "e.g. bristol-vlan-group", "scope": "ipam"},

    # 3. Device & VM Naming Scope
    "zone": {"label": "Zone / Role / Vendor (Optional)", "placeholder": "e.g. CORE, DIST, EDGE, PA", "scope": "naming"},
    "vendor": {"label": "Vendor (Optional)", "placeholder": "e.g. PA, CISCO, HUAWEI", "scope": "naming"},
    "seq": {"label": "Sequence Number", "placeholder": "e.g. 01, 02", "scope": "naming"},
    "stack_id": {"label": "Stack / Member ID (Optional)", "placeholder": "e.g. 0, 1", "scope": "naming"},
    "local_device": {"label": "Local Device Hostname", "placeholder": "e.g. SWUSNYC01-0", "scope": "naming"},
    "local_port": {"label": "Local Port", "placeholder": "e.g. Gi1/0/48, Te1/0/1", "scope": "naming"},
    "remote_device": {"label": "Remote Device Hostname", "placeholder": "e.g. SWUSNYC02-0", "scope": "naming"},
    "remote_port": {"label": "Remote Port", "placeholder": "e.g. Gi1/0/48, Te1/0/1", "scope": "naming"},
    "local_po_id": {"label": "Local Port-Channel ID", "placeholder": "LAG1", "scope": "naming"},
    "device": {"label": "Connected Device", "placeholder": "e.g. WAP01", "scope": "naming"},
    "port": {"label": "Connected Port", "placeholder": "e.g. Gi0/1", "scope": "naming"},
    "role_zone": {"label": "Security Zone / Role", "placeholder": "e.g. INSIDE, OUTSIDE", "scope": "naming"},
    "role_esx": {"label": "Host Role (Optional)", "placeholder": "e.g. esx, otinfhost, infhost", "scope": "naming"},
    "site_prefix": {"label": "Site Prefix (Short Code)", "placeholder": "e.g. age, nyc, lon, syd", "scope": "naming"},
    "vm_site": {"label": "Site Prefix / Country & Site", "placeholder": "e.g. age, usnyc, uklon", "scope": "naming"},

    # 4. Hypervisor Virtualization & Networking Scope
    "vmnic": {"label": "vmnic Name", "placeholder": "vmnic", "default": "vmnic", "scope": "hypervisor"},
    "v_switch": {"label": "vSwitch Name", "placeholder": "vSwitch", "default": "vSwitch", "scope": "hypervisor"},
    "purpose": {"label": "Purpose / Service", "placeholder": "e.g. Management, vMotion, Storage", "scope": "hypervisor"},
    "pg_network": {"label": "Network Name", "placeholder": "e.g. VM Network", "scope": "hypervisor"},
    "port_group": {"label": "Port Group / vSwitch", "placeholder": "e.g. vSwitch0", "default": "vSwitch", "scope": "hypervisor"},
    "active_vmnics": {"label": "Active vmnics", "placeholder": "e.g. vmnic0, vmnic1", "default": "vmnic", "scope": "hypervisor"},
    "standby_vmnics": {"label": "Standby vmnics (Optional)", "placeholder": "e.g. vmnic2", "scope": "hypervisor"},
    "vmk": {"label": "vmk Name", "placeholder": "vmk", "default": "vmk", "scope": "hypervisor"},
    "switch_zone": {"label": "Switch Zone / Network Zone", "placeholder": "e.g. DMZ, Production, Management", "scope": "hypervisor"},
}

def get_grouped_pattern_variables(rules: dict) -> dict:
    """Return pattern variables organized by functional scope with self-healing registration."""
    vars_dict = get_pattern_variables(rules)
    
    # Self-healing scan: discover all tokens used across active patterns and schemas
    discovered_tokens = set()
    patterns = rules.get("naming_patterns", {})
    if isinstance(patterns, dict):
        for pat in patterns.values():
            if isinstance(pat, str):
                discovered_tokens.update(extract_tokens(pat))
                
    vlan_presets = rules.get("vlan_presets", {})
    if isinstance(vlan_presets, dict):
        for grp in vlan_presets.values():
            if isinstance(grp, dict):
                discovered_tokens.update(extract_tokens(grp.get("vlan_name_pattern", "")))
                discovered_tokens.update(extract_tokens(grp.get("prefix_pattern", "")))
                
    csv_schemas = rules.get("csv_schemas", {})
    if isinstance(csv_schemas, dict):
        for sch in csv_schemas.values():
            if isinstance(sch, dict):
                for cell in sch.get("row_template", []):
                    discovered_tokens.update(extract_tokens(str(cell)))
                for cell in sch.get("supernet_template", []):
                    discovered_tokens.update(extract_tokens(str(cell)))

    # Register any missing tokens into the variable registry dynamically
    for token in discovered_tokens:
        if token and token not in vars_dict:
            # Assign scope based on token naming convention
            scope = "shared"
            if any(k in token for k in ["vlan", "prefix", "subnet", "vid", "scope"]):
                scope = "ipam"
            elif any(k in token for k in ["vmnic", "switch", "vmk", "purpose", "pg_", "zone"]):
                scope = "hypervisor"
            elif any(k in token for k in ["device", "port", "seq", "stack", "vendor"]):
                scope = "naming"

            vars_dict[token] = {
                "label": token.replace("_", " ").title(),
                "placeholder": f"e.g. {token}",
                "scope": scope,
                "optional": True
            }

    # Group into clean scope buckets
    grouped = {
        "shared": {},
        "ipam": {},
        "naming": {},
        "hypervisor": {}
    }

    for name, meta in vars_dict.items():
        if not isinstance(meta, dict):
            meta = {"label": str(meta), "placeholder": f"e.g. {name}", "scope": "naming"}
        scope = meta.get("scope", "naming")
        if scope not in grouped:
            scope = "naming"
        grouped[scope][name] = meta

    return grouped

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

# Factory defaults for the "HOSTS TYPE PRESETS" card (physical hypervisor
# hosts). Both standard hypervisors must always be present here so that
# "Reset to Defaults" restores the full standard list instead of a single
# entry.
DEFAULT_HOST_TYPE_PRESETS = [
    {"code": "ESXi", "label": "ESXi Host", "pattern_key": "esxi_host",
     "description": "ESXi Hypervisor Host"},
    {"code": "Proxmox", "label": "Proxmox Host", "pattern_key": "host_vm_proxmox",
     "description": "Proxmox VE Hypervisor Host"},
]

DEFAULT_VM_PRESETS = [
    {"code": "cvi", "label": "Core Virtualization (cvi)", "pattern_key": "vm_host",
     "description": "Core / Virtualization VM"},
    {"code": "afs", "label": "App & File Services (afs)", "pattern_key": "vm_host",
     "description": "Application / File Services VM"},
    {"code": "sani", "label": "Storage Infrastructure (sani)", "pattern_key": "vm_host",
     "description": "Storage Infrastructure VM"},
    {"code": "vlab", "label": "Virtual Lab / Test (vlab)", "pattern_key": "vm_host",
     "description": "Virtual Lab / Test VM"},
]

# Canonical default value for the "host_vm_presets" rules key: host types
# first, then the VM roles (see _host_editor / _vm_editor, which both read
# and write this single list).
DEFAULT_HOST_VM_PRESETS = DEFAULT_HOST_TYPE_PRESETS + DEFAULT_VM_PRESETS

HOST_VM_PRESETS = DEFAULT_HOST_VM_PRESETS

ESXI_NETWORK_PRESETS = [
    {"code": "Uplink", "label": "Physical Uplink", "pattern_key": "esxi_uplink",
     "description": "vmnic Uplink Interface"},
    {"code": "PortGroup", "label": "Port Group", "pattern_key": "esxi_portgroup",
     "description": "Standard / Distributed Port Group"},
    {"code": "VMkernel", "label": "VMkernel", "pattern_key": "esxi_vmkernel",
     "description": "VMkernel Management / vMotion / Storage"},
]

DEFAULT_VLAN_PRESETS = {
    "Branch Office VLAN Preset": {
        "vlan_name_pattern": "<role>",
        "prefix_pattern": "<site> <role> -- VLAN <vid>",
        "items": [
            {"vid": 300, "role": "Corporate WiFi"},
            {"vid": 100, "role": "Workstations"},
            {"vid": 5, "role": "Management"},
            {"vid": 700, "role": "Printers"},
            {"vid": 200, "role": "Guests"},
            {"vid": 800, "role": "Audio Visual"},
            {"vid": 400, "role": "Mobiles"}
        ]
    },
    "Data Center VLAN Preset": {
        "vlan_name_pattern": "<role>",
        "prefix_pattern": "<site> <role> -- VLAN <vid>",
        "items": [
            {"vid": 10, "role": "Server Management"},
            {"vid": 20, "role": "Production App"},
            {"vid": 30, "role": "Database"},
            {"vid": 40, "role": "DMZ"},
            {"vid": 50, "role": "Storage / vSAN"}
        ]
    },
    "Custom / Empty Preset": {
        "vlan_name_pattern": "",
        "prefix_pattern": "",
        "items": []
    }
}

DEFAULT_CSV_SCHEMAS = {
    "import_site": {
        "headers": ["name", "slug", "status"],
        "row_template": ["\"<site>\"", "\"<site_slug>\"", "active"]
    },
    "import_vlan_group": {
        "headers": ["name", "slug", "scope_type", "scope_id"],
        "row_template": ["\"<vlan_group>\"", "\"<vlan_group_slug>\"", "\"<scope_type>\"", "<scope_id>"]
    },
    "import_vlans": {
        "headers": ["vid", "name", "status", "site", "group", "description", "role"],
        "row_template": ["<vid>", "\"<vlan_name>\"", "active", "\"<site>\"", "\"<vlan_group>\"", "\"<vlan_desc>\"", "\"<role>\""]
    },
    "import_prefixes": {
        "headers": ["prefix", "status", "scope_type", "scope_id", "vlan_group", "vlan", "role", "description"],
        "row_template": ["\"<prefix>\"", "active", "\"<scope_type>\"", "<scope_id>", "\"<vlan_group>\"", "<vid>", "\"<role>\"", "\"<prefix_desc>\""],
        "supernet_template": ["\"<site_supernet>\"", "active", "\"<scope_type>\"", "<scope_id>", "\"<vlan_group>\"", "", "", "\"<supernet_desc>\""]
    }
}

def get_csv_schemas(rules: dict) -> dict:
    """Return configured CSV schemas or defaults."""
    import copy
    raw = rules.get("csv_schemas")
    if isinstance(raw, dict) and raw:
        return copy.deepcopy(raw)
    return copy.deepcopy(DEFAULT_CSV_SCHEMAS)

DEFAULT_IPAM_ROLE_MAPPINGS = [
    {"pattern": r"(?i)^guests?$", "replacement": "Guests", "description": "Normalize guest / guests to Guests", "enabled": True},
    {"pattern": r"(?i)^corp[_\s\-]?wifi$", "replacement": "Corporate WiFi", "description": "Normalize corporate wifi variations", "enabled": True},
    {"pattern": r"(?i)^workstations?$", "replacement": "Workstations", "description": "Normalize workstation / workstations", "enabled": True},
    {"pattern": r"(?i)^mobiles?$", "replacement": "Mobiles", "description": "Normalize mobile / mobiles", "enabled": True},
    {"pattern": r"(?i)^printers?$", "replacement": "Printers", "description": "Normalize printer / printers", "enabled": True},
    {"pattern": r"(?i)^mgmt|management$", "replacement": "Management", "description": "Normalize mgmt / management", "enabled": True},
    {"pattern": r"(?i)^av|audiovisual|audio[\s\-]?visual$", "replacement": "Audio Visual", "description": "Normalize AV variations", "enabled": True},
]

def get_ipam_role_mappings(rules: dict) -> list:
    raw = rules.get("ipam_role_mappings")
    if isinstance(raw, list) and raw:
        return list(raw)
    return list(DEFAULT_IPAM_ROLE_MAPPINGS)

DEFAULT_VLAN_DESCRIPTION_MAPPINGS = {
    "Corporate WiFi": "VIN_Corp",
    "Workstations": "Wired Workstations",
    "Management": "Management",
    "Printers": "Printers",
    "Guests": "VIN_Guest",
    "Audio Visual": "AV equipment",
    "Mobiles": "VIN_Mobi",
}

LEGACY_PATTERN_KEYS = list(DEFAULT_NAMING_PATTERNS.keys())

# Canonical variable key consolidation: legacy/pre-normalization keys map onto their
# canonical snake_case counterpart so templates referencing old spellings still resolve.
VARIABLE_ALIASES = {
    "host_seq": "seq",
    "Domain": "domain",
    "Role": "role",
}

# Legacy PascalCase/CamelCase variable keys → canonical lower_snake_case. This is the
# source of truth for the automated migration pass that runs on startup/load so the UI
# never displays legacy uppercase keys and every pattern token matches snake_case.
LEGACY_VARIABLE_MIGRATION = {
    "Country": "country",
    "State": "state",
    "Site": "site",
    "Zone": "zone",
    "Vendor": "vendor",
    "Seq": "seq",
    "StackID": "stack_id",
    "Local_Device": "local_device",
    "Local_Port": "local_port",
    "Remote_Device": "remote_device",
    "Remote_Port": "remote_port",
    "Local_Po_ID": "local_po_id",
    "VLAN_Name": "vlan_name",
    "VLAN_ID": "vlan_id",
    "Device": "device",
    "Port": "port",
    "Role_Zone": "role_zone",
    "vSwitch": "v_switch",
    "Purpose": "purpose",
    "Status": "status",
    "PortGroup": "port_group",
    "Active_vmnics": "active_vmnics",
    "Standby_vmnics": "standby_vmnics",
}


def _snake_case(name: str) -> str:
    """Convert a CamelCase/PascalCase identifier to lower_snake_case."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", str(name))
    s = s.replace(" ", "_")
    s = re.sub(r"_+", "_", s)
    return s.strip("_").lower()


def migrate_variable_names(rules: dict) -> dict:
    """Automated variable-name normalization pass over a rules dict (in place).

    Converts every legacy PascalCase/CamelCase variable key to its canonical
    lower_snake_case form, rewrites each ``<OldToken>`` reference in all pattern
    templates, syncs ``naming_patterns`` / ``token_order``, and drops duplicate
    legacy keys. Safe to run repeatedly (idempotent).
    """
    mapped = dict(LEGACY_VARIABLE_MIGRATION)
    for alias, canon in VARIABLE_ALIASES.items():
        mapped.setdefault(alias, canon)

    variables = rules.get("pattern_variables")
    if isinstance(variables, dict):
        for key in list(variables.keys()):
            canon = mapped.get(key) or _snake_case(key)
            if canon and canon != key:
                if canon not in variables:
                    variables[canon] = variables[key]
                mapped.setdefault(key, canon)

    def _rewrite(value):
        if not _is_str(value):
            return value
        out = value
        for legacy, canon in mapped.items():
            if legacy and canon and legacy != canon:
                out = out.replace(f"<{legacy}>", f"<{canon}>")
        return out

    for key in list(rules.keys()):
        rules[key] = _rewrite(rules[key])

    patterns = rules.get("naming_patterns")
    if isinstance(patterns, dict):
        for key, val in list(patterns.items()):
            patterns[key] = _rewrite(val)
        for legacy, canon in mapped.items():
            if legacy != canon and legacy in patterns and canon not in patterns:
                patterns[canon] = patterns.pop(legacy)

    if isinstance(variables, dict):
        for legacy, canon in mapped.items():
            if legacy != canon and legacy in variables:
                if canon in variables:
                    variables.pop(legacy, None)
                else:
                    variables[canon] = variables.pop(legacy)

    to = rules.get("token_order")
    if isinstance(to, dict):
        rules["token_order"] = {
            str(k): [mapped.get(x, x) for x in v]
            for k, v in to.items() if isinstance(v, (list, tuple))
        }
    return rules


def _migrate_and_persist():
    """Run the variable-name migration and persist normalized data back to disk."""
    if not os.path.exists(RULES_FILE):
        return
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    migrated = migrate_variable_names(dict(raw))
    if migrated != raw:
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(migrated, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

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
    raw = migrate_variable_names(raw)
    raw_patterns = raw.get("naming_patterns")
    if not isinstance(raw_patterns, dict):
        raw_patterns = {
            k: v for k, v in raw.items()
            if _is_str(v) and k not in ("pattern_variables", "naming_patterns")
        }
    patterns = {k: str(v) for k, v in raw_patterns.items()}
    patterns = _split_legacy_device_patterns(patterns)
    patterns = {k: (v.replace("<Local_Port_Short>", "<Local_Port>").replace("<Remote_Port_Short>", "<Remote_Port>") if isinstance(v, str) else v) for k, v in patterns.items()}
    # Normalize legacy variable spellings to canonical snake_case keys so historical
    # patterns (<Domain>, <Role>, <host_seq>) still resolve against the deduplicated set.
    patterns = {
        k: (v.replace("<Domain>", "<domain>").replace("<Role>", "<role>").replace("<host_seq>", "<seq>") if isinstance(v, str) else v)
        for k, v in patterns.items()
    }
    for key in LEGACY_PATTERN_KEYS:
        if key not in patterns:
            patterns[key] = DEFAULT_NAMING_PATTERNS.get(key, "")

    merged = dict(patterns)

    custom = raw.get("custom_patterns")
    if isinstance(custom, list):
        merged["custom_patterns"] = [{"label": str(c.get("label", "")), "key": str(c.get("key", "")), "pattern": str(c.get("pattern", ""))} for c in custom if isinstance(c, dict)]

    merged["device_presets"] = _normalize_presets(raw.get("device_presets"), DEVICE_PRESETS)
    merged["interface_presets"] = _normalize_presets(raw.get("interface_presets"), INTERFACE_PRESETS)
    merged["host_vm_presets"] = _normalize_presets(raw.get("host_vm_presets"), HOST_VM_PRESETS)
    merged["esxi_network_presets"] = _normalize_presets(raw.get("esxi_network_presets"), ESXI_NETWORK_PRESETS)
    merged["vlan_presets"] = get_vlan_presets(raw)

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
        # Consolidate legacy uppercase/duplicate keys into canonical snake_case names
        for alias, canon in VARIABLE_ALIASES.items():
            if alias in merged_vars and canon in merged_vars:
                merged_vars.pop(alias, None)
            elif alias in merged_vars and canon not in merged_vars:
                merged_vars[canon] = merged_vars.pop(alias)
        variables = merged_vars

    merged["naming_patterns"] = dict(patterns)
    merged["pattern_variables"] = variables
    if isinstance(raw.get("site_code_rules"), dict):
        merged["site_code_rules"] = raw["site_code_rules"]

    if isinstance(raw.get("vlan_description_mappings"), dict):
        merged["vlan_description_mappings"] = raw["vlan_description_mappings"]

    if isinstance(raw.get("ipam_role_mappings"), dict):
        merged["ipam_role_mappings"] = raw["ipam_role_mappings"]
    elif isinstance(raw.get("ipam_role_mappings"), list):
        merged["ipam_role_mappings"] = raw["ipam_role_mappings"]

    merged["csv_schemas"] = get_csv_schemas(raw)

    if isinstance(raw.get("token_order"), dict):
        normalized_to = {}
        for k, v in raw["token_order"].items():
            if isinstance(v, (list, tuple)):
                normalized_to[str(k)] = [str(x) for x in v]
            elif _is_str(v):
                normalized_to[str(k)] = [x for x in v.split(",") if x]
        if normalized_to:
            merged["token_order"] = normalized_to

    # Auto-migrate legacy 'esxi' scope → 'hypervisor' and seed the 9 canonical
    # hypervisor variables when they are absent.
    ensure_hypervisor_variables(merged)
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


def get_host_vm_presets(rules: dict) -> list:
    """Return the host/virtual-machine presets list from a rules dict."""
    raw = rules.get("host_vm_presets")
    return _normalize_presets(raw, HOST_VM_PRESETS)


def get_esxi_network_presets(rules: dict) -> list:
    """Return the ESXi network description presets list from a rules dict."""
    raw = rules.get("esxi_network_presets")
    return _normalize_presets(raw, ESXI_NETWORK_PRESETS)


def get_hardware_slot_mappings(rules: dict) -> dict:
    """Return the hardware-slot mapping dict from a rules dict.

    Keys are vmnic bare numbers (str) such as ``"0"`` or ``"1"``; values are
    slot labels like ``"PCIe1"``.  Loaded from the optional
    ``hardware_slot_mappings`` key in the YAML; falls back to an empty dict
    when absent so every caller sees a clean mapping with no surprises.
    """
    raw = rules.get("hardware_slot_mappings")
    if isinstance(raw, dict) and raw:
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def get_vlan_description_mappings(rules: dict) -> dict:
    """Return the VLAN Description mappings (Role -> Description) from a rules dict.

    Keys are returned with their original display casing for UI editing; callers
    performing lookups should compare case-insensitively (see ``resolve_vlan_description``).
    Falls back to ``DEFAULT_VLAN_DESCRIPTION_MAPPINGS`` when none are configured.
    """
    raw = rules.get("vlan_description_mappings")
    if isinstance(raw, dict) and raw:
        return {str(k): str(v) for k, v in raw.items()}
    return dict(DEFAULT_VLAN_DESCRIPTION_MAPPINGS)


def _normalize_vlan_group(group_data):
    """Normalize a VLAN preset group into the new dict schema.

    Accepts either:
      - A dict with keys ``vlan_name_pattern``, ``prefix_pattern``, ``items``
        (already normalized).
      - An old-style plain list of dicts (backward compat): extracts a default
        ``vlan_name_pattern`` of ``<role>``, a default ``prefix_pattern`` of
        ``<site> <role> -- VLAN <vid>``, and wraps each item as
        ``{"vid": ..., "role": ...}``.
    """
    if isinstance(group_data, dict) and "items" in group_data:
        items = group_data.get("items", [])
        vlan_name_pattern = str(group_data.get("vlan_name_pattern", "")).strip()
        prefix_pattern = str(group_data.get("prefix_pattern", "")).strip()
        if not isinstance(items, list):
            items = []
        normalized_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            vid = it.get("vid")
            role = str(it.get("role", "")).strip()
            normalized_items.append({"vid": vid, "role": role})
        return {
            "vlan_name_pattern": vlan_name_pattern,
            "prefix_pattern": prefix_pattern,
            "items": normalized_items,
        }

    # Backward-compat: old-style plain list of dicts.
    if isinstance(group_data, list):
        # Infer defaults from the first item, if any.
        def _infer(item):
            if not isinstance(item, dict):
                return
            for key in ("name_pattern", "vlan_name_pattern"):
                v = item.get(key)
                if v and str(v).strip():
                    return str(v).strip()
            return "<role>"

        def _infer_prefix(item):
            if not isinstance(item, dict):
                return
            for key in ("pattern_template", "prefix_pattern"):
                v = item.get(key)
                if v and str(v).strip():
                    return str(v).strip()
            return ""

        default_name_pattern = "<role>"
        default_prefix_pattern = ""
        sample = next((it for it in group_data if isinstance(it, dict)), None)
        if sample:
            default_name_pattern = _infer(sample)
            default_prefix_pattern = _infer_prefix(sample)
        normalized_items = []
        for it in group_data:
            if not isinstance(it, dict):
                continue
            vid = it.get("vid")
            role = str(it.get("role", "")).strip()
            if vid not in (None, "") or role:
                normalized_items.append({"vid": vid, "role": role})
        return {
            "vlan_name_pattern": default_name_pattern,
            "prefix_pattern": default_prefix_pattern,
            "items": normalized_items,
        }

    # Completely unexpected shape – return a blank normalized group.
    return {
        "vlan_name_pattern": "<role>",
        "prefix_pattern": "",
        "items": [],
    }


def _normalize_vlan_presets(raw_presets):
    """Normalize a list of VLAN preset items (vid/role/vlan_name/pattern_template).

    Returns a list of dicts with the four canonical keys, dropping malformed entries
    that lack a usable id or role.
    """
    if not isinstance(raw_presets, list):
        return list(DEFAULT_VLAN_PRESETS)
    normalized = []
    for p in raw_presets:
        if not isinstance(p, dict):
            continue
        item = {
            "vid": p.get("vid"),
            "role": str(p.get("role", "")).strip(),
            "vlan_name": str(p.get("vlan_name", "")).strip(),
            "name_pattern": str(p.get("name_pattern", "<role>")).strip() or "<role>",
            "pattern_template": str(p.get("pattern_template", "")).strip(),
        }
        if item["vid"] not in (None, "") or item["role"]:
            normalized.append(item)
    return normalized


def get_vlan_presets(rules: dict) -> dict:
    """Return the VLAN allocation presets dict (defaults if missing or malformed).

    Preset groups are stored under ``vlan_presets`` as a mapping of
    ``group_name -> {vlan_name_pattern, prefix_pattern, items}`` where each item is
    ``{vid, role}``. Every group is normalized through ``_normalize_vlan_group`` so
    user edits stay clean. Old-style plain-list groups are normalized on load.
    """
    import copy
    raw = rules.get("vlan_presets")
    if not isinstance(raw, dict) or not raw:
        return copy.deepcopy(dict(DEFAULT_VLAN_PRESETS))
    normalized = {}
    for group_name, group_data in raw.items():
        normalized[str(group_name)] = _normalize_vlan_group(group_data)
    if not normalized:
        return copy.deepcopy(dict(DEFAULT_VLAN_PRESETS))
    if "Custom / Empty Preset" not in normalized:
        normalized["Custom / Empty Preset"] = {
            "vlan_name_pattern": "",
            "prefix_pattern": "",
            "items": []
        }
    return normalized


# ── Site Code Calculation Rules (data-driven from YAML, no hardcoded logic) ──

DEFAULT_SITE_CODE_RULES = {
    "separators": [" ", "-", "_"],
    "multi_word_chars_per_word": [2, 2],
    "single_word_max_chars": 4,
    "fallback": "SITE",
    "exact_mappings": {},
}

def get_site_code_rules(rules):
    raw = rules.get("site_code_rules")
    if isinstance(raw, dict) and raw:
        merged = dict(DEFAULT_SITE_CODE_RULES)
        for k, v in raw.items():
            merged[k] = v
        return merged
    return dict(DEFAULT_SITE_CODE_RULES)

def compute_suggested_site_code(location_name: str, rules=None) -> str:
    rules = rules if isinstance(rules, dict) else {}
    sr = get_site_code_rules(rules)
    cleaned = re.sub(r"[^a-zA-Z\s\-_]", "", (location_name or "")).strip()
    if not cleaned:
        return str(sr.get("fallback", "SITE"))

    # Normalise to single-space lowercase for exact matching.
    key = re.sub(r"[\s\-_]+", " ", cleaned).strip().lower()
    exact = sr.get("exact_mappings")
    if isinstance(exact, dict):
        for pat, code in exact.items():
            pat_str = str(pat).strip().lower()
            if key == pat_str or key.startswith(pat_str + " "):
                return str(code).upper()

    words = [w for w in re.split(r"[\s\-_]+", cleaned) if w]
    if len(words) >= 2:
        per = sr.get("multi_word_chars_per_word", [2, 2])
        try:
            n1 = int(str(per[0]).strip()) if per else 2
        except (ValueError, IndexError):
            n1 = 2
        try:
            n2 = int(str(per[1]).strip()) if len(per) > 1 else 2
        except (ValueError, IndexError):
            n2 = 2
        return (words[0][:n1] + words[1][:n2]).upper()
    elif len(words) == 1:
        w = words[0]
        try:
            maxc = int(sr.get("single_word_max_chars", 4))
        except (ValueError, TypeError):
            maxc = 4
        return w[:maxc].upper()
    return str(sr.get("fallback", "SITE"))


DEFAULT_PRESET_DEFS = {
    "device": DEVICE_PRESETS,
    "interface": INTERFACE_PRESETS,
    "host_vm": HOST_VM_PRESETS,
    "esxi_network": ESXI_NETWORK_PRESETS,
}

DEFAULT_PRESET_KEY_FIELD = {
    "device": "device_presets",
    "interface": "interface_presets",
    "host_vm": "host_vm_presets",
    "esxi_network": "esxi_network_presets",
}


def default_presets_for(kind: str) -> list:
    """Return the factory-default preset list for a preset *kind* (deep copy)."""
    import copy
    defaults = DEFAULT_PRESET_DEFS.get(kind, [])
    return copy.deepcopy(list(defaults))


def make_preset_key(code: str, prefix: str = "branch") -> str:
    """Generate a safe, collision-free pattern key from a preset code."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", code.strip()).strip("_").lower()
    return f"{prefix}_{slug}" if slug else f"{prefix}_custom"


def load_naming_rules() -> Dict[str, str]:
    import streamlit as st
    if hasattr(st, "session_state") and "_cached_naming_rules" in st.session_state:
        cached = st.session_state["_cached_naming_rules"]
        if isinstance(cached, dict) and cached:
            return cached

    _migrate_and_persist()
    rules = _load_rules_dict_from_file()
    normalized = _normalize_rules(rules if rules else DEFAULT_NAMING_PATTERNS.copy())

    # Auto-persist scope migrations so legacy 'esxi' scopes and missing
    # hypervisor defaults land on disk on first load.
    if rules and _needs_hypervisor_migration(rules):
        try:
            save_naming_rules(normalized, source="Hypervisor scope migration")
        except Exception:
            pass

    if hasattr(st, "session_state"):
        st.session_state["_cached_naming_rules"] = normalized
    return normalized


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


def _delta_item_key(item) -> object:
    """Return the stable identity key used to match items across two preset lists.

    Falls back to the item's known fields, then to a JSON dump, and finally to a
    positional sentinel so non-preset lists are compared element-by-element.
    """
    if isinstance(item, dict):
        for k in ("code", "pattern_key", "key", "vid", "role"):
            v = item.get(k)
            if v not in (None, ""):
                return ("__key__", str(v))
        try:
            import json
            return ("__json__", json.dumps(item, sort_keys=True))
        except (TypeError, ValueError):
            pass
    return ("__pos__", str(item))


def compute_delta(old: dict, new: dict) -> dict:
    """Return a granular, path-based set of changes between two rules dicts.

    Recursively traverses nested dicts and list items instead of collapsing them to an
    ``"X item(s) -> X item(s)"`` summary. List entries are matched by their stable
    ``code`` / ``pattern_key`` / ``key`` so reordered or renamed presets still diff
    field-by-field. Every returned key is a ``Parent > Child > ...`` path whose value is
    ``{"old": ..., "new": ...}``, suitable for display as ``Path / Field |
    Previous Value | New Value``.
    """
    if not old:
        result = {}
        def _seed(v, path=""):
            if isinstance(v, dict):
                for k, x in v.items():
                    _seed(x, f"{path} > {k}" if path else k)
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    _seed(x, f"{path}[{i}]" if path else f"[{i}]")
            else:
                result[path] = {"old": None, "new": v}
        _seed(new or {}, "")
        return result

    delta = {}

    def _walk(old_v, new_v, path):
        if isinstance(old_v, dict) and isinstance(new_v, dict):
            for k in list(dict.fromkeys(list(old_v.keys()) + list(new_v.keys()))):
                child = f"{path} > {k}" if path else k
                _walk(old_v.get(k), new_v.get(k), child)
        elif isinstance(old_v, list) and isinstance(new_v, list):
            old_map = {_delta_item_key(item): item for item in old_v}
            new_map = {_delta_item_key(item): item for item in new_v}
            for key in list(dict.fromkeys(list(old_map.keys()) + list(new_map.keys()))):
                child = f"{path} > {key[1]}" if path else key[1]
                _walk(old_map.get(key), new_map.get(key), child)
        else:
            if old_v != new_v:
                delta[path] = {"old": old_v, "new": new_v}

    _walk(old, new, "")
    return delta


def save_naming_rules(rules: Dict[str, str], source: str = "Manual Edit"):
    """Save naming rules and add to history."""
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)

    old_raw = {}
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                old_raw = json.load(f)
        except Exception:
            old_raw = {}

    stored = _normalize_rules(dict(rules))
    old = _normalize_rules(dict(old_raw)) if isinstance(old_raw, dict) and old_raw else {}
    delta = compute_delta(old, stored)
    add_to_history(delta, source)
    migrate_variable_names(stored)
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(stored, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

    import streamlit as st
    if hasattr(st, "session_state"):
        st.session_state["_cached_naming_rules"] = stored

DEFAULT_RULES = _normalize_rules(DEFAULT_NAMING_PATTERNS.copy())

def add_to_history(delta: Dict[str, str], source: str = "Manual Edit"):
    """Add a delta entry to the change history.

    ``delta`` is the result of ``compute_delta``: ``{field: {"old": ..., "new": ...}}``.
    The full snapshot is also stored for restoration via ``restore_from_history``.
    """
    history = load_history()
    snapshot = {}
    try:
        if os.path.exists(RULES_FILE):
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
    except Exception:
        pass

    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "delta": delta,
        "rules": snapshot,
        "change_count": len(delta),
        "changed_keys": list(delta.keys()),
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
