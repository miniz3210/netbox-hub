import ipaddress
import re
from typing import List, Dict, Any, Optional

BRANCH_VLAN_PRESET = [
    {"vid": 300, "role": "Corporate WiFi", "vlan_name": "VIN_Corp", "desc": "Corporate WiFi"},
    {"vid": 100, "role": "Workstations", "vlan_name": "Wired Workstations", "desc": "Wired Workstations"},
    {"vid": 5, "role": "Management", "vlan_name": "Management", "desc": "Management"},
    {"vid": 700, "role": "Printers", "vlan_name": "Printers", "desc": "Printers"},
    {"vid": 800, "role": "Audio Visual", "vlan_name": "AV equipment", "desc": "AV equipment"},
    {"vid": 200, "role": "Guests", "vlan_name": "VIN_Guest", "desc": "VIN_Guest"},
    {"vid": 400, "role": "Mobiles", "vlan_name": "VIN_Mobi", "desc": "VIN_Mobi"},
    {"vid": 90, "role": "Routing", "vlan_name": "Routing interface VLANs", "desc": "Routing interface VLANs"},
    {"vid": 500, "role": "OT", "vlan_name": "OT", "desc": "OT"},
    {"vid": 600, "role": "IoT", "vlan_name": "IoT/Security", "desc": "IoT/Security"},
]

DATACENTER_VLAN_PRESET = [
    {"vid": 10, "role": "OOB / IPMI / iLO", "vlan_name": "OOB-Mgmt", "desc": "Out-of-Band server lights-out management (iLO/iDRAC)"},
    {"vid": 20, "role": "In-Band Management", "vlan_name": "InBand-Mgmt", "desc": "ESXi hypervisors and core switch management"},
    {"vid": 30, "role": "vMotion", "vlan_name": "vMotion", "desc": "Hypervisor live migration network"},
    {"vid": 40, "role": "Storage / iSCSI-A", "vlan_name": "Storage-A", "desc": "Primary SAN / IP Storage traffic"},
    {"vid": 50, "role": "Storage / iSCSI-B", "vlan_name": "Storage-B", "desc": "Secondary redundant SAN / IP Storage traffic"},
    {"vid": 100, "role": "Production App / Web", "vlan_name": "Prod-App", "desc": "Production application server workloads"},
    {"vid": 200, "role": "Production Database", "vlan_name": "Prod-DB", "desc": "Production database clusters"},
    {"vid": 300, "role": "DMZ / Perimeter", "vlan_name": "DMZ", "desc": "External-facing reverse proxies and DMZ hosts"},
    {"vid": 400, "role": "Core Infrastructure", "vlan_name": "Core-Infra", "desc": "DNS, Active Directory, NTP, and monitoring services"},
    {"vid": 500, "role": "Backup / Recovery", "vlan_name": "Backup", "desc": "Dedicated data protection and backup traffic"},
]

VLAN_PRESETS = {
    "-- Custom / Empty --": [],
    "🏢 Branch Office VLAN Preset": BRANCH_VLAN_PRESET,
    "🏛️ Data Center VLAN Preset": DATACENTER_VLAN_PRESET
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
    clean = name.strip()
    if clean.isupper() and len(clean) <= 4:
        return clean
    return " ".join([word.capitalize() for word in clean.split()])

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

def compute_chained_rows(supernet_str: str, working_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calculates suggested contiguous /24 network IPs without touching or resetting user inputs."""
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

        # Increment to next /24
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
    existing_prefixes: List[str]
) -> Dict[str, str]:
    clean_sub = sanitize_cidr(subnet_str)
    clean_supernet = sanitize_cidr(supernet_str)

    branch = format_branch_display(site_name) or "Site"
    clean_role = role.strip() if role else "Data"
    desc = f"{branch} - VLAN {vid} ({clean_role})" if vid else f"{branch} - {clean_role}"

    if not clean_sub or "/" not in clean_sub:
        return {"usable_range": "-", "status": "⚪ Unassigned", "desc": desc}

    try:
        net = ipaddress.ip_network(clean_sub, strict=False)
    except ValueError:
        return {"usable_range": "Invalid CIDR", "status": "❌ Invalid CIDR", "desc": desc}

    usable_range = calculate_ip_range_str(net)

    # 1. Direct Match in NetBox DB
    for exist_str in existing_prefixes:
        exist_clean = sanitize_cidr(exist_str)
        if exist_clean == clean_sub:
            return {
                "usable_range": usable_range,
                "status": "🔴 IN-USE (NetBox DB)",
                "desc": desc
            }

    # 2. Overlaps with existing DB prefixes
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

    # 3. Container boundary check
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

def generate_netbox_site_csv(site_name: str) -> str:
    clean = format_branch_display(site_name)
    slug = slugify(clean)
    return f"name,slug,status\n\"{clean}\",\"{slug}\",active"

def generate_netbox_vlan_group_csv(site_name: str, scope_id: str) -> str:
    clean = format_branch_display(site_name)
    slug = slugify(clean)
    return f"name,slug,scope_type,scope_id,description\n\"{clean} VLANs\",\"{slug}-vlans\",\"dcim.site\",{scope_id or '0'},\"{clean} VLAN Group\""

def generate_netbox_vlans_csv(site_name: str, rows: List[Dict[str, Any]]) -> str:
    clean = format_branch_display(site_name)
    lines = ["vid,name,status,group,description"]
    for r in rows:
        vid = r.get("VLAN ID")
        subnet = sanitize_cidr(str(r.get("Subnet (CIDR)") or "").strip())
        if not vid or not subnet or "/" not in subnet:
            continue
        vname = r.get("VLAN Name") or r.get("Role") or f"VLAN_{vid}"
        desc = r.get("VLAN Description") or f"{clean} {vname}"
        lines.append(f"{vid},\"{vname}\",active,\"{clean} VLANs\",\"{desc}\"")
    return "\n".join(lines)

def generate_netbox_prefixes_csv(site_name: str, scope_id: str, supernet_str: str, rows: List[Dict[str, Any]]) -> str:
    clean = format_branch_display(site_name)
    clean_supernet = sanitize_cidr(supernet_str)
    lines = ["prefix,status,scope_type,scope_id,vlan_group,vlan,description"]
    
    if clean_supernet and "/" in clean_supernet:
        lines.append(f"\"{clean_supernet}\",container,\"dcim.site\",{scope_id or '0'},,,\"{clean} - Site Supernet\"")

    for r in rows:
        subnet = sanitize_cidr(str(r.get("Subnet (CIDR)") or "").strip())
        vid = r.get("VLAN ID")
        if not subnet or "/" not in subnet or not vid or subnet == clean_supernet:
            continue
        vname = r.get("VLAN Name") or r.get("Role") or f"VLAN_{vid}"
        desc = r.get("Prefix Description") or f"{clean} - VLAN {vid} ({vname})"
        lines.append(f"\"{subnet}\",active,\"dcim.site\",{scope_id or '0'},\"{clean} VLANs\",{vid},\"{desc}\"")
        
    return "\n".join(lines)