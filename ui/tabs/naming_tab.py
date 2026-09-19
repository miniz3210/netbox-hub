import re
import streamlit as st
from utils.formatters import (
    compute_suggested_site_code, 
    normalize_port_shortname,
    normalize_vswitch,
    normalize_vmnic,
    normalize_vmnic_list,
    normalize_network_name
)
from utils.pattern_formatter import apply_pattern
from core.naming_engine import verify_and_suggest_with_ai
from datetime import datetime
from core.db_manager import (
    save_universal_csv, 
    get_records_by_category, 
    clear_inventory_records,
    clear_device_records,
    clear_vm_records,
    get_total_record_count,
    get_sync_metadata,
    get_file_sync_metadata
)
from core.session_manager import SessionStateManager as SSM
from core.shared_backup_state import SharedBackupState
from ui.components import render_ai_chat, render_backup_uploader

def build_naming_system_prompt(prompt: str) -> str:
    """Build the grounded naming/inventory system prompt for the AI Assistant."""
    from core.ai_helper import build_comprehensive_naming_context

    comprehensive_context = build_comprehensive_naming_context(prompt)

    return f"""You are an expert in infrastructure naming conventions, inventory management, and NetBox administration.
You have DIRECT ACCESS to the complete inventory database. Analyze the user's request and respond accurately using the ACTUAL DATABASE DATA provided below.

=== COMPLETE DATABASE CONTEXT ===
{comprehensive_context}

=== IMPORTANT GUIDELINES ===
1. **Answer ALL questions using the ACTUAL DATA above** - You have complete database access
2. When asked to list devices, VMs, or inventory:
   - Reference the inventory sections above
   - Provide actual device names, roles, manufacturers, sites
   - Show real data, not generic examples
3. When asked about specific sites:
   - Use the "Inventory for Site" section if available
   - List actual devices/VMs at that site with their details
4. When asked about VLANs at a site:
   - Explain that VLAN data is in the IPAM tab, but you can see the devices/VMs at the site
5. For naming convention questions:
   - Analyze the actual hostnames in the database
   - Identify patterns and standards being used
   - Suggest improvements based on real examples
6. When counting items (e.g., "how many devices in AGE"):
   - Provide exact counts from the data above
   - List the actual device names
7. Format responses clearly:
   - Use bullet points for lists
   - Include device names, roles, manufacturers, sites
   - Show actual counts and statistics
8. If no data exists for a query, clearly state "No records found in database for [query]"
9. Be specific and data-driven - always reference actual inventory items
10. For naming suggestions, consider:
    - Site codes, device types, sequence numbers
    - Consistency with existing naming patterns in the database
11. If a "NETBOX MASTER BACKUP" section is present it is the full NetBox export:
    - Treat it as authoritative for any object type (sites, racks, devices, interfaces, IPs, VMs, clusters, tenants, circuits, VRFs)
    - Use its "total in backup" figures when stating counts
    - Quote the exact records listed rather than generalizing"""

def apply_case(text: str, mode: str) -> str:
    return text.upper() if mode == "UPPERCASE" else text.lower()

def handle_csv_upload(uploader_key: str):
    """Universal file upload handler using dynamic schema classification."""
    uploaded_files = st.session_state.get(uploader_key)
    if not uploaded_files:
        return

    if not isinstance(uploaded_files, list):
        uploaded_files = [uploaded_files]

    # Use universal uploader for automatic classification and routing
    from core.universal_uploader import UniversalUploader
    
    try:
        uploader = UniversalUploader()
        results = uploader.process_uploaded_files(uploaded_files)
        
        if results['errors']:
            for err in results['errors']:
                st.error(err)
        
        if results['total_records'] > 0:
            # Generate detailed toast message with model breakdown
            model_breakdown = ", ".join([
                f"{count} {model.split('.')[-1]}"
                for model, count in sorted(results['by_model'].items())
            ])
            
            # Check for unclassified files
            has_unclassified = any('unclassified' in model for model in results['by_model'].keys())
            
            if has_unclassified:
                st.warning(
                    f"⚠️ Stored {results['total_records']} records in unclassified table: {model_breakdown}\n\n"
                    "💡 **Tip:** Upload a NetBox backup JSON first to enable automatic classification. "
                    "The data is safely stored and can be re-classified later.",
                    icon="⚠️"
                )
            else:
                st.toast(f"✅ Ingested {results['total_records']} records: {model_breakdown}", icon="🚀")
            
            # Update SharedBackupState for dynamic backup display
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Build source file list for display
            source_files = ", ".join([f.name for f in uploaded_files])
            for model_key, count in results['by_model'].items():
                # Map model keys to endpoint paths for SharedBackupState
                endpoint = model_key.replace('.', '/')
                SharedBackupState.add_csv_override(endpoint, count, source_files, now)
            
            # Clear uploader by incrementing key
            st.session_state["naming_csv_key"] = st.session_state.get("naming_csv_key", 0) + 1
        elif not results['errors']:
            # No data ingested and no errors - clear uploader anyway
            st.session_state["naming_csv_key"] = st.session_state.get("naming_csv_key", 0) + 1
            
    except Exception as e:
        st.error(f"❌ Upload failed: {str(e)}")
        # Show helpful message if schema registry not initialized
        if "schema registry" in str(e).lower() or "classify" in str(e).lower():
            st.info(
                "💡 **Tip:** Upload a NetBox backup JSON first to initialize the schema registry, "
                "then CSV files will be automatically classified and routed."
            )

def handle_csv_reset():
    clear_inventory_records()
    
    # Also clear CSV entries from SharedBackupState
    from core.shared_backup_state import SharedBackupState
    SharedBackupState.clear_csv_only()
    
    st.toast("🗑️ Database Cleared. Restored default examples.", icon="🧹")

def display_reference_box(category_key: str, default_lines: str, label: str, site_filter: str = "", name_filter: str = ""):
    real_items = get_records_by_category(category_key, site_filter=site_filter)
    
    # Apply additional name-based filtering if provided (e.g., WAP, FW, SW prefix)
    if name_filter and real_items:
        name_filter_upper = name_filter.upper()
        real_items = [r for r in real_items if r.get('name', '').upper().startswith(name_filter_upper)]
    
    filter_hint = f" matching '{site_filter.upper()}'" if site_filter else ""
    if name_filter:
        filter_hint += f" (prefix: {name_filter})"
    
    with st.expander(f"💡 Click to view reference {label} examples ({len(real_items) if real_items else 'Default'} records{filter_hint})", expanded=False):
        if real_items:
            st.markdown(f"##### 🟢 NetBox Ingested Data ({len(real_items)} records{filter_hint}):")
            formatted = []
            for r in real_items[:15]:
                meta_parts = []
                if r.get('manufacturer') and r.get('model_or_role'):
                    meta_parts.append(f"{r['manufacturer']} - {r['model_or_role']}")
                elif r.get('model_or_role'):
                    meta_parts.append(r['model_or_role'])
                if r.get('site'):
                    meta_parts.append(f"Site: {r['site']}")
                if r.get('description'):
                    meta_parts.append(r['description'])
                
                meta_str = f"  ({', '.join(meta_parts)})" if meta_parts else ""
                formatted.append(f"{r['name']}{meta_str}")
            st.code("\n".join(formatted), language="text")
        else:
            st.markdown("##### 🟡 Default Examples:")
            st.code(default_lines, language="text")

def render_compact_toolbar(active_model):
    # Early restore: Check if backup exists in DB but not in session state and restore it
    from core.shared_backup_state import SharedBackupState
    from core.backup_manager import get_backup_metadata, restore_backup_to_session_state
    
    if not SharedBackupState.has_backup():
        meta = get_backup_metadata()
        if meta.get("loaded", False):
            try:
                restore_backup_to_session_state()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to restore backup on tab load: {e}")
    
    total_recs = get_total_record_count()
    device_count = len(get_records_by_category("device")) + len(get_records_by_category("hypervisor"))
    vm_count = len(get_records_by_category("vm"))
    
    status_tag = f"🟢 ({device_count} Devices, {vm_count} VMs in DB)" if total_recs > 0 else "⚪ (Default Examples)"
    tick_devices = " ✅" if device_count > 0 else ""
    tick_vms = " ✅" if vm_count > 0 else ""
    
    with st.expander(f"📥 Ingest NetBox Data (Backup / CSV) {status_tag}", expanded=False):
        # Schema Registry Status Check
        from core.universal_schema_registry import UniversalSchemaRegistry
        registry = UniversalSchemaRegistry.load_from_session_state()
        
        if registry and registry.model_signatures:
            model_count = len(registry.model_signatures)
            st.success(f"✅ **Schema Registry Active:** {model_count} NetBox models loaded. CSV files will be automatically classified.", icon="🔍")
        else:
            st.warning(
                "⚠️ **Schema Registry Not Initialized:** CSV files will be stored in 'unclassified' tables. "
                "Upload a NetBox backup JSON below to enable automatic classification.",
                icon="⚠️"
            )
        
        if total_recs > 0:
            # Get metadata for devices and VMs
            meta_devices = get_sync_metadata("netbox_devices")
            meta_vms = get_sync_metadata("netbox_virtual_machines")
            
            # Use the most recent source that's not "None"
            sources = [m['source'] for m in [meta_devices, meta_vms] if m['source'] != "None"]
            display_source = sources[0] if sources else "Manual CSV Upload"
            
            st.markdown(f"**DB Status:** `Source: {display_source}`")

        render_backup_uploader("naming")
    
    # AI Assistant
    render_ai_chat(
        history_key="naming_chat_history",
        caption="Ask for naming suggestions and verification (e.g., 'List all devices in AGE' or 'What devices are in Bristol?')",
        placeholder="Ask about devices and naming...",
        active_model=active_model,
        build_system_prompt=build_naming_system_prompt,
    )

    # Casing selector with radio buttons on same line
    case_mode = st.radio(
        "Casing",
        ["UPPERCASE", "lowercase"],
        index=0 if SSM.get_naming_case_mode() == "UPPERCASE" else 1,
        horizontal=True,
        key="naming_case_radio",
        help="Render output in UPPERCASE or lowercase."
    )
    
    # Store the selection
    SSM.set_naming_case_mode(case_mode)

    return case_mode

def _legacy_render_naming_tab(active_model):
    st.subheader("🏷️ Standardized Infrastructure Naming Generator")
    st.caption("Generate and validate standardized hostnames for network devices, servers, VMs, and ESXi configurations using AI-powered naming conventions aligned with your NetBox inventory data.")
    
    # Always reload naming rules from file to ensure latest updates from Standards tab are applied
    from config.naming_rules import load_naming_rules
    naming_rules = load_naming_rules()
    st.session_state["naming_rules"] = naming_rules
    
    # Call toolbar which includes Ingest and AI Assistant
    case_mode = render_compact_toolbar(active_model)
    
    naming_cat = st.radio(
        "Select Asset Class",
        [
            "1. Network & Security Devices (Switches, APs, Firewalls, Routers)",
            "2. Hosts & Virtual Machines (ESXi & VMs)",
            "3. ESXi Network Descriptions (vmnic, PortGroup, VMkernel)"
        ],
        horizontal=True
    )

    st.markdown("---")

    # Network & Security Devices
    if "1. Network" in naming_cat:
        st.markdown("##### 📍 Location & Site Code Assistant")
        loc_col1, loc_col2 = st.columns([2, 1])
        with loc_col1:
            input_location = st.text_input("Location / City Name", value="", placeholder="e.g. Sydney, London, Dallas, New York", key="loc_input_help")
        with loc_col2:
            auto_code = compute_suggested_site_code(input_location) if input_location else ""
            st.info(f"Suggested Site Code: **`{auto_code or '----'}`**")

        st.markdown("---")
        col_a, col_b = st.columns([1, 1])
        
        with col_a:
            st.markdown("#### 🛠️ Universal Device Hostname Generator")
            dev_type_preset = st.selectbox(
                "Device Type / Prefix",
                [
                    "SW (Switch)", "VS (Virtual Chassis / Stack)", "OTSW (OT Switch)",
                    "WAP (Wireless Access Point)", "FW (Firewall / Security Appliance)",
                    "ION (Prisma SD-WAN)", "VA (Virtual Appliance)", "RTR (Router)", "✏️ Custom Prefix..."
                ],
                index=0, key="dev_prefix_sel"
            )

            if "Custom Prefix" in dev_type_preset:
                dev_prefix = st.text_input("Enter Custom Prefix", value="", placeholder="e.g. SVR, GW, AGG", key="dev_custom_pre").strip()
            else:
                dev_prefix = dev_type_preset.split()[0].strip()

            c_ctry = st.text_input("Country Code (2-letter)", value="", placeholder="e.g. US, UK, AU, DE, JP", key="u_ctry").strip()
            c_state = st.text_input("State / Region (Optional)", value="", placeholder="e.g. NY, CA, TX, NSW", key="u_state").strip()
            c_site = st.text_input("Site Code", value=auto_code, placeholder="e.g. NYC, LON, SYD, AGE", key="u_site").strip()
            c_zone = st.text_input("Zone / Role / Vendor (Optional)", value="", placeholder="e.g. CORE, DIST, EDGE, PA", key="u_zone").strip()
            c_seq = st.text_input("Sequence Number", value="01", placeholder="e.g. 01, 02", key="u_seq").strip()
            c_stack = st.text_input("Stack / Member ID (Optional)", value="", placeholder="e.g. 0, 1", key="u_stk").strip()

            # Determine which naming pattern to use based on device type
            pattern_key = None
            if "SW" in dev_prefix or "Switch" in dev_type_preset or "VS" in dev_prefix or "Virtual Chassis" in dev_type_preset:
                pattern_key = "branch_switch"
            elif "WAP" in dev_prefix or "Wireless" in dev_type_preset:
                pattern_key = "branch_ap"
            elif "FW" in dev_prefix or "Firewall" in dev_type_preset or "ION" in dev_prefix or "SD-WAN" in dev_type_preset:
                pattern_key = "branch_security"
            
            # Generate device name using pattern or fallback to manual construction
            if pattern_key and pattern_key in naming_rules:
                pattern = naming_rules[pattern_key]
                # Extract the first pattern if multiple patterns are separated by " / "
                if " / " in pattern:
                    patterns = pattern.split(" / ")
                    # Choose appropriate pattern based on prefix
                    if "VS" in dev_prefix and len(patterns) > 1:
                        pattern = patterns[1] if "VS" in patterns[1] else patterns[0]
                    elif "ION" in dev_prefix and len(patterns) > 1:
                        pattern = patterns[1] if "ION" in patterns[1] else patterns[0]
                    else:
                        pattern = patterns[0]
                
                # Apply pattern with variables
                device_name_raw = apply_pattern(pattern, {
                    "Country": c_ctry,
                    "State": c_state,
                    "Site": c_site,
                    "Zone": c_zone,
                    "Vendor": c_zone,  # Zone/Vendor are used interchangeably
                    "Seq": c_seq,
                    "StackID": c_stack
                })
                
                # Remove angle brackets for any unfilled variables and clean up
                import re
                device_name_raw = re.sub(r'<[^>]+>', '', device_name_raw)
                # Clean up any double hyphens or trailing hyphens
                device_name_raw = re.sub(r'-+', '-', device_name_raw).strip('-')
                
                final_device_name = apply_case(device_name_raw, case_mode)
            else:
                # Fallback to manual construction if no pattern found
                raw_base = f"{dev_prefix}{c_ctry}{c_state}{c_site}{c_zone}{c_seq}"
                final_device_name = apply_case(f"{raw_base}-{c_stack}" if c_stack else raw_base, case_mode)

            st.caption("Generated Device Hostname:")
            st.code(final_device_name, language="text")

            if st.button("🤖 AI Verify / Suggest Device Hostname", key="ai_chk_dev"):
                with st.spinner("Auditing against NetBox Data & standards..."):
                    st.info(verify_and_suggest_with_ai(
                        final_device_name, 
                        active_model, 
                        asset_type=f"Network/Security Device ({dev_type_preset})", 
                        category_key="device",
                        site_filter=c_site
                    ))

            # Dynamic reference box based on device type and site
            ref_label = "Device"
            name_prefix_filter = ""
            default_examples = "SWUSNYC01-0       (Switch Stack, Member 0)\nWAPUSNYC01        (Access Point 01)\nFWUSNYCPA01       (Firewall 01)"
            
            if "SW" in dev_prefix or "Switch" in dev_type_preset:
                ref_label = "Switch"
                name_prefix_filter = "SW"
                default_examples = "SWUSNYC01-0       (Switch Stack, Member 0)\nSWUSLON01         (London Switch 01)\nSWAUSYD02         (Sydney Switch 02)"
            elif "WAP" in dev_prefix or "Wireless" in dev_type_preset:
                ref_label = "Wireless AP"
                name_prefix_filter = "WAP"
                default_examples = "WAPUSNYC01        (Access Point NYC 01)\nWAPUSLON01        (Access Point London 01)\nWAPAUSYD01        (Access Point Sydney 01)"
            elif "FW" in dev_prefix or "Firewall" in dev_type_preset:
                ref_label = "Firewall"
                name_prefix_filter = "FW"
                default_examples = "FWUSNYC01         (NYC Firewall 01)\nFWUSNYCPA01       (NYC Palo Alto FW 01)\nFWUSLONCISCO01    (London Cisco FW 01)"
            elif "RTR" in dev_prefix or "Router" in dev_type_preset:
                ref_label = "Router"
                name_prefix_filter = "RTR"
                default_examples = "RTRUSNYC01        (NYC Router 01)\nRTRUSLON01        (London Router 01)\nRTRAUSYD01        (Sydney Router 01)"
            elif "ION" in dev_prefix:
                ref_label = "SD-WAN ION"
                name_prefix_filter = "ION"
                default_examples = "IONUSNYC01        (NYC SD-WAN 01)\nIONUSLON01        (London SD-WAN 01)\nIONAUSYD01        (Sydney SD-WAN 01)"
            elif "VS" in dev_prefix:
                ref_label = "Virtual Chassis"
                name_prefix_filter = "VS"
                default_examples = "VSUSNYC01-0       (Virtual Stack Member 0)\nVSUSLON01-1       (Virtual Stack Member 1)"
            
            display_reference_box(
                category_key="device",
                default_lines=default_examples,
                label=ref_label,
                site_filter=c_site,
                name_filter=name_prefix_filter
            )

        with col_b:
            st.markdown("#### 🔌 Switch & Firewall Interface Formatter")
            p_cat = st.radio("Interface Type", [
                "Switch Uplink (Inter-Switch)", "Switch Port-Channel (Logical)", 
                "Switch LAG Member Port (LACP)", "Switch Access Port (Endpoint)", "Firewall Security Zone Interface"
            ], key="p_cat_sel")
            
            if p_cat == "Switch Uplink (Inter-Switch)":
                l_dev = st.text_input("Local Device Hostname", value="", placeholder="e.g. SWUSNYC01-0", key="up_ld").strip()
                l_port_raw = st.text_input("Local Port", value="", placeholder="e.g. Gi1/0/48, Te1/0/1", key="up_lp")
                r_dev = st.text_input("Remote Device Hostname", value="", placeholder="e.g. SWUSNYC02-0", key="up_rd").strip()
                r_port_raw = st.text_input("Remote Port", value="", placeholder="e.g. Gi1/0/48, Te1/0/1", key="up_rp")

                l_port_short = normalize_port_shortname(l_port_raw)
                r_port_short = normalize_port_shortname(r_port_raw)

                # Use pattern from naming rules
                pattern = naming_rules.get("switch_uplink_desc", "Uplink_to_<Remote_Device>_<Remote_Port_Short>")
                
                if r_dev and r_port_raw:
                    uplink_desc_local = apply_pattern(pattern, {
                        "Local_Device": l_dev,
                        "Local_Port": l_port_raw,
                        "Local_Port_Short": l_port_short,
                        "Remote_Device": r_dev,
                        "Remote_Port": r_port_raw,
                        "Remote_Port_Short": r_port_short
                    })
                else:
                    uplink_desc_local = apply_pattern(pattern, {
                        "Local_Device": "<Local_Device>",
                        "Local_Port": "<Local_Port>",
                        "Local_Port_Short": "<Local_Port_Short>",
                        "Remote_Device": "<Remote_Device>",
                        "Remote_Port": "<Remote_Port>",
                        "Remote_Port_Short": "<Remote_Port_Short>"
                    })
                
                if l_dev and l_port_raw:
                    uplink_desc_remote = apply_pattern(pattern, {
                        "Local_Device": r_dev,
                        "Local_Port": r_port_raw,
                        "Local_Port_Short": r_port_short,
                        "Remote_Device": l_dev,
                        "Remote_Port": l_port_raw,
                        "Remote_Port_Short": l_port_short
                    })
                else:
                    uplink_desc_remote = apply_pattern(pattern, {
                        "Local_Device": "<Remote_Device>",
                        "Local_Port": "<Remote_Port>",
                        "Local_Port_Short": "<Remote_Port_Short>",
                        "Remote_Device": "<Local_Device>",
                        "Remote_Port": "<Local_Port>",
                        "Remote_Port_Short": "<Local_Port_Short>"
                    })

                st.caption(f"On Local Device (`{l_dev or 'LOCAL'}`):")
                st.code(uplink_desc_local, language="text")
                st.caption(f"On Remote Device (`{r_dev or 'REMOTE'}`):")
                st.code(uplink_desc_remote, language="text")
                
                if st.button("🤖 AI Verify Uplink Description", key="ai_chk_uplink"):
                    with st.spinner("Auditing against Standards..."):
                        st.info(verify_and_suggest_with_ai(
                            uplink_desc_local, 
                            active_model, 
                            asset_type="Switch Uplink Description", 
                            category_key="device",
                            site_filter=c_site
                        ))
                
                display_reference_box(
                    category_key="device",
                    default_lines="Uplink_to_SWUSNYC02-0_Gi1/0/48\nUplink_to_FWUSNYC01_Te1/0/1\nUplink_to_Huawei-Core_XGE0/0/31",
                    label="Switch Uplink Interface",
                    site_filter=c_site
                )
            
            elif p_cat == "Switch LAG Member Port (LACP)":
                r_dev_lag = st.text_input("Remote Device Hostname", value="", placeholder="e.g. SWUSNYC02-0", key="lag_rd").strip()
                r_port_lag_raw = st.text_input("Remote Port", value="", placeholder="e.g. Gi1/0/1, Te1/0/1, Po1", key="lag_rp")

                r_port_lag_short = normalize_port_shortname(r_port_lag_raw)
                
                # Use pattern from naming rules
                pattern = naming_rules.get("switch_lag_member", "LACP_to_<Remote_Device>_<Remote_Port_Short>")
                lag_desc = apply_pattern(pattern, {
                    "Remote_Device": r_dev_lag if r_dev_lag else "<Remote_Device>",
                    "Remote_Port": r_port_lag_raw if r_port_lag_raw else "<Remote_Port>",
                    "Remote_Port_Short": r_port_lag_short if r_port_lag_raw else "<Remote_Port_Short>"
                })

                st.caption(f"Generated LAG Member Port Description:")
                st.code(lag_desc, language="text")
                
                if st.button("🤖 AI Verify LAG Member Description", key="ai_chk_lag"):
                    with st.spinner("Auditing against Standards..."):
                        st.info(verify_and_suggest_with_ai(
                            lag_desc, 
                            active_model, 
                            asset_type="Switch LAG Member Port Description", 
                            category_key="device",
                            site_filter=c_site
                        ))
                
                display_reference_box(
                    category_key="device",
                    default_lines="LACP_to_SWUSNYC02-0_Gi1/0/1\nLACP_to_FWUSNYC01_Po1\nLACP_to_SWUSLONCORE01_Te1/0/1",
                    label="LAG Member Port",
                    site_filter=c_site
                )
            
            elif p_cat == "Switch Port-Channel (Logical)":
                local_po_id = st.text_input("Local Port-Channel ID", value="LAG1", placeholder="e.g. LAG1, LAG2, Po1", key="pc_local_id").strip()
                r_dev_po = st.text_input("Remote Device Hostname", value="", placeholder="e.g. SWUSNYC02-0, CV-CPD", key="pc_rd").strip()

                # Use pattern from naming rules
                pattern = naming_rules.get("switch_port_channel", "<Local_Po_ID>_to_<Remote_Device>")
                po_desc = apply_pattern(pattern, {
                    "Local_Po_ID": local_po_id if local_po_id else "<Local_Po_ID>",
                    "Remote_Device": r_dev_po if r_dev_po else "<Remote_Device>"
                })

                st.caption(f"Generated Port-Channel Description:")
                st.code(po_desc, language="text")
                
                if st.button("🤖 AI Verify Port-Channel Description", key="ai_chk_po"):
                    with st.spinner("Auditing against Standards..."):
                        st.info(verify_and_suggest_with_ai(
                            po_desc, 
                            active_model, 
                            asset_type="Switch Port-Channel Description", 
                            category_key="device",
                            site_filter=c_site
                        ))
                
                display_reference_box(
                    category_key="device",
                    default_lines="LAG1_to_SWUSNYC02-0\nLAG2_to_FWUSNYC01\nLAG5_to_CV-CPD",
                    label="Port-Channel Interface",
                    site_filter=c_site
                )
            
            elif p_cat == "Switch Access Port (Endpoint)":
                access_vlan_id = st.text_input("Access VLAN ID (Optional)", value="", placeholder="e.g. 10, 100", key="ac_vlan").strip()
                access_vlan_name = st.text_input("VLAN Name (Optional)", value="", placeholder="e.g. Data, Voice, Guest", key="ac_vlan_name").strip()
                endpoint_device = st.text_input("Connected Device/Host", value="", placeholder="e.g. PC-001, Printer-Lab", key="ac_device").strip()
                endpoint_port = st.text_input("Endpoint Port (Optional)", value="", placeholder="e.g. eth0, NIC1", key="ac_port").strip()

                # Use pattern from naming rules
                pattern = naming_rules.get("switch_access_desc", "<VLAN_Name> - <Device>_<Port>")
                
                # Build device and port parts
                device_val = endpoint_device if endpoint_device else "<Device>"
                port_val = endpoint_port if endpoint_port else "<Port>"
                device_port = f"{device_val}_{port_val}" if endpoint_port else device_val
                
                # Determine VLAN display
                vlan_display = ""
                if access_vlan_name:
                    vlan_display = access_vlan_name
                elif access_vlan_id:
                    vlan_display = f"VLAN{access_vlan_id}"
                
                # Apply pattern with VLAN or without
                if vlan_display:
                    access_desc = apply_pattern(pattern, {
                        "VLAN_ID": access_vlan_id if access_vlan_id else "<VLAN_ID>",
                        "VLAN_Name": vlan_display,
                        "Device": device_val,
                        "Port": port_val
                    })
                    # If pattern doesn't contain VLAN placeholders, use simple format
                    if "<VLAN" not in pattern:
                        access_desc = f"{vlan_display} - {device_port}"
                else:
                    # No VLAN, just device_port
                    access_desc = device_port

                st.caption(f"Generated Access Port Description:")
                st.code(access_desc, language="text")
                
                if st.button("🤖 AI Verify Access Port Description", key="ai_chk_access"):
                    with st.spinner("Auditing against Standards..."):
                        st.info(verify_and_suggest_with_ai(
                            access_desc, 
                            active_model, 
                            asset_type="Switch Access Port Description", 
                            category_key="device",
                            site_filter=c_site
                        ))
                
                display_reference_box(
                    category_key="device",
                    default_lines="Data - PC-001_eth0\nVoice - IP-Phone-101_PoE\nGuest - Printer-Lab_NIC1",
                    label="Access Port Interface",
                    site_filter=c_site
                )
            
            elif p_cat == "Firewall Security Zone Interface":
                fw_role = st.text_input("Role / Zone", value="", placeholder="e.g. TRUST, UNTRUST, DMZ", key="fw_role").strip()
                fw_vlan_id = st.text_input("VLAN ID", value="", placeholder="e.g. 10, 100", key="fw_vlan").strip()

                # Use pattern from naming rules
                pattern = naming_rules.get("firewall_interface", "<Role_Zone>_<VLAN_ID>")
                fw_desc = apply_pattern(pattern, {
                    "Role_Zone": fw_role if fw_role else "<Role_Zone>",
                    "VLAN_ID": fw_vlan_id if fw_vlan_id else "<VLAN_ID>"
                })

                st.caption(f"Generated Firewall Interface Description:")
                st.code(fw_desc, language="text")
                
                if st.button("🤖 AI Verify Firewall Interface Description", key="ai_chk_fw"):
                    with st.spinner("Auditing against Standards..."):
                        st.info(verify_and_suggest_with_ai(
                            fw_desc, 
                            active_model, 
                            asset_type="Firewall Interface Description", 
                            category_key="device",
                            site_filter=c_site
                        ))
                
                display_reference_box(
                    category_key="device",
                    default_lines="TRUST_10\nUNTRUST_100\nDMZ_50",
                    label="Firewall Interface",
                    site_filter=c_site
                )

    # Hosts & VMs
    elif "2. Hosts" in naming_cat:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### 🖥️ ESXi Hypervisor Hostname")
            h_site = st.text_input("Site Prefix", value="", placeholder="e.g. age, nyc, lon, syd", key="esx_site").strip()
            h_role = st.text_input("Host Role (Optional)", value="", placeholder="e.g. esx, otinfhost, infhost", key="esx_role").strip()
            h_num = st.text_input("Host Sequence Number", value="001", placeholder="e.g. 001, 01, 1", key="esx_num").strip()
            h_dom = st.text_input("Domain Name (FQDN Suffix)", value="", placeholder="e.g. corp.example.com, internal.net, corp.local", key="esx_dom").strip()

            # Use pattern from naming rules if available
            if "esxi_host" in naming_rules and naming_rules["esxi_host"]:
                pattern = naming_rules["esxi_host"]
                # Remove any comments in parentheses from the pattern
                import re
                pattern = re.sub(r'\s*\([^)]*\)', '', pattern).strip()
                
                # Apply pattern with variables
                gen_esx_raw = apply_pattern(pattern, {
                    "Site": h_site,
                    "site": h_site.lower(),
                    "Role": h_role if h_role else "esx",
                    "role": (h_role if h_role else "esx").lower(),
                    "Seq": h_num,
                    "seq": h_num,
                    "Domain": h_dom,
                    "domain": h_dom.lower()
                })
                
                # Remove any unfilled variables
                gen_esx_raw = re.sub(r'<[^>]+>', '', gen_esx_raw)
                # Clean up dots and extra characters
                gen_esx_raw = re.sub(r'\.+', '.', gen_esx_raw).strip('.')
                
                # Apply casing to the hostname part (before domain)
                if '.' in gen_esx_raw and h_dom:
                    parts = gen_esx_raw.split('.', 1)
                    gen_esx = f"{apply_case(parts[0], case_mode)}.{parts[1].lower()}"
                else:
                    gen_esx = apply_case(gen_esx_raw, case_mode)
            else:
                # Fallback to manual construction
                raw_host = f"{h_site}{h_role or 'esx'}{h_num}"
                host_formatted = apply_case(raw_host, case_mode)
                gen_esx = f"{host_formatted}.{h_dom.lower()}" if h_dom else host_formatted

            st.caption("Generated ESXi Hostname:")
            st.code(gen_esx, language="text")

            if st.button("🤖 AI Verify ESXi Host", key="ai_chk_esx"):
                with st.spinner("Auditing against NetBox Data & standards..."):
                    st.info(verify_and_suggest_with_ai(
                        gen_esx, 
                        active_model, 
                        asset_type="ESXi Hypervisor Hostname", 
                        category_key="hypervisor",
                        site_filter=h_site
                    ))

            display_reference_box(
                category_key="hypervisor",
                default_lines="NYCESX001.corp.internal  (Enterprise ESXi Node 001)\nLONESX001.corp.internal  (Enterprise ESXi Node 001)\nSYDESX01.corp.local      (Branch Hypervisor Standalone)",
                label="Hypervisor",
                site_filter=h_site
            )

        with col_b:
            st.markdown("#### 🖲️ Virtual Machine (VM) Hostname")
            v_site = st.text_input("Site Prefix / Country & Site", value="", placeholder="e.g. age, usnyc, uklon", key="vm_site").strip()
            v_role = st.text_input("Role Code / Workload", value="", placeholder="e.g. app, web, db, fs, dc", key="vm_role").strip()
            v_seq = st.text_input("Sequence Number", value="01", placeholder="e.g. 01, 02", key="vm_seq").strip()

            # Use pattern from naming rules if available
            if "vm_host" in naming_rules and naming_rules["vm_host"]:
                pattern = naming_rules["vm_host"]
                # Remove any comments in parentheses from the pattern
                import re
                pattern = re.sub(r'\s*\([^)]*\)', '', pattern).strip()
                # If multiple patterns separated by "or", use the first one
                if " or " in pattern:
                    pattern = pattern.split(" or ")[0].strip()
                
                # Apply pattern with variables
                gen_vm_raw = apply_pattern(pattern, {
                    "Site": v_site,
                    "site": v_site.lower(),
                    "Country": v_site[:2] if len(v_site) > 2 else v_site,  # First 2 chars as country
                    "Role": v_role,
                    "role": v_role.lower(),
                    "Seq": v_seq,
                    "seq": v_seq
                })
                
                # Remove any unfilled variables
                gen_vm_raw = re.sub(r'<[^>]+>', '', gen_vm_raw)
                
                gen_vm = apply_case(gen_vm_raw, case_mode)
            else:
                # Fallback to manual construction
                raw_vm = f"{v_site}{v_role}{v_seq}"
                gen_vm = apply_case(raw_vm, case_mode)

            st.caption("Generated VM Hostname:")
            st.code(gen_vm, language="text")

            if st.button("🤖 AI Verify VM Hostname", key="ai_chk_vm"):
                with st.spinner("Auditing against NetBox Data & standards..."):
                    st.info(verify_and_suggest_with_ai(
                        gen_vm, 
                        active_model, 
                        asset_type="Virtual Machine (VM) Hostname", 
                        category_key="vm",
                        site_filter=v_site
                    ))

            display_reference_box(
                category_key="vm",
                default_lines="USNYCAPP01     (NYC Application Server 01)\nUKLONDB01      (London Database Server 01)\nAUSYDFS01      (Sydney File Server 01)",
                label="Virtual Machine",
                site_filter=v_site
            )

    # ESXi Network Descriptions
    else:
        auto_correct = st.checkbox(
            "⚡ Auto-Correct VMware Syntax (e.g. vswitch1 -> vSwitch1, nic0 -> vmnic0)",
            value=True,
            help="When checked, automatically normalizes vSwitch and vmnic naming."
        )

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("#### 1. Physical Uplink :green[(PCIeX/PortX)]")
            vmnic_raw = st.text_input("vmnic Name", value="vmnic", placeholder="e.g. vmnic0", key="vmnic_in")
            vsw1_raw = st.text_input("vSwitch Name", value="vSwitch", placeholder="e.g. vSwitch0", key="vsw1")
            vmnic_purpose = st.text_input("Purpose / Service", value="", placeholder="e.g. Management, vMotion, Storage", key="vmnic_purpose")
            status = st.radio("Status", ["Active Uplink", "Standby Uplink"], horizontal=True, key="vmnic_status")
            
            # Apply normalization - always normalize vSwitch regardless of auto_correct
            if vmnic_raw:
                clean_vmnic = normalize_vmnic(vmnic_raw) if auto_correct else vmnic_raw.strip()
            else:
                clean_vmnic = ""
                
            if vsw1_raw:
                # Always normalize vSwitch to fix common typos like "vswtich"
                clean_vsw1 = normalize_vswitch(vsw1_raw)
            else:
                clean_vsw1 = ""
            
            # Normalize purpose name for proper capitalization
            if vmnic_purpose:
                clean_purpose = normalize_network_name(vmnic_purpose.strip())
            else:
                clean_purpose = ""
            
            # Only generate if we have actual values
            if clean_vmnic and clean_vsw1:
                # Build with purpose if provided
                if clean_purpose:
                    gen_vmnic = f"{clean_vmnic} - {clean_vsw1} {clean_purpose} {status}"
                else:
                    gen_vmnic = f"{clean_vmnic} - {clean_vsw1} {status}"
            else:
                # Show placeholder pattern
                if clean_purpose:
                    gen_vmnic = f"{clean_vmnic or '<vmnic>'} - {clean_vsw1 or '<vSwitch>'} {clean_purpose} {status}"
                else:
                    gen_vmnic = f"{clean_vmnic or '<vmnic>'} - {clean_vsw1 or '<vSwitch>'} {status}"
            
            st.caption("Generated Physical Uplink Description:")
            st.code(gen_vmnic, language="text")

        with col_b:
            st.markdown("#### 2. Port Group Teaming :green[(Network)]")
            pg_network_raw = st.text_input("Network", value="", placeholder="e.g. VM Network", key="pg_network_in", label_visibility="visible")
            vsw_pg_raw = st.text_input("vSwitch Name", value="vSwitch", placeholder="e.g. vSwitch0", help="Port Group name (defaults to PG- prefix).", key="vsw2")
            act_nics_raw = st.text_input("Active vmnics", value="", placeholder="e.g. vmnic0, vmnic1", key="act_nics_in")
            stb_nics_raw = st.text_input("Standby vmnics (Optional)", value="", placeholder="e.g. vmnic2", key="stb_nics_in")
            
            # Apply normalization - always normalize network name and vSwitch
            clean_pg_network = normalize_network_name(pg_network_raw) if pg_network_raw.strip() else ""
            # Always normalize vSwitch to fix common typos like "vswtich"
            clean_vsw_pg = normalize_vswitch(vsw_pg_raw) if vsw_pg_raw.strip() else ""
            
            if act_nics_raw:
                clean_act = normalize_vmnic_list(act_nics_raw) if auto_correct else act_nics_raw.strip()
            else:
                clean_act = ""
                
            if stb_nics_raw:
                clean_stb = normalize_vmnic_list(stb_nics_raw) if auto_correct else stb_nics_raw.strip()
            else:
                clean_stb = ""

            # Generate PG- prefix name
            gen_pg_prefix = f"PG-{clean_pg_network}" if clean_pg_network else "PG-<pg_network>"
            
            # Build the teaming description
            if clean_vsw_pg and clean_act:
                # Build the active/standby string
                if clean_stb:
                    # Both active and standby
                    gen_pg = f"{clean_vsw_pg} ({clean_act} Active / {clean_stb} Standby)"
                else:
                    # Only active
                    gen_pg = f"{clean_vsw_pg} ({clean_act} Active)"
            else:
                # Show placeholder pattern
                if clean_act:
                    gen_pg = f"<vSwitch> ({clean_act} Active)"
                else:
                    gen_pg = "<vSwitch> (<Active_vmnics> Active)"
            
            st.caption("Generated Port Group Name:")
            st.code(gen_pg_prefix, language="text")
            st.caption("Generated Port Group Description:")
            st.code(gen_pg, language="text")

        with col_c:
            st.markdown("#### 3. VMkernel Adapter (`vmk`)")
            vmk_name_raw = st.text_input("vmk Name", value="vmk", placeholder="e.g. vmk0, vmk1", key="vmk_name_in")
            vmk_purp = st.text_input("Purpose / Service", value="", placeholder="e.g. Management, vMotion, Storage", key="vmk_p_in").strip()
            vsw_vmk_raw = st.text_input("vSwitch Name", value="vSwitch", placeholder="e.g. vSwitch0", key="vsw3")
            vmk_act_nics_raw = st.text_input("Active vmnics (Optional)", value="", placeholder="e.g. vmnic0, vmnic1", key="vmk_act_nics_in")
            vmk_stb_nics_raw = st.text_input("Standby vmnics (Optional)", value="", placeholder="e.g. vmnic2", key="vmk_stb_nics_in")
            
            # Apply normalization - always normalize vSwitch and purpose
            if vsw_vmk_raw:
                # Always normalize vSwitch to fix common typos like "vswtich"
                clean_vsw_vmk = normalize_vswitch(vsw_vmk_raw)
            else:
                clean_vsw_vmk = ""
            
            # Normalize purpose name for proper capitalization (e.g., "vmontion" -> "vMotion")
            if vmk_purp:
                clean_vmk_purp = normalize_network_name(vmk_purp)
            else:
                clean_vmk_purp = ""
            
            # Clean vmk name
            clean_vmk_name = vmk_name_raw.strip() if vmk_name_raw else ""
            
            # Normalize vmnic lists
            if vmk_act_nics_raw:
                clean_vmk_act = normalize_vmnic_list(vmk_act_nics_raw) if auto_correct else vmk_act_nics_raw.strip()
            else:
                clean_vmk_act = ""
                
            if vmk_stb_nics_raw:
                clean_vmk_stb = normalize_vmnic_list(vmk_stb_nics_raw) if auto_correct else vmk_stb_nics_raw.strip()
            else:
                clean_vmk_stb = ""
            
            # Build the VMkernel description
            # Format: "Management Network - vSwitch0 (vmnic0 Active / vmnic1 Standby)"
            if clean_vmk_purp:
                # Start with purpose
                if clean_vmk_purp.lower() in ['management', 'vmotion', 'storage', 'iscsi']:
                    purpose_display = f"{clean_vmk_purp} Network"
                else:
                    purpose_display = clean_vmk_purp
            else:
                purpose_display = "<Purpose>"
            
            # Build teaming part
            if clean_vsw_vmk and (clean_vmk_act or clean_vmk_stb):
                # Has vSwitch and at least one vmnic
                if clean_vmk_act and clean_vmk_stb:
                    # Both active and standby
                    teaming_part = f"{clean_vsw_vmk} ({clean_vmk_act} Active / {clean_vmk_stb} Standby)"
                elif clean_vmk_act:
                    # Only active
                    teaming_part = f"{clean_vsw_vmk} ({clean_vmk_act} Active)"
                else:
                    # Only standby (unusual but handle it)
                    teaming_part = f"{clean_vsw_vmk} ({clean_vmk_stb} Standby)"
                
                fallback_disp = f"{purpose_display} - {teaming_part}"
            elif clean_vsw_vmk:
                # Has vSwitch but no vmnics
                fallback_disp = f"{purpose_display} ({clean_vsw_vmk})"
            else:
                # No vSwitch
                fallback_disp = f"{purpose_display} (<vSwitch>)"

            st.caption("Generated vmk Name:")
            st.code(clean_vmk_name if clean_vmk_name else "<vmk>", language="text")
            st.caption("Generated VMkernel Description:")
            st.code(fallback_disp, language="text")


_TOKEN_RE = re.compile(r"<([^<>]+)>")


def _structured_naming_rules(raw_rules):
    """Return patterns and variable metadata from structured or legacy rules."""
    if not isinstance(raw_rules, dict):
        return {}, {}
    patterns = raw_rules.get("naming_patterns") or raw_rules.get("patterns")
    variables = raw_rules.get("pattern_variables") or {}
    if isinstance(patterns, dict) and any(isinstance(value, dict) for value in patterns.values()):
        patterns = {
            pattern_key: pattern_value
            for group in patterns.values() if isinstance(group, dict)
            for pattern_key, pattern_value in group.items()
        }
    if not isinstance(patterns, dict):
        # load_naming_rules currently returns the legacy flat mapping.
        patterns = {
            key: value for key, value in raw_rules.items()
            if isinstance(value, str)
        }
    return patterns, variables if isinstance(variables, dict) else {}


def _pattern_options(pattern):
    if not isinstance(pattern, str):
        return []
    cleaned = re.sub(r"\s*\([^)]*\)", "", pattern).strip()
    return [part.strip() for part in re.split(r"\s+(?:or|/|\|)\s+", cleaned) if part.strip()]


def _render_token_pattern(pattern, variables, key, case_mode):
    """Render one configured pattern; empty values intentionally retain literals."""
    tokens = list(dict.fromkeys(_TOKEN_RE.findall(pattern)))
    values = {}
    columns = st.columns(2) if tokens else []
    for index, token in enumerate(tokens):
        metadata = variables.get(token, {}) if isinstance(variables, dict) else {}
        if isinstance(metadata, str):
            metadata = {"label": metadata}
        if not isinstance(metadata, dict):
            metadata = {}
        label = metadata.get("label", token.replace("_", " "))
        default = str(metadata.get("default", ""))
        placeholder = metadata.get("placeholder", "")
        with columns[index % 2] if columns else st.container():
            value = st.text_input(
                str(label), value=default, placeholder=str(placeholder),
                key=f"naming_token_{key}_{index}_{token}"
            )
        values[token] = value.strip()

    rendered = _TOKEN_RE.sub(lambda match: values.get(match.group(1), ""), pattern)
    return apply_case(rendered, case_mode)


def render_naming_tab(active_model):
    """Render all naming asset classes from configured tokenized patterns."""
    st.subheader("🏷️ Standardized Infrastructure Naming Generator")
    st.caption("Generate and validate standardized names from the naming rules configured in Standards.")

    from config.naming_rules import load_naming_rules
    raw_rules = load_naming_rules()
    st.session_state["naming_rules"] = raw_rules
    case_mode = render_compact_toolbar(active_model)
    naming_cat = st.radio(
        "Select Asset Class",
        [
            "1. Network & Security Devices (Switches, APs, Firewalls, Routers)",
            "2. Hosts & Virtual Machines (ESXi & VMs)",
            "3. ESXi Network Descriptions (vmnic, PortGroup, VMkernel)",
        ], horizontal=True, key="naming_asset_class",
    )
    st.markdown("---")

    patterns, variables = _structured_naming_rules(raw_rules)
    if "1. Network" in naming_cat:
        keys = ["branch_switch", "branch_ap", "branch_security", "switch_uplink_desc", "switch_uplink_local", "switch_uplink_remote",
                "switch_lag_member", "switch_port_channel", "switch_access_desc", "firewall_interface"]
        category_key, asset_type = "device", "Network/Security Asset"
    elif "2. Hosts" in naming_cat:
        keys = ["esxi_host", "vm_host"]
        category_key, asset_type = "hypervisor", "Host or Virtual Machine"
    else:
        keys = ["esxi_uplink", "esxi_portgroup", "esxi_portgroup_name", "esxi_portgroup_desc", "esxi_vmkernel", "esxi_vmkernel_name", "esxi_vmkernel_desc"]
        category_key, asset_type = "device", "ESXi Network Description"

    configured = [(key, patterns.get(key)) for key in keys if patterns.get(key)]
    if not configured:
        st.warning("No naming patterns are configured for this asset class.")
        return

    labels = {key: key.replace("_", " ").title() for key, _ in configured}
    selected_key = st.selectbox("Naming Pattern", [key for key, _ in configured],
                                format_func=lambda key: labels[key], key="naming_pattern_selector")
    options = _pattern_options(patterns[selected_key])
    selected_pattern = st.selectbox("Pattern Variant", options, key=f"naming_variant_{selected_key}") if len(options) > 1 else options[0]
    generated = _render_token_pattern(selected_pattern, variables, selected_key, case_mode)

    st.caption("Generated Name / Description:")
    st.code(generated, language="text")
    if st.button("🤖 AI Verify / Suggest", key="ai_verify_generic_naming"):
        with st.spinner("Auditing against NetBox Data & standards..."):
            st.info(verify_and_suggest_with_ai(
                generated, active_model, asset_type=f"{asset_type}: {labels[selected_key]}",
                category_key=category_key, site_filter="",
            ))
