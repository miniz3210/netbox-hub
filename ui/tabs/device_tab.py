import streamlit as st
import pandas as pd
from core.db_manager import save_devices_from_csv, get_imported_devices

def render_device_tab(catalog, active_model):
    st.subheader("🖥️ NetBox Device Library Hub & CSV Importer")
    
    with st.expander("📤 Upload NetBox Device Export CSV (Database Writeback)", expanded=False):
        st.markdown("Upload a standard NetBox device export CSV to persist inventory records into the local database.")
        uploaded_file = st.file_uploader("Choose NetBox CSV Export", type=["csv"], key="netbox_csv_upload")
        
        if uploaded_file is not None:
            if st.button("📥 Import & Write to Database"):
                try:
                    count = save_devices_from_csv(uploaded_file)
                    st.success(f"Successfully imported and saved **{count}** devices to the database!")
                except Exception as ex:
                    st.error(f"Import Error: {ex}")

    st.markdown("---")
    st.markdown("### 📊 Database Inventory View (Real Data / Fallback Mock)")
    
    devices = get_imported_devices()
    if devices:
        st.info(f"Loaded **{len(devices)}** real records from database storage.")
        df_dev = pd.DataFrame(devices)
        st.dataframe(df_dev[["name", "manufacturer", "device_type", "role", "site", "status"]], use_container_width=True)
    else:
        st.warning("No database records found in local storage. Upload a CSV above or use the standard catalog search below.")
        
    st.markdown("---")
    st.markdown("### 🔍 NetBox Community Catalog Search")
    # Catalog search helper UI
    search_query = st.text_input("Search Catalog Models", placeholder="e.g. ProLiant, Catalyst, PA-3220")
    if search_query:
        matches = [m for m in catalog if search_query.lower() in m.lower()][:10]
        st.write("Matching models:", matches)