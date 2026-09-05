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
from core.netbox_object_checker import (
    analyze_netbox_objects,
    generate_import_scripts,
    generate_combined_import_bundle
)


def render_azure_tab(active_model=None):
    """Render the Azure VM import tab in Streamlit UI."""
    
    st.header("☁️ Azure Virtual Machine Analysis for NetBox")
    st.write("""
    Analyze Azure Virtual Machines and identify which ones need to be added to NetBox. This tool will:
    1. **Check** if VMs already exist in the NetBox Hub database
    2. **Identify** new VMs that need to be added to your NetBox instance
    3. **Show** the required NetBox objects that need to be created:
        - **SUBSCRIPTION** → Tenant (Azure)
        - **RESOURCE GROUP** → Custom Field: Resource Group
        - **LOCATION** → Site (Cloud)
        - **SIZE** → Custom Field: Instance Type
        - **OPERATING SYSTEM** → Platform
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
    if 'azure_object_analysis' not in st.session_state:
        st.session_state.azure_object_analysis = None
    
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
            
            # Check which VMs exist in database
            st.write("**Checking VMs against database...**")
            vm_status_list = []
            for vm in vm_records:
                existing = check_vm_exists_in_db(vm['name'])
                vm_status = {
                    'Name': vm['name'],
                    'Subscription': vm['subscription'],
                    'Resource Group': vm['resource_group'],
                    'Location': vm['location'],
                    'Status': vm['status'],
                    'OS': vm['operating_system'],
                    'Size': vm['size'],
                    'In Database': '✅ Yes' if existing else '❌ No (Need to add to NetBox)'
                }
                vm_status_list.append(vm_status)
            
            # Create DataFrame with status
            df_with_status = pd.DataFrame(vm_status_list)
            
            # Show data table with color coding
            st.dataframe(
                df_with_status,
                use_container_width=True,
                height=400
            )
            
            # Show summary
            vms_in_db = sum(1 for vm in vm_status_list if '✅' in vm['In Database'])
            vms_not_in_db = len(vm_status_list) - vms_in_db
            
            col_a, col_b = st.columns(2)
            with col_a:
                st.info(f"**✅ Already in Database:** {vms_in_db} VMs")
            with col_b:
                st.warning(f"**❌ Need to Add to NetBox:** {vms_not_in_db} VMs")
            
            # NetBox Objects Summary
            st.subheader("3️⃣ NetBox Objects Required")
            
            if st.button("📋 Analyze NetBox Requirements", type="primary"):
                with st.spinner("Analyzing NetBox requirements..."):
                    netbox_records, metadata = map_azure_to_netbox(vm_records)
                    st.session_state.azure_vms_mapped = netbox_records
                    st.session_state.azure_metadata = metadata
                    st.session_state.azure_object_analysis = analyze_netbox_objects(metadata)

                st.success("✅ Analysis complete!")

            # Show NetBox requirements
            if st.session_state.azure_vms_mapped and st.session_state.azure_metadata:
                metadata = st.session_state.azure_metadata
                analysis = st.session_state.get("azure_object_analysis") or analyze_netbox_objects(metadata)

                st.markdown("### 📊 NetBox Objects to Create")
                st.caption(
                    "Checked against the local NetBox database (backup / CSV ingest). "
                    "Only objects reported as missing need to be imported."
                )

                total_missing = sum(len(d["missing"]) for d in analysis.values())
                if total_missing:
                    st.warning(f"**{total_missing} objects** are missing from NetBox and need to be created.")
                else:
                    st.success("✅ All required NetBox objects already exist.")

                # Per-category counters: existing vs missing
                summary_rows = []
                for data in analysis.values():
                    summary_rows.append({
                        "Object Type": data["label"],
                        "NetBox Object": data["netbox_object"],
                        "Required": data["total"],
                        "✅ Exists": len(data["existing"]),
                        "❌ Missing": len(data["missing"]),
                    })
                st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

                # Detail per category
                for key, data in analysis.items():
                    missing = data["missing"]
                    existing = data["existing"]
                    icon = "❌" if missing else "✅"
                    header = f"{icon} {data['label']} — {len(missing)} missing / {len(existing)} existing"
                    with st.expander(header, expanded=bool(missing)):
                        det_a, det_b = st.columns(2)
                        with det_a:
                            st.markdown("**❌ Missing (needs import)**")
                            if missing:
                                for value in missing:
                                    st.text(f"  • {value}")
                            else:
                                st.caption("None — all present in NetBox.")
                        with det_b:
                            st.markdown("**✅ Already in NetBox**")
                            if existing:
                                for value in existing[:20]:
                                    st.text(f"  • {value}")
                                if len(existing) > 20:
                                    st.caption(f"... and {len(existing) - 20} more")
                            else:
                                st.caption("None found in the local NetBox data.")

                # Import payloads for the missing objects only
                st.divider()
                st.markdown("### 📥 Generated NetBox Import Scripts")

                scripts = generate_import_scripts(analysis)
                if not scripts:
                    st.info("Nothing to import — every required object already exists in NetBox.")
                else:
                    st.caption("Copy each block into its matching NetBox import form.")
                    for key, script in scripts.items():
                        with st.expander(f"📄 {script['label']} ({script['count']} missing)", expanded=True):
                            st.caption(script["instructions"])
                            lang = "csv" if script["format"] == "csv" else "text"
                            st.code(script["content"], language=lang)
                            st.download_button(
                                f"📥 Download {script['label']}",
                                script["content"].encode("utf-8"),
                                script["filename"],
                                "text/plain",
                                key=f"dl_{key}",
                            )

                    bundle = generate_combined_import_bundle(scripts)
                    st.download_button(
                        "📦 Download All Import Scripts (bundle)",
                        bundle.encode("utf-8"),
                        f"netbox-import-bundle-{pd.Timestamp.now().strftime('%Y%m%d')}.txt",
                        "text/plain",
                        key="dl_bundle",
                    )
                
                # VMs that need to be added
                st.divider()
                st.markdown("### 🆕 VMs to Add to NetBox")
                
                if metadata['new_vms']:
                    st.success(f"**{len(metadata['new_vms'])} new VMs** need to be added to your NetBox instance:")
                    
                    # Create downloadable list
                    new_vms_df = pd.DataFrame([
                        vm for vm in vm_records if vm['name'] in metadata['new_vms']
                    ])
                    
                    st.dataframe(
                        new_vms_df[['name', 'subscription', 'resource_group', 'location', 'size', 'operating_system']],
                        use_container_width=True,
                        height=300
                    )
                    
                    # Download button for new VMs
                    csv_new = new_vms_df.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        "📥 Download List of New VMs",
                        csv_new,
                        f"new-vms-for-netbox-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                        "text/csv",
                        help="Download CSV of VMs that need to be added to NetBox"
                    )
                else:
                    st.info("✅ All VMs from this export already exist in the database.")
                
                # Already in database
                if metadata['existing_vms']:
                    st.markdown("### ✅ VMs Already in Database")
                    st.info(f"**{len(metadata['existing_vms'])} VMs** are already tracked in NetBox Hub database.")
                    
                    with st.expander("View VMs already in database"):
                        for existing in metadata['existing_vms'][:20]:
                            st.text(f"  • {existing['name']}")
                        if len(metadata['existing_vms']) > 20:
                            st.text(f"  ... and {len(metadata['existing_vms']) - 20} more")
        
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
                    st.info(f"ℹ️ VM **{vm_name_check}** not found in database - needs to be added to NetBox")
            else:
                st.warning("Please enter a VM name")
    
    with col2:
        st.markdown("### 📊 Export Database")
        st.write("Export current VM inventory from NetBox Hub database")
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
                    f"netbox-hub-vms-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                    "text/csv"
                )
                st.success(f"✅ Ready to export {len(df_export)} VMs")
            except Exception as e:
                st.error(f"Error exporting: {str(e)}")
