import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.catalog import load_catalog
from core.db_manager import init_db
from ui.sidebar import render_sidebar
from ui.tabs.device_tab import render_device_tab
from ui.tabs.module_tab import render_module_tab
from ui.tabs.rack_tab import render_rack_tab
from ui.tabs.image_tab import render_image_tab
from ui.tabs.batch_tab import render_batch_tab
from ui.tabs.naming_tab import render_naming_tab
from ui.tabs.standards_tab import render_standards_tab

st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

init_db()

# Render sidebar
active_model = render_sidebar()

# Main Page Header
st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Images | Excel Engine | Naming Standards | OmniRoute AI")

# Load catalog
with st.spinner("Synchronizing device-type repository..."):
    catalog = load_catalog()

# Navigation Tabs
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🖥️ Device Types",
    "🧩 Module Types",
    "🗄️ Rack Types",
    "🖼️ Images (Elevation & Module)",
    "📊 Batch Excel Engine",
    "🏷️ Naming Generator",
    "📖 Naming Standards Context"
])

with tab1:
    render_device_tab(catalog, active_model)

with tab2:
    render_module_tab(catalog, active_model)

with tab3:
    render_rack_tab(catalog)

with tab4:
    render_image_tab(catalog)

with tab5:
    render_batch_tab(catalog, active_model)

with tab6:
    render_naming_tab(active_model)

with tab7:
    render_standards_tab(active_model)