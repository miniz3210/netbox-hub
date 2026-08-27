"""
NetBox Universal Library Hub - Main Application
Streamlit entry point with REST Ingest Endpoint for Remote Sync Agents.
"""

import os
import logging
from typing import Any, Dict, List, Optional

import streamlit as st
from dotenv import load_dotenv

from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS

st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("netbox-hub")

# ── REST API Ingest Endpoint ────────────────────────────────────────────────
def _register_rest_api() -> None:
    try:
        from flask import Flask, jsonify, request
        from flask_cors import CORS
        from core.db_manager import save_sites_batch, save_ipam_records_batch, save_records_batch
    except Exception as e:
        logger.warning("REST API initialization skipped: %s", e)
        return

    api = Flask("netbox-hub-rest")
    CORS(api)

    HUB_SECRET_KEY = os.getenv("HUB_SYNC_KEY", "netbox-hub-secret-sync-key")

    @api.get("/api/v1/health")
    def _health():
        return jsonify({"status": "online", "version": APP_VERSION})

    @api.post("/api/v1/sync/push")
    def _sync_push():
        auth_header = request.headers.get("X-Hub-Key", "")
        payload: Dict[str, Any] = request.get_json(silent=True) or {}
        
        # Verify sync key if configured
        if HUB_SECRET_KEY and auth_header != HUB_SECRET_KEY and payload.get("sync_key") != HUB_SECRET_KEY:
            return jsonify({"success": False, "error": "Unauthorized: Invalid X-Hub-Key"}), 401

        sites_data = payload.get("sites") or []
        vlans_data = payload.get("vlans") or []
        prefixes_data = payload.get("prefixes") or []
        devices_data = payload.get("devices") or []
        vms_data = payload.get("vms") or []

        imported = {"sites": 0, "prefixes": 0, "devices": 0, "vms": 0}

        # 1. Process Sites
        if sites_data:
            site_records = []
            for s in sites_data:
                s_id = s.get("id")
                s_name = s.get("name")
                s_slug = s.get("slug")
                if s_id and s_name:
                    site_records.append({"id": s_id, "name": s_name, "slug": s_slug})
            if site_records:
                imported["sites"] = save_sites_batch(site_records, clear_first=True)

        # 2. Process VLANs / Prefixes
        ipam_records = []
        for v in vlans_data:
            vid = v.get("vid")
            vname = v.get("name", "")
            role_val = v.get("role")
            role_str = role_val.get("name", "") if isinstance(role_val, dict) else str(role_val or "")
            site_val = v.get("site")
            site_str = site_val.get("name", "") if isinstance(site_val, dict) else str(site_val or "")
            desc = v.get("description", "")
            raw_pfxs = v.get("prefixes", []) or []

            # Handle either string or object prefix lists
            for p in raw_pfxs:
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
            imported["prefixes"] = save_ipam_records_batch(ipam_records, clear_first=True)

        # 3. Process Devices & VMs
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
            counts = save_records_batch(inv_records, clear_first=True)
            imported["devices"] = counts.get("device", 0) + counts.get("hypervisor", 0)
            imported["vms"] = counts.get("vm", 0)

        return jsonify({"success": True, "imported": imported}), 200

    try:
        from streamlit import server as st_server
        server = st_server.get_server()
        if server is not None:
            server._Application__application.add_rules([("/api/v1/.*", api)])
            logger.info("REST Ingestion API mounted at /api/v1/*")
    except Exception as e:
        logger.warning("Could not mount REST API onto Streamlit: %s", e)

_register_rest_api()

# ── Application boot ───────────────────────────────────────────────────────
from core.catalog import get_repo_catalog
from core.db_manager import init_db
from core.exceptions import GitHubCatalogError
from ui.components import render_sidebar

init_db()
active_model = render_sidebar()

catalog: Optional[Dict[str, Any]] = None
try:
    catalog = get_repo_catalog()
except GitHubCatalogError as exc:
    logger.exception("GitHub catalog failed to load")
    st.error(f"❌ Failed to load official GitHub catalog: {exc}")

def _catalog_required(_renderer):
    def _wrap():
        if catalog is None:
            st.info("🔒 This tab requires the GitHub catalog. Please retry later.")
            return
        _renderer(catalog, active_model)
    return _wrap

@_catalog_required
def _device_tab(catalog, active_model):
    from ui.tabs.device_tab import render_device_tab as _fn
    _fn(catalog, active_model)

@_catalog_required
def _module_tab(catalog, active_model):
    from ui.tabs.module_tab import render_module_tab as _fn
    _fn(catalog, active_model)

@_catalog_required
def _rack_tab(catalog, active_model):
    from ui.tabs.rack_tab import render_rack_tab as _fn
    _fn(catalog, active_model)

@_catalog_required
def _image_tab(catalog, _active_model):
    from ui.tabs.image_tab import render_image_tab as _fn
    _fn(catalog)

@_catalog_required
def _batch_tab(catalog, active_model):
    from ui.tabs.batch_tab import render_batch_tab as _fn
    _fn(catalog, active_model)

def _ipam_tab():
    from ui.tabs.ipam_tab import render_ipam_tab as _fn
    _fn(active_model)

def _naming_tab():
    from ui.tabs.naming_tab import render_naming_tab as _fn
    _fn(active_model)

def _standards_tab():
    from ui.tabs.standards_tab import render_standards_tab as _fn
    _fn(active_model)

TABS = [
    ("🖥️ Device Types", _device_tab),
    ("🧩 Module Types", _module_tab),
    ("🗄️ Rack Types", _rack_tab),
    ("🎨 Images", _image_tab),
    ("📦 Batch", _batch_tab),
    ("🌐 IPAM", _ipam_tab),
    ("🏷️ Naming", _naming_tab),
    ("📖 Standards", _standards_tab),
]

st.markdown(
    f"<div style='text-align:right;color:#94a3b8;font-family:monospace;"
    f"font-size:0.8rem;'>⚡ NetBox Hub {APP_VERSION}</div>",
    unsafe_allow_html=True,
)

tab_objs = st.tabs([label for label, _ in TABS])
for (_label, renderer), tab in zip(TABS, tab_objs):
    with tab:
        try:
            renderer()
        except Exception as exc:
            logger.exception("Tab '%s' crashed", _label)
            st.error(f"❌ Tab '{_label}' failed: {exc}")