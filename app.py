"""
NetBox Universal Library Hub - Main Application
Streamlit entry point with Native Tornado REST Ingest Endpoint.
"""

import os
import json
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

# ── Native Tornado REST Ingest Handler ──────────────────────────────────────
def _mount_native_tornado_api():
    try:
        import tornado.web
        from tornado.routing import PathMatches, Rule
        from core.db_manager import save_sites_batch, save_ipam_records_batch, save_records_batch

        class NetBoxSyncPushHandler(tornado.web.RequestHandler):
            def set_default_headers(self):
                self.set_header("Access-Control-Allow-Origin", "*")
                self.set_header("Access-Control-Allow-Headers", "content-type, x-hub-key")
                self.set_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

            def options(self, *args, **kwargs):
                self.set_status(204)
                self.finish()

            def get(self, *args, **kwargs):
                self.write({"status": "online", "version": APP_VERSION, "endpoint": "/api/v1/sync/push"})

            def post(self, *args, **kwargs):
                try:
                    hub_secret = os.getenv("HUB_SYNC_KEY", "netbox-hub-secret-sync-key")
                    auth_header = self.request.headers.get("X-Hub-Key", "")
                    
                    body = self.request.body.decode('utf-8')
                    payload = json.loads(body) if body else {}
                    
                    if hub_secret and auth_header != hub_secret and payload.get("sync_key") != hub_secret:
                        self.set_status(401)
                        self.write({"success": False, "error": "Unauthorized: Invalid X-Hub-Key"})
                        return

                    # Support both standard keys and PowerShell script keys (dcim_sites, ipam_vlans, etc.)
                    sites_data = payload.get("sites") or payload.get("dcim_sites") or []
                    vlans_data = payload.get("vlans") or payload.get("ipam_vlans") or []
                    prefixes_data = payload.get("prefixes") or payload.get("ipam_prefixes") or []
                    devices_data = payload.get("devices") or payload.get("dcim_devices") or []
                    vms_data = payload.get("vms") or payload.get("virtualization_virtual_machines") or []

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
                            imported["sites"] = save_sites_batch(site_records, clear_first=True)

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
                        imported["prefixes"] = save_ipam_records_batch(ipam_records, clear_first=True)

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
                        counts = save_records_batch(inv_records, clear_first=True)
                        imported["devices"] = counts.get("device", 0) + counts.get("hypervisor", 0)
                        imported["vms"] = counts.get("vm", 0)

                    self.write({"success": True, "imported": imported})
                except Exception as e:
                    self.set_status(500)
                    self.write({"success": False, "error": str(e)})

        server = None
        try:
            from streamlit.web.server.server import Server
            server = Server.get_current()
        except Exception:
            try:
                from streamlit.server.server import Server
                server = Server.get_current()
            except Exception:
                pass

        if server is not None:
            tornado_app = server._app
            # Insert top-priority rules matching with and without trailing slash
            tornado_app.wildcard_router.rules.insert(
                0,
                Rule(PathMatches(r"^/api/v1/sync/push/?$"), NetBoxSyncPushHandler)
            )
            tornado_app.wildcard_router.rules.insert(
                0,
                Rule(PathMatches(r"^/api/v1/health/?$"), NetBoxSyncPushHandler)
            )
            logger.info("Native Tornado REST handler registered for /api/v1/sync/push")
    except Exception as e:
        logger.warning("Could not mount native Tornado REST handler: %s", e)

_mount_native_tornado_api()

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
            st.error(f"❌ Tab '%s' failed: %s", _label, exc)