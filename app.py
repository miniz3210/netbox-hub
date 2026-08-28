"""
NetBox Universal Library Hub - Main Application
Streamlit UI definition.
"""

import logging
from typing import Any, Dict, Optional

import streamlit as st
from dotenv import load_dotenv

from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS
from core.catalog import get_repo_catalog
from core.db_manager import init_db
from core.exceptions import GitHubCatalogError
from ui.components import render_sidebar

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