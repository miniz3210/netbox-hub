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
    """Matches Excel range string (e.g. 10.113.64.0 - 10.113.64.255)"""
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
        clean = str(sub).strip()
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

def calculate_suggested_subnets(
    supernet_str: str, 
    vlan_list: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Exact replication of Excel Column F formulas (F4: =G1, F5:F18 = CEILING.MATH alignment).
    """
    try:
        clean_sup = supernet_str.strip()
        sup_net = ipaddress.ip_network(clean_sup, strict=False)
        current_ip_int = int(sup_net.network_address)
    except Exception:
        sup_net = None
        current_ip_int = None

    rows = []
    for v in vlan_list:
        item = dict(v)
        fallback = item.get("fallback_subnet")
        mask = item.get("mask", "/24")
        prefixlen = int(mask.replace("/", ""))
        
        if fallback:
            item["suggest_subnet"] = fallback
        elif current_ip_int is not None:
            block_size = 2 ** (32 - prefixlen)
            aligned_int = ((current_ip_int + block_size - 1) // block_size) * block_size
            item["suggest_subnet"] = str(ipaddress.IPv4Address(aligned_int))
            # Advance current IP integer for next sequential row
            current_ip_int = aligned_int + block_size
        else:
            item["suggest_subnet"] = ""
            
        rows.append(item)
    return rows

def evaluate_subnet_row(
    subnet_input: Any,
    vlan_id: Any = None,
    vlan_role: str = "",
    site_name: str = "",
    supernet_str: str = "",
    existing_prefixes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Evaluates any typed CIDR or IP, returning the exact usable range, collision tag, description, and status.
    """
    try:
        clean = "" if subnet_input is None else str(subnet_input).strip()
    except Exception:
        clean = ""

    vid_str = str(vlan_id) if vlan_id not in (None, "", "nan") else ""
    role_text = str(vlan_role or "").strip()
    site_text = str(site_name or "").strip()
    desc = f"{site_text} {role_text} -- VLAN {vid_str}".strip() if vid_str else f"{site_text} {role_text}".strip()

    if not clean or clean.lower() in ("nan", "none", "null", "<na>", "—", "-"):
        return {"usable_range": "—", "status": "Unassigned", "desc": desc, "in_use": False}

    if "x" in clean.lower():
        return {"usable_range": "RFC1918 Custom Pool", "status": "Special Pool", "desc": desc, "in_use": False}

    # Normalize single IP input into /24 if mask was omitted
    if "/" not in clean and clean.count(".") == 3:
        clean = f"{clean}/24"

    if "/" not in clean:
        return {"usable_range": "—", "status": "Pending Input", "desc": desc, "in_use": False}

    try:
        net = ipaddress.ip_network(clean, strict=False)
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
    except (ValueError, TypeError):
        return {"usable_range": "Invalid CIDR", "status": "Syntax Error", "desc": desc, "in_use": False}

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
        desc = r.get("VLAN Description") or r.get("Description") or r.get("desc", name)
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
        name = r.get("VLAN Name") or r.get("name", "")
        role = r.get("Role") or r.get("role", "")
        desc = r.get("Prefix Description") or r.get("desc") or f"{site_name} {role} -- VLAN {vid}"
        sub = str(r.get("Subnet (CIDR)") or r.get("Subnet") or r.get("assigned_subnet", "")).strip()
        
        if "/" in sub:
            lines.append(f"{sub},active,dcim.site,{scope_id},\"{group_name}\",{vid},\"{role}\",\"{desc}\"")
            
    return "\n".join(lines)