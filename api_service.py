import os
import json
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from core.db_manager import (
    save_sites_batch, 
    save_ipam_records_batch, 
    save_records_batch, 
    init_db,
    set_sync_metadata
)
from config.constants import APP_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("netbox-hub-api")

init_db()

app = Flask("netbox-hub-api")
CORS(app)

HUB_SECRET_KEY = os.getenv("HUB_SYNC_KEY", "netbox-hub-secret-sync-key")

@app.route("/api/v1/health", methods=["GET"])
def health():
    return jsonify({"status": "online", "version": APP_VERSION})

@app.route("/api/v1/sync/push", methods=["GET", "POST", "OPTIONS"])
def sync_push():
    if request.method == "OPTIONS":
        return "", 204
    if request.method == "GET":
        return jsonify({"status": "online", "endpoint": "/api/v1/sync/push"})

    auth_header = request.headers.get("X-Hub-Key", "")
    payload = request.get_json(silent=True) or {}

    if HUB_SECRET_KEY and auth_header != HUB_SECRET_KEY and payload.get("sync_key") != HUB_SECRET_KEY:
        return jsonify({"success": False, "error": "Unauthorized: Invalid X-Hub-Key"}), 401

    # Safe key extraction supporting both hyphenated and underscored JSON keys
    sites_data = payload.get("sites") or payload.get("dcim_sites") or []
    vlans_data = payload.get("vlans") or payload.get("ipam_vlans") or []
    prefixes_data = payload.get("prefixes") or payload.get("ipam_prefixes") or []
    devices_data = payload.get("devices") or payload.get("dcim_devices") or []
    vms_data = (
        payload.get("vms") 
        or payload.get("virtualization_virtual-machines") 
        or payload.get("virtualization_virtual_machines") 
        or []
    )

    imported = {"sites": 0, "prefixes": 0, "devices": 0, "vms": 0}

    # 1. Sites
    if sites_data:
        site_records = []
        for s in sites_data:
            s_id = s.get("id")
            s_name = s.get("name")
            s_slug = s.get("slug")
            if s_id and s_name:
                site_records.append({"id": s_id, "name": s_name, "slug": s_slug})
        if site_records:
            imported["sites"] = save_sites_batch(site_records, clear_first=True, source="Agent (PowerShell)")

    # 2. IPAM (VLANs & Prefixes)
    ipam_records = []
    for v in vlans_data:
        vid = v.get("vid")
        vname = v.get("name", "")
        role_val = v.get("role")
        role_str = role_val.get("name", "") if isinstance(role_val, dict) else str(role_val or "")
        site_val = v.get("site")
        site_str = site_val.get("name", "") if isinstance(site_val, dict) else str(site_val or "")
        desc = v.get("description", "")
        for p in (v.get("prefixes", []) or []):
            pfx_str = p.get("prefix", "") if isinstance(p, dict) else str(p)
            if pfx_str and "/" in pfx_str:
                ipam_records.append({
                    "prefix_or_subnet": pfx_str,
                    "vlan_id": vid,
                    "vlan_name": vname,
                    "role": role_str,
                    "site": site_str,
                    "description": desc
                })

    for p in prefixes_data:
        pfx_str = p.get("prefix", "")
        vlan_obj = p.get("vlan") or {}
        vid = vlan_obj.get("vid")
        vname = vlan_obj.get("name", "")
        role_val = p.get("role") or {}
        role_str = role_val.get("name", "") if isinstance(role_val, dict) else str(role_val or "")
        site_val = p.get("site") or {}
        site_str = site_val.get("name", "") if isinstance(site_val, dict) else str(site_val or "")
        desc = p.get("description", "")
        if pfx_str and "/" in pfx_str:
            ipam_records.append({
                "prefix_or_subnet": pfx_str,
                "vlan_id": vid,
                "vlan_name": vname,
                "role": role_str,
                "site": site_str,
                "description": desc
            })

    if ipam_records:
        imported["prefixes"] = save_ipam_records_batch(ipam_records, clear_first=True, source="Agent (PowerShell)")

    # 3. Inventory (Devices & VMs)
    inv_records = []
    for d in devices_data:
        name = d.get("name") or ""
        if not name:
            continue
        dtype = (d.get("device_type") or {}).get("model", "")
        mfg = ((d.get("device_type") or {}).get("manufacturer") or {}).get("name", "")
        role = (d.get("role") or d.get("device_role") or {}).get("name", "")
        site = (d.get("site") or {}).get("name", "")
        cluster = (d.get("cluster") or {}).get("name", "")
        desc = d.get("description") or ""

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

    for vm in vms_data:
        name = vm.get("name") or ""
        if not name:
            continue
        role = (vm.get("role") or {}).get("name", "")
        site = (vm.get("site") or {}).get("name", "")
        cluster = (vm.get("cluster") or {}).get("name", "")
        desc = vm.get("description") or ""

        inv_records.append({
            "category": "vm",
            "name": name,
            "description": desc,
            "manufacturer": "Virtual Machine",
            "model_or_role": role or "VM",
            "site": site,
            "cluster": cluster
        })

    if inv_records:
        counts = save_records_batch(inv_records, clear_first=True, source="Agent (PowerShell)")
        imported["devices"] = counts.get("device", 0) + counts.get("hypervisor", 0)
        imported["vms"] = counts.get("vm", 0)

    set_sync_metadata("ipam", "Agent (PowerShell)")
    set_sync_metadata("naming", "Agent (PowerShell)")

    return jsonify({"success": True, "imported": imported})

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)