import logging
from typing import Optional, Dict, List

import streamlit as st

# Project imports
from core.catalog import get_repo_catalog
from core.exceptions import GitHubCatalogError
from core.db_manager import init_db
from ui.components import render_sidebar
from ui.tabs.device_tab import render_device_tab
from ui.tabs.module_tab import render_module_tab
from ui.tabs.rack_tab import render_rack_tab
from ui.tabs.image_tab import render_image_tab
from ui.tabs.batch_tab import render_batch_tab
from ui.tabs.ipam_tab import render_ipam_tab
from ui.tabs.naming_tab import render_naming_tab
from ui.tabs.standards_tab import render_standards_tab

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session-state defaults (ensure keys exist across reruns)
# ---------------------------------------------------------------------------
DEFAULT_SESSION_STATE = {
    "selected_device": None,
    "selected_site": None,
    "active_tab": 0,
    "ai_generation_count": 0,
    "last_error": None,
}
for key, default in DEFAULT_SESSION_STATE.items():
    st.session_state.setdefault(key, default)


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "About": "NetBox Hub – AI-powered NetBox provisioning toolkit",
        "Get Help": "https://github.com/your-org/netbox-hub",
        "Report a bug": "https://github.com/your-org/netbox-hub/issues",
    },
)


# ---------------------------------------------------------------------------
# App boot
# ---------------------------------------------------------------------------
init_db()

st.title("⚡ NetBox Universal Library Hub")
st.caption(
    "Device Types | Module Types | Rack Types | Images | Excel Engine | "
    "IPAM Provisioning | Naming Standards | OmniRoute AI"
)

# ---------------------------------------------------------------------------
# Sidebar – resolves active_model first (must run before any tab logic)
# ---------------------------------------------------------------------------
try:
    active_model = render_sidebar()
except ValueError as e:
    st.error(f"❌ {e}")
    st.stop()


# ---------------------------------------------------------------------------
# GitHub catalog – graceful degradation if GitHub is unavailable
# ---------------------------------------------------------------------------
catalog: Optional[Dict[str, List[str]]] = None
try:
    catalog = get_repo_catalog()
except GitHubCatalogError as e:
    log.exception("GitHub catalog failed to load")
    st.error(f"❌ Failed to load official GitHub catalog: {e}")
    st.warning(
        "Tabs requiring the catalog are disabled. "
        "IPAM, Naming, and Standards tabs remain available."
    )

needs_catalog = catalog is not None


# ---------------------------------------------------------------------------
# Tab definitions – (label, requires_catalog, renderer_lambda)
# ---------------------------------------------------------------------------
TABS = [
    ("🖥️ Device Types",      needs_catalog, lambda: render_device_tab(catalog, active_model)),
    ("🧩 Module Types",      needs_catalog, lambda: render_module_tab(catalog, active_model)),
    ("🗄️ Rack Types",        needs_catalog, lambda: render_rack_tab(catalog, active_model)),
    ("🖼️ Images",            needs_catalog, lambda: render_image_tab(catalog)),
    ("📊 Batch Excel Engine",needs_catalog, lambda: render_batch_tab(catalog, active_model)),
    ("🌐 IPAM Provisioning", True,          lambda: render_ipam_tab(active_model)),
    ("🏷️ Naming Generator",  True,          lambda: render_naming_tab(active_model)),
    ("📖 Naming Standards",  True,          lambda: render_standards_tab(active_model)),
]

labels = [t[0] for t in TABS]
chosen = st.tabs(labels)

# ---------------------------------------------------------------------------
# Render only the selected tab – avoids running all 8 renderers on every reload
# ---------------------------------------------------------------------------
for (label, enabled, renderer), tab in zip(TABS, chosen):
    with tab:
        if enabled:
            try:
                renderer()
            except Exception as exc:
                log.exception("Tab `%s` crashed", label)
                st.error(f"❌ Tab `{label}` crashed: {exc}")
        else:
            st.info(f"🔒 `{label}` is unavailable — GitHub catalog failed to load.")