"""
NetBox Universal Library Hub - Main Application
Streamlit entry point that boots the full app, sidebar, and tabs.
Also exposes a lightweight REST API (under /api/v1) for IPAM provisioning
so external callers (e.g. the JS client in ui/ipam_provisioning.js) can
trigger provisioning without a browser.
"""

import os
import logging
from typing import Any, Dict, List, Optional

import streamlit as st
from dotenv import load_dotenv

# ── Streamlit must be configured BEFORE any other Streamlit call ────────────
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS

st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("netbox-hub")

# ── Optional REST API (Flask).  We attach a tiny Flask blueprint onto the
# Streamlit server's underlying Tornado app via streamlit.server.server,
# but only if the user opts in by setting ENABLE_REST_API=1.
# Keeping it opt-in avoids surprising users who don't need it. ──────────────
def _register_rest_api() -> None:
    if os.getenv("ENABLE_REST_API", "0") != "1":
        return
    try:
        from flask import Flask, jsonify, request
        from flask_cors import CORS
        from core.provisioning_service import IPAMProvisioningService, NetBoxClient
    except Exception as e:  # pragma: no cover - depends on optional deps
        logger.warning("REST API disabled (Flask not installed): %s", e)
        return

    api = Flask("netbox-hub-rest")
    CORS(api)

    netbox_url = os.getenv("NETBOX_URL", "https://ipam.aw.ads")
    netbox_key = os.getenv("NETBOX_API_KEY", "")

    nb_client = NetBoxClient(netbox_url, netbox_key) if netbox_key else None
    site_cache: List[Dict[str, Any]] = []
    prefix_cache: List[Dict[str, Any]] = []
    service = IPAMProvisioningService(
        netbox_client=nb_client,
        site_data=site_cache,
        prefix_data=prefix_cache,
    )

    @api.get("/health")
    def _health():  # noqa: D401
        return jsonify(
            {
                "status": "healthy",
                "netbox_configured": bool(netbox_key),
                "models_configured": len(AVAILABLE_MODELS),
            }
        )

    @api.post("/provision/prefixes")
    def _provision():
        payload: Dict[str, Any] = request.get_json(silent=True) or {}
        site_name = payload.get("site_name")
        vlan_configs = payload.get("vlan_configs") or []
        option_vlans = payload.get("option_vlans")
        include_opts = bool(payload.get("include_option_vlans", True))
        if not site_name:
            return jsonify({"success": False, "error": "site_name is required"}), 400
        result = service.provision_site_prefixes(
            site_name=site_name,
            vlan_configs=vlan_configs,
            option_vlans=option_vlans,
            include_option_vlans=include_opts,
        )
        return jsonify(result)

    @api.post("/import/netbox")
    def _import():
        payload: Dict[str, Any] = request.get_json(silent=True) or {}
        site_name = payload.get("site_name")
        if not site_name:
            return jsonify({"success": False, "error": "site_name is required"}), 400
        result = service.import_to_netbox(
            site_name=site_name,
            vlan_configs=payload.get("vlan_configs") or [],
            option_vlans=payload.get("option_vlans"),
            include_option_vlans=bool(payload.get("include_option_vlans", True)),
            dry_run=bool(payload.get("dry_run", True)),
        )
        return jsonify(result)

    # ── PowerShell agent sync endpoint ──────────────────────────────────────
    # Accepts bulk push from the PowerShell sync agent.
    # Mirrors the original Tornado handler that lived in the legacy app.py
    # (commit feat(api): add REST ingest sync endpoint for PowerShell agents).
    @api.get("/sync/push")
    def _sync_push_health():
        # Health check used by the PowerShell agent
        return jsonify(
            {
                "status": "online",
                "version": APP_VERSION,
                "endpoint": "/api/v1/sync/push",
            }
        )

    @api.post("/sync/push")
    def _sync_push():
        from core.db_manager import (
            save_sites_batch,
            save_ipam_records_batch,
            save_records_batch,
        )

        hub_secret = os.getenv("HUB_SYNC_KEY", "netbox-hub-secret-sync-key")
        payload: Dict[str, Any] = request.get_json(silent=True) or {}

        # Auth: prefer header, fall back to body
        auth_header = request.headers.get("X-Hub-Key", "")
        if hub_secret and auth_header != hub_secret and payload.get("sync_key") != hub_secret:
            return jsonify({"success": False, "error": "Unauthorized: Invalid X-Hub-Key"}), 401

        sites_data    = payload.get("sites")    or []
        vlans_data    = payload.get("vlans")    or []
        prefixes_data = payload.get("prefixes") or []
        devices_data  = payload.get("devices")  or []
        vms_data      = payload.get("vms")      or []

        imported = {"sites": 0, "prefixes": 0, "devices": 0, "vms": 0}

        # 1. Sites
        if sites_data:
            site_records = []
            for s in sites_data:
                s_id   = s.get("id")
                s_name = s.get("name")
                s_slug = s.get("slug")
                if s_id and s_name:
                    site_records.append({"id": s_id, "name": s_name, "slug": s_slug})
            if site_records:
                imported["sites"] = save_sites_batch(site_records, clear_first=True)

        # 2. IPAM (VLANs + Prefixes)
        ipam_records: List[Dict[str, Any]] = []
        for v in vlans_data:
            vid   = v.get("vid")
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
                        "vlan_id":          vid,
                        "vlan_name":        vname,
                        "role":             role_str,
                        "site":             site_str,
                        "description":      desc,
                    })

        for p in prefixes_data:
            pfx_str  = p.get("prefix", "")
            vlan_obj = p.get("vlan") or {}
            vid      = vlan_obj.get("vid")
            vname    = vlan_obj.get("name", "")
            role_val = p.get("role") or {}
            role_str = role_val.get("name", "") if isinstance(role_val, dict) else str(role_val or "")
            site_val = p.get("site") or {}
            site_str = site_val.get("name", "") if isinstance(site_val, dict) else str(site_val or "")
            desc = p.get("description", "")
            if pfx_str and "/" in pfx_str:
                ipam_records.append({
                    "prefix_or_subnet": pfx_str,
                    "vlan_id":          vid,
                    "vlan_name":        vname,
                    "role":             role_str,
                    "site":             site_str,
                    "description":      desc,
                })

        if ipam_records:
            imported["prefixes"] = save_ipam_records_batch(ipam_records, clear_first=True)

        # 3. Inventory (Devices + VMs)
        inv_records: List[Dict[str, Any]] = []
        for d in devices_data:
            name = d.get("name") or ""
            if not name:
                continue
            dtype = (d.get("device_type") or {}).get("model", "")
            mfg   = ((d.get("device_type") or {}).get("manufacturer") or {}).get("name", "")
            role  = (d.get("role") or d.get("device_role") or {}).get("name", "")
            site  = (d.get("site") or {}).get("name", "")
            cluster = (d.get("cluster") or {}).get("name", "")
            desc  = d.get("description") or ""

            combined = f"{name} {role} {dtype} {desc}".lower()
            cat = "hypervisor" if any(h in combined for h in ["esx", "hypervisor", "infhost", "vmhost", "esxi"]) else "device"
            inv_records.append({
                "category":      cat,
                "name":          name,
                "description":   desc,
                "manufacturer":  mfg,
                "model_or_role": dtype or role,
                "site":          site,
                "cluster":       cluster,
            })

        for vm in vms_data:
            name = vm.get("name") or ""
            if not name:
                continue
            role    = (vm.get("role") or {}).get("name", "")
            site    = (vm.get("site") or {}).get("name", "")
            cluster = (vm.get("cluster") or {}).get("name", "")
            desc    = vm.get("description") or ""
            inv_records.append({
                "category":      "vm",
                "name":          name,
                "description":   desc,
                "manufacturer":  "Virtual Machine",
                "model_or_role": role or "VM",
                "site":          site,
                "cluster":       cluster,
            })

        if inv_records:
            counts = save_records_batch(inv_records, clear_first=True)
            imported["devices"] = counts.get("device", 0) + counts.get("hypervisor", 0)
            imported["vms"]     = counts.get("vm", 0)

        return jsonify({"success": True, "imported": imported})

    # Streamlit ≥ 1.30 exposes the running Tornado server; we hook a
    # wildcard rule so /api/v1/* hits Flask while the rest goes to Streamlit.
    # The private attribute name varies across versions (SafeSessionMiddleware
    # wrapper vs bare Application), so we walk the attributes defensively.
    try:
        from streamlit import server as st_server  # type: ignore

        st_server_inst = st_server.get_server()
        if st_server_inst is None:
            raise RuntimeError("Streamlit server is not initialised yet")

        # Walk to the underlying tornado.web.Application.  Different Streamlit
        # versions store it under different attribute names.
        candidate_attrs = [
            "_Application__application",   # older name-mangled form
            "_application",                # bare
            "application",                 # post-refactor
            "app",                         # fallback
        ]
        tornado_app = None
        for attr in candidate_attrs:
            tornado_app = getattr(st_server_inst, attr, None)
            if tornado_app is not None and hasattr(tornado_app, "add_rules"):
                logger.debug("Found Tornado Application at attribute %r", attr)
                break
        if tornado_app is None:
            raise RuntimeError(
                f"Could not locate Tornado Application on streamlit server "
                f"(tried {candidate_attrs})"
            )

        # Register the Flask WSGI app under the /api/v1/* prefix.
        # Tornado's Rule.path_args uses regex group names; \g<api_v1> matches
        # /api/v1/<anything> and is forwarded to the Flask handler.
        tornado_app.add_rules([(r"/api/v1/(?P<api_v1>.*)", api)])
        logger.info("REST API mounted at /api/v1/*  (Flask rules: %s)",
                    [str(r) for r in api.url_map.iter_rules()])
    except Exception as e:  # pragma: no cover
        logger.warning("Could not mount REST API onto Streamlit server: %s", e)


_register_rest_api()

# ── Application boot ───────────────────────────────────────────────────────
from core.catalog import get_repo_catalog
from core.db_manager import init_db
from core.exceptions import GitHubCatalogError
from ui.components import render_sidebar

# Default session state initialization
_DEFAULT_SESSION_STATE = {
    "active_tab": 0,
    "ai_generation_count": 0,
    "last_error": None,
    "model_test_history": {},
}
for _key, _default in _DEFAULT_SESSION_STATE.items():
    st.session_state.setdefault(_key, _default)

# Initialize local database
init_db()

# Sidebar (returns active model selection)
active_model = render_sidebar()

# Catalog with graceful degradation
catalog: Optional[Dict[str, Any]] = None
catalog_error: Optional[str] = None
try:
    catalog = get_repo_catalog()
except GitHubCatalogError as exc:
    catalog_error = str(exc)
    logger.exception("GitHub catalog failed to load")
    st.error(f"❌ Failed to load official GitHub catalog: {exc}")
    st.warning(
        "⚠️ Running in OFFLINE mode — only IPAM / Naming / Standards tabs are available."
    )

# ── Tab registry (lazy-aware: disabled tabs render a friendly notice) ──────
def _catalog_required(_renderer):
    """Decorator that runs the renderer only when the catalog is loaded."""
    def _wrap():
        if catalog is None:
            st.info("🔒 This tab requires the GitHub catalog. Please retry later.")
            return
        _renderer(catalog, active_model)
    return _wrap


@_catalog_required
def _device_tab(catalog, active_model):  # type: ignore[no-redef]
    from ui.tabs.device_tab import render_device_tab as _fn
    _fn(catalog, active_model)


@_catalog_required
def _module_tab(catalog, active_model):  # type: ignore[no-redef]
    from ui.tabs.module_tab import render_module_tab as _fn
    _fn(catalog, active_model)


@_catalog_required
def _rack_tab(catalog, active_model):  # type: ignore[no-redef]
    from ui.tabs.rack_tab import render_rack_tab as _fn
    _fn(catalog, active_model)


@_catalog_required
def _image_tab(catalog, _active_model):  # type: ignore[no-redef]
    from ui.tabs.image_tab import render_image_tab as _fn
    _fn(catalog)


@_catalog_required
def _batch_tab(catalog, active_model):  # type: ignore[no-redef]
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

# Sticky status badge
st.markdown(
    f"<div style='text-align:right;color:#94a3b8;font-family:monospace;"
    f"font-size:0.8rem;'>⚡ NetBox Hub {APP_VERSION}"
    f"{' — catalog: ' + str(len(catalog.get('device_types', []))) + ' device types' if catalog else ' — catalog: offline'}"
    f"</div>",
    unsafe_allow_html=True,
)

tab_objs = st.tabs([label for label, _ in TABS])
for (_label, renderer), tab in zip(TABS, tab_objs):
    with tab:
        try:
            renderer()
        except Exception as exc:  # pragma: no cover - render-time guard
            logger.exception("Tab '%s' crashed", _label)
            st.error(f"❌ Tab '{_label}' failed: {exc}")
