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

    # Streamlit ≥ 1.30 exposes the running Tornado server; we hook a
    # wildcard rule so /api/v1/* hits Flask while the rest goes to Streamlit.
    try:
        from streamlit import server as st_server  # type: ignore

        server = st_server.get_server()
        if server is not None:
            server._Application__application.add_rules(  # type: ignore[attr-defined]
                [(f"/api/v1/.*", api)]
            )
            logger.info("REST API mounted at /api/v1/*")
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
