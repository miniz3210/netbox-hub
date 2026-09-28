import ipaddress
import re
from typing import List, Dict, Any, Optional
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


def normalize_role_name(role: str, role_mappings: dict | None = None) -> str:
    """Normalize a raw role string to its canonical, standard-cased form.

    1. Strips leading/trailing whitespace.
    2. Performs a case-insensitive lookup against ``role_mappings`` (falling back
       to ``DEFAULT_IPAM_ROLE_MAPPINGS`` when ``None``), e.g. "guest" ->
       "Guests", "corp wifi" -> "Corporate WiFi".
    3. Otherwise the stripped role is returned as-is.
    """
    from config.naming_rules import get_ipam_role_mappings
    role = str(role or "").strip()
    if not role:
        return ""

    mappings = get_ipam_role_mappings({}) if role_mappings is None else role_mappings
    canonical = mappings.get(role.lower(), role)
    return canonical


def resolve_vlan_description(vid, role: str, vlan_presets=None, vlan_desc_mappings=None, role_mappings: dict | None = None) -> str:
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
    norm_role = normalize_role_name(role, role_mappings)
    norm_key = norm_role.lower()

    if isinstance(vlan_desc_mappings, dict):
        for k, v in vlan_desc_mappings.items():
            if str(k).strip().lower() == norm_key:
                mapped = str(v).strip()
                if mapped:
                    return mapped

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

def slugify(text: str) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'[^a-z0-9-]', '', text)
    return text.strip('-')

def format_branch_display(name: str) -> str:
    if not name:
        return ""
    clean = name.strip()
    if clean.isupper() and len(clean) <= 4:
        return clean
    return " ".join([word.capitalize() for word in clean.split()])

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
        out = out.replace("<vlan_name>", vlan_name)
        return out.strip()

    return f"{site_display} {template} {vid}".strip()

def sanitize_cidr(cidr_raw: str) -> str:
    if not cidr_raw:
        return ""
    s = str(cidr_raw).strip()
    match = re.match(r'^((?:\d{1,3}\.){3}\d{1,3})\.(\d{1,2})$', s)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return s

def calculate_ip_range_str(net: ipaddress.IPv4Network) -> str:
    if net.num_addresses <= 2:
        return f"{net.network_address} - {net.broadcast_address}"
    first_host = net.network_address + 1
    last_host = net.broadcast_address - 1
    return f"{first_host} - {last_host}"

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

# ── BULK NETBOX CSV GENERATORS ──────────────────────────────────────────

def generate_netbox_site_csv(site_name: str) -> str:
    clean = format_branch_display(site_name)
    slug = slugify(clean)
    return f"name,slug,status\n\"{clean}\",\"{slug}\",active"

def generate_netbox_vlan_group_csv(site_name: str, scope_id: str) -> str:
    clean = format_branch_display(site_name)
    slug = slugify(f"{clean} VLAN Group")
    scope_val = scope_id if scope_id else "<SCOPE_ID>"
    return f"name,slug,scope_type,scope_id\n\"{clean} VLAN Group\",\"{slug}\",\"dcim.site\",{scope_val}"

def generate_netbox_vlans_csv(site_name: str, rows: List[Dict[str, Any]]) -> str:
    clean = format_branch_display(site_name)
    lines = ["vid,name,status,site,group,description,role"]
    for r in rows:
        vid = r.get("VLAN ID")
        subnet = sanitize_cidr(str(r.get("Subnet (CIDR)") or "").strip())
        if not vid or not subnet or "/" not in subnet:
            continue
        vname = r.get("VLAN Name") or r.get("Role") or f"VLAN_{vid}"
        desc = str(r.get("VLAN Description") or "").strip()
        role_val = r.get("Role") or vname
        lines.append(f"{vid},\"{vname}\",active,\"{clean}\",\"{clean} VLAN Group\",\"{desc}\",\"{role_val}\"")
    return "\n".join(lines)

def generate_netbox_prefixes_csv(
    site_name: str,
    scope_id: str,
    supernet_str: str,
    rows: List[Dict[str, Any]],
    include_site_subnet: bool = True,
) -> str:
    clean = format_branch_display(site_name)
    clean_supernet = sanitize_cidr(supernet_str)
    scope_val = scope_id if scope_id else "<SCOPE_ID>"
    lines = ["prefix,status,scope_type,scope_id,vlan_group,vlan,role,description"]
    
    # 1. Top-Level Supernet Container
    if include_site_subnet and clean_supernet and "/" in clean_supernet:
        try:
            sup_net = ipaddress.ip_network(clean_supernet, strict=False)
            bound_str = calculate_subnet_boundary_str(sup_net)
            supernet_desc = f"Site Subnet - {bound_str}"
        except ValueError:
            supernet_desc = f"Site Subnet - {clean_supernet}"
            
        lines.append(f"\"{clean_supernet}\",active,\"dcim.site\",{scope_val},\"{clean} VLAN Group\",,,,\"{supernet_desc}\"")

    # 2. Member Subnets
    for r in rows:
        subnet = sanitize_cidr(str(r.get("Subnet (CIDR)") or "").strip())
        vid = r.get("VLAN ID")
        if not subnet or "/" not in subnet or not vid or subnet == clean_supernet:
            continue
        role_val = r.get("Role") or r.get("VLAN Name") or ""
        desc = str(r.get("Prefix Description") or "").strip()
        if not desc:
            desc = f"{clean} {role_val} -- VLAN {vid}"
        lines.append(f"\"{subnet}\",active,\"dcim.site\",{scope_val},\"{clean} VLAN Group\",{vid},\"{role_val}\",\"{desc}\"")
        
    return "\n".join(lines)
