import ipaddress
import re
from typing import Dict, List, Any, Optional

STANDARD_VLAN_TEMPLATES = [
    {"vid": 300, "name": "Corporate WiFi", "desc": "VIN_Corp", "role": "Corporate WiFi", "mask": "/24", "opt": False},
    {"vid": 100, "name": "Workstations", "desc": "Wired Workstations", "role": "Workstations", "mask": "/24", "opt": False},
    {"vid": 5, "name": "Management", "desc": "Management", "role": "Management", "mask": "/24", "opt": False},
    {"vid": 700, "name": "Printers", "desc": "Printers", "role": "Printers", "mask": "/24", "opt": False},
    {"vid": 800, "name": "Audio Visual", "desc": "AV equipment", "role": "Audio Visual", "mask": "/24", "opt": False},
    {"vid": 200, "name": "Guests", "desc": "VIN_Guest", "role": "Guests", "mask": "/24", "opt": False, "fallback_subnet": "172.16.x.x"},
    {"vid": 400, "name": "Mobiles", "desc": "VIN_Mobi", "role": "Mobiles", "mask": "/24", "opt": False, "fallback_subnet": "172.18.x.x"},
    {"vid": 90, "name": "Routing", "desc": "Routing interface VLANs", "role": "Routing", "mask": "/24", "opt": True},
    {"vid": 500, "name": "OT", "desc": "OT", "role": "OT", "mask": "/24", "opt": True},
    {"vid": 600, "name": "IoT", "desc": "IoT/Security", "role": "IoT", "mask": "/24", "opt": True}
]

def slugify(text: str) -> str:
    """Matches Excel D1: =LOWER(SUBSTITUTE(SUBSTITUTE(B1, " - ", "-"), " ", "-"))"""
    s = str(text).strip().lower()
    s = s.replace(" - ", "-").replace(" ", "-")
    return re.sub(r"[^\w-]", "", s)

def calculate_ip_range_str(network: ipaddress.IPv4Network) -> str:
    """Matches Excel range string (e.g. 10.113.252.0 - 10.113.252.255)"""
    return f"{network.network_address} - {network.broadcast_address}"

def check_prefix_collision(target_net: ipaddress.IPv4Network, existing_prefixes: List[str]) -> bool:
    """Matches Excel REDUCE/LAMBDA overlap check against Prefixes sheet."""
    for p in existing_prefixes:
        try:
            ex_net = ipaddress.ip_network(p.strip(), strict=False)
            if target_net.overlaps(ex_net):
                return True
        except ValueError:
            continue
    return False

def calculate_remaining_subnets(supernet_str: str, allocated_subnets: List[str]) -> Dict[str, int]:
    """Matches Excel M3:M9 usable subnet capacity matrix."""
    try:
        sup_net = ipaddress.ip_network(supernet_str.strip(), strict=False)
    except ValueError:
        return {f"/{c}": 0 for c in range(23, 30)}

    total_ips = sup_net.num_addresses
    used_ips = 0
    
    for sub in allocated_subnets:
        clean = sub.strip()
        if "/" in clean and not "x" in clean.lower():
            try:
                n = ipaddress.ip_network(clean, strict=False)
                if n.subnet_of(sup_net):
                    used_ips += n.num_addresses
            except ValueError:
                continue

    remain_ips = max(0, total_ips - used_ips)
    
    capacity = {}
    for cidr in range(23, 30):
        block_size = 2 ** (32 - cidr)
        capacity[f"/{cidr}"] = remain_ips // block_size
        
    return capacity

def evaluate_subnet_row(
    subnet_input: str,
    vlan_id: Any,
    vlan_role: str,
    site_name: str,
    supernet_str: str = "",
    existing_prefixes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Evaluates an individual row, generating range, collision tag, description, and status.

    Description formula (matches Excel pattern in calculate_usable_range):
        "{site_name} {vlan_role} -- VLAN {vlan_id}"
    e.g. "bristol Corporate WiFi -- VLAN 300".

    When the user enters a misaligned CIDR like "10.113.249.0/23" the live
    range is CEILING-aligned to the next valid block boundary (matching the
    Excel CEILING.MATH pattern used in calculate_next_subnet) so the
    displayed range never overlaps with the previous allocation.
    """
    clean = str(subnet_input).strip()
    desc = f"{site_name} {vlan_role} -- VLAN {vlan_id}" if vlan_id else f"{site_name} {vlan_role}"

    # Friendly view for empty / mid-typing rows. This prevents "Invalid CIDR"
    # from leaking into the live preview when the user is still typing.
    if not clean or clean == "nan" or clean.lower() in ("none", "null"):
        return {"usable_range": "—", "status": "Unassigned", "desc": desc, "in_use": False}

    if "x" in clean.lower():
        return {"usable_range": "RFC1918 Custom Pool", "status": "Special Pool", "desc": desc, "in_use": False}

    # Only attempt a parse if the input has the shape of a CIDR (e.g. "10.0.0.0/24").
    # Without this guard, partial / malformed values from the Streamlit data_editor
    # cascade into ipaddress.ip_network() and surface as "Invalid CIDR".
    if "/" not in clean or clean.count("/") > 1:
        return {"usable_range": "—", "status": "Pending Input", "desc": desc, "in_use": False}

    head = clean.split("/")[0]
    if head.count(".") != 3 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in head.split(".")):
        return {"usable_range": "—", "status": "Pending Input", "desc": desc, "in_use": False}

    try:
        # First parse the CIDR — strict=False lets the user type a host IP.
        net = ipaddress.ip_network(clean, strict=False)

        # CEILING.MATH-style alignment: round the network address UP to the
        # next aligned boundary for this prefix length. This way typing
        # "10.113.249.0/23" surfaces the next valid /23 (10.113.250.0/23,
        # which then displays as 10.113.250.0 - 10.113.251.255) instead of
        # silently FLOORing down to 10.113.248.0/23.
        block_size = 2 ** (32 - net.prefixlen)
        ip_int = int(net.network_address)
        aligned_int = ((ip_int + block_size - 1) // block_size) * block_size
        net = ipaddress.ip_network(f"{aligned_int}/{net.prefixlen}", strict=False)

        range_str = calculate_ip_range_str(net)
        is_in_use = check_prefix_collision(net, existing_prefixes or [])

        status = "OK"
        if is_in_use:
            status = "⚠️ [IN-USE]"
            range_str += " ⚠️ [IN-USE]"
        elif supernet_str:
            try:
                sup = ipaddress.ip_network(supernet_str.strip(), strict=False)
                if not net.subnet_of(sup):
                    status = "Outside Supernet"
            except ValueError:
                pass

        return {
            "usable_range": range_str,
            "status": status,
            "desc": desc,
            "in_use": is_in_use,
            "net": net
        }
    except ValueError:
        return {"usable_range": "Invalid CIDR", "status": "❌ Syntax Error", "desc": desc, "in_use": False}

def slice_supernet(
    supernet_str: str, 
    vlan_list: List[Dict[str, Any]], 
    site_name: str,
    existing_prefixes: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Implements Excel F5:F8 CEILING.MATH dynamic slicing logic."""
    try:
        sup_net = ipaddress.ip_network(supernet_str.strip(), strict=False)
    except ValueError:
        sup_net = None

    assigned = []
    current_iter = None
    if sup_net:
        if sup_net.prefixlen < 24:
            current_iter = sup_net.subnets(new_prefix=24)
        else:
            current_iter = iter([sup_net])

    for v in vlan_list:
        item = dict(v)
        assigned_net = None
        if current_iter and not item.get("fallback_subnet"):
            try:
                assigned_net = next(current_iter)
            except StopIteration:
                assigned_net = None

        if assigned_net:
            item["assigned_subnet"] = str(assigned_net)
        elif item.get("fallback_subnet"):
            item["assigned_subnet"] = item["fallback_subnet"]
        else:
            item["assigned_subnet"] = ""

        eval_res = evaluate_subnet_row(
            item["assigned_subnet"],
            item["vid"],
            item["role"],
            site_name,
            supernet_str,
            existing_prefixes
        )
        item["usable_range"] = eval_res["usable_range"]
        item["status"] = eval_res["status"]
        item["desc"] = eval_res["desc"]
        assigned.append(item)

    return assigned

# ── CSV GENERATORS (Matching Excel Rows 22-61) ──────────────────────────

def generate_netbox_site_csv(site_name: str) -> str:
    return f"name,slug,status\n{site_name},{slugify(site_name)},active"

def generate_netbox_vlan_group_csv(site_name: str, scope_id: str) -> str:
    group_name = f"{site_name} VLAN Group"
    slug = f"{slugify(site_name)}-vlan-group"
    return f"name,slug,scope_type,scope_id\n{group_name},{slug},dcim.site,{scope_id}"

def generate_netbox_vlans_csv(site_name: str, records: List[Dict[str, Any]]) -> str:
    group_name = f"{site_name} VLAN Group"
    lines = ["vid,name,status,site,group,description,role"]
    for r in records:
        vid = r.get("VLAN ID") or r.get("vid", "")
        name = r.get("VLAN Name") or r.get("name", "")
        desc = r.get("Description") or r.get("desc", name)
        role = r.get("Role") or r.get("role", name)
        lines.append(f"{vid},{name},active,{site_name},{group_name},{desc},{role}")
    return "\n".join(lines)

def generate_netbox_prefixes_csv(site_name: str, scope_id: str, supernet_str: str, records: List[Dict[str, Any]]) -> str:
    group_name = f"{site_name} VLAN Group"
    lines = ["prefix,status,scope_type,scope_id,vlan_group,vlan,role,description"]
    
    if supernet_str:
        try:
            s_net = ipaddress.ip_network(supernet_str.strip(), strict=False)
            desc = f"Site Subnet - {calculate_ip_range_str(s_net)}"
            lines.append(f"{s_net},active,dcim.site,{scope_id},\"{group_name}\",,,\"{desc}\"")
        except ValueError:
            pass

    for r in records:
        vid = r.get("VLAN ID") or r.get("vid", "")
        role = r.get("Role") or r.get("role", "")
        desc = r.get("Description") or r.get("desc") or f"{site_name} {role} -- VLAN {vid}"
        sub = str(r.get("Subnet") or r.get("assigned_subnet", "")).strip()
        
        if "/" in sub:
            lines.append(f"{sub},active,dcim.site,{scope_id},\"{group_name}\",{vid},\"{role}\",\"{desc}\"")
            
    return "\n".join(lines)