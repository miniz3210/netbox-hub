"""
NetBox Universal Library Hub - Main Application
Streamlit UI definition.
Version 2.4.0
"""

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

import streamlit as st
from dotenv import load_dotenv

from config.constants import APP_VERSION, APP_NAME
from core.catalog import get_repo_catalog
from core.db_manager import init_db
from core.exceptions import GitHubCatalogError
from core.session_manager import SessionStateManager as SSM
from ui.components import render_sidebar

st.set_page_config(
    page_title=f"{APP_NAME} v{APP_VERSION}",
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

# Load naming rules on startup using Session State Manager
if not SSM.get_naming_rules_loaded():
    from config.naming_rules import load_naming_rules
    SSM.set_naming_rules(load_naming_rules())
    SSM.set_naming_rules_loaded(True)

active_model = render_sidebar()

# Lazy-load catalog inside session state so it doesn't block the UI on first paint.
catalog = SSM.get_catalog()
if catalog is None:
    try:
        with st.spinner("Indexing NetBox devicetype-library from GitHub..."):
            SSM.set_catalog(get_repo_catalog())
    except GitHubCatalogError as exc:
        logger.exception("GitHub catalog failed to load")
        SSM.set_catalog(None)
        st.error(f"❌ Failed to load official GitHub catalog: {exc}")
    
    catalog = SSM.get_catalog()


def _hardware_catalog_tab(catalog, active_model):
    subtab = st.radio(
        "Hardware Catalog",
        ["Device Types", "Module Types", "Rack Types", "Elevation Images", "Batch Import"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if subtab == "Device Types":
        from ui.tabs.device_tab import render_device_tab as _fn
        _fn(catalog, active_model)
    elif subtab == "Module Types":
        from ui.tabs.module_tab import render_module_tab as _fn
        _fn(catalog, active_model)
    elif subtab == "Rack Types":
        from ui.tabs.rack_tab import render_rack_tab as _fn
        _fn(catalog, active_model)
    elif subtab == "Elevation Images":
        from ui.tabs.image_tab import render_image_tab as _fn
        _fn(catalog)
    else:
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


def _azure_tab(active_model):
    from ui.tabs.azure_tab import render_azure_tab as _fn

    _fn(active_model)


# Tab registry: (label, renderer, requires_catalog)
TABS: List[Tuple[str, Callable, bool]] = [
    ("📦 Hardware Catalog", _hardware_catalog_tab, True),
    ("🌐 IPAM", _ipam_tab, False),
    ("🏷️ Naming", _naming_tab, False),
    ("☁️ Azure VMs", _azure_tab, False),
    ("📋 Standards", _standards_tab, False),
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


# Initialize tab state in session
if "active_tab_index" not in st.session_state:
    st.session_state.active_tab_index = 0

# Prominent main-application header navbar styling for the top-level tab selector.
st.markdown(
    """
    <style>
    div[data-testid="stRadio"] > div[role="radiogroup"][aria-label="Select Tab"],
    div[data-testid="stRadio"] > div[role="radiogroup"] {
        gap: 0.75rem;
        align-items: center;
    }
    div[data-testid="stRadio"] label[data-testid="stWidgetLabel"] { display: none; }
    div[data-testid="stRadio"] label {
        font-size: 17px;
        font-weight: 600;
        padding: 0.55rem 1.1rem;
        border-radius: 10px;
        background-color: rgba(80, 120, 220, 0.10);
        border: 1px solid rgba(80, 120, 220, 0.35);
        transition: background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    div[data-testid="stRadio"] label:hover {
        background-color: rgba(80, 120, 220, 0.20);
        border-color: rgba(80, 120, 220, 0.60);
    }
    div[data-testid="stRadio"] label:has(input[type="radio"]:checked) {
        background: linear-gradient(135deg, #3f6ad8, #274b9e);
        border-color: #3f6ad8;
        color: #ffffff;
        font-weight: 700;
        box-shadow: 0 3px 10px rgba(63, 106, 216, 0.4);
    }
    div[data-testid="stRadio"] label p {
        font-size: 17px;
        font-weight: 600;
    }
    div[data-testid="stRadio"] label:has(input[type="radio"]:checked) p {
        color: #ffffff;
        font-weight: 700;
    }
    div[data-testid="stRadio"] input[type="radio"] {
        accent-color: #3f6ad8;
        width: 17px;
        height: 17px;
        vertical-align: middle;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Use radio buttons for tab selection to preserve state across reruns
tab_labels = [label for label, _, _ in TABS]
selected_tab = st.radio(
    "Select Tab",
    options=tab_labels,
    index=st.session_state.active_tab_index,
    horizontal=True,
    key="tab_selector",
    label_visibility="collapsed"
)

# Update active tab index
st.session_state.active_tab_index = tab_labels.index(selected_tab)

# Render the selected tab
selected_label, selected_renderer, selected_needs_catalog = TABS[st.session_state.active_tab_index]
_render_tab(selected_label, selected_renderer, selected_needs_catalog)
