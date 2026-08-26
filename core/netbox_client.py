import logging
import requests
from typing import Dict, List, Any, Tuple

logger = logging.getLogger("netbox-hub")

def fetch_netbox_full_sync(netbox_url: str, api_token: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetches DCIM inventory and IPAM prefixes from NetBox REST API."""
    clean_url = netbox_url.rstrip("/")
    clean_token = api_token.replace("Token ", "").replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Token {clean_token}",
        "Accept": "application/json"
    }

    inv_records = []
    ipam_records = []

    # 1. DCIM Devices
    try:
        dev_res = requests.get(f"{clean_url}/api/dcim/devices/?limit=1000", headers=headers, timeout=15)
        if dev_res.status_code == 200:
            for d in dev_res.json().get("results", []):
                name = d.get("name") or ""
                if not name:
                    continue
                dtype_obj = d.get("device_type") or {}
                mfg = dtype_obj.get("manufacturer", {}).get("name", "")
                dtype = dtype_obj.get("model", "")
                role = (d.get("role") or d.get("device_role") or {}).get("name", "")
                site = (d.get("site") or {}).get("name", "")
                cluster = (d.get("cluster") or {}).get("name", "")
                desc = d.get("description") or d.get("comments") or ""

                combined = f"{name} {role} {dtype} {desc}".lower()
                cat = "hypervisor" if any(h in combined for h in ["esx", "hypervisor", "infhost", "vmhost", "esxi"]) else "device"

                inv_records.append({
                    "category": cat,
                    "name": name,
                    "description": desc,
                    "manufacturer": mfg,
                    "model_or_role": dtype or role,
                    "site": site,
                    "cluster": cluster
                })
    except Exception as e:
        logger.error(f"DCIM API Error: {e}")

    # 2. Virtualization VMs
    try:
        vm_res = requests.get(f"{clean_url}/api/virtualization/virtual-machines/?limit=1000", headers=headers, timeout=15)
        if vm_res.status_code == 200:
            for vm in vm_res.json().get("results", []):
                name = vm.get("name") or ""
                if not name:
                    continue
                role = (vm.get("role") or {}).get("name", "")
                site = (vm.get("site") or {}).get("name", "")
                cluster = (vm.get("cluster") or {}).get("name", "")
                desc = vm.get("description") or vm.get("comments") or ""

                inv_records.append({
                    "category": "vm",
                    "name": name,
                    "description": desc,
                    "manufacturer": "Virtual Machine",
                    "model_or_role": role or "VM",
                    "site": site,
                    "cluster": cluster
                })
    except Exception as e:
        logger.warning(f"VM API Error: {e}")

    # 3. IPAM Prefixes & VLANs
    try:
        pfx_res = requests.get(f"{clean_url}/api/ipam/prefixes/?limit=1000", headers=headers, timeout=15)
        if pfx_res.status_code == 200:
            for p in pfx_res.json().get("results", []):
                prefix_str = p.get("prefix") or ""
                vlan_obj = p.get("vlan") or {}
                vid = vlan_obj.get("vid")
                vname = vlan_obj.get("name", "")
                role_str = (p.get("role") or {}).get("name", "")
                site_str = (p.get("site") or {}).get("name", "")
                desc_str = p.get("description") or ""

                ipam_records.append({
                    "prefix_or_subnet": prefix_str,
                    "vlan_id": vid,
                    "vlan_name": vname,
                    "role": role_str,
                    "site": site_str,
                    "description": desc_str
                })
    except Exception as e:
        logger.warning(f"IPAM Prefixes API Error: {e}")

    return inv_records, ipam_records

def fetch_netbox_inventory(netbox_url: str, api_token: str) -> List[Dict[str, Any]]:
    inv_records, _ = fetch_netbox_full_sync(netbox_url, api_token)
    return inv_records