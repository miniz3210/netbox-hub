import ipaddress
import re
from typing import Dict, List, Any, Optional

STANDARD_VLAN_TEMPLATES = [
    {"vid": 300, "name": "Corporate WiFi", "desc": "VIN_Corp", "role": "Corporate WiFi", "mask": "/24", "opt": False},
    {"vid": 100, "name": "Workstations", "desc": "Wired Workstations", "role": "Workstations", "mask": "/24", "opt": False},
    {"vid": 5, "name": "Management", "desc": "Management", "role": "Management", "mask": "/24", "opt": False},
    {"vid": 700, "name": "Printers", "desc": "Printers", "role": "Printers", "mask": "/24", "opt": False},
    {"vid": 800, "name": "Audio Visual", "desc": "AV equipment", "role": "Audio Visual", "mask": "/24", "opt": False},
    {"vid": 200, "name": "Guests", "desc": "VIN_Guest", "role": "Guests", "mask": "/24", "opt": False, "suggest_fallback": "172.16.x.x"},
    {"vid": 400, "name": "Mobiles", "desc": "VIN_Mobi", "role": "Mobiles", "mask": "/24", "opt": False, "suggest_fallback": "172.18.x.x"},
    {"vid": 90, "name": "Routing", "desc": "Routing interface VLANs", "role": "Routing", "mask": "/24", "opt": True},
    {"vid": 500, "name": "OT", "desc": "OT", "role": "OT", "mask": "/24", "opt": True},
    {"vid": 600, "name": "IoT", "desc": "IoT/Security", "role": "IoT", "mask": "/24", "opt": True}
]

def slugify(text: str) -> str:
    s = str(text).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"[-\s]+", "-", s)

def calculate_usable_range(network: ipaddress.IPv4Network) -> str:
    if network.num_addresses <= 2:
        return f"{network[0]} - {network[-1]}"
    first_usable = network[1]
    last_usable = network[-2]
    return f"{first_usable} - {last_usable}"

def evaluate_subnet_entry(subnet_str: str, supernet_str: str = "") -> Dict[str, str]:
    clean = str(subnet_str).strip()
    if not clean or clean == "nan":
        return {"usable_range": "-", "status": "Unassigned"}
    if "x" in clean.lower():
        return {"usable_range": "RFC1918 Custom Pool", "status": "Special Pool"}
    try:
        net = ipaddress.ip_network(clean, strict=False)
        usable = calculate_usable_range(net)
        status = "OK"
        if supernet_str:
            try:
                sup = ipaddress.ip_network(supernet_str.strip(), strict=False)
                if not net.subnet_of(sup):
                    status = "⚠️ Outside Supernet"
            except ValueError:
                pass
        return {"usable_range": usable, "status": status}
    except ValueError:
        return {"usable_range": "Invalid CIDR", "status": "❌ Syntax Error"}

def slice_supernet(supernet_str: str, vlan_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        clean_sup = supernet_str.strip()
        base_net = ipaddress.ip_network(clean_sup, strict=False)
    except ValueError:
        base_net = None

    assigned = []
    current_iter = None
    if base_net:
        if base_net.prefixlen < 24:
            current_iter = base_net.subnets(new_prefix=24)
        else:
            current_iter = iter([base_net])

    for v in vlan_list:
        item = dict(v)
        assigned_net = None
        if current_iter and not item.get("suggest_fallback"):
            try:
                assigned_net = next(current_iter)
            except StopIteration:
                assigned_net = None

        if assigned_net:
            item["assigned_subnet"] = str(assigned_net)
            item["usable_range"] = calculate_usable_range(assigned_net)
            item["status"] = "OK"
        elif item.get("suggest_fallback"):
            item["assigned_subnet"] = item["suggest_fallback"]
            item["usable_range"] = "RFC1918 Custom Pool"
            item["status"] = "Special Pool"
        else:
            item["assigned_subnet"] = ""
            item["usable_range"] = "⚠️ Supernet Depleted"
            item["status"] = "Exhausted"
        assigned.append(item)
    return assigned

def generate_netbox_site_csv(site_name: str) -> str:
    slug = slugify(site_name)
    return f"name,slug,status\n{site_name},{slug},active"

def generate_netbox_vlan_group_csv(site_name: str, scope_id: str) -> str:
    group_name = f"{site_name} VLAN Group"
    slug = slugify(group_name)
    return f"name,slug,scope_type,scope_id\n{group_name},{slug},dcim.site,{scope_id}"

def generate_netbox_vlans_csv(site_name: str, df_records: List[Dict[str, Any]]) -> str:
    group_name = f"{site_name} VLAN Group"
    lines = ["vid,name,status,site,group,description,role"]
    for r in df_records:
        vid = r.get("VLAN ID") or r.get("vid", "")
        name = r.get("VLAN Name") or r.get("name", "")
        desc = r.get("Description") or r.get("desc", name)
        role = r.get("Role") or r.get("role", name)
        lines.append(f"{vid},{name},active,{site_name},{group_name},{desc},{role}")
    return "\n".join(lines)

def generate_netbox_prefixes_csv(site_name: str, scope_id: str, supernet_str: str, df_records: List[Dict[str, Any]]) -> str:
    group_name = f"{site_name} VLAN Group"
    lines = ["prefix,status,scope_type,scope_id,vlan_group,vlan,role,description"]
    
    if supernet_str:
        try:
            s_net = ipaddress.ip_network(supernet_str.strip(), strict=False)
            desc = f"Site Subnet - {s_net[0]} - {s_net[-1]}"
            lines.append(f"{s_net},active,dcim.site,{scope_id},{group_name},,,{desc}")
        except ValueError:
            pass

    for r in df_records:
        vid = r.get("VLAN ID") or r.get("vid", "")
        name = r.get("VLAN Name") or r.get("name", "")
        role = r.get("Role") or r.get("role", name)
        sub = str(r.get("Subnet") or r.get("assigned_subnet", "")).strip()
        
        if "/" in sub:
            desc = f"{site_name} {name} -- VLAN {vid}"
            lines.append(f"{sub},active,dcim.site,{scope_id},{group_name},{vid},{role},{desc}")
        else:
            lines.append(f"/24,active,dcim.site,{scope_id},{group_name},{vid},{role},{site_name} {name} -- VLAN {vid}")
            
    return "\n".join(lines)