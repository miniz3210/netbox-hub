"""
NetBox Universal Library Hub - Main Application
Streamlit UI definition.
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import streamlit as st
from dotenv import load_dotenv

from config.constants import APP_VERSION
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

# Load naming rules on startup to ensure standards are always current
if "naming_rules_loaded" not in st.session_state:
    from config.naming_rules import load_naming_rules
    st.session_state["naming_rules"] = load_naming_rules()
    st.session_state["naming_rules_loaded"] = True

active_model = render_sidebar()

# Lazy-load catalog inside session state so it doesn't block the UI on first paint.
if "catalog" not in st.session_state:
    try:
        with st.spinner("Indexing NetBox devicetype-library from GitHub..."):
            st.session_state["catalog"] = get_repo_catalog()
    except GitHubCatalogError as exc:
        logger.exception("GitHub catalog failed to load")
        st.session_state["catalog"] = None
        st.error(f"❌ Failed to load official GitHub catalog: {exc}")

catalog: Optional[Dict[str, Any]] = st.session_state.get("catalog")


def _device_tab(catalog, active_model):
    from ui.tabs.device_tab import render_device_tab as _fn

    _fn(catalog, active_model)


def _module_tab(catalog, active_model):
    from ui.tabs.module_tab import render_module_tab as _fn

    _fn(catalog, active_model)


def _rack_tab(catalog, active_model):
    from ui.tabs.rack_tab import render_rack_tab as _fn

    _fn(catalog, active_model)


def _image_tab(catalog, _active_model):
    from ui.tabs.image_tab import render_image_tab as _fn

    _fn(catalog)


def _batch_tab(catalog, active_model):
    from ui.tabs.batch_tab import render_batch_tab as _fn

    _fn(catalog, active_model)


def _ipam_tab(active_model):
    from ui.tabs.ipam_tab import render_ipam_tab as _fn

    _fn(active_model)


def _naming_tab(active_model):
    from ui.tabs.naming_tab import render_naming_tab as _fn

    _fn(active_model)


def _standards_tab(active_model):
    from ui.tabs.standards_tab import render_standards_tab as _fn

    _fn(active_model)


# Tab registry: (label, renderer, requires_catalog)
TABS: List[Tuple[str, Callable, bool]] = [
    ("🖥️ Device Types", _device_tab, True),
    ("🧩 Module Types", _module_tab, True),
    ("🗄️ Rack Types", _rack_tab, True),
    ("🎨 Images", _image_tab, True),
    ("📦 Batch", _batch_tab, True),
    ("🌐 IPAM", _ipam_tab, False),
    ("🏷️ Naming", _naming_tab, False),
    ("📖 Standards", _standards_tab, False),
]


def _render_tab(label: str, renderer: Callable, requires_catalog: bool) -> None:
    """Render a single tab with error handling and catalog guard."""
    try:
        if requires_catalog:
            if catalog is None:
                st.info("🔒 This tab requires the GitHub catalog. Please retry later.")
                return
            renderer(catalog, active_model)
        else:
            renderer(active_model)
    except Exception as exc:
        logger.exception("Tab '%s' crashed", label)
        st.error(f"❌ Tab '{label}' failed: {exc}")


def _render_version_badge() -> None:
    """Render floating version badge in bottom left."""
    st.markdown(
        f"""
        <style>
        .netbox-hub-version-badge {{
            position: fixed !important;
            bottom: 12px !important;
            left: 12px !important;
            background: #1e293b !important;
            color: #38bdf8 !important;
            padding: 5px 12px !important;
            border-radius: 6px !important;
            font-size: 12px !important;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace !important;
            font-weight: 600 !important;
            border: 1px solid #334155 !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3) !important;
            z-index: 2147483647 !important;
            pointer-events: none !important;
        }}
        </style>
        <div class="netbox-hub-version-badge">📦 NetBox Hub v{APP_VERSION}</div>
        """,
        unsafe_allow_html=True,
    )


tab_objs = st.tabs([label for label, _, _ in TABS])
for (_label, _renderer, _needs_catalog), tab in zip(TABS, tab_objs):
    with tab:
        _render_tab(_label, _renderer, _needs_catalog)

_render_version_badge()
