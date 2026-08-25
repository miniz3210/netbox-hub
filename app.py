import streamlit as st
from core.catalog import get_repo_catalog
from core.exceptions import GitHubCatalogError
from ui.components import render_sidebar
from ui.tabs.device_tab import render_device_tab
from ui.tabs.module_tab import render_module_tab
from ui.tabs.rack_tab import render_rack_tab
from ui.tabs.image_tab import render_image_tab
from ui.tabs.batch_tab import render_batch_tab
from ui.tabs.naming_tab import render_naming_tab
from ui.tabs.standards_tab import render_standards_tab

st.set_page_config(page_title="NetBox Universal Library Hub", page_icon="⚡", layout="wide")

st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Images | Excel Engine | Naming Standards | OmniRoute AI")

active_model = render_sidebar()

try:
    catalog = get_repo_catalog()
except GitHubCatalogError as e:
    st.error(f"❌ Failed to load official GitHub catalog: {str(e)}")
    st.stop()

t1, t2, t3, t4, t5, t6, t7 = st.tabs([
    "🖥️ Device Types", 
    "🧩 Module Types", 
    "🗄️ Rack Types", 
    "🖼️ Images (Elevation & Module)", 
    "📊 Batch Excel Engine",
    "🏷️ Naming Generator",
    "📖 Naming Standards Context"
])

with t1: render_device_tab(catalog, active_model)
with t2: render_module_tab(catalog, active_model)
with t3: render_rack_tab(catalog, active_model)
with t4: render_image_tab(catalog)
with t5: render_batch_tab(catalog, active_model)
with t6: render_naming_tab(active_model)
with t7: render_standards_tab(active_model)