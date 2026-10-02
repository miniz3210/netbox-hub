import ipaddress
import re
from typing import List, Dict, Any, Optional

import streamlit as st

_SLUGIFY_WHITESPACE = re.compile(r'[\s_]+')
_SLUGIFY_NON_ALPHANUM = re.compile(r'[^a-z0-9-]')
_CIDR_DOT_NOTATION = re.compile(r'^((?:\d{1,3}\.){3}\d{1,3})\.(\d{1,2})$')

from core.db_manager import lookup_vlan_description_from_db

ROLE_TO_DESC_MAP = {
    "corporate wifi": "VIN_Corp",
    "workstations": "Wired Workstations",
    "management": "Management",
    "printers": "Printers",
    "audio visual": "AV equipment",
    "audio": "AV equipment",
    "guests": "VIN_Guest",
    "mobiles": "VIN_Mobi",
    "routing": "Routing interface VLANs",
    "ot": "OT",
    "iot": "IoT/Security",
    "oob / ipmi / ilo": "OOB-Mgmt",
    "in-band management": "InBand-Mgmt",
    "vmotion": "vMotion",
    "storage / iscsi-a": "Storage-A",
    "storage / iscsi-b": "Storage-B",
    "production app / web": "Prod-App",
    "production database": "Prod-DB",
    "dmz / perimeter": "DMZ",
    "core infrastructure": "Core-Infra",
    "backup / recovery": "Backup",
}

# Canonical role-name aliases removed — now driven by DEFAULT_IPAM_ROLE_MAPPINGS
# in config/naming_rules.py, loaded via get_ipam_role_mappings().


def normalize_role_name(role: str, role_rules: list | None = None) -> str:
    """Normalize a raw role string to its canonical, standard-cased form.

    Returns "" for empty input. Iterates through role_rules (falling back to
    get_ipam_role_mappings), applying each enabled regex pattern via re.sub.
    """
    role = str(role or "").strip()
    if not role:
        return ""

    if role_rules is None:
        from config.naming_rules import get_ipam_role_mappings
        role_rules = get_ipam_role_mappings({})

    for rule in role_rules:
        if not isinstance(rule, dict):
            continue
        if not rule.get("enabled", True):
            continue
        pattern = rule.get("pattern", "")
        replacement = rule.get("replacement", "")
        if not pattern:
            continue
        try:
            replaced = re.sub(pattern, replacement, role)
        except re.error:
            continue
        if replaced != role:
            return replaced

    return role


def resolve_vlan_name(site: str, vid: int | str, role: str, name_pattern: str | None = None) -> str:
    """Render a VLAN name from a dynamic name_pattern template.

    Supports the tokens ``<site>``, ``<vid>``, and ``<role>``. Falls back to
    the literal ``role`` string when the pattern is empty or yields no tokens.
    """
    site = str(site or "").strip()
    vid = str(vid if vid not in (None, "") else "").strip()
    role = str(role or "").strip()
    pattern = (name_pattern or "<role>").strip()

    if "<" not in pattern or ">" not in pattern:
        return role or "VLAN"

    rendered = pattern
    rendered = rendered.replace("<site>", site)
    rendered = rendered.replace("<vid>", vid)
    rendered = rendered.replace("<role>", role)
    rendered = rendered.replace("<name>", role)

    result = rendered.strip()
    return result if result else role


def resolve_vlan_description(vid, role: str, vlan_presets=None, vlan_desc_mappings=None, role_rules: list | None = None) -> str:
    """Resolve the NetBox 'VLAN Description' tag for a (VID, Role) pair.

    Resolution is driven by the dynamic ``vlan_description_mappings`` configured in
    the Standards Tab (Role -> Description). There are no hardcoded lookup
    dictionaries in this function.

    Resolution order:
      1. Normalize the role name (see ``normalize_role_name``), so aliases like
         "guest"/"guests" become "Guests" and pick up their standard casing.
      2. Check ``vlan_desc_mappings`` (Role -> Description) case-insensitively.
         If a match exists and the mapped value is non-blank, return it
         (e.g. "Guests" -> "VIN_Guest").
      3. Fallback to the normalized ``Role`` name itself (e.g. "CustomLab").
         Returns "" only when no role is supplied.

    The ``vlan_presets`` argument is retained for backward compatibility but is
    no longer consulted for description resolution — that responsibility now lives
    entirely in ``vlan_desc_mappings``.
    """
    norm_role = normalize_role_name(role, role_rules)
    norm_key = norm_role.lower()

    if isinstance(vlan_desc_mappings, dict):
        for k, v in vlan_desc_mappings.items():
            if str(k).strip().lower() == norm_key:
                mapped = str(v).strip()
                if mapped:
                    # Dynamically substitute <role> and <name> tokens in the mapped value
                    rendered = mapped.replace("<role>", norm_role).replace("<name>", norm_role)
                    return rendered.strip() or mapped

    return norm_role

def lookup_role_description(role_str: str) -> str:
    """1. Queries the SQLite DB for description. 2. Falls back to dictionary."""
    if not role_str:
        return ""
    clean = role_str.strip()

    # 1. Database Lookup
    db_desc = lookup_vlan_description_from_db(clean)
    if db_desc:
        return db_desc

    # 2. Dictionary Fallback
    lower_role = clean.lower()
    if lower_role in ROLE_TO_DESC_MAP:
        return ROLE_TO_DESC_MAP[lower_role]
    for key, desc in ROLE_TO_DESC_MAP.items():
        if key in lower_role:
            return desc
    return clean

@st.cache_data(ttl=300, show_spinner=False)
def slugify(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = _SLUGIFY_WHITESPACE.sub('-', text)
    text = _SLUGIFY_NON_ALPHANUM.sub('', text)
    return text.strip('-')

@st.cache_data(ttl=300, show_spinner=False)
def format_branch_display(name: str) -> str:
    if not name:
        return ""
    clean = name.strip()
    if clean.isupper() and len(clean) <= 4:
        return clean
    return " ".join([word.capitalize() for word in clean.split()])

@st.cache_data(ttl=300, show_spinner=False)
def resolve_pattern_template(template: str, site_display: str, vid, role: str, vlan_name: str) -> str:
    """Resolve a naming pattern_template into a concrete VLAN/Prefix description.

    Supported ``<>`` tokens: ``<site>`` / ``<branch>``, ``<vid>``, ``<role>``,
    ``<vlan_name>``. If the template contains no ``<>`` tokens (legacy form like
    ``Management Testing -- VLAN``), it is resolved dynamically as
    ``{site_display} {template} {vid}``.
    """
    template = (template or "").strip()
    site_display = str(site_display or "Site").strip()
    role = str(role or "").strip()
    vlan_name = str(vlan_name or "").strip()

    if not template:
        return f"{site_display} {role or 'Data'} -- VLAN {vid}" if vid else f"{site_display} {role or 'Data'}"

    if "<" in template and ">" in template:
        out = template
        out = out.replace("<site>", site_display).replace("<branch>", site_display)
        out = out.replace("<vid>", str(vid) if vid not in (None, "") else "")
        out = out.replace("<role>", role)
        out = out.replace("<name>", role)
        out = out.replace("<vlan_name>", vlan_name)
        return out.strip()

    return f"{site_display} {template} {vid}".strip()

@st.cache_data(ttl=300, show_spinner=False)
def sanitize_cidr(cidr_raw: str) -> str:
    if not cidr_raw:
        return ""
    s = str(cidr_raw).strip()
    match = _CIDR_DOT_NOTATION.match(s)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return s

@st.cache_data(ttl=300, show_spinner=False)
def calculate_ip_range_str(net: ipaddress.IPv4Network) -> str:
    if net.num_addresses <= 2:
        return f"{net.network_address} - {net.broadcast_address}"
    first_host = net.network_address + 1
    last_host = net.broadcast_address - 1
    return f"{first_host} - {last_host}"

@st.cache_data(ttl=300, show_spinner=False)
def calculate_subnet_boundary_str(net: ipaddress.IPv4Network) -> str:
    return f"{net.network_address} - {net.broadcast_address}"

def compute_chained_rows(supernet_str: str, working_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean_supernet = sanitize_cidr(supernet_str)
    current_base = None
    if clean_supernet and "/" in clean_supernet:
        try:
            sup_net = ipaddress.ip_network(clean_supernet, strict=False)
            current_base = sup_net.network_address
        except ValueError:
            pass

    out = []
    for r in working_rows:
        row = dict(r)
        user_subnet = sanitize_cidr(str(row.get("Subnet (CIDR)") or "").strip())

        suggested_ip_only = ""
        if current_base is not None:
            suggested_ip_only = str(current_base)

        row["Suggest Subnet"] = suggested_ip_only

        active_sub = user_subnet if (user_subnet and "/" in user_subnet) else (f"{suggested_ip_only}/24" if suggested_ip_only else "")
        if active_sub and "/" in active_sub:
            try:
                active_net = ipaddress.ip_network(active_sub, strict=False)
                current_base = active_net.broadcast_address + 1
            except ValueError:
                pass

        out.append(row)
    return out

def evaluate_subnet_row(
    subnet_str: str, 
    vid: Optional[int], 
    role: str, 
    site_name: str, 
    supernet_str: str, 
    existing_prefixes: List[str],
    vlan_name: str = "",
    pattern_template: str = "",
) -> Dict[str, str]:
    clean_sub = sanitize_cidr(subnet_str)
    clean_supernet = sanitize_cidr(supernet_str)

    branch = format_branch_display(site_name)
    clean_role = role.strip() if role else "Data"
    desc = resolve_pattern_template(pattern_template, branch, vid, clean_role, vlan_name) or (
        f"{branch or 'Site'} {clean_role} -- VLAN {vid}" if vid else f"{branch or 'Site'} {clean_role}"
    )

    if not clean_sub or "/" not in clean_sub:
        return {"usable_range": "-", "status": "⚪ Unassigned", "desc": desc}

    try:
        net = ipaddress.ip_network(clean_sub, strict=False)
    except ValueError:
        return {"usable_range": "Invalid CIDR", "status": "❌ Invalid CIDR", "desc": desc}

    usable_range = calculate_ip_range_str(net)

    for exist_str in existing_prefixes:
        exist_clean = sanitize_cidr(exist_str)
        if exist_clean == clean_sub:
            return {
                "usable_range": usable_range,
                "status": "🔴 IN-USE (NetBox DB)",
                "desc": desc
            }

    for exist_str in existing_prefixes:
        exist_clean = sanitize_cidr(exist_str)
        if exist_clean == clean_supernet:
            continue
        try:
            exist_net = ipaddress.ip_network(exist_clean, strict=False)
            if net.overlaps(exist_net):
                return {
                    "usable_range": usable_range,
                    "status": f"🚨 IN-USE (Overlaps {exist_clean})",
                    "desc": desc
                }
        except ValueError:
            pass

    if clean_supernet and "/" in clean_supernet:
        try:
            sup_net = ipaddress.ip_network(clean_supernet, strict=False)
            if not net.subnet_of(sup_net) and net != sup_net:
                return {
                    "usable_range": usable_range,
                    "status": "⚠️ Outside Supernet",
                    "desc": desc
                }
        except ValueError:
            pass

    return {"usable_range": usable_range, "status": "🟢 Available", "desc": desc}

def calculate_remaining_subnets(supernet_str: str, allocated_subnets: List[str]) -> Dict[str, int]:
    result = {"/24": 0, "/25": 0, "/26": 0, "/27": 0, "/28": 0}
    clean_supernet = sanitize_cidr(supernet_str)
    if not clean_supernet or "/" not in clean_supernet:
        return result

    try:
        sup_net = ipaddress.ip_network(clean_supernet, strict=False)
    except ValueError:
        return result

    valid_allocations = []
    for s in allocated_subnets:
        clean_s = sanitize_cidr(s)
        if clean_s and "/" in clean_s and clean_s != clean_supernet:
            try:
                sub = ipaddress.ip_network(clean_s, strict=False)
                if sub.subnet_of(sup_net):
                    valid_allocations.append(sub)
            except ValueError:
                pass

    total_ips = sup_net.num_addresses
    used_ips = sum(n.num_addresses for n in valid_allocations)
    free_ips = max(0, total_ips - used_ips)

    for prefix in [24, 25, 26, 27, 28]:
        size = 2 ** (32 - prefix)
        result[f"/{prefix}"] = free_ips // size

    return result

def get_subnet_availability_analysis(
    supernet_str: str, 
    known_prefixes: List[str], 
    requested_prefix_len: int = 24, 
    max_candidates: int = 16
) -> str:
    """
    Computes exact mathematical CIDR availability and overlap status for subnets inside a supernet.
    Returns a clear pre-calculated breakdown for AI context and prompt grounding.
    """
    clean_sup = sanitize_cidr(supernet_str)
    if not clean_sup or "/" not in clean_sup:
        return ""
    
    try:
        sup_net = ipaddress.ip_network(clean_sup, strict=False)
    except ValueError:
        return ""

    if requested_prefix_len <= sup_net.prefixlen:
        return f"Requested /{requested_prefix_len} block is equal to or larger than parent supernet {clean_sup}."

    known_nets = []
    for p in known_prefixes:
        clean_p = sanitize_cidr(p)
        if clean_p and "/" in clean_p and clean_p != clean_sup:
            try:
                known_nets.append(ipaddress.ip_network(clean_p, strict=False))
            except ValueError:
                pass

    try:
        candidate_subnets = list(sup_net.subnets(new_prefix=requested_prefix_len))
    except Exception:
        return ""

    analysis_lines = [
        f"PRE-CALCULATED SUBNET AVAILABILITY ANALYSIS for target /{requested_prefix_len} inside {clean_sup}:"
    ]
    
    first_available = None
    count = 0

    for cand in candidate_subnets:
        if count >= max_candidates:
            analysis_lines.append(f"... ({len(candidate_subnets)} total /{requested_prefix_len} subnets exist in {clean_sup})")
            break
        
        overlapping = [k for k in known_nets if cand.overlaps(k)]
        if overlapping:
            overlap_strs = ", ".join(str(o) for o in overlapping)
            analysis_lines.append(f"  ❌ {cand}: OCCUPIED / OVERLAPS ({overlap_strs})")
        else:
            if first_available is None:
                first_available = cand
                analysis_lines.append(f"  ✅ {cand}: NEXT AVAILABLE FREE SUBNET")
            else:
                analysis_lines.append(f"  ✅ {cand}: AVAILABLE")
        count += 1

    if first_available:
        analysis_lines.insert(1, f"-> NEXT AVAILABLE /{requested_prefix_len} SUBNET: {first_available}")
    else:
        analysis_lines.insert(1, f"-> NO AVAILABLE /{requested_prefix_len} SUBNETS REMAINING IN {clean_sup}")

    return "\n".join(analysis_lines)

# ── SCHEMA-DRIVEN BULK NETBOX CSV GENERATORS ─────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def render_csv_cell(template: str, context: Dict[str, Any]) -> str:
    """Render a single CSV cell template using universal context substitution."""
    if not template:
        return ""
    result = template
    for key, val in context.items():
        val_str = str(val).replace('"', '""') if val is not None else ""
        result = result.replace(f"<{key}>", val_str)
    return result

def _get_global_ipam_context(site_name: str, scope_id: str, supernet_str: str = "") -> Dict[str, Any]:
    clean_site = format_branch_display(site_name)
    clean_slug = slugify(clean_site)
    clean_supernet = sanitize_cidr(supernet_str)
    scope_val = str(scope_id).strip() if scope_id else "<SCOPE_ID>"
    vlan_group = f"{clean_site} VLAN Group"
    vlan_group_slug = slugify(vlan_group)

    supernet_desc = f"Site Subnet - {clean_supernet}"
    if clean_supernet and "/" in clean_supernet:
        try:
            sup_net = ipaddress.ip_network(clean_supernet, strict=False)
            supernet_desc = f"Site Subnet - {calculate_subnet_boundary_str(sup_net)}"
        except ValueError:
            pass

    return {
        "site": clean_site,
        "site_slug": clean_slug,
        "scope_id": scope_val,
        "scope_type": "dcim.site",
        "site_supernet": clean_supernet,
        "supernet_desc": supernet_desc,
        "vlan_group": vlan_group,
        "vlan_group_slug": vlan_group_slug,
        "status": "active"
    }

def generate_netbox_site_csv(site_name: str, rules: Optional[Dict[str, Any]] = None) -> str:
    from config.naming_rules import get_csv_schemas, load_naming_rules
    if rules is None:
        rules = load_naming_rules()
    schemas = get_csv_schemas(rules)
    schema = schemas.get("import_site", {})
    headers = schema.get("headers", ["name", "slug", "status"])
    row_tpl = schema.get("row_template", ["\"<site>\"", "\"<site_slug>\"", "active"])
    
    ctx = _get_global_ipam_context(site_name, "")
    rendered_row = [render_csv_cell(cell, ctx) for cell in row_tpl]
    return f"{','.join(headers)}\n{','.join(rendered_row)}"

def generate_netbox_vlan_group_csv(site_name: str, scope_id: str, rules: Optional[Dict[str, Any]] = None) -> str:
    from config.naming_rules import get_csv_schemas, load_naming_rules
    if rules is None:
        rules = load_naming_rules()
    schemas = get_csv_schemas(rules)
    schema = schemas.get("import_vlan_group", {})
    headers = schema.get("headers", ["name", "slug", "scope_type", "scope_id"])
    row_tpl = schema.get("row_template", ["\"<vlan_group>\"", "\"<vlan_group_slug>\"", "\"<scope_type>\"", "<scope_id>"])

    ctx = _get_global_ipam_context(site_name, scope_id)
    rendered_row = [render_csv_cell(cell, ctx) for cell in row_tpl]
    return f"{','.join(headers)}\n{','.join(rendered_row)}"

def generate_netbox_vlans_csv(site_name: str, computed_rows: List[Dict[str, Any]]) -> str:
    from config.naming_rules import load_naming_rules, get_csv_schemas
    rules = load_naming_rules()
    schemas = get_csv_schemas(rules)
    vlan_schema = schemas.get("import_vlans", {})
    headers_list = vlan_schema.get("headers", ["vid", "name", "status", "site", "group", "description", "role"])
    headers = ",".join(headers_list)
    row_tpl = vlan_schema.get("row_template", ["<vid>", '"<vlan_name>"', "active", '"<site>"', '"<vlan_group>"', '"<vlan_desc>"', '"<role>"'])
    
    lines = [headers]
    site_display = format_branch_display(site_name) or "Site"
    group_name = f"{site_display} VLAN Group"
    
    valid_rows = [r for r in (computed_rows or []) if r.get("VLAN ID") is not None]
    
    if valid_rows:
        for r in valid_rows:
            vid_val = str(r.get("VLAN ID", ""))
            vname_val = str(r.get("VLAN Name", "") or r.get("Role", ""))
            role_val = str(r.get("Role", ""))
            vdesc_val = str(r.get("VLAN Description", "") or r.get("Prefix Description", ""))
            
            row_cells = []
            for token_cell in row_tpl:
                cell = str(token_cell)
                cell = cell.replace("<vid>", vid_val)
                cell = cell.replace("<vlan_name>", vname_val)
                cell = cell.replace("<site>", site_display)
                cell = cell.replace("<vlan_group>", group_name)
                cell = cell.replace("<vlan_desc>", vdesc_val)
                cell = cell.replace("<role>", role_val)
                row_cells.append(cell)
            lines.append(",".join(row_cells))
    else:
        # Fallback default preview row matching Site and VLAN Group behavior
        row_cells = []
        for token_cell in row_tpl:
            cell = str(token_cell)
            cell = cell.replace("<vid>", "300")
            cell = cell.replace("<vlan_name>", "Corporate WiFi")
            cell = cell.replace("<site>", site_display)
            cell = cell.replace("<vlan_group>", group_name)
            cell = cell.replace("<vlan_desc>", "VIN_Corp")
            cell = cell.replace("<role>", "Corporate WiFi")
            row_cells.append(cell)
        lines.append(",".join(row_cells))
        
    return "\n".join(lines)

def generate_netbox_prefixes_csv(
    site_name: str,
    scope_id: str,
    supernet_str: str,
    rows: List[Dict[str, Any]],
    include_site_subnet: bool = True,
    rules: Optional[Dict[str, Any]] = None,
) -> str:
    from config.naming_rules import get_csv_schemas, load_naming_rules
    if rules is None:
        rules = load_naming_rules()
    schemas = get_csv_schemas(rules)
    schema = schemas.get("import_prefixes", {})
    headers = schema.get("headers", ["prefix", "status", "scope_type", "scope_id", "vlan_group", "vlan", "role", "description"])
    row_tpl = schema.get("row_template", ["\"<prefix>\"", "active", "\"<scope_type>\"", "<scope_id>", "\"<vlan_group>\"", "<vid>", "\"<role>\"", "\"<prefix_desc>\""])
    sup_tpl = schema.get("supernet_template", ["\"<site_supernet>\"", "active", "\"<scope_type>\"", "<scope_id>", "\"<vlan_group>\"", "", "", "\"<supernet_desc>\""])

    if not rows and not include_site_subnet:
        return "\n".join(headers)
    if not rows:
        # Still need to return headers even if no rows but include_site_subnet is True
        pass

    global_ctx = _get_global_ipam_context(site_name, scope_id, supernet_str)
    lines = [",".join(headers)]

    # 1. Top-Level Supernet Container
    clean_supernet = global_ctx["site_supernet"]
    if include_site_subnet and clean_supernet and "/" in clean_supernet:
        rendered_sup = [render_csv_cell(cell, global_ctx) for cell in sup_tpl]
        lines.append(",".join(rendered_sup))

    # 2. Member Subnets
    for r in rows:
        subnet = sanitize_cidr(str(r.get("Subnet (CIDR)") or "").strip())
        vid = r.get("VLAN ID")
        if not subnet or "/" not in subnet or not vid or subnet == clean_supernet:
            continue
        role_val = r.get("Role") or r.get("VLAN Name") or ""
        desc = str(r.get("Prefix Description") or "").strip()
        if not desc:
            desc = f"{global_ctx['site']} {role_val} -- VLAN {vid}"

        row_ctx = {
            **global_ctx,
            "vid": vid,
            "vlan_id": vid,
            "role": role_val,
            "vlan_name": r.get("VLAN Name") or role_val,
            "prefix": subnet,
            "subnet": subnet,
            "prefix_desc": desc
        }
        rendered_cells = [render_csv_cell(cell, row_ctx) for cell in row_tpl]
        lines.append(",".join(rendered_cells))

    return "\n".join(lines)
