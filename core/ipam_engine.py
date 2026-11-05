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

# ── BUG-6: Deep IPAM Overlap & Hierarchy-Aware Allocation ──────────────────

_CONTAINMENT_OVERLAP = "CONTAINED_BY_SUPERNET"
_COLLISION_OVERLAP = "COLLISION_ERROR"


def _get_trailing_digits(name: str) -> str:
    """Return the trailing numeric suffix (e.g. '01' from 'fwAZESWINE101')."""
    m = re.search(r'(\d+)$', name)
    return m.group(1) if m else ""


def _names_differ_only_by_trailing_seq(a: str, b: str) -> bool:
    """True when a and b are identical except for a trailing digit sequence.

    Example: 'fwAZESWINE1' vs 'fwAZESWINE2' → True  (blocked by suffix guard).
    Example: 'fwAZESWINE1' vs 'fwAZESWINE10' → False (different names).
    """
    if a.lower() == b.lower():
        return False
    al, bl = a.lower(), b.lower()
    da, db = _get_trailing_digits(al), _get_trailing_digits(bl)
    if not da or not db:
        return False
    prefix_a = al[: len(al) - len(da)]
    prefix_b = bl[: len(bl) - len(db)]
    return prefix_a == prefix_b and da != db


def check_prefix_overlap(
    prefix_str: str,
    known_prefixes: List[Dict[str, Any]],
    vrf_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Detect ALL overlapping/colliding prefixes within the SAME VRF.

    Unlike the previous single-return version, this traverses every candidate
    prefix in the supplied list, categorises the overlap as either a legitimate
    parent-supernet containment (info-level) or a destructive peer collision
    (error-level), and returns a comprehensive list of findings.

    Cross-VRF overlaps are intentionally ignored — they are valid NetBox
    deployments.

    Returns a list of dicts:
        {"prefix": str, "vlan": str/None, "site": str/None,
         "status": str, "conflict_type": str}
    """
    clean = sanitize_cidr(prefix_str)
    if not clean or "/" not in clean:
        return []

    try:
        query_net = ipaddress.ip_network(clean, strict=False)
    except ValueError:
        return []

    findings: List[Dict[str, Any]] = []
    for cand in known_prefixes:
        cand_str = sanitize_cidr(str(cand.get("prefix", "") or ""))
        if not cand_str or "/" not in cand_str:
            continue
        # Skip self-reference
        if cand_str == clean:
            continue
        # Respect VRF filter — cross-VRF overlaps are valid
        cand_vrf = str(cand.get("vrf", "") or "").strip()
        if vrf_id and cand_vrf and cand_vrf != vrf_id:
            continue
        try:
            cand_net = ipaddress.ip_network(cand_str, strict=False)
        except ValueError:
            continue

        if not query_net.overlaps(cand_net):
            continue

        # Determine containment relationship
        try:
            is_parent = query_net.supernet_of(cand_net)  # query contains cand
            is_child = cand_net.supernet_of(query_net)   # cand contains query
        except ValueError:
            is_parent = is_child = False

        if is_parent or is_child:
            # Legitimate hierarchical containment (info-level)
            findings.append({
                "prefix": cand_str,
                "vlan": cand.get("vlan") or None,
                "site": cand.get("site") or None,
                "status": "INFO",
                "conflict_type": _CONTAINMENT_OVERLAP,
            })
        else:
            # Destructive peer collision — partial or equal-length overlap
            findings.append({
                "prefix": cand_str,
                "vlan": cand.get("vlan") or None,
                "site": cand.get("site") or None,
                "status": "ERROR",
                "conflict_type": _COLLISION_OVERLAP,
            })

    return findings


# ---------------------------------------------------------------------------
# BUG-7: Fragmentation-Aware Subnet Allocator
# ---------------------------------------------------------------------------


def _cidr_boundary_mask(prefix_len: int) -> int:
    """Return the integer mask that defines the CIDR binary boundary for *prefix_len*."""
    return (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF


def _is_aligned(network: ipaddress.IPv4Network, prefix_len: int) -> bool:
    """Check whether *network*'s start address is aligned to its CIDR boundary."""
    if network.prefixlen != prefix_len:
        return False
    net_int = int(network.network_address)
    mask = _cidr_boundary_mask(prefix_len)
    return (net_int & mask) == net_int


def _cidr_subnets(start: ipaddress.IPv4Address, length: int, prefix: int) -> List[ipaddress.IPv4Network]:
    """Yield all CIDR-legal /prefix subnets contained in the range starting at
    *start* with *length* addresses (must be a power of two)."""
    if length == 0:
        return []
    base = ipaddress.IPv4Network((start, prefix), strict=False)
    return list(base.subnets())


def _find_frag_chunks(
    supernet: ipaddress.IPv4Network,
    existing: List[ipaddress.IPv4Network],
    desired_prefix: int,
    max_results: int = 3,
) -> List[ipaddress.IPv4Network]:
    """Return up to *max_results* non-overlapping CIDR-legal subnets inside
    *supernet* that do NOT collide with any *existing* prefix.

    The allocator prefers already-fragmented regions (smaller subnets near the
    tail) while preserving large contiguous blocks near the head for bigger
    /24–/26 allocations.
    """
    candidates: List[ipaddress.IPv4Network] = []
    if desired_prefix > supernet.prefixlen:
        try:
            candidates = list(supernet.subnets(new_prefix=desired_prefix))
        except ValueError:
            return []

    # Sort: small fragmented chunks near the tail first, large head blocks last.
    # Total count in supernet for that prefix size.
    total = len(candidates) if candidates else 1
    # Reverse so tail-first; already fragmented tail chunks come first.
    candidates.reverse()

    used: List[ipaddress.IPv4Network] = list(existing)
    available: List[ipaddress.IPv4Network] = []
    for cand in candidates:
        if any(cand.overlaps(u) for u in used):
            continue
        available.append(cand)
        if len(available) >= max_results:
            break

    # If we didn't find enough via tail-first, fall back to head-first
    if len(available) < max_results:
        candidates.reverse()
        seen = set(str(c) for c in available)
        for cand in candidates:
            if str(cand) in seen:
                continue
            if any(cand.overlaps(u) for u in used):
                continue
            available.append(cand)
            if len(available) >= max_results:
                break

    return available[:max_results]


def get_top_3_available_subnets(
    supernet_str: str,
    existing_prefixes: List[str],
    requested_prefix_len: int,
) -> List[Dict[str, Any]]:
    """Fragmentation-aware subnet allocator returning Top-3 non-overlapping
    CIDR-aligned candidates inside *supernet_str*.

    Returns a list of dicts:
        {"prefix": str, "range": str, "notes": str}
    """
    clean_sup = sanitize_cidr(supernet_str)
    if not clean_sup or "/" not in clean_sup:
        return []
    try:
        sup = ipaddress.ip_network(clean_sup, strict=False)
    except ValueError:
        return []

    if requested_prefix_len < sup.prefixlen:
        return []  # Requested prefix is larger than supernet itself

    parsed_existing: List[ipaddress.IPv4Network] = []
    for p in existing_prefixes:
        cp = sanitize_cidr(str(p).strip())
        if not cp or "/" not in cp:
            continue
        try:
            parsed_existing.append(ipaddress.ip_network(cp, strict=False))
        except ValueError:
            continue

    results = _find_frag_chunks(sup, parsed_existing, requested_prefix_len, max_results=3)

    out: List[Dict[str, Any]] = []
    for idx, net in enumerate(results, start=1):
        notes_parts = [f"CIDR-aligned /{net.prefixlen}"]
        if net.prefixlen <= 26:
            notes_parts.append("large-block candidate")
        elif net.prefixlen >= 29:
            notes_parts.append("fragmented-tail placement")
        out.append({
            "prefix": str(net),
            "range": calculate_ip_range_str(net),
            "notes": "; ".join(notes_parts),
            "rank": idx,
        })
    return out


# ── CACHE INVALIDATION HOOK ─────────────────────────────────────────────

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
        # Fallback template preview row matching Site and VLAN Group behavior (Strict NO HARDCODE)
        row_cells = []
        for token_cell in row_tpl:
            cell = str(token_cell)
            cell = cell.replace("<site>", site_display)
            cell = cell.replace("<vlan_group>", group_name)
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


# ── CACHE INVALIDATION HOOK ─────────────────────────────────────────────
# Call this function whenever database records are updated or a new backup
# is ingested to ensure cached IPAM calculations reflect current data.

def clear_ipam_cache() -> None:
    """Clear all @st.cache_data caches in this module.

    Should be called after any database write operation that affects IPAM
    data (new backup ingestion, CSV import, manual record edits).
    """
    st.cache_data.clear()
