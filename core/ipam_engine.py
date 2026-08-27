import ipaddress
import re
from typing import Dict, List, Any, Optional

ROLE_LOOKUP_TABLE = {
    "corporate wifi": {"canonical_role": "Corporate WiFi", "vlan_name": "Corporate WiFi", "vlan_desc": "VIN_Corp", "fallback": None},
    "workstations": {"canonical_role": "Workstations", "vlan_name": "Workstations", "vlan_desc": "Wired Workstations", "fallback": None},
    "management": {"canonical_role": "Management", "vlan_name": "Management", "vlan_desc": "Management", "fallback": None},
    "printers": {"canonical_role": "Printers", "vlan_name": "Printers", "vlan_desc": "Printers", "fallback": None},
    "audio visual": {"canonical_role": "Audio Visual", "vlan_name": "Audio Visual", "vlan_desc": "AV equipment", "fallback": None},
    "guests": {"canonical_role": "Guests", "vlan_name": "Guests", "vlan_desc": "VIN_Guest", "fallback": "172.16.x.x"},
    "mobiles": {"canonical_role": "Mobiles", "vlan_name": "Mobiles", "vlan_desc": "VIN_Mobi", "fallback": "172.18.x.x"},
    "routing": {"canonical_role": "Routing", "vlan_name": "Routing", "vlan_desc": "Routing interface VLANs", "fallback": None},
    "ot": {"canonical_role": "OT", "vlan_name": "OT", "vlan_desc": "OT", "fallback": None},
    "iot": {"canonical_role": "IoT", "vlan_name": "IoT", "vlan_desc": "IoT/Security", "fallback": None},
}

ROLE_ALIASES = {
    "corp": "corporate wifi",
    "corp wifi": "corporate wifi",
    "corporate": "corporate wifi",
    "vin_corp": "corporate wifi",
    "workstation": "workstations",
    "wired": "workstations",
    "wired workstations": "workstations",
    "pc": "workstations",
    "desktop": "workstations",
    "mgmt": "management",
    "printer": "printers",
    "av": "audio visual",
    "audiovisual": "audio visual",
    "av equipment": "audio visual",
    "guest": "guests",
    "vin_guest": "guests",
    "guest wifi": "guests",
    "mobile": "mobiles",
    "mobi": "mobiles",
    "vin_mobi": "mobiles",
    "mobi wifi": "mobiles",
    "router": "routing",
    "rtr": "routing",
    "routing interface": "routing",
    "iot/security": "iot",
    "security": "iot",
}

STANDARD_VLAN_TEMPLATES = [
    {"vid": 300, "role": "Corporate WiFi", "opt": False},
    {"vid": 100, "role": "Workstations", "opt": False},
    {"vid": 5, "role": "Management", "opt": False},
    {"vid": 700, "role": "Printers", "opt": False},
    {"vid": 800, "role": "Audio Visual", "opt": False},
    {"vid": 200, "role": "Guests", "opt": False, "fallback_subnet": "172.16.x.x"},
    {"vid": 400, "role": "Mobiles", "opt": False, "fallback_subnet": "172.18.x.x"},
    {"vid": 90, "role": "Routing", "opt": True},
    {"vid": 500, "role": "OT", "opt": True},
    {"vid": 600, "role": "IoT", "opt": True}
]

def format_branch_display(site_text: str) -> str:
    """Auto-capitalizes/uppercases branch site name (e.g. bristol -> Bristol, age -> AGE)."""
    clean = str(site_text or "").strip()
    if not clean:
        return ""
    if len(clean) <= 4:
        return clean.upper()
    return clean.title()

def resolve_role_details(user_role_input: str) -> Dict[str, Any]:
    clean = re.sub(r"[^a-zA-Z0-9/_\s-]", "", str(user_role_input or "")).strip().lower()
    if not clean:
        return {"canonical_role": "", "vlan_name": "", "vlan_desc": "", "fallback": None}
    
    if clean in ROLE_LOOKUP_TABLE:
        return ROLE_LOOKUP_TABLE[clean]
    
    if clean in ROLE_ALIASES:
        target = ROLE_ALIASES[clean]
        return ROLE_LOOKUP_TABLE[target]
    
    for alias, target in ROLE_ALIASES.items():
        if alias in clean or clean in alias:
            return ROLE_LOOKUP_TABLE[target]
            
    for key, val in ROLE_LOOKUP_TABLE.items():
        if key in clean or clean in key:
            return val
            
    return {
        "canonical_role": str(user_role_input).strip(),
        "vlan_name": str(user_role_input).strip(),
        "vlan_desc": str(user_role_input).strip(),
        "fallback": None
    }

def slugify(text: str) -> str:
    s = str(text).strip().lower()
    s = s.replace(" - ", "-").replace(" ", "-")
    return re.sub(r"[^\w-]", "", s)

def calculate_ip_range_str(network: ipaddress.IPv4Network) -> str:
    return f"{network.network_address} - {network.broadcast_address}"

def check_prefix_collision(target_net: ipaddress.IPv4Network, existing_prefixes: List[str]) -> bool:
    for p in existing_prefixes:
        try:
            ex_net = ipaddress.ip_network(p.strip(), strict=False)
            if target_net.overlaps(ex_net):
                return True
        except ValueError:
            continue
    return False

def calculate_remaining_subnets(supernet_str: str, allocated_subnets: List[str]) -> Dict[str, int]:
    try:
        sup_net = ipaddress.ip_network(supernet_str.strip(), strict=False)
    except ValueError:
        return {f"/{c}": 0 for c in range(23, 30)}

    total_ips = sup_net.num_addresses
    used_ips = 0
    
    for sub in allocated_subnets:
        clean = str(sub).strip()
        if "/" in clean and "x" not in clean.lower():
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

def compute_chained_rows(
    supernet_str: str, 
    rows_data: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    try:
        clean_sup = supernet_str.strip()
        sup_net = ipaddress.ip_network(clean_sup, strict=False)
        base_ip = str(sup_net.network_address)
    except Exception:
        sup_net = None
        base_ip = ""

    processed = []

    for idx, r in enumerate(rows_data):
        role_raw = r.get("Role", "")
        role_info = resolve_role_details(role_raw)
        
        canonical_role = role_info["canonical_role"] or str(role_raw).strip()
        
        custom_name = str(r.get("VLAN Name") or "").strip()
        custom_desc = str(r.get("VLAN Description") or "").strip()
        
        vlan_name = custom_name if (custom_name and custom_name.lower() != "none") else (role_info["vlan_name"] or canonical_role)
        vlan_desc = custom_desc if (custom_desc and custom_desc.lower() != "none") else (role_info["vlan_desc"] or canonical_role)
        fallback = role_info["fallback"] or r.get("fallback_subnet")

        user_subnet = str(r.get("Subnet (CIDR)") or r.get("Subnet") or "").strip()
        if user_subnet.lower() in ("nan", "none", "null", "—", "-"):
            user_subnet = ""

        suggest = ""
        if fallback:
            suggest = fallback
        elif idx == 0:
            suggest = base_ip
        else:
            prev_row = processed[idx - 1]
            prev_sub = prev_row.get("Subnet (CIDR)", "").strip()
            if prev_sub and "x" not in prev_sub.lower():
                try:
                    clean_prev = prev_sub if "/" in prev_sub else f"{prev_sub}/24"
                    prev_net = ipaddress.ip_network(clean_prev, strict=False)
                    next_int = int(prev_net.broadcast_address) + 1
                    suggest = str(ipaddress.IPv4Address(next_int))
                except Exception:
                    suggest = ""
            else:
                suggest = ""

        processed.append({
            "VLAN ID": r.get("VLAN ID") if r.get("VLAN ID") not in (None, "None", "") else None,
            "VLAN Name": vlan_name,
            "Role": canonical_role,
            "VLAN Description": vlan_desc,
            "Suggest Subnet": suggest,
            "Subnet (CIDR)": user_subnet,
            "fallback_subnet": fallback or ""
        })
        
    return processed

def evaluate_subnet_row(
    subnet_input: Any,
    vlan_id: Any = None,
    vlan_role: str = "",
    site_name: str = "",
    supernet_str: str = "",
    existing_prefixes: Optional[List[str]] = None
) -> Dict[str, Any]:
    try:
        clean = "" if subnet_input is None else str(subnet_input).strip()
    except Exception:
        clean = ""

    vid_str = str(vlan_id) if vlan_id not in (None, "", "nan", "None") else ""
    role_text = str(vlan_role or "").strip()
    site_display = format_branch_display(site_name)
    desc = f"{site_display} {role_text} -- VLAN {vid_str}".strip() if vid_str else f"{site_display} {role_text}".strip()

    if not clean or clean.lower() in ("nan", "none", "null", "<na>", "—", "-"):
        return {"usable_range": "—", "status": "Unassigned", "desc": desc, "in_use": False}

    if "x" in clean.lower():
        return {"usable_range": "RFC1918 Custom Pool", "status": "Special Pool", "desc": desc, "in_use": False}

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
    display = format_branch_display(site_name) or "Site"
    return f"name,slug,status\n{display},{slugify(display)},active"

def generate_netbox_vlan_group_csv(site_name: str, scope_id: str) -> str:
    display = format_branch_display(site_name) or "Site"
    group_name = f"{display} VLAN Group"
    slug = f"{slugify(display)}-vlan-group"
    return f"name,slug,scope_type,scope_id\n{group_name},{slug},dcim.site,{scope_id}"

def generate_netbox_vlans_csv(site_name: str, records: List[Dict[str, Any]]) -> str:
    display = format_branch_display(site_name) or "Site"
    group_name = f"{display} VLAN Group"
    lines = ["vid,name,status,site,group,description,role"]
    for r in records:
        vid = r.get("VLAN ID") or r.get("vid", "")
        if not vid or str(vid).lower() == "none":
            continue
        name = r.get("VLAN Name") or r.get("name", "")
        desc = r.get("VLAN Description") or r.get("Description") or r.get("desc", name)
        role = r.get("Role") or r.get("role", name)
        lines.append(f"{vid},{name},active,{display},{group_name},{desc},{role}")
    return "\n".join(lines)

def generate_netbox_prefixes_csv(site_name: str, scope_id: str, supernet_str: str, records: List[Dict[str, Any]]) -> str:
    display = format_branch_display(site_name) or "Site"
    group_name = f"{display} VLAN Group"
    lines = ["prefix,status,scope_type,scope_id,vlan_group,vlan,role,description"]
    
    if supernet_str:
        try:
            s_net = ipaddress.ip_network(supernet_str.strip(), strict=False)
            desc = f"{display} Subnet - {calculate_ip_range_str(s_net)}"
            lines.append(f"{s_net},active,dcim.site,{scope_id},\"{group_name}\",,,\"{desc}\"")
        except ValueError:
            pass

    for r in records:
        vid = r.get("VLAN ID") or r.get("vid", "")
        role = r.get("Role") or r.get("role", "")
        desc = r.get("Prefix Description") or r.get("desc") or f"{display} {role} -- VLAN {vid}"
        sub = str(r.get("Subnet (CIDR)") or r.get("Subnet") or r.get("assigned_subnet", "")).strip()
        
        vid_str = str(vid) if vid and str(vid).lower() != "none" else ""
        if "/" in sub:
            lines.append(f"{sub},active,dcim.site,{scope_id},\"{group_name}\",{vid_str},\"{role}\",\"{desc}\"")
            
    return "\n".join(lines)