import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.db_manager import init_db

# ----------------- Safe Tab Imports -----------------
# 1. Device Tab
try:
    from ui.tabs.device_tab import render_device_tab
except ImportError:
    render_device_tab = lambda cat, model: st.warning("Device tab module not found.")

# 2. Module Tab
try:
    from ui.tabs.module_tab import render_module_tab
except ImportError:
    render_module_tab = lambda cat, model: st.warning("Module tab module not found.")

# 3. Rack Tab
try:
    from ui.tabs.rack_tab import render_rack_tab
except ImportError:
    render_rack_tab = lambda cat: st.warning("Rack tab module not found.")

# 4. Image Tab
try:
    from ui.tabs.image_tab import render_image_tab
except ImportError:
    try:
        from ui.tabs.images_tab import render_images_tab as render_image_tab
    except ImportError:
        render_image_tab = lambda cat: st.warning("Image tab module not found.")

# 5. Batch / Excel Tab
try:
    from ui.tabs.batch_tab import render_batch_tab as render_excel_tab
except ImportError:
    try:
        from ui.tabs.excel_tab import render_excel_tab
    except ImportError:
        try:
            from ui.tabs.batch_excel_tab import render_batch_excel_tab as render_excel_tab
        except ImportError:
            render_excel_tab = lambda cat, model: st.warning("Batch Excel tab module not found.")

# 6. Naming Generator Tab
try:
    from ui.tabs.naming_tab import render_naming_tab
except ImportError:
    render_naming_tab = lambda model: st.warning("Naming tab module not found.")

# 7. Rules Context Tab
try:
    from ui.tabs.rules_tab import render_rules_tab
except ImportError:
    try:
        from ui.tabs.standards_tab import render_standards_tab as render_rules_tab
    except ImportError:
        render_rules_tab = lambda model: st.warning("Rules context tab module not found.")

# ----------------- Streamlit Page Configuration -----------------
st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

init_db()

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
    
    selected_preset = st.selectbox(
        "Preset Models",
        options=AVAILABLE_MODELS,
        index=0,
        help="Choose from your configured environment presets."
    )

    custom_model = st.text_input(
        "Custom Model",
        value="",
        placeholder="e.g. gemini/gemini-3.1-flash-lite",
        help="Type any valid model slug here to override the preset on the fly."
    ).strip()

    active_model = custom_model if custom_model else selected_preset

    st.info(f"**Active Model:** `{active_model}`")
    st.caption(f"📡 Routed via OmniRoute (`{OPENROUTER_BASE_URL}`)")
    st.markdown("---")
    st.caption(f"⚡ NetBox Hub `{APP_VERSION}`")

# ----------------- Main Page Header -----------------
st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Images | Excel Engine | Naming Standards | OmniRoute AI")

# Safe Catalog Loading
catalog = {"manufacturers": [], "device_types": [], "module_types": [], "rack_types": []}
try:
    import core.catalog as cat_module
    with st.spinner("Synchronizing device-type repository..."):
        if hasattr(cat_module, "load_catalog"):
            catalog = cat_module.load_catalog()
        elif hasattr(cat_module, "load_github_catalog"):
            catalog = cat_module.load_github_catalog()
        elif hasattr(cat_module, "get_catalog"):
            catalog = cat_module.get_catalog()
except Exception as e:
    st.warning(f"Could not load GitHub repository catalog: {e}")

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