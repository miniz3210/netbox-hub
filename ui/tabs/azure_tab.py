"""
Azure VM Import Tab for NetBox Hub
Provides UI for importing Azure Virtual Machine exports into NetBox.
"""

import streamlit as st
import pandas as pd
from pathlib import Path
from typing import Optional

from core.azure_vm_importer import (
    parse_azure_vm_csv,
    map_azure_to_netbox,
    save_azure_vms_to_db,
    generate_netbox_import_summary,
    check_vm_exists_in_db
)


def render_azure_tab(active_model=None):
    """Render the Azure VM import tab in Streamlit UI."""
    
    st.header("☁️ Azure Virtual Machine Import")
    st.write("""
    Import Azure Virtual Machines from CSV exports. This tool will:
    1. **Check** if VMs already exist in the database
    2. **Map** Azure data to NetBox objects:
        - **SUBSCRIPTION** → Tenant (Azure)
        - **RESOURCE GROUP** → Custom Field: Resource Group
        - **LOCATION** → Site (Cloud)
        - **SIZE** → Custom Field: Instance Type
        - **OPERATING SYSTEM** → Platform
    3. **Import** VMs into NetBox Hub database
    """)
    
    # Instructions section
    with st.expander("📋 How to Export Azure VMs", expanded=False):
        st.markdown("""
        ### Export from Azure Portal
        1. Navigate to **Virtual Machines** in Azure Portal
        2. Click **Export to CSV** button at the top of the VM list
        3. Save the CSV file to your computer
        4. Upload the file using the uploader below
        
        ### Required CSV Columns
        - `NAME`: VM name
        - `SUBSCRIPTION`: Azure subscription
        - `RESOURCE GROUP`: Resource group name
        - `LOCATION`: Azure region (e.g., "Australia East")
        - `STATUS`: Running, Stopped, etc.
        - `OPERATING SYSTEM`: Windows or Linux
        - `SIZE`: VM SKU (e.g., "Standard_E2as_v4")
        - `PUBLIC IP ADDRESS`: Public IP or "-"
        - `DISKS`: Number of disks
        - `RESOURCE LINK`: Azure portal URL
        """)
    
    # File uploader
    st.subheader("1️⃣ Upload Azure VM CSV Export")
    uploaded_file = st.file_uploader(
        "Select Azure VM CSV file",
        type=["csv"],
        help="Upload the CSV file exported from Azure Portal or PowerShell"
    )
    
    # Session state for parsed data
    if 'azure_vms_parsed' not in st.session_state:
        st.session_state.azure_vms_parsed = None
    if 'azure_vms_mapped' not in st.session_state:
        st.session_state.azure_vms_mapped = None
    if 'azure_metadata' not in st.session_state:
        st.session_state.azure_metadata = None
    
    # Parse and preview
    if uploaded_file is not None:
        try:
            # Save uploaded file temporarily
            temp_path = Path("data/temp_azure_upload.csv")
            temp_path.parent.mkdir(exist_ok=True)
            
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            # Parse the CSV
            with st.spinner("Parsing Azure VM data..."):
                vm_records, warnings = parse_azure_vm_csv(str(temp_path))
                st.session_state.azure_vms_parsed = vm_records
            
            # Show warnings if any
            if warnings:
                with st.expander("⚠️ Parsing Warnings", expanded=True):
                    for warning in warnings:
                        st.warning(warning)
            
            # Show preview
            st.success(f"✅ Parsed {len(vm_records)} Azure VMs")
            
            st.subheader("2️⃣ Preview Azure VMs")
            
            # Convert to DataFrame for display
            df_preview = pd.DataFrame(vm_records)
            
            # Show summary statistics
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total VMs", len(vm_records))
            with col2:
                running_count = sum(1 for vm in vm_records if vm.get('status', '').lower() == 'running')
                st.metric("Running", running_count)
            with col3:
                unique_subscriptions = len(set(vm.get('subscription', '') for vm in vm_records))
                st.metric("Subscriptions", unique_subscriptions)
            with col4:
                unique_locations = len(set(vm.get('location', '') for vm in vm_records))
                st.metric("Locations", unique_locations)
            
            # Show data table
            st.dataframe(
                df_preview[['name', 'subscription', 'resource_group', 'location', 'status', 
                           'operating_system', 'size']],
                use_container_width=True,
                height=300
            )
            
            # Map to NetBox format
            st.subheader("3️⃣ Map to NetBox Format")
            
            if st.button("🔄 Map Azure Data to NetBox", type="primary"):
                with st.spinner("Mapping Azure VMs to NetBox format..."):
                    netbox_records, metadata = map_azure_to_netbox(vm_records)
                    st.session_state.azure_vms_mapped = netbox_records
                    st.session_state.azure_metadata = metadata
                
                st.success("✅ Mapping complete!")
            
            # Show mapping results
            if st.session_state.azure_vms_mapped and st.session_state.azure_metadata:
                metadata = st.session_state.azure_metadata
                
                # Show summary
                st.info(generate_netbox_import_summary(metadata))
                
                # Show mapped data preview
                st.subheader("NetBox VM Records Preview")
                df_netbox = pd.DataFrame(st.session_state.azure_vms_mapped)
                st.dataframe(
                    df_netbox[['name', 'manufacturer', 'model_or_role', 'site', 'cluster']],
                    use_container_width=True,
                    height=300
                )
                
                # Existing VMs warning
                if metadata['existing_vms']:
                    st.warning(f"""
                    ⚠️ **{len(metadata['existing_vms'])} VMs already exist** in the database.
                    You can choose to update them or skip them during import.
                    """)
                    
                    with st.expander("View Existing VMs"):
                        for existing in metadata['existing_vms'][:10]:
                            st.text(f"- {existing['name']}")
                        if len(metadata['existing_vms']) > 10:
                            st.text(f"... and {len(metadata['existing_vms']) - 10} more")
                
                # Import section
                st.subheader("4️⃣ Import to Database")
                
                col1, col2 = st.columns([2, 1])
                with col1:
                    update_existing = st.checkbox(
                        "Update existing VMs",
                        value=False,
                        help="If checked, existing VMs will be updated with new data. Otherwise, they will be skipped."
                    )
                
                if st.button("💾 Import VMs to NetBox Hub Database", type="primary"):
                    with st.spinner("Importing Azure VMs to database..."):
                        stats = save_azure_vms_to_db(
                            st.session_state.azure_vms_mapped,
                            update_existing=update_existing,
                            source="Azure CSV Import"
                        )
                    
                    # Show results
                    st.success("✅ Import completed!")
                    
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("Inserted", stats['inserted'], delta=stats['inserted'])
                    with col2:
                        st.metric("Updated", stats['updated'], delta=stats['updated'])
                    with col3:
                        st.metric("Skipped", stats['skipped'])
                    with col4:
                        if stats['errors'] > 0:
                            st.metric("Errors", stats['errors'], delta=-stats['errors'], delta_color="inverse")
                        else:
                            st.metric("Errors", stats['errors'])
                    
                    st.balloons()
                    
                    # Clear session state
                    if st.button("🔄 Import Another File"):
                        st.session_state.azure_vms_parsed = None
                        st.session_state.azure_vms_mapped = None
                        st.session_state.azure_metadata = None
                        st.rerun()
        
        except Exception as e:
            st.error(f"❌ Error processing Azure VM CSV: {str(e)}")
            import traceback
            with st.expander("Error Details"):
                st.code(traceback.format_exc())
    
    else:
        # Show sample data format when no file uploaded
        st.subheader("Sample Azure VM CSV Format")
        sample_data = {
            'NAME': ['ANZAPP002', 'AUPDJDEI01', 'AU-AZ-WLC02'],
            'SUBSCRIPTION': ['AW-MS-Prod-AUEast-001', 'JDE-AuEast-001', 'Corp-SharedServices-AuEast-001'],
            'RESOURCE GROUP': ['rg-anzapp002', 'rg-app-jde-production-aueast-001', 'Rg-Infra-WLC-SharedServices-AuEast-001'],
            'LOCATION': ['Australia East', 'Australia East', 'Australia East'],
            'STATUS': ['Running', 'Running', 'Running'],
            'OPERATING SYSTEM': ['Windows', 'Windows', 'Linux'],
            'SIZE': ['Standard_E2as_v4', 'Standard_E2ads_v5', 'Standard_F4s_v2'],
            'PUBLIC IP ADDRESS': ['-', '-', '203.0.113.10'],
            'DISKS': ['2', '2', '1']
        }
        sample_df = pd.DataFrame(sample_data)
        st.dataframe(sample_df, use_container_width=True)
        
        st.download_button(
            "📄 Download Sample CSV Template",
            sample_df.to_csv(index=False).encode('utf-8'),
            "azure-vms-sample.csv",
            "text/csv",
            help="Download a sample CSV file with the correct format"
        )
    
    # Additional features section
    st.divider()
    st.subheader("🔧 Additional Actions")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 🔍 Check VM Status")
        vm_name_check = st.text_input("Enter VM name to check", placeholder="e.g., ANZAPP002")
        if st.button("Check VM"):
            if vm_name_check:
                existing = check_vm_exists_in_db(vm_name_check)
                if existing:
                    st.success(f"✅ VM **{vm_name_check}** exists in database")
                    st.json(existing)
                else:
                    st.info(f"ℹ️ VM **{vm_name_check}** not found in database")
            else:
                st.warning("Please enter a VM name")
    
    with col2:
        st.markdown("### 📊 Export Database")
        st.write("Export current VM inventory from database")
        if st.button("Export VMs to CSV"):
            try:
                from core.db_manager import DB_PATH
                import sqlite3
                
                conn = sqlite3.connect(DB_PATH)
                query = """
                    SELECT name, category, description, manufacturer, model_or_role, 
                           site, cluster, imported_at
                    FROM inventory_records
                    WHERE category = 'vm'
                    ORDER BY name
                """
                df_export = pd.read_sql_query(query, conn)
                conn.close()
                
                st.download_button(
                    "📥 Download VMs CSV",
                    df_export.to_csv(index=False).encode('utf-8'),
                    f"netbox-vms-export-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                    "text/csv"
                )
                st.success(f"✅ Exported {len(df_export)} VMs")
            except Exception as e:
                st.error(f"Error exporting: {str(e)}")
