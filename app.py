import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.catalog import load_github_catalog
from core.db_manager import init_db
from ui.tabs.device_tab import render_device_tab
from ui.tabs.module_tab import render_module_tab
from ui.tabs.rack_tab import render_rack_tab
from ui.tabs.image_tab import render_image_tab
from ui.tabs.excel_tab import render_excel_tab
from ui.tabs.naming_tab import render_naming_tab
from ui.tabs.rules_tab import render_rules_tab

# Initialize Streamlit Page Configuration
st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize SQLite database schema
init_db()

# Custom Header / App Styling
st.markdown("""
    <style>
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    .stSelectbox label, .stTextInput label {
        font-weight: 600;
    }
    </style>
""", unsafe_allow_html=True)

# ----------------- Sidebar -----------------
with st.sidebar:
    st.markdown("### ⚙️ AI Engine Selection")
    
    # 1. Preset Dropdown
    selected_preset = st.selectbox(
        "Preset Models",
        options=AVAILABLE_MODELS,
        index=0,
        help="Choose from your configured environment presets."
    )

    # 2. Custom Model Text Box
    custom_model = st.text_input(
        "Custom Model",
        value="",
        placeholder="e.g. gemini/gemini-3.1-flash-lite",
        help="Type any valid model slug here to override the preset on the fly."
    ).strip()

    # Custom model takes precedence if typed
    active_model = custom_model if custom_model else selected_preset

    st.info(f"**Active Model:** `{active_model}`")
    st.caption(f"📡 Routed via OmniRoute (`{OPENROUTER_BASE_URL}`)")
    st.markdown("---")
    st.caption(f"⚡ NetBox Hub `{APP_VERSION}`")

# ----------------- Main Page Header -----------------
st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Images | Excel Engine | Naming Standards | OmniRoute AI")

# Load GitHub Device-Type Catalog into memory
with st.spinner("Synchronizing device-type repository..."):
    catalog = load_github_catalog()

# ----------------- Navigation Tabs -----------------
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
    render_excel_tab(catalog, active_model)

with tab6:
    render_naming_tab(active_model)

with tab7:
    render_rules_tab(active_model)