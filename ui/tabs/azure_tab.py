"""
Azure VM Import Tab for NetBox Hub
Provides UI for importing Azure Virtual Machine exports into NetBox.
"""

import csv
import io
from pathlib import Path

import pandas as pd
import streamlit as st
from typing import Optional

from core.azure_vm_importer import (
    parse_azure_vm_csv,
    map_azure_to_netbox,
    check_vm_exists_in_db,
    build_vm_ip_index,
    lookup_vm_ip_addresses,
    _strip_azure_prefix,
)
from core.netbox_object_checker import (
    analyze_netbox_objects,
    generate_import_scripts,
    generate_combined_import_bundle,
    get_existing_custom_field_values,
    INSTANCE_TYPE_FIELD,
    RESOURCE_GROUP_FIELD,
    OWNER_FIELD,
    INSTANCE_TYPE_CHOICE_SET,
    RESOURCE_GROUP_CHOICE_SET,
    OWNER_CHOICE_SET,
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
        ### Export from Azure Resource Graph Explorer
        1. In the search bar at the top of the Azure Portal, type and select **Resource Graph Explorer**.
        2. Paste the following KQL query into the query editor:
        """)
        st.code('''Resources
| where type =~ "microsoft.compute/virtualmachines"
| extend 
    vmSize = tostring(properties.hardwareProfile.vmSize),
    osPlatform = case(
        isnotempty(properties.storageProfile.osDisk.osType), tostring(properties.storageProfile.osDisk.osType),
        isnotempty(properties.osProfile.windowsConfiguration), "Windows",
        isnotempty(properties.osProfile.linuxConfiguration), "Linux",
        "Unknown"
    ),
    nicIds = properties.networkProfile.networkInterfaces,
    tagOrg = tostring(tags["Organization"]),
    tagOwner = tostring(tags["Owner"]),
    tagPurpose = tostring(tags["Purpose"]),
    tagRole = tostring(tags["Role"]),
    NetBoxSite = case(
        location =~ "australiaeast", "Azure - Australia East",
        location =~ "australiasoutheast", "Azure - Australia Southeast",
        location =~ "francecentral", "Azure - France Central",
        location =~ "spaincentral", "Azure - Spain Central",
        location =~ "uksouth", "Azure - UK South",
        strcat("UNMAPPED - ", location)
    )
| mv-expand nicId = nicIds
| extend nicIdStr = tostring(nicId.id)
| join kind=leftouter (
    Resources
    | where type =~ "microsoft.network/networkinterfaces"
    | project nicIdStr = id, ipConfigs = properties.ipConfigurations
    | mv-expand ipConfig = ipConfigs
    | extend isPrimaryIp = tostring(ipConfig.properties.primary)
    | where isPrimaryIp =~ "true" or isempty(isPrimaryIp)
    | project 
        nicIdStr,
        PrimaryIPv4 = tostring(ipConfig.properties.privateIPAddress),
        SubnetId = tostring(ipConfig.properties.subnet.id)
    | parse SubnetId with * "/virtualNetworks/" VNetName "/subnets/" SubnetName
) on nicIdStr
| join kind=leftouter (
    ResourceContainers
    | where type =~ "microsoft.resources/subscriptions"
    | project subscriptionId, SubscriptionName = name
) on subscriptionId
| summarize 
    PrimaryIPv4 = take_any(PrimaryIPv4),
    VNet = take_any(VNetName),
    Subnet = take_any(SubnetName)
    by id, name, resourceGroup, location, NetBoxSite, SubscriptionName, subscriptionId, vmSize, osPlatform, tagOrg, tagOwner, tagPurpose, tagRole
| project 
    ['Name'] = name,
    ['Status'] = "active",
    ['Site'] = NetBoxSite,
    ['Tenant'] = SubscriptionName,
    ['Role'] = tagRole,
    ['Platform'] = iff(osPlatform =~ "Windows", "Windows Server", osPlatform),
    ['PrimaryIPv4'] = PrimaryIPv4,
    ['VNet'] = VNet,
    ['Subnet'] = Subnet,
    ['cf_InstanceType'] = vmSize,
    ['cf_ResourceGroups'] = resourceGroup,
    ['cf_Organization'] = tagOrg,
    ['cf_Owner'] = tagOwner,
    ['cf_Purpose'] = tagPurpose,
    ['Azure_Region'] = location,
    ['SubscriptionId'] = subscriptionId''', language="kusto")
        st.markdown("""
        3. Select **Run query**.
        4. Export the results as CSV.
        5. Upload the CSV file using the uploader below.
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
            
            # Check which VMs exist in the database and resolve their NetBox IPs
            st.write("**Checking VMs against database...**")
            ip_index = build_vm_ip_index()
            vm_status_list = []
            ip_matched = 0
            for vm in vm_records:
                existing = check_vm_exists_in_db(vm['name'])
                ip_entry = ip_index.get(vm['name'].strip().lower()) or {}
                primary_ip = ip_entry.get('primary', '')
                assigned_ips = ip_entry.get('assigned', [])
                resolved_ip = primary_ip or (assigned_ips[0] if assigned_ips else '')
                if resolved_ip:
                    ip_matched += 1

                # Flag extra addresses beyond the one shown.
                extra = len(assigned_ips) - 1 if assigned_ips and resolved_ip in assigned_ips else len(assigned_ips)
                ip_display = resolved_ip or '—'
                if resolved_ip and extra > 0:
                    ip_display = f"{resolved_ip} (+{extra})"

                vm_status = {
                    'Name': vm['name'],
                    'Subscription': vm['subscription'],
                    'Resource Group': vm['resource_group'],
                    'Location': vm['location'],
                    'Status': vm['status'],
                    'OS': vm['operating_system'],
                    'Size': vm['size'],
                    'Azure IP': vm.get('public_ip') or '—',
                    'VNet': vm.get('vnet') or '—',
                    'Subnet': vm.get('subnet') or '—',
                    'Owner': vm.get('owner') or '—',
                    'NetBox IP': ip_display,
                    'In Database': '✅ Yes' if existing else '❌ No (Need to add to NetBox)'
                }
                vm_status_list.append(vm_status)
            
            # Create DataFrame with status
            df_with_status = pd.DataFrame(vm_status_list)
            
            # Show data table with color coding
            st.dataframe(
                df_with_status,
                width="stretch",
                height=400
            )
            
            # Show summary
            vms_in_db = sum(1 for vm in vm_status_list if '✅' in vm['In Database'])
            vms_not_in_db = len(vm_status_list) - vms_in_db
            
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.info(f"**✅ Already in Database:** {vms_in_db} VMs")
            with col_b:
                st.warning(f"**❌ Need to Add to NetBox:** {vms_not_in_db} VMs")
            with col_c:
                if ip_index:
                    st.info(f"**🌐 IP Found in NetBox:** {ip_matched} VMs")
                else:
                    st.caption(
                        "🌐 No NetBox backup ingested — upload one under "
                        "*Ingest NetBox Data* to resolve VM IP addresses."
                    )

            if ip_index and ip_matched < len(vm_records):
                st.caption(
                    f"ℹ️ {len(vm_records) - ip_matched} VM(s) have no IP recorded in NetBox. "
                    "NetBox only reports an address when the VM has a Primary IP set or "
                    "an IP assigned to one of its interfaces."
                )
            
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
                st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)

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

                    # map_azure_to_netbox annotates every record with its resolved
                    # NetBox IP, so include it when the column is present.
                    display_cols = ['name', 'subscription', 'resource_group', 'location', 'size', 'operating_system']
                    if 'netbox_ip' in new_vms_df.columns:
                        display_cols.append('netbox_ip')

                    st.dataframe(
                        new_vms_df[display_cols],
                        width="stretch",
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

                    # Generated NetBox VM import scripts for the new VMs
                    st.markdown("#### 📄 Generated NetBox VMs Import Scripts")
                    new_vm_records = [
                        vm for vm in vm_records if vm['name'] in metadata['new_vms']
                    ]
                    instance_type_values = get_existing_custom_field_values(
                        INSTANCE_TYPE_FIELD, INSTANCE_TYPE_CHOICE_SET
                    )
                    resource_group_values = get_existing_custom_field_values(
                        RESOURCE_GROUP_FIELD, RESOURCE_GROUP_CHOICE_SET
                    )
                    owner_values = get_existing_custom_field_values(OWNER_FIELD, OWNER_CHOICE_SET)

                    def canonical_value(value, existing_values):
                        clean = (value or '').strip()
                        matches = {
                            candidate.strip().lower(): candidate.strip()
                            for candidate in existing_values
                            if candidate and candidate.strip()
                        }
                        return matches.get(clean.lower(), clean)

                    vm_import_rows = [[
                        "name", "status", "site", "role", "tenant", "platform",
                        "cf_instance_type", "cf_resource_group", "cf_owner"
                    ]]
                    for vm in new_vm_records:
                        vm_import_rows.append([
                            vm.get('name', ''),
                            vm.get('status', ''),
                            f"Azure - {_strip_azure_prefix(vm.get('location', ''))}" if vm.get('location') else '',
                            vm.get('role', ''),
                            vm.get('subscription', ''),
                            vm.get('operating_system', ''),
                            canonical_value(vm.get('size', ''), instance_type_values),
                            canonical_value(vm.get('resource_group', ''), resource_group_values),
                            canonical_value(vm.get('owner', ''), owner_values),
                        ])
                    csv_buffer = io.StringIO(newline='')
                    csv.writer(csv_buffer, lineterminator='\n').writerows(vm_import_rows)
                    vm_import_script = csv_buffer.getvalue()
                    st.code(vm_import_script, language="csv")
                    st.download_button(
                        "📥 Download NetBox VMs Import CSV",
                        vm_import_script.encode("utf-8"),
                        f"netbox-vms-import-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                        "text/csv",
                        key=f"dl_vms_import_{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}",
                    )
                else:
                    st.info("✅ All VMs from this export already exist in the database.")
                
                
        
        except Exception as e:
            st.error(f"❌ Error processing Azure VM CSV: {str(e)}")
            import traceback
            with st.expander("Error Details"):
                st.code(traceback.format_exc())
    
    else:
        # Show sample data format when no file uploaded.
        # Fictional placeholder data only — no real hostnames, subscriptions,
        # resource groups or routable IPs.
        st.subheader("Sample Azure VM CSV Format")
        st.caption(
            "Illustrative placeholder data. Replace every value with your own "
            "Azure export; the column headers are what the parser relies on."
        )
        sample_data = {
            'NAME': ['VM-APP-001', 'VM-SQL-002', 'VM-WEB-003'],
            'SUBSCRIPTION': ['Example-Prod-Sub-001', 'Example-Prod-Sub-001', 'Example-Dev-Sub-002'],
            'RESOURCE GROUP': ['rg-example-app-prod', 'rg-example-sql-prod', 'rg-example-web-dev'],
            'LOCATION': ['Australia East', 'Australia East', 'UK South'],
            'STATUS': ['Running', 'Running', 'Stopped'],
            'OPERATING SYSTEM': ['Windows', 'Windows', 'Linux'],
            'SIZE': ['Standard_D2s_v3', 'Standard_E4ds_v4', 'Standard_B2ms'],
            'PUBLIC IP ADDRESS': ['-', '-', '198.51.100.10'],
            'DISKS': ['2', '3', '1']
        }
        sample_df = pd.DataFrame(sample_data)
        st.dataframe(sample_df, width="stretch")
        
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
