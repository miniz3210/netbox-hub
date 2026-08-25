import streamlit as st
import pandas as pd
from core.db_manager import save_devices_from_csv, get_imported_devices

def render_device_tab(active_model):
    st.subheader("🖥️ NetBox Device Library Hub & CSV Importer")
    
    with st.expander("📤 Upload NetBox Device Export CSV (Database Writeback)", expanded=True):
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
        st.warning("No database records found. Showing fallback mock device example below:")
        mock_data = [
            {"name": "SW-NYC-CORE01", "manufacturer": "Cisco", "device_type": "Catalyst 9300", "role": "Core Switch", "site": "NYC", "status": "Active"},
            {"name": "FW-LDN-PA01", "manufacturer": "Palo Alto Networks", "device_type": "PA-3220", "role": "Firewall", "site": "London", "status": "Active"}
        ]
        st.dataframe(pd.DataFrame(mock_data), use_container_width=True)