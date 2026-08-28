import ipaddress
import re
from typing import List, Dict, Any, Optional

STANDARD_VLAN_TEMPLATES = [
    {"vid": 1, "role": "Site Subnet", "prefix_len": 16, "fallback_subnet": ""},
    {"vid": 10, "role": "In-Band Management", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 20, "role": "Data", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 30, "role": "Voice", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 40, "role": "Corporate WiFi", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 50, "role": "Guest WiFi", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 60, "role": "Printers", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 70, "role": "Security / CCTV", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 80, "role": "Building Management", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 90, "role": "Audio Visual", "prefix_len": 24, "fallback_subnet": ""},
    {"vid": 100, "role": "Server / DMZ", "prefix_len": 24, "fallback_subnet": ""},
]

ROLE_DESCRIPTIONS = {
    "site subnet": "Top-level site container subnet",
    "in-band management": "Management interfaces for network hardware",
    "data": "Primary wired office endpoints",
    "voice": "VoIP telephony network",
    "corporate wifi": "Corporate wireless clients",
    "guest wifi": "Isolated guest internet access",
    "printers": "Network printers and multi-function devices",
    "security / cctv": "Physical security cameras and access control",
    "building management": "HVAC and BMS controllers",
    "audio visual": "Video conferencing and digital signage",
    "server / dmz": "Local branch infrastructure servers",
}

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
    return name.strip()

def calculate_ip_range_str(net: ipaddress.IPv4Network) -> str:
    if net.num_addresses <= 2:
        return f"{net.network_address} - {net.broadcast_address}"
    first_host = net.network_address + 1
    last_host = net.broadcast_address - 1
    return f"{first_host} - {last_host}"

def compute_chained_rows(supernet_str: str, working_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calculates suggested contiguous non-overlapping subnets based on supernet and prior row assignments."""
    current_base = None
    if supernet_str and "/" in supernet_str:
        try:
            sup_net = ipaddress.ip_network(supernet_str, strict=False)
            current_base = sup_net.network_address
        except ValueError:
            pass

    out = []
    for r in working_rows:
        row = dict(r)
        role = str(row.get("Role") or "").strip()
        vname = str(row.get("VLAN Name") or "").strip()
        vdesc = str(row.get("VLAN Description") or "").strip()
        
        if not vname and role:
            row["VLAN Name"] = role
        if not vdesc and role:
            row["VLAN Description"] = ROLE_DESCRIPTIONS.get(role.lower(), f"{role} Network")

        suggested = ""
        user_subnet = str(row.get("Subnet (CIDR)") or "").strip()

        if current_base is not None:
            # First row or site container
            if str(row.get("VLAN ID")) == "1" or role.lower() == "site subnet":
                suggested = supernet_str
            else:
                suggested = f"{current_base}/24"

        row["Suggest Subnet"] = suggested

        # Advance pointer for next subnet
        active_sub = user_subnet or suggested
        if active_sub and "/" in active_sub:
            try:
                active_net = ipaddress.ip_network(active_sub, strict=False)
                if str(row.get("VLAN ID")) != "1" and role.lower() != "site subnet":
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
    existing_prefixes: List[str]
) -> Dict[str, str]:
    if not subnet_str or "/" not in subnet_str:
        return {"usable_range": "-", "status": "⚪ Unassigned", "desc": ""}

    try:
        net = ipaddress.ip_network(subnet_str, strict=False)
    except ValueError:
        return {"usable_range": "Invalid CIDR", "status": "❌ Invalid CIDR", "desc": ""}

    usable_range = calculate_ip_range_str(net)
    
    # Description Generator
    branch = format_branch_display(site_name) or "Site"
    clean_role = role.strip() if role else "Data"
    if str(vid) == "1" or clean_role.lower() == "site subnet":
        desc = f"{branch} - Site Supernet"
    else:
        desc = f"{branch} - VLAN {vid} ({clean_role})" if vid else f"{branch} - {clean_role}"

    # Supernet container boundary check
    if supernet_str and "/" in supernet_str:
        try:
            sup_net = ipaddress.ip_network(supernet_str, strict=False)
            if not net.subnet_of(sup_net) and net != sup_net:
                return {
                    "usable_range": usable_range,
                    "status": "⚠️ Outside Supernet",
                    "desc": desc
                }
        except ValueError:
            pass

    # Global DB collision / overlap check
    for exist_str in existing_prefixes:
        if exist_str == subnet_str:
            continue
        try:
            exist_net = ipaddress.ip_network(exist_str, strict=False)
            if net.overlaps(exist_net) and net != exist_net:
                # Allow standard site container containing its own subnets
                if not (net.subnet_of(exist_net) and exist_net.prefixlen < 24):
                    return {
                        "usable_range": usable_range,
                        "status": f"🚨 Overlaps {exist_str}",
                        "desc": desc
                    }
        except ValueError:
            pass

    return {"usable_range": usable_range, "status": "🟢 Available", "desc": desc}

def calculate_remaining_subnets(supernet_str: str, allocated_subnets: List[str]) -> Dict[str, int]:
    result = {"/24": 0, "/25": 0, "/26": 0, "/27": 0, "/28": 0}
    if not supernet_str or "/" not in supernet_str:
        return result

    try:
        sup_net = ipaddress.ip_network(supernet_str, strict=False)
    except ValueError:
        return result

    valid_allocations = []
    for s in allocated_subnets:
        if s and "/" in s and s != supernet_str:
            try:
                sub = ipaddress.ip_network(s, strict=False)
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

# ── BULK NETBOX CSV GENERATORS ──────────────────────────────────────────

def generate_netbox_site_csv(site_name: str) -> str:
    clean = format_branch_display(site_name)
    slug = slugify(clean)
    return f"name,slug,status\n\"{clean}\",\"{slug}\",active"

def generate_netbox_vlan_group_csv(site_name: str, scope_id: str) -> str:
    clean = format_branch_display(site_name)
    slug = slugify(clean)
    return f"name,slug,scope_type,scope_id,description\n\"{clean} VLANs\",\"{slug}-vlans\",\"dcim.site\",{scope_id or '0'},\"Standard VLAN Group for {clean}\""

def generate_netbox_vlans_csv(site_name: str, rows: List[Dict[str, Any]]) -> str:
    clean = format_branch_display(site_name)
    slug = slugify(clean)
    lines = ["vid,name,status,group,description"]
    for r in rows:
        vid = r.get("VLAN ID")
        if not vid or str(vid) == "1":
            continue
        vname = r.get("VLAN Name") or r.get("Role") or f"VLAN_{vid}"
        desc = r.get("VLAN Description") or f"{clean} {vname}"
        lines.append(f"{vid},\"{vname}\",active,\"{clean} VLANs\",\"{desc}\"")
    return "\n".join(lines)

def generate_netbox_prefixes_csv(site_name: str, scope_id: str, supernet_str: str, rows: List[Dict[str, Any]]) -> str:
    clean = format_branch_display(site_name)
    lines = ["prefix,status,scope_type,scope_id,vlan_group,vlan,description"]
    
    # 1. Top-Level Site Container
    if supernet_str and "/" in supernet_str:
        lines.append(f"\"{supernet_str}\",container,\"dcim.site\",{scope_id or '0'},,,\"{clean} - Site Supernet\"")

    # 2. Subnets
    for r in rows:
        subnet = str(r.get("Subnet (CIDR)") or "").strip()
        vid = r.get("VLAN ID")
        if not subnet or "/" not in subnet or str(vid) == "1" or subnet == supernet_str:
            continue
        vname = r.get("VLAN Name") or r.get("Role") or f"VLAN_{vid}"
        desc = r.get("Prefix Description") or f"{clean} - VLAN {vid} ({vname})"
        lines.append(f"\"{subnet}\",active,\"dcim.site\",{scope_id or '0'},\"{clean} VLANs\",{vid},\"{desc}\"")
        
    return "\n".join(lines)