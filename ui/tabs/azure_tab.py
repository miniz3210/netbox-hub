"""
Azure VM Import Tab for NetBox Hub
Provides UI for importing Azure Virtual Machine exports into NetBox.
"""

import csv
import html
import io
import json
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
    _build_netbox_tags,
)
from core.azure_csv_manager import (
    save_azure_csv_upload,
    get_azure_csv_upload,
    clear_azure_csv_upload,
    has_azure_csv_upload,
)
from core.netbox_object_checker import (
    analyze_netbox_objects,
    generate_import_scripts,
    generate_combined_import_bundle,
    get_existing_custom_field_values,
    get_existing_roles,
    INSTANCE_TYPE_FIELD,
    RESOURCE_GROUP_FIELD,
    OWNER_FIELD,
    INSTANCE_TYPE_CHOICE_SET,
    RESOURCE_GROUP_CHOICE_SET,
    OWNER_CHOICE_SET,
)


def render_azure_tab(active_model=None):
    """Render the Azure VM import tab in Streamlit UI."""
    
    st.header("☁️ Azure VM Import for NetBox")
    
    # KQL query stored for reference.
    kql_query = '''Resources
| where type =~ "microsoft.compute/virtualmachines"
| extend 
    vmSize = tostring(properties.hardwareProfile.vmSize),
    baseOsType = case(
        isnotempty(properties.storageProfile.osDisk.osType), tostring(properties.storageProfile.osDisk.osType),
        isnotempty(properties.osProfile.windowsConfiguration), "Windows",
        isnotempty(properties.osProfile.linuxConfiguration), "Linux",
        "Unknown"
    ),
    // 1. Exact OS Name reported by the Azure VM Guest Agent (e.g. "Windows Server 2019 Datacenter")
    guestOsName = tostring(properties.extended.instanceView.osName),
    guestOsVersion = tostring(properties.extended.instanceView.osVersion),
    // 2. Fallback to image reference if agent hasn't reported
    imgOffer = tostring(properties.storageProfile.imageReference.offer),
    imgSku = tostring(properties.storageProfile.imageReference.sku),
    imgVersion = coalesce(tostring(properties.storageProfile.imageReference.exactVersion), tostring(properties.storageProfile.imageReference.version)),
    nicIds = properties.networkProfile.networkInterfaces,
    // Extract Tags
    tagOrg = coalesce(tostring(tags["Organization"]), tostring(tags["organization"])),
    tagOwner = coalesce(tostring(tags["Owner"]), tostring(tags["owner"])),
    tagPurpose = coalesce(tostring(tags["Purpose"]), tostring(tags["purpose"])),
    tagRole = coalesce(tostring(tags["Role"]), tostring(tags["role"])),
    tagApp = coalesce(tostring(tags["Application"]), tostring(tags["application"])),
    tagEnv = coalesce(tostring(tags["Environment"]), tostring(tags["environment"])),
    tagCostCentre = coalesce(tostring(tags["CostCentre"]), tostring(tags["costcentre"])),
    tagCrit = coalesce(tostring(tags["BusinessCriticality"]), tostring(tags["businesscriticality"])),
    tagDeploy = coalesce(tostring(tags["Deploymentmethod"]), tostring(tags["deploymentmethod"])),
    tagBackup = coalesce(tostring(tags["Backup"]), tostring(tags["backup"])),
    RawTags = tostring(tags),
    // Exact NetBox Site matching
    NetBoxSite = case(
        location =~ "australiaeast", "Azure - Australia East",
        location =~ "australiasoutheast", "Azure - Australia Southeast",
        location =~ "francecentral", "Azure - France Central",
        location =~ "spaincentral", "Azure - Spain Central",
        location =~ "uksouth", "Azure - UK South",
        strcat("UNMAPPED - ", location)
    )
| extend
    // Formats Operating System exactly as shown on the Azure Portal Overview: "Windows (Windows Server 2019 Datacenter)"
    ExactOperatingSystem = case(
        isnotempty(guestOsName), strcat(baseOsType, " (", guestOsName, ")"),
        imgOffer =~ "sql2019-ws2019", "Windows (Windows Server 2019 Datacenter)",
        imgOffer =~ "sql2022-ws2022", "Windows (Windows Server 2022 Datacenter)",
        imgOffer =~ "windowsserver", strcat("Windows (Windows Server ", imgSku, ")"),
        isnotempty(imgSku), strcat(baseOsType, " (", imgOffer, " ", imgSku, ")"),
        baseOsType
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
| join kind=leftouter (
    ResourceContainers
    | where type =~ "microsoft.resources/subscriptions/resourcegroups"
    | project subscriptionId, resourceGroup = tolower(name), ExactResourceGroupName = name
) on subscriptionId, resourceGroup
| summarize 
    PrimaryIPv4 = take_any(PrimaryIPv4),
    VNet = take_any(VNetName),
    Subnet = take_any(SubnetName)
    by id, name, ExactResourceGroupName, resourceGroup, location, NetBoxSite, SubscriptionName, subscriptionId, vmSize, baseOsType, ExactOperatingSystem, guestOsName, guestOsVersion, imgOffer, imgSku, imgVersion, tagOrg, tagOwner, tagPurpose, tagRole, tagApp, tagEnv, tagCostCentre, tagCrit, tagDeploy, tagBackup, RawTags
| project 
    ['Name'] = name,
    ['Status'] = "active",
    ['Site'] = NetBoxSite,
    ['Tenant'] = SubscriptionName,
    ['Role'] = coalesce(tagRole, tagApp),
    ['Operating_System'] = ExactOperatingSystem,
    ['Platform'] = iff(baseOsType =~ "Windows", "Windows Server", baseOsType),
    ['PrimaryIPv4'] = PrimaryIPv4,
    ['VNet'] = VNet,
    ['Subnet'] = Subnet,
    ['cf_InstanceType'] = vmSize,
    ['cf_ResourceGroups'] = coalesce(ExactResourceGroupName, resourceGroup),
    ['cf_Organization'] = tagOrg,
    ['cf_Owner'] = tagOwner,
    ['cf_Purpose'] = tagPurpose,
    ['Tag_Application'] = tagApp,
    ['Tag_Environment'] = tagEnv,
    ['Tag_CostCentre'] = tagCostCentre,
    ['Tag_BusinessCriticality'] = tagCrit,
    ['Tag_DeploymentMethod'] = tagDeploy,
    ['Tag_Backup'] = tagBackup,
    ['Tags'] = RawTags'''
    
    # File uploader section with status indicator
    st.subheader("1️⃣ Upload Azure VM CSV Export")
    
    # Check if there's a saved upload
    saved_upload = get_azure_csv_upload()
    vm_count = saved_upload['row_count'] if saved_upload else 0
    
    status_tag = f"🟢 ({vm_count} VMs in DB)" if vm_count > 0 else "⚪ (No data)"
    
    # Initialize uploaded_file to None by default
    uploaded_file = None
    
    with st.expander(f"📥 Ingest Azure VM Data (CSV Export) {status_tag}", expanded=False):
        # Show description
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
            - **APPLICATION** → Role
        """)
        
        if saved_upload:
            st.markdown(f"**DB Status:** `Source CSV`")
        
        st.markdown("---")
        
        # Instructions section
        with st.expander("📋 How to Export Azure VMs", expanded=False):
            st.markdown("""
            ### Export from Azure Resource Graph Explorer
            1. In the search bar at the top of the Azure Portal, type and select **Resource Graph Explorer**.
            2. Paste the following KQL query into the query editor:
            """)

            # Global clipboard-copy handler (works in HTTP/HTTPS and iframes).
            st.html("""
            <script>
            function copyTextToClipboard(text) {
                if (navigator.clipboard && window.isSecureContext) {
                    navigator.clipboard.writeText(text).catch(function() {
                        fallbackCopy(text);
                    });
                } else {
                    fallbackCopy(text);
                }
            }

            function fallbackCopy(text) {
                var textArea = document.createElement("textarea");
                textArea.value = text;
                textArea.style.top = "0";
                textArea.style.left = "0";
                textArea.style.position = "fixed";
                textArea.style.opacity = "0";
                document.body.appendChild(textArea);
                textArea.focus();
                textArea.select();
                try {
                    document.execCommand('copy');
                } catch (err) {
                    console.error('Fallback clipboard copy failed:', err);
                }
                document.body.removeChild(textArea);
            }
            </script>
            """)
            
            st.code(kql_query, language="kusto")

            st.markdown("""
            3. Select **Run query**.
            4. Export the results as CSV.
            5. Upload the CSV file using the uploader below.
            """)
        
        st.markdown("---")
        
        # Show saved CSV info
        if saved_upload:
            st.caption(f"**Azure CSV export:** `{saved_upload['filename']}` - Uploaded: {saved_upload['uploaded_at']}")
        
        # Consolidated upload section
        c_up, c_clr, c_ref = st.columns([3, 1, 1])
        with c_up:
            uploaded_file = st.file_uploader(
                "Upload Azure VM CSV file",
                type=["csv"],
                help="Upload the CSV file exported from Azure Resource Graph Explorer",
                key="azure_csv_uploader",
                label_visibility="collapsed"
            )
        with c_clr:
            if saved_upload:
                if st.button("🗑️ Clear Azure CSV", key="clear_azure_csv_btn", help="Clear Azure CSV data", use_container_width=True):
                    clear_azure_csv_upload()
                    # Clear session state
                    st.session_state.azure_vms_parsed = None
                    st.session_state.azure_vms_mapped = None
                    st.session_state.azure_metadata = None
                    st.session_state.azure_object_analysis = None
                    st.session_state.azure_dedup_cache = None
                    st.session_state.azure_preview_table_df = None
                    st.session_state.azure_last_uploaded_file = None
                    st.rerun()
        with c_ref:
            if st.button("🔄 Refresh", key="ref_azure_btn", use_container_width=True, help="Reload the view"):
                # Clear preview table cache to force rebuild
                st.session_state.azure_preview_table_df = None
                st.rerun()
    
    # Session state for parsed data
    if 'azure_vms_parsed' not in st.session_state:
        st.session_state.azure_vms_parsed = None
    if 'azure_vms_mapped' not in st.session_state:
        st.session_state.azure_vms_mapped = None
    if 'azure_metadata' not in st.session_state:
        st.session_state.azure_metadata = None
    if 'azure_object_analysis' not in st.session_state:
        st.session_state.azure_object_analysis = None
    if 'azure_dedup_cache' not in st.session_state:
        st.session_state.azure_dedup_cache = None
    if 'azure_last_uploaded_file' not in st.session_state:
        st.session_state.azure_last_uploaded_file = None

    # Load saved CSV on page refresh if no new upload
    if uploaded_file is None and saved_upload and st.session_state.azure_vms_parsed is None:
        with st.spinner("Loading saved CSV data..."):
            try:
                # Convert saved DataFrame to VM records format
                df_saved = saved_upload['csv_data']
                vm_records = df_saved.to_dict('records')
                
                st.session_state.azure_vms_parsed = vm_records
                st.session_state.azure_parsed_vms_table = vm_records
                st.session_state.azure_raw_vm_records = vm_records
            except Exception as e:
                st.error(f"Error loading saved CSV: {e}")
                import traceback
                st.code(traceback.format_exc())

    # Parse and preview
    # Check if this is a new file (different from last processed file)
    current_file_id = None
    if uploaded_file is not None:
        # Create a unique identifier for the uploaded file
        current_file_id = f"{uploaded_file.name}_{uploaded_file.size}"
    
    is_new_file = (uploaded_file is not None and 
                   uploaded_file != "loaded_from_db" and 
                   current_file_id != st.session_state.azure_last_uploaded_file)
    
    if is_new_file:
        try:
            # Clear previous state before parsing begins.
            st.session_state.azure_vms_parsed = None
            st.session_state.azure_vms_mapped = None
            st.session_state.azure_metadata = None
            st.session_state.azure_object_analysis = None
            st.session_state.azure_dedup_cache = None
            st.session_state.azure_preview_table_df = None

            # Save uploaded file temporarily
            temp_path = Path("data/temp_azure_upload.csv")
            temp_path.parent.mkdir(exist_ok=True)
            
            with open(temp_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            # Parse the CSV
            with st.spinner("Parsing Azure VM data..."):
                vm_records, warnings = parse_azure_vm_csv(str(temp_path))
                st.session_state.azure_vms_parsed = vm_records
                st.session_state.azure_parsed_vms_table = vm_records
            
            # Save to database for persistence
            df_for_save = pd.DataFrame(vm_records)
            save_result = save_azure_csv_upload(uploaded_file.name, df_for_save)
            
            # Mark this file as processed
            st.session_state.azure_last_uploaded_file = current_file_id
            
            # Trigger auto-refresh to reload with saved data
            st.rerun()

            # Convert to DataFrame for display
            df_preview = pd.DataFrame(vm_records)
            # Store raw VM records for later use
            st.session_state.azure_raw_vm_records = vm_records

            # Build a clean, enriched export dataset.
            export_records = []
            for vm in vm_records:
                tag_names = [t['name'] for t in _build_netbox_tags(vm)]
                record = {
                    'name': vm.get('name', ''),
                    'subscription': vm.get('subscription', ''),
                    'resource_group': vm.get('resource_group', ''),
                    'location': vm.get('location', ''),
                    'status': vm.get('status', ''),
                    'operating_system': vm.get('operating_system', ''),
                    'platform_value': vm.get('platform_value', ''),
                    'size': vm.get('size', ''),
                    'primary_ip': vm.get('public_ip', ''),
                    'vnet': vm.get('vnet', ''),
                    'subnet': vm.get('subnet', ''),
                    'owner': vm.get('owner', ''),
                    'role': vm.get('role', ''),
                    'tag_environment': vm.get('tag_environment', ''),
                    'tag_cost_centre': vm.get('tag_cost_centre', ''),
                    'tag_business_criticality': vm.get('tag_business_criticality', ''),
                    'tag_deployment_method': vm.get('tag_deployment_method', ''),
                    'tag_backup': vm.get('tag_backup', ''),
                    'netbox_tags': ', '.join(tag_names),
                    'source': vm.get('source', ''),
                    'imported_at': vm.get('imported_at', ''),
                }
                export_records.append(record)

            df_export = pd.DataFrame(export_records)

            # Export parsed dataset as CSV
            export_csv_col, _ = st.columns([1, 3])
            with export_csv_col:
                st.download_button(
                    "📥 Download Parsed VMs CSV",
                    df_export.to_csv(index=False).encode("utf-8"),
                    f"azure-vms-parsed-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                    "text/csv",
                    help="Download the cleaned Azure VM dataset as CSV",
                )

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

                tag_names = [t['name'] for t in _build_netbox_tags(vm)]
                if tag_names:
                    chips = "".join(
                        f'<span class="nb-tag-chip" title="{html.escape(n)}">{html.escape(n)}</span>'
                        for n in tag_names
                    )
                    netbox_tags_html = f'<div class="nb-tags-cell">{chips}</div>'
                else:
                    netbox_tags_html = '—'

                vm_status = {
                    'Name': vm['name'],
                    'Subscription': vm['subscription'],
                    'Resource Group': vm['resource_group'],
                    'Location': vm['location'],
                    'Status': vm['status'],
                    'Operating System': vm.get('operating_system') or '—',
                    'Role': vm.get('role') or vm.get('tag_application') or '—',
                    'Size': vm['size'],
                    'Azure IP': vm.get('public_ip') or '—',
                    'VNet': vm.get('vnet') or '—',
                    'Subnet': vm.get('subnet') or '—',
                    'Owner': vm.get('owner') or '—',
                    'NetBox Tags': netbox_tags_html,
                    'NetBox IP': ip_display,
                    'In Database': '✅ Yes' if existing else '❌ No (Need to add to NetBox)'
                }

                vm_status_list.append(vm_status)
            
            # Store the actual preview table DataFrame with proper column names
            st.session_state.azure_preview_table_df = pd.DataFrame(vm_status_list)
            
            table_columns = list(vm_status_list[0].keys()) if vm_status_list else []
            table_headers = "".join(f"<th>{html.escape(column)}</th>" for column in table_columns)
            table_rows = []
            for row in vm_status_list:
                cells = []
                for column in table_columns:
                    value = row[column]
                    if column == "NetBox Tags" and value != "—":
                        cell = f'<td class="nb-tags-column"><div class="nb-tags-cell">{value}</div></td>'
                    else:
                        cell = f"<td>{html.escape(str(value))}</td>"
                    cells.append(cell)
                table_rows.append(f"<tr>{''.join(cells)}</tr>")

            st.markdown(
                f"""
                <style>
                .nb-table-scroll {{
                    max-width: 100%;
                    max-height: 400px;
                    overflow: auto;
                    border: 1px solid rgba(128, 128, 128, 0.25);
                }}
                .nb-vm-table {{
                    border-collapse: collapse;
                    min-width: 1900px;
                    width: max-content;
                    font-size: 12px;
                }}
                .nb-vm-table th, .nb-vm-table td {{
                    padding: 6px 8px;
                    border-bottom: 1px solid rgba(128, 128, 128, 0.2);
                    text-align: left;
                    vertical-align: top;
                    white-space: nowrap;
                }}
                .nb-vm-table th {{
                    position: sticky;
                    top: 0;
                    z-index: 1;
                    background: var(--background-color);
                }}
                .nb-vm-table .nb-tags-column {{
                    min-width: 320px;
                    max-width: 520px;
                    white-space: normal;
                }}
                .nb-tags-cell {{
                    display: flex;
                    flex-wrap: wrap;
                    gap: 4px;
                    min-width: 320px;
                    white-space: normal;
                    line-height: 1.4;
                }}
                .nb-tag-chip {{
                    display: inline-flex;
                    align-items: center;
                    padding: 2px 8px;
                    font-size: 11px;
                    border: 1px solid rgba(128, 128, 128, 0.35);
                    border-radius: 9999px;
                    background: rgba(128, 128, 128, 0.08);
                    max-width: 100%;
                }}
                </style>
                <div class="nb-table-scroll">
                    <table class="nb-vm-table">
                        <thead><tr>{table_headers}</tr></thead>
                        <tbody>{''.join(table_rows)}</tbody>
                    </table>
                </div>
                """,
                unsafe_allow_html=True,
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
            
            if "analyze_netbox_clicked" not in st.session_state:
                st.session_state["analyze_netbox_clicked"] = False
            if st.button("📋 Analyze NetBox Requirements", key="btn_analyze_netbox"):
                st.session_state["analyze_netbox_clicked"] = True
                st.rerun()
            
            if st.session_state.get("analyze_netbox_clicked", False):
                with st.spinner("Analyzing NetBox requirements..."):
                    netbox_records, metadata = map_azure_to_netbox(vm_records)
                    st.session_state.azure_vms_mapped = netbox_records
                    st.session_state.azure_metadata = metadata
                    st.session_state.azure_object_analysis = analyze_netbox_objects(metadata)

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

                # Detail per category - display in 2 columns
                analysis_items = list(analysis.items())
                obj_col1, obj_col2 = st.columns(2)
                
                for idx, (key, data) in enumerate(analysis_items):
                    missing = data["missing"]
                    existing = data["existing"]
                    icon = "❌" if missing else "✅"
                    header = f"{icon} {data['label']} — {len(missing)} missing, {len(existing)} exist"
                    
                    # Alternate between columns
                    target_col = obj_col1 if idx % 2 == 0 else obj_col2
                    
                    with target_col:
                        with st.expander(header, expanded=False):
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
                        with st.expander(f"📄 {script['label']} ({script['count']} missing)", expanded=False):
                            st.caption(script["instructions"])
                            lang = "csv" if script["format"] == "csv" else "text"
                            st.code(script["content"], language=lang)

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
                    
                    # Enrich VM records with data from NetBox database if available
                    for vm in new_vm_records:
                        db_vm = check_vm_exists_in_db(vm['name'])
                        if db_vm:
                            # Merge owner field from database if not present in CSV
                            if not vm.get('owner') and db_vm.get('owner'):
                                vm['owner'] = db_vm['owner']
                            # Also check custom_fields for owner
                            if not vm.get('owner') and isinstance(db_vm.get('custom_fields'), dict):
                                cf_owner = db_vm['custom_fields'].get('owner')
                                if cf_owner:
                                    vm['owner'] = cf_owner
                    instance_type_values = get_existing_custom_field_values(
                        INSTANCE_TYPE_FIELD, INSTANCE_TYPE_CHOICE_SET
                    )
                    resource_group_values = get_existing_custom_field_values(
                        RESOURCE_GROUP_FIELD, RESOURCE_GROUP_CHOICE_SET
                    )
                    owner_values = get_existing_custom_field_values(OWNER_FIELD, OWNER_CHOICE_SET)
                    role_values = get_existing_roles()

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
                        "cf_instance_type", "cf_resource_group", "owner",
                        "cf_application", "cf_environment", "cf_cost_centre",
                        "cf_business_criticality", "cf_deployment_method", "cf_backup", "cf_operating_system", "tags"
                    ]]
                    for vm in new_vm_records:
                        vm_import_rows.append([
                            vm.get('name', ''),
                            vm.get('status', ''),
                            f"Azure - {_strip_azure_prefix(vm.get('location', ''))}" if vm.get('location') else '',
                            canonical_value(vm.get('role', '') or vm.get('tag_application', ''), role_values),
                            vm.get('subscription', ''),
                            vm.get('platform_value') or vm.get('operating_system', ''),
                            canonical_value(vm.get('size', ''), instance_type_values),
                            canonical_value(vm.get('resource_group', ''), resource_group_values),
                            canonical_value(vm.get('owner', ''), owner_values),
                            vm.get('tag_application', ''),
                            vm.get('tag_environment', ''),
                            vm.get('tag_cost_centre', ''),
                            vm.get('tag_business_criticality', ''),
                            vm.get('tag_deployment_method', ''),
                            vm.get('tag_backup', ''),
                            vm.get('tag_operating_system', ''),
                            vm.get('tags', ''),
                        ])
                    csv_buffer = io.StringIO(newline='')
                    csv.writer(csv_buffer, lineterminator='\n').writerows(vm_import_rows)
                    vm_import_script = csv_buffer.getvalue()
                    
                    with st.expander(f"📄 NetBox VMs Import CSV ({len(new_vm_records)} VMs)", expanded=True):
                        st.caption("Copy this CSV into NetBox's Virtual Machine bulk import form.")
                        st.code(vm_import_script, language="csv")

                    st.download_button(
                        "📥 Download NetBox VMs Import CSV",
                        vm_import_script.encode("utf-8"),
                        f"netbox-vms-import-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                        "text/csv",
                        key=f"dl_vms_import_{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}",
                    )
                
                st.divider()
                st.markdown("### 🔍 Netbox Helper")
                vm_search_input = st.text_input("Enter VM Name / Hostname", placeholder="e.g., VM-APP-001", key="netbox_vm_search_query", label_visibility="collapsed")
                
                if vm_search_input and vm_search_input.strip():
                    clean_target = vm_search_input.strip().lower()
                    
                    db_vm = check_vm_exists_in_db(vm_search_input)
                    csv_vm = None
                    
                    preview_df = st.session_state.get('azure_preview_table_df')
                    matched_row = None
                    if preview_df is not None and not preview_df.empty:
                        name_col = next((c for c in preview_df.columns if c.strip().lower() in ['name', 'vm name', 'hostname']), None)
                        if name_col:
                            matches = preview_df[preview_df[name_col].astype(str).str.strip().str.lower() == clean_target]
                            if not matches.empty:
                                matched_row = matches.iloc[0].to_dict()
                    
                    if matched_row:
                        csv_vm = matched_row
                    
                    if not db_vm and not csv_vm:
                        st.warning(f"VM '{vm_search_input.strip()}' not found in NetBox database or uploaded Azure CSV.")
                    else:
                        # Debug: Show what data sources are available
                        with st.expander("🔍 Debug: Data Sources", expanded=False):
                            st.write("**Database VM Data:**")
                            if db_vm:
                                st.json(db_vm)
                                st.write("**Custom Fields Extracted:**")
                                cf_debug = db_vm.get('custom_fields', {})
                                if cf_debug:
                                    st.json(cf_debug)
                                    st.write(f"Available keys: {list(cf_debug.keys())}")
                                else:
                                    st.write("No custom fields found")
                            else:
                                st.write("None")
                            st.write("**CSV Row Data:**")
                            if matched_row:
                                st.json(matched_row)
                            else:
                                st.write("None")
                        def extract_val(source_dict, candidate_keys):
                            """Extract value from dict, return None if not found (allows or-chaining)"""
                            if not source_dict or not isinstance(source_dict, dict):
                                return None
                            norm_dict = {str(k).strip().lower(): v for k, v in source_dict.items()}
                            for k in candidate_keys:
                                k_norm = k.strip().lower()
                                if k_norm in norm_dict:
                                    val = norm_dict[k_norm]
                                    if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                                        return str(val).strip()
                            return None
                        
                        def format_tags(tags_val):
                            if not tags_val:
                                return "—"
                            if isinstance(tags_val, list):
                                tag_names = [t.get('name', str(t)) if isinstance(t, dict) else str(t) for t in tags_val if t]
                                return ", ".join(tag_names) if tag_names else "—"
                            if isinstance(tags_val, str) and tags_val.strip() not in ["", "nan", "None", "—"]:
                                # Strip HTML tags if present
                                import re
                                clean_text = re.sub(r'<[^>]+>', ', ', tags_val)  # Replace tags with comma+space
                                # Clean up multiple commas and spaces
                                clean_text = re.sub(r',\s*,', ',', clean_text)  # Remove duplicate commas
                                clean_text = re.sub(r'^\s*,\s*|\s*,\s*$', '', clean_text)  # Remove leading/trailing commas
                                clean_text = clean_text.replace('&nbsp;', ' ')
                                return clean_text.strip() if clean_text.strip() else "—"
                            return "—"
                        
                        def get_val_from_row(row_dict, candidate_keys, default=None):
                            """Extract value from row dict, return None if not found (allows or-chaining)"""
                            if not row_dict:
                                return default
                            row_norm = {str(k).strip().lower(): v for k, v in row_dict.items()}
                            for k in candidate_keys:
                                k_norm = k.strip().lower()
                                if k_norm in row_norm:
                                    val = row_norm[k_norm]
                                    if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                                        return str(val).strip()
                            return default

                        # Extract all field values with proper fallback chains
                        vm_name = get_val_from_row(matched_row, ['Name', 'name']) or extract_val(db_vm, ['name']) or clean_target.upper()
                        
                        vm_role = extract_val(db_vm, ['role', 'model_or_role']) or get_val_from_row(matched_row, ['Role', 'role']) or "---------"
                        
                        vm_status = extract_val(db_vm, ['status']) or get_val_from_row(matched_row, ['Status', 'status']) or "Active"
                        if isinstance(vm_status, str):
                            vm_status = vm_status.capitalize()
                        
                        vm_desc = extract_val(db_vm, ['description']) or get_val_from_row(matched_row, ['Description', 'description', 'Purpose']) or ""
                        
                        # Tags: prioritize database tags
                        db_tags = db_vm.get('tags', []) if db_vm else []
                        if db_tags:
                            vm_tags_display = format_tags(db_tags)
                        else:
                            raw_tags = get_val_from_row(matched_row, ['NetBox Tags', 'Tags', 'tags'], default="—")
                            vm_tags_display = format_tags(raw_tags) if raw_tags else "—"
                        
                        # Prioritize database values, then CSV values
                        vm_location = extract_val(db_vm, ['site']) or get_val_from_row(matched_row, ['Location', 'site', 'Site']) or "Australia East"
                        if isinstance(vm_location, dict):
                            vm_location = vm_location.get('name', 'Australia East')
                        
                        vm_cluster = extract_val(db_vm, ['cluster']) or "---------"
                        if isinstance(vm_cluster, dict):
                            vm_cluster = vm_cluster.get('name', '---------')
                        
                        vm_tenant_group = "Azure"
                        vm_tenant = extract_val(db_vm, ['tenant']) or get_val_from_row(matched_row, ['Subscription', 'tenant', 'Tenant']) or "---------"
                        if isinstance(vm_tenant, dict):
                            vm_tenant = vm_tenant.get('name', '---------')
                        
                        vm_platform = extract_val(db_vm, ['platform']) or get_val_from_row(matched_row, ['Operating System', 'Platform', 'platform']) or "Windows Server"
                        if isinstance(vm_platform, dict):
                            vm_platform = vm_platform.get('name', 'Windows Server')
                        
                        vm_ip = extract_val(db_vm, ['primary_ip', 'primary_ip4', 'ip']) or get_val_from_row(matched_row, ['Azure IP', 'ip', 'Primary IPv4']) or "---------"
                        if isinstance(vm_ip, dict):
                            vm_ip = vm_ip.get('address', '---------')
                        
                        # Custom fields: prioritize database values
                        custom_fields = db_vm.get('custom_fields', {}) if (db_vm and isinstance(db_vm.get('custom_fields'), dict)) else {}
                        vm_instance = extract_val(custom_fields, ['instance_type', 'instancetype']) or extract_val(db_vm, ['instance_type']) or get_val_from_row(matched_row, ['Size', 'Instance Type', 'cf_instance_type']) or "---------"
                        vm_rg = extract_val(custom_fields, ['resource_group', 'resourcegroup', 'resource_groups', 'resourcegroups']) or extract_val(db_vm, ['resource_group']) or get_val_from_row(matched_row, ['Resource Group', 'Resource Groups', 'cf_resource_group']) or "---------"
                        
                        # Owner: check custom fields first, then top-level owner field, then CSV
                        vm_owner = extract_val(custom_fields, ['owner']) or extract_val(db_vm, ['owner']) or get_val_from_row(matched_row, ['Owner', 'owner']) or "---------"
                        if isinstance(vm_owner, dict):
                            vm_owner = vm_owner.get('name', '---------')
                        
                        # Owner group: NetBox native ownership feature (may not be in backup)
                        vm_owner_group = "---------"  # Not typically stored in minimal backups
                        
                        vm_device = extract_val(db_vm, ['device']) or get_val_from_row(matched_row, ['Device', 'device']) or "---------"
                        
                        # Determine data source indicator
                        if db_vm and csv_vm:
                            source_icon = "☁️📦"
                            source_text = "Data from Azure CSV and NetBox Database"
                        elif db_vm:
                            source_icon = "📦"
                            source_text = "Data from NetBox Database"
                        else:
                            source_icon = "☁️"
                            source_text = "Data from Azure CSV"
                        
                        st.info(f"{source_icon} **{source_text}**")
                        
                        # Compact three-column layout with st.code for built-in copy functionality
                        # Use clean_target as part of the key to ensure widgets refresh for each new search
                        key_suffix = clean_target.replace(' ', '_').replace('.', '_')
                        col1, col2, col3 = st.columns(3)
                        
                        with col1:
                            st.markdown("**Virtual Machine**")
                            st.caption("Name:")
                            st.code(str(vm_name), language="text")
                            st.caption("Role:")
                            st.code(str(vm_role), language="text")
                            st.caption("Status:")
                            st.code(str(vm_status), language="text")
                            st.caption("Description:")
                            st.code(str(vm_desc), language="text")
                            st.caption("Tags:")
                            st.code(str(vm_tags_display), language="text")
                            
                            st.markdown("**Placement**")
                            # Site: use database value as-is if it already has the Azure prefix
                            if vm_location and vm_location.startswith("Azure - "):
                                vm_site = vm_location
                            elif vm_location:
                                raw_loc = _strip_azure_prefix(vm_location)
                                vm_site = f"Azure - {raw_loc}"
                            else:
                                vm_site = "Azure - Unknown"
                            st.caption("Site:")
                            st.code(str(vm_site), language="text")
                            st.caption("Cluster:")
                            st.code(str(vm_cluster), language="text")
                            st.caption("Device:")
                            st.code(str(vm_device), language="text")
                        
                        with col2:
                            st.markdown("**Tenancy**")
                            st.caption("Tenant group:")
                            st.code(str(vm_tenant_group), language="text")
                            st.caption("Tenant:")
                            st.code(str(vm_tenant), language="text")
                            
                            st.markdown("**Management**")
                            st.caption("Platform:")
                            st.code(str(vm_platform), language="text")
                            
                            st.caption("Primary IPv4:")
                            st.code(str(vm_ip), language="text")
                        
                        with col3:
                            st.markdown("**Custom Fields**")
                            st.caption("Instance Type:")
                            st.code(str(vm_instance), language="text")
                            st.caption("Resource Groups:")
                            st.code(str(vm_rg), language="text")
                            
                            st.markdown("**Ownership**")
                            st.caption("Owner:")
                            st.code(str(vm_owner), language="text")
                            st.caption("Owner group:")
                            st.code(str(vm_owner_group), language="text")
                            
                            # Display ALL other custom fields dynamically
                            if custom_fields:
                                displayed_fields = {'instance_type', 'instancetype', 'resource_group', 
                                                   'resourcegroup', 'resource_groups', 'resourcegroups', 'owner'}
                                
                                for cf_name, cf_value in custom_fields.items():
                                    if cf_name.lower() not in displayed_fields:
                                        display_name = cf_name.replace('_', ' ').title()
                                        
                                        if cf_value is None or str(cf_value).strip() == "":
                                            formatted_value = "---------"
                                        elif isinstance(cf_value, dict):
                                            formatted_value = cf_value.get('name') or cf_value.get('value') or str(cf_value)
                                        elif isinstance(cf_value, list):
                                            formatted_value = ", ".join(str(v) for v in cf_value if v)
                                        else:
                                            formatted_value = str(cf_value).strip()
                                        
                                        if formatted_value and formatted_value != "---------":
                                            st.caption(f"{display_name}:")
                                            st.code(formatted_value, language="text")
                
                
        
        except Exception as e:
            st.error(f"❌ Error processing Azure VM CSV: {str(e)}")
            import traceback
            with st.expander("Error Details"):
                st.code(traceback.format_exc())
    
    # Show preview and analysis sections if data is loaded (either from upload or database)
    elif st.session_state.azure_vms_parsed is not None:
        vm_records = st.session_state.azure_vms_parsed
        
        # Show that data is loaded
        st.subheader("2️⃣ Preview Azure VMs")
        st.caption(f"📊 {len(vm_records)} VMs loaded (data persists across page refreshes)")
        
        # Build export dataset
        export_records = []
        for vm in vm_records:
            tag_names = [t['name'] for t in _build_netbox_tags(vm)]
            record = {
                'name': vm.get('name', ''),
                'subscription': vm.get('subscription', ''),
                'resource_group': vm.get('resource_group', ''),
                'location': vm.get('location', ''),
                'status': vm.get('status', ''),
                'operating_system': vm.get('operating_system', ''),
                'platform_value': vm.get('platform_value', ''),
                'size': vm.get('size', ''),
                'primary_ip': vm.get('public_ip', ''),
                'vnet': vm.get('vnet', ''),
                'subnet': vm.get('subnet', ''),
                'owner': vm.get('owner', ''),
                'role': vm.get('role', ''),
                'tag_environment': vm.get('tag_environment', ''),
                'tag_cost_centre': vm.get('tag_cost_centre', ''),
                'tag_business_criticality': vm.get('tag_business_criticality', ''),
                'tag_deployment_method': vm.get('tag_deployment_method', ''),
                'tag_backup': vm.get('tag_backup', ''),
                'netbox_tags': ', '.join(tag_names),
                'source': vm.get('source', ''),
                'imported_at': vm.get('imported_at', ''),
            }
            export_records.append(record)

        df_export = pd.DataFrame(export_records)

        # Export parsed dataset as CSV
        export_csv_col, _ = st.columns([1, 3])
        with export_csv_col:
            st.download_button(
                "📥 Download Parsed VMs CSV",
                df_export.to_csv(index=False).encode("utf-8"),
                f"azure-vms-parsed-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                "text/csv",
                help="Download the cleaned Azure VM dataset as CSV",
            )

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

        # Build preview table if not already in session state
        if st.session_state.get('azure_preview_table_df') is None:
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

                extra = len(assigned_ips) - 1 if assigned_ips and resolved_ip in assigned_ips else len(assigned_ips)
                ip_display = resolved_ip or '—'
                if resolved_ip and extra > 0:
                    ip_display = f"{resolved_ip} (+{extra})"

                tag_names = [t['name'] for t in _build_netbox_tags(vm)]
                if tag_names:
                    chips = "".join(
                        f'<span class="nb-tag-chip" title="{html.escape(n)}">{html.escape(n)}</span>'
                        for n in tag_names
                    )
                    netbox_tags_html = f'<div class="nb-tags-cell">{chips}</div>'
                else:
                    netbox_tags_html = '—'

                vm_status = {
                    'Name': vm['name'],
                    'Subscription': vm['subscription'],
                    'Resource Group': vm['resource_group'],
                    'Location': vm['location'],
                    'Status': vm['status'],
                    'Operating System': vm.get('operating_system') or '—',
                    'Role': vm.get('role') or vm.get('tag_application') or '—',
                    'Size': vm['size'],
                    'Azure IP': vm.get('public_ip') or '—',
                    'VNet': vm.get('vnet') or '—',
                    'Subnet': vm.get('subnet') or '—',
                    'Owner': vm.get('owner') or '—',
                    'NetBox Tags': netbox_tags_html,
                    'NetBox IP': ip_display,
                    'In Database': '✅ Yes' if existing else '❌ No (Need to add to NetBox)'
                }
                vm_status_list.append(vm_status)
            
            st.session_state.azure_preview_table_df = pd.DataFrame(vm_status_list)
            # Store additional data needed for display
            st.session_state.azure_preview_table_data = {
                'vm_status_list': vm_status_list,
                'ip_matched': ip_matched,
                'ip_index': ip_index
            }
        
        # Always display the table from cached data
        if st.session_state.get('azure_preview_table_df') is not None:
            cached_data = st.session_state.get('azure_preview_table_data', {})
            vm_status_list = cached_data.get('vm_status_list', [])
            ip_matched = cached_data.get('ip_matched', 0)
            ip_index = cached_data.get('ip_index', {})
            
            if vm_status_list:
                # Display the table
                table_columns = list(vm_status_list[0].keys())
                table_headers = "".join(f"<th>{html.escape(column)}</th>" for column in table_columns)
                table_rows = []
                for row in vm_status_list:
                    cells = []
                    for column in table_columns:
                        value = row[column]
                        if column == "NetBox Tags" and value != "—":
                            cell = f'<td class="nb-tags-column"><div class="nb-tags-cell">{value}</div></td>'
                        else:
                            cell = f"<td>{html.escape(str(value))}</td>"
                        cells.append(cell)
                    table_rows.append(f"<tr>{''.join(cells)}</tr>")

                st.markdown(
                    f"""
                    <style>
                    .nb-table-scroll {{
                        max-width: 100%;
                        max-height: 400px;
                        overflow: auto;
                        border: 1px solid rgba(128, 128, 128, 0.25);
                    }}
                    .nb-vm-table {{
                        width: 100%;
                        border-collapse: collapse;
                        font-size: 13px;
                    }}
                    .nb-vm-table thead {{
                        background-color: rgba(128, 128, 128, 0.1);
                        position: sticky;
                        top: 0;
                    }}
                    .nb-vm-table th, .nb-vm-table td {{
                        padding: 6px 10px;
                        text-align: left;
                        border-bottom: 1px solid rgba(128, 128, 128, 0.1);
                    }}
                    .nb-vm-table th {{
                        font-weight: 600;
                    }}
                    .nb-tags-column {{
                        max-width: 300px;
                    }}
                    .nb-tags-cell {{
                        display: flex;
                        flex-wrap: wrap;
                        gap: 4px;
                    }}
                    .nb-tag-chip {{
                        display: inline-block;
                        padding: 2px 6px;
                        background-color: rgba(59, 130, 246, 0.15);
                        color: rgb(59, 130, 246);
                        border-radius: 4px;
                        font-size: 11px;
                        white-space: nowrap;
                    }}
                    </style>
                    <div class="nb-table-scroll">
                        <table class="nb-vm-table">
                            <thead><tr>{table_headers}</tr></thead>
                            <tbody>{''.join(table_rows)}</tbody>
                        </table>
                    </div>
                    """,
                    unsafe_allow_html=True,
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
        
        # NetBox Objects Summary
        st.subheader("3️⃣ NetBox Objects Required")
        
        if "analyze_netbox_clicked" not in st.session_state:
            st.session_state["analyze_netbox_clicked"] = False
        if st.button("📋 Analyze NetBox Requirements", key="btn_analyze_netbox_loaded"):
            st.session_state["analyze_netbox_clicked"] = True
        
        if st.session_state.get("analyze_netbox_clicked", False):
            with st.spinner("Analyzing NetBox requirements..."):
                netbox_records, metadata = map_azure_to_netbox(vm_records)
                st.session_state.azure_vms_mapped = netbox_records
                st.session_state.azure_metadata = metadata
                st.session_state.azure_object_analysis = analyze_netbox_objects(metadata)

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

            # Detail per category - display in 2 columns
            analysis_items = list(analysis.items())
            obj_col1, obj_col2 = st.columns(2)
            
            for idx, (key, data) in enumerate(analysis_items):
                missing = data["missing"]
                existing = data["existing"]
                icon = "❌" if missing else "✅"
                header = f"{icon} {data['label']} — {len(missing)} missing, {len(existing)} exist"
                
                # Alternate between columns
                target_col = obj_col1 if idx % 2 == 0 else obj_col2
                
                with target_col:
                    with st.expander(header, expanded=False):
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
                    with st.expander(f"📄 {script['label']} ({script['count']} missing)", expanded=False):
                        st.caption(script["instructions"])
                        lang = "csv" if script["format"] == "csv" else "text"
                        st.code(script["content"], language=lang)

                bundle = generate_combined_import_bundle(scripts)
                st.download_button(
                    "📦 Download All Import Scripts (bundle)",
                    bundle.encode("utf-8"),
                    f"netbox-import-bundle-{pd.Timestamp.now().strftime('%Y%m%d')}.txt",
                    "text/plain",
                    key="dl_bundle_loaded",
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
                
                # Enrich VM records with data from NetBox database if available
                for vm in new_vm_records:
                    db_vm = check_vm_exists_in_db(vm['name'])
                    if db_vm:
                        # Merge owner field from database if not present in CSV
                        if not vm.get('owner') and db_vm.get('owner'):
                            vm['owner'] = db_vm['owner']
                        # Also check custom_fields for owner
                        if not vm.get('owner') and isinstance(db_vm.get('custom_fields'), dict):
                            cf_owner = db_vm['custom_fields'].get('owner')
                            if cf_owner:
                                vm['owner'] = cf_owner
                instance_type_values = get_existing_custom_field_values(
                    INSTANCE_TYPE_FIELD, INSTANCE_TYPE_CHOICE_SET
                )
                resource_group_values = get_existing_custom_field_values(
                    RESOURCE_GROUP_FIELD, RESOURCE_GROUP_CHOICE_SET
                )
                owner_values = get_existing_custom_field_values(OWNER_FIELD, OWNER_CHOICE_SET)
                role_values = get_existing_roles()

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
                    "cf_instance_type", "cf_resource_group", "owner",
                    "cf_application", "cf_environment", "cf_cost_centre",
                    "cf_business_criticality", "cf_deployment_method", "cf_backup", "cf_operating_system", "tags"
                ]]
                for vm in new_vm_records:
                    vm_import_rows.append([
                        vm.get('name', ''),
                        vm.get('status', ''),
                        f"Azure - {_strip_azure_prefix(vm.get('location', ''))}" if vm.get('location') else '',
                        canonical_value(vm.get('role', '') or vm.get('tag_application', ''), role_values),
                        vm.get('subscription', ''),
                        vm.get('platform_value') or vm.get('operating_system', ''),
                        canonical_value(vm.get('size', ''), instance_type_values),
                        canonical_value(vm.get('resource_group', ''), resource_group_values),
                        canonical_value(vm.get('owner', ''), owner_values),
                        vm.get('tag_application', ''),
                        vm.get('tag_environment', ''),
                        vm.get('tag_cost_centre', ''),
                        vm.get('tag_business_criticality', ''),
                        vm.get('tag_deployment_method', ''),
                        vm.get('tag_backup', ''),
                        vm.get('tag_operating_system', ''),
                        vm.get('tags', ''),
                    ])
                csv_buffer = io.StringIO(newline='')
                csv.writer(csv_buffer, lineterminator='\n').writerows(vm_import_rows)
                vm_import_script = csv_buffer.getvalue()
                
                with st.expander(f"📄 NetBox VMs Import CSV ({len(new_vm_records)} VMs)", expanded=True):
                    st.caption("Copy this CSV into NetBox's Virtual Machine bulk import form.")
                    st.code(vm_import_script, language="csv")

                st.download_button(
                    "📥 Download NetBox VMs Import CSV",
                    vm_import_script.encode("utf-8"),
                    f"netbox-vms-import-{pd.Timestamp.now().strftime('%Y%m%d')}.csv",
                    "text/csv",
                    key=f"dl_vms_import_loaded_{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}",
                )
            
            st.divider()
            st.markdown("### 🔍 Netbox Helper")
            vm_search_input = st.text_input("Enter VM Name / Hostname", placeholder="e.g., VM-APP-001", key="netbox_vm_search_query_loaded", label_visibility="collapsed")
            
            if vm_search_input and vm_search_input.strip():
                clean_target = vm_search_input.strip().lower()
                
                db_vm = check_vm_exists_in_db(vm_search_input)
                csv_vm = None
                
                preview_df = st.session_state.get('azure_preview_table_df')
                matched_row = None
                if preview_df is not None and not preview_df.empty:
                    name_col = next((c for c in preview_df.columns if c.strip().lower() in ['name', 'vm name', 'hostname']), None)
                    if name_col:
                        matches = preview_df[preview_df[name_col].astype(str).str.strip().str.lower() == clean_target]
                        if not matches.empty:
                            matched_row = matches.iloc[0].to_dict()
                
                if matched_row:
                    csv_vm = matched_row
                
                if not db_vm and not csv_vm:
                    st.warning(f"VM '{vm_search_input.strip()}' not found in NetBox database or uploaded Azure CSV.")
                else:
                    def extract_val(source_dict, candidate_keys):
                        """Extract value from dict, return None if not found (allows or-chaining)"""
                        if not source_dict or not isinstance(source_dict, dict):
                            return None
                        norm_dict = {str(k).strip().lower(): v for k, v in source_dict.items()}
                        for k in candidate_keys:
                            k_norm = k.strip().lower()
                            if k_norm in norm_dict:
                                val = norm_dict[k_norm]
                                if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                                    return str(val).strip()
                        return None
                    
                    def format_tags(tags_val):
                        if not tags_val:
                            return "—"
                        if isinstance(tags_val, list):
                            tag_names = [t.get('name', str(t)) if isinstance(t, dict) else str(t) for t in tags_val if t]
                            return ", ".join(tag_names) if tag_names else "—"
                        if isinstance(tags_val, str) and tags_val.strip() not in ["", "nan", "None", "—"]:
                            import re
                            clean_text = re.sub(r'<[^>]+>', ', ', tags_val)
                            clean_text = re.sub(r',\s*,', ',', clean_text)
                            clean_text = re.sub(r'^\s*,\s*|\s*,\s*$', '', clean_text)
                            clean_text = clean_text.replace('&nbsp;', ' ')
                            return clean_text.strip() if clean_text.strip() else "—"
                        return "—"
                    
                    def get_val_from_row(row_dict, candidate_keys, default=None):
                        """Extract value from row dict, return None if not found (allows or-chaining)"""
                        if not row_dict:
                            return default
                        row_norm = {str(k).strip().lower(): v for k, v in row_dict.items()}
                        for k in candidate_keys:
                            k_norm = k.strip().lower()
                            if k_norm in row_norm:
                                val = row_norm[k_norm]
                                if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                                    return str(val).strip()
                        return default

                    # Extract all field values with proper fallback chains
                    vm_name = get_val_from_row(matched_row, ['Name', 'name']) or extract_val(db_vm, ['name']) or clean_target.upper()
                    
                    vm_role = extract_val(db_vm, ['role', 'model_or_role']) or get_val_from_row(matched_row, ['Role', 'role']) or "---------"
                    
                    vm_status = extract_val(db_vm, ['status']) or get_val_from_row(matched_row, ['Status', 'status']) or "Active"
                    if isinstance(vm_status, str):
                        vm_status = vm_status.capitalize()
                    
                    vm_desc = extract_val(db_vm, ['description']) or get_val_from_row(matched_row, ['Description', 'description', 'Purpose']) or ""
                    
                    # Tags: prioritize database tags
                    db_tags = db_vm.get('tags', []) if db_vm else []
                    if db_tags:
                        vm_tags_display = format_tags(db_tags)
                    else:
                        raw_tags = get_val_from_row(matched_row, ['NetBox Tags', 'Tags', 'tags'], default="—")
                        vm_tags_display = format_tags(raw_tags) if raw_tags else "—"
                    
                    # Prioritize database values, then CSV values
                    vm_location = extract_val(db_vm, ['site']) or get_val_from_row(matched_row, ['Location', 'site', 'Site']) or "Australia East"
                    if isinstance(vm_location, dict):
                        vm_location = vm_location.get('name', 'Australia East')
                    
                    vm_cluster = extract_val(db_vm, ['cluster']) or "---------"
                    if isinstance(vm_cluster, dict):
                        vm_cluster = vm_cluster.get('name', '---------')
                    
                    vm_tenant_group = "Azure"
                    vm_tenant = extract_val(db_vm, ['tenant']) or get_val_from_row(matched_row, ['Subscription', 'tenant', 'Tenant']) or "---------"
                    if isinstance(vm_tenant, dict):
                        vm_tenant = vm_tenant.get('name', '---------')
                    
                    vm_platform = extract_val(db_vm, ['platform']) or get_val_from_row(matched_row, ['Operating System', 'Platform', 'platform']) or "Windows Server"
                    if isinstance(vm_platform, dict):
                        vm_platform = vm_platform.get('name', 'Windows Server')
                    
                    vm_ip = extract_val(db_vm, ['primary_ip', 'primary_ip4', 'ip']) or get_val_from_row(matched_row, ['Azure IP', 'ip', 'Primary IPv4']) or "---------"
                    if isinstance(vm_ip, dict):
                        vm_ip = vm_ip.get('address', '---------')
                    
                    # Custom fields: prioritize database values
                    custom_fields = db_vm.get('custom_fields', {}) if (db_vm and isinstance(db_vm.get('custom_fields'), dict)) else {}
                    vm_instance = extract_val(custom_fields, ['instance_type', 'instancetype']) or extract_val(db_vm, ['instance_type']) or get_val_from_row(matched_row, ['Size', 'Instance Type', 'cf_instance_type']) or "---------"
                    vm_rg = extract_val(custom_fields, ['resource_group', 'resourcegroup', 'resource_groups', 'resourcegroups']) or extract_val(db_vm, ['resource_group']) or get_val_from_row(matched_row, ['Resource Group', 'Resource Groups', 'cf_resource_group']) or "---------"
                    
                    # Owner: check custom fields first, then top-level owner field, then CSV
                    vm_owner = extract_val(custom_fields, ['owner']) or extract_val(db_vm, ['owner']) or get_val_from_row(matched_row, ['Owner', 'owner']) or "---------"
                    if isinstance(vm_owner, dict):
                        vm_owner = vm_owner.get('name', '---------')
                    
                    vm_owner_group = "---------"
                    
                    vm_device = extract_val(db_vm, ['device']) or get_val_from_row(matched_row, ['Device', 'device']) or "---------"
                    
                    # Determine data source indicator
                    if db_vm and csv_vm:
                        source_icon = "☁️📦"
                        source_text = "Data from Azure CSV and NetBox Database"
                    elif db_vm:
                        source_icon = "📦"
                        source_text = "Data from NetBox Database"
                    else:
                        source_icon = "☁️"
                        source_text = "Data from Azure CSV"
                    
                    st.info(f"{source_icon} **{source_text}**")
                    
                    # Compact three-column layout with st.code for built-in copy functionality
                    key_suffix = clean_target.replace(' ', '_').replace('.', '_')
                    vm_col1, vm_col2, vm_col3 = st.columns(3)
                    
                    with vm_col1:
                        st.markdown("**Virtual Machine**")
                        st.caption("Name:")
                        st.code(str(vm_name), language="text")
                        st.caption("Role:")
                        st.code(str(vm_role), language="text")
                        st.caption("Status:")
                        st.code(str(vm_status), language="text")
                        st.caption("Description:")
                        st.code(str(vm_desc), language="text")
                        st.caption("Tags:")
                        st.code(str(vm_tags_display), language="text")
                        
                        st.markdown("**Placement**")
                        if vm_location and vm_location.startswith("Azure - "):
                            vm_site = vm_location
                        elif vm_location:
                            raw_loc = _strip_azure_prefix(vm_location)
                            vm_site = f"Azure - {raw_loc}"
                        else:
                            vm_site = "Azure - Unknown"
                        st.caption("Site:")
                        st.code(str(vm_site), language="text")
                        st.caption("Cluster:")
                        st.code(str(vm_cluster), language="text")
                        st.caption("Device:")
                        st.code(str(vm_device), language="text")
                    
                    with vm_col2:
                        st.markdown("**Tenancy**")
                        st.caption("Tenant group:")
                        st.code(str(vm_tenant_group), language="text")
                        st.caption("Tenant:")
                        st.code(str(vm_tenant), language="text")
                        
                        st.markdown("**Management**")
                        st.caption("Platform:")
                        st.code(str(vm_platform), language="text")
                        st.caption("Primary IPv4:")
                        st.code(str(vm_ip), language="text")
                    
                    with vm_col3:
                        st.markdown("**Custom Fields**")
                        st.caption("Instance Type:")
                        st.code(str(vm_instance), language="text")
                        st.caption("Resource Groups:")
                        st.code(str(vm_rg), language="text")
                        
                        st.markdown("**Ownership**")
                        st.caption("Owner:")
                        st.code(str(vm_owner), language="text")
                        st.caption("Owner group:")
                        st.code(str(vm_owner_group), language="text")
                        
                        # Display ALL other custom fields dynamically
                        if custom_fields:
                            displayed_fields = {'instance_type', 'instancetype', 'resource_group', 
                                               'resourcegroup', 'resource_groups', 'resourcegroups', 'owner'}
                            
                            for cf_name, cf_value in custom_fields.items():
                                if cf_name.lower() not in displayed_fields:
                                    display_name = cf_name.replace('_', ' ').title()
                                    
                                    if cf_value is None or str(cf_value).strip() == "":
                                        formatted_value = "---------"
                                    elif isinstance(cf_value, dict):
                                        formatted_value = cf_value.get('name') or cf_value.get('value') or str(cf_value)
                                    elif isinstance(cf_value, list):
                                        formatted_value = ", ".join(str(v) for v in cf_value if v)
                                    else:
                                        formatted_value = str(cf_value).strip()
                                    
                                    if formatted_value and formatted_value != "---------":
                                        st.caption(f"{display_name}:")
                                        st.code(formatted_value, language="text")
