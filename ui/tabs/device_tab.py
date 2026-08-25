import streamlit as st
import pandas as pd
from core.db_manager import save_devices_from_csv, get_imported_devices

def render_device_tab(catalog, active_model):
    st.subheader("🖥️ NetBox Device Library Hub")
    
    # Standard Catalog Search UI (Restored)
    st.markdown("Search or select device models from the official NetBox Community Device-Type Library.")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        selected_model = st.selectbox("Select Device Model", options=["Select or search..."] + list(catalog), key="dev_cat_select")
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Load / Generate Device Type", type="primary"):
            st.success(f"Loading specification for: {selected_model}")

    st.markdown("---")
    
    # Dynamic Reference Examples with NetBox CSV Upload Integration
    with st.expander("💡 Click to view reference examples (Upload NetBox CSV for Real Data)", expanded=False):
        st.markdown("Upload a NetBox device export CSV below to populate these examples with your real inventory data. If no file is uploaded, fallback dummy examples are displayed.")
        
        uploaded_csv = st.file_uploader("Upload NetBox Device Export CSV", type=["csv"], key="netbox_ref_csv_upload")
        
        real_devices = []
        if uploaded_csv is not None:
            try:
                count = save_devices_from_csv(uploaded_csv)
                real_devices = get_imported_devices()
                st.success(f"Successfully loaded **{len(real_devices)}** real records from uploaded CSV!")
            except Exception as e:
                st.error(f"Error parsing CSV: {e}")
        else:
            real_devices = get_imported_devices()

        if real_devices:
            st.markdown("##### 🟢 Real Data Examples (from Database / Uploaded CSV):")
            real_lines = [f"{d['name']} ({d['manufacturer']} - {d['device_type']}, Site: {d['site']})" for d in real_devices[:10]]
            st.code("\n".join(real_lines), language="text")
        else:
            st.markdown("##### 🟡 Fallback Dummy Examples:")
            st.code(
                "SW-NYC-CORE01 (Cisco Catalyst 9300, Site: NYC)\n"
                "FW-LDN-PA01   (Palo Alto Networks PA-3220, Site: London)",
                language="text"
            )