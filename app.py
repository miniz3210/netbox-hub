import inspect
import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.db_manager import init_db
from core.catalog import load_catalog

# Safe Tab Imports
try:
    from ui.tabs.device_tab import render_device_tab
except ImportError:
    render_device_tab = lambda cat, model: st.warning("Device tab module not found.")

try:
    from ui.tabs.module_tab import render_module_tab
except ImportError:
    render_module_tab = lambda cat, model: st.warning("Module tab module not found.")

try:
    from ui.tabs.rack_tab import render_rack_tab
except ImportError:
    render_rack_tab = lambda cat, model: st.warning("Rack tab module not found.")

try:
    from ui.tabs.image_tab import render_image_tab
except ImportError:
    try:
        from ui.tabs.images_tab import render_images_tab as render_image_tab
    except ImportError:
        render_image_tab = lambda cat: st.warning("Image tab module not found.")

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

try:
    from ui.tabs.naming_tab import render_naming_tab
except ImportError:
    render_naming_tab = lambda model: st.warning("Naming tab module not found.")

try:
    from ui.tabs.rules_tab import render_rules_tab
except ImportError:
    try:
        from ui.tabs.standards_tab import render_standards_tab as render_rules_tab
    except ImportError:
        render_rules_tab = lambda model: st.warning("Rules context tab module not found.")

st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

init_db()

# Fixed bottom-left version badge
st.markdown(f"""
    <style>
    .block-container {{
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }}
    .stSelectbox label, .stTextInput label {{
        font-weight: 600;
    }}
    .fixed-version-corner {{
        position: fixed;
        bottom: 14px;
        left: 18px;
        background-color: rgba(15, 23, 42, 0.85);
        color: #94a3b8;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.78rem;
        font-family: monospace;
        border: 1px solid rgba(255, 255, 255, 0.1);
        z-index: 999999;
        pointer-events: none;
    }}
    </style>
    <div class="fixed-version-corner">⚡ NetBox Hub {APP_VERSION}</div>
""", unsafe_allow_html=True)

# Sidebar AI Selector
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

st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Images | Excel Engine | Naming Standards | OmniRoute AI")

# Load Catalog
with st.spinner("Synchronizing device-type repository..."):
    catalog = load_catalog()

def dispatch_tab(render_fn, cat, model):
    try:
        sig = inspect.signature(render_fn)
        if len(sig.parameters) == 2:
            render_fn(cat, model)
        elif len(sig.parameters) == 1:
            first_name = list(sig.parameters.keys())[0]
            if "model" in first_name:
                render_fn(model)
            else:
                render_fn(cat)
        else:
            render_fn()
    except Exception:
        try:
            render_fn(cat, model)
        except TypeError:
            try:
                render_fn(cat)
            except TypeError:
                render_fn(model)

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
    dispatch_tab(render_device_tab, catalog, active_model)

with tab2:
    dispatch_tab(render_module_tab, catalog, active_model)

with tab3:
    dispatch_tab(render_rack_tab, catalog, active_model)

with tab4:
    dispatch_tab(render_image_tab, catalog, active_model)

with tab5:
    dispatch_tab(render_excel_tab, catalog, active_model)

with tab6:
    dispatch_tab(render_naming_tab, catalog, active_model)

with tab7:
    dispatch_tab(render_rules_tab, catalog, active_model)