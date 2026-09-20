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
from core.naming_dynamic_helper import (
    interpolate_pattern,
    render_token_widgets,
    render_esxi_network_inputs,
    render_edit_mode_ui,
    extract_tokens,
    pick_sub_pattern,
)

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


import re

from config.naming_rules import get_naming_patterns, get_pattern_variables, load_naming_rules
from utils.formatters import normalize_network_name


DEVICE_TYPE_OPTIONS = [
    "SW (Switch)", "VS (Virtual Chassis / Stack)", "OTSW (OT Switch)",
    "WAP (Wireless Access Point)", "FW (Firewall / Security Appliance)",
    "ION (Prisma SD-WAN)", "VA (Virtual Appliance)", "RTR (Router)", "Custom Prefix...",
]
INTERFACE_OPTIONS = [
    "Switch Uplink (Inter-Switch)",
    "Switch LAG Member (LACP)",
    "Switch Port-Channel (Logical)",
    "Switch Access Port (Endpoint)",
    "Firewall Security Zone Interface",
]


def _edit_toggle(pattern_key):
    return st.toggle(
        "Edit Mode",
        value=st.session_state.get(f"edit_mode_{pattern_key}", False),
        key=f"edit_mode_{pattern_key}",
        help="Turn ON to edit the raw pattern template and add new variables.",
    )


def _sel_pattern_key(dev_type_preset):
    if "WAP" in dev_type_preset:
        return "branch_ap"
    elif "ION" in dev_type_preset or "FW" in dev_type_preset:
        return "branch_security"
    return "branch_switch"


def _interface_key(opt):
    return {
        INTERFACE_OPTIONS[0]: "switch_uplink_desc",
        INTERFACE_OPTIONS[1]: "switch_lag_member",
        INTERFACE_OPTIONS[2]: "switch_port_channel",
        INTERFACE_OPTIONS[3]: "switch_access_desc",
        INTERFACE_OPTIONS[4]: "firewall_interface",
    }.get(opt, "switch_uplink_desc")


def _interface_ref(opt):
    return {
        INTERFACE_OPTIONS[0]: ("Switch Uplink Interface", "Uplink_to_SWUSNYC02-0_Gi1/0/48\nUplink_to_FWUSNYC01_Te1/0/1\nUplink_to_Huawei-Core_XGE0/0/31"),
        INTERFACE_OPTIONS[1]: ("LAG Member Port", "LACP_to_SWUSNYC02-0_Gi1/0/1\nLACP_to_FWUSNYC01_Po1\nLACP_to_SWUSLONCORE01_Te1/0/1"),
        INTERFACE_OPTIONS[2]: ("Port-Channel Interface", "LAG1_to_SWUSNYC02-0\nLAG2_to_FWUSNYC01\nLAG5_to_CV-CPD"),
        INTERFACE_OPTIONS[3]: ("Access Port Interface", "Data - PC-001_eth0\nVoice - IP-Phone-101_PoE\nGuest - Printer-Lab_NIC1"),
        INTERFACE_OPTIONS[4]: ("Firewall Interface", "TRUST_10\nUNTRUST_100\nDMZ_50"),
    }.get(opt, ("Interface", ""))


def _ref_info(dev_type):
    if "SW" in dev_type or "Switch" in dev_type:
        return "Switch", "SW", "SWUSNYC01-0       (Switch Stack, Member 0)\nSWUSLON01         (London Switch 01)"
    if "WAP" in dev_type or "Wireless" in dev_type:
        return "Wireless AP", "WAP", "WAPUSNYC01        (Access Point NYC 01)\nWAPUSLON01        (Access Point London 01)"
    if "FW" in dev_type or "Firewall" in dev_type:
        return "Firewall", "FW", "FWUSNYC01         (NYC Firewall 01)\nFWUSNYCPA01       (NYC Palo Alto FW 01)"
    if "RTR" in dev_type or "Router" in dev_type:
        return "Router", "RTR", "RTRUSNYC01        (NYC Router 01)\nRTRUSLON01"
    if "ION" in dev_type:
        return "SD-WAN ION", "ION", "IONUSNYC01        (NYC SD-WAN 01)\nIONUSLON01"
    return "Device", "", "SWUSNYC01-0       (Switch Stack)\nWAPUSNYC01        (Access Point 01)\nFWUSNYCPA01       (Firewall 01)"


def render_naming_tab(active_model):
    st.subheader("Standardized Infrastructure Naming Generator")
    st.caption("Generate and validate standardized hostnames for network devices, servers, VMs, and ESXi configurations using AI-powered naming conventions aligned with your NetBox inventory data.")

    naming_rules = load_naming_rules()
    st.session_state["naming_rules"] = naming_rules
    naming_patterns = get_naming_patterns(naming_rules)
    variables = get_pattern_variables(naming_rules)
    case_mode = render_compact_toolbar(active_model)

    naming_cat = st.radio(
        "Select Asset Class",
        [
            "1. Network & Security Devices (Switches, APs, Firewalls, Routers)",
            "2. Hosts & Virtual Machines (ESXi & VMs)",
            "3. ESXi Network Descriptions (vmnic, PortGroup, VMkernel)",
        ],
        horizontal=True,
    )
    st.markdown("---")

    if "1. Network" in naming_cat:
        _asset_class_1(case_mode, active_model, naming_patterns, variables)
    elif "2. Hosts" in naming_cat:
        _asset_class_2(case_mode, active_model, naming_patterns, variables)
    else:
        _asset_class_3(case_mode, active_model, naming_patterns, variables)


def _asset_class_1(case_mode, active_model, naming_patterns, variables):
    st.markdown("##### Location & Site Code Assistant")
    loc_c1, loc_c2 = st.columns([2, 1])
    with loc_c1:
        loc = st.text_input("Location / City Name", value="", placeholder="e.g. Sydney, London", key="loc_input_help")
    with loc_c2:
        auto_code = compute_suggested_site_code(loc) if loc else ""
        st.info(f"Suggested Site Code: **{auto_code or '----'}**")
    st.markdown("---")

    col_a, col_b = st.columns([1, 1])
    with col_a:
        st.markdown("#### Universal Device Hostname Generator")
        dev_type = st.selectbox("Device Type / Prefix", DEVICE_TYPE_OPTIONS, index=0, key="dev_prefix_sel")
        if "Custom" in dev_type:
            dev_prefix = st.text_input("Enter Custom Prefix", value="", placeholder="e.g. SVR, GW", key="dev_custom_pre").strip()
        else:
            dev_prefix = dev_type.split()[0].strip()

        pk = _sel_pattern_key(dev_type)
        edit_on = _edit_toggle(pk)
        pat = naming_patterns.get(pk, "")
        if edit_on:
            render_edit_mode_ui(pk, pat, variables)
            st.stop()
            return

        pat = pick_sub_pattern(pat, dev_type)
        defaults = {"Seq": "01"}
        if auto_code:
            defaults["Site"] = auto_code
        values = render_token_widgets(pat, variables, "dev", defaults)
        interpolated = interpolate_pattern(pat, {k: v for k, v in values.items() if v})
        if not any(values.values()):
            interpolated = interpolate_pattern(pat, {k: f"<{k}>" for k in values})
        final = apply_case(interpolated, case_mode)
        st.caption("Generated Device Hostname:")
        st.code(final, language="text")

        if st.button("AI Verify / Suggest Device Hostname", key="ai_chk_dev"):
            with st.spinner("Auditing..."):
                st.info(verify_and_suggest_with_ai(final, active_model, asset_type=f"Network/Security Device ({dev_type})", category_key="device", site_filter=values.get("Site", "")))

        ref_lbl, ref_flt, ref_ex = _ref_info(dev_type)
        display_reference_box("device", ref_ex, ref_lbl, values.get("Site", ""), ref_flt)

    with col_b:
        st.markdown("#### Switch & Firewall Interface Formatter")
        intf_type = st.radio("Interface Type", INTERFACE_OPTIONS, key="p_cat_sel")
        ipk = _interface_key(intf_type)
        edit_on = _edit_toggle(ipk)
        pat = naming_patterns.get(ipk, "")
        if edit_on:
            render_edit_mode_ui(ipk, pat, variables)
            st.stop()
            return

        if ipk == "switch_access_desc":
            vlan_id = st.text_input("Access VLAN ID (Optional)", value="", placeholder="e.g. 10, 100", key="ac_vlan").strip()
            vlan_name = st.text_input("VLAN Name (Optional)", value="", placeholder="e.g. Data, Voice", key="ac_vlan_name").strip()
            dev_end = st.text_input("Connected Device/Host", value="", placeholder="e.g. PC-001", key="ac_device").strip()
            port_end = st.text_input("Endpoint Port (Optional)", value="", placeholder="e.g. eth0", key="ac_port").strip()
            vlan_disp = vlan_name or (f"VLAN{vlan_id}" if vlan_id else "")
            vals = {"VLAN_ID": vlan_id or "<VLAN_ID>", "VLAN_Name": vlan_disp, "Device": dev_end or "<Device>", "Port": port_end or "<Port>"}
            gen = interpolate_pattern(pat, {k: v for k, v in vals.items() if v})
        else:
            vals = render_token_widgets(pat, variables, f"intf_{ipk}")
            gen = interpolate_pattern(pat, {k: (v if v else f"<{k}>") for k, v in vals.items()})

        st.caption("Generated Interface Description:")
        st.code(gen, language="text")
        ref_lbl, ref_ex = _interface_ref(intf_type)
        if st.button("AI Verify Interface Description", key="ai_chk_intf"):
            with st.spinner("Auditing..."):
                st.info(verify_and_suggest_with_ai(gen, active_model, asset_type=ref_lbl, category_key="device", site_filter=""))
        display_reference_box("device", ref_ex, ref_lbl, "")


def _asset_class_2(case_mode, active_model, naming_patterns, variables):
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### ESXi Hypervisor Hostname")
        pk = "esxi_host"
        pat = naming_patterns.get(pk, "")
        if _edit_toggle(pk):
            render_edit_mode_ui(pk, pat, variables)
            st.stop()
            return
        values = render_token_widgets(pat, variables, "esx", defaults={"seq": "001"})
        gen_raw = interpolate_pattern(pat, {k: v for k, v in values.items() if v})
        gen_raw = re.sub(r"\s*\([^)]*\)", "", gen_raw).strip()
        gen_raw = re.sub(r"<[^>]+>", "", gen_raw)
        gen_raw = re.sub(r"\.+", ".", gen_raw).strip(".")
        if "." in gen_raw:
            parts = gen_raw.split(".", 1)
            gen = f"{apply_case(parts[0], case_mode)}.{parts[1].lower()}"
        else:
            gen = apply_case(gen_raw, case_mode)
        st.caption("Generated ESXi Hostname:")
        st.code(gen, language="text")
        if st.button("AI Verify ESXi Host", key="ai_chk_esx"):
            with st.spinner("Auditing..."):
                st.info(verify_and_suggest_with_ai(gen, active_model, asset_type="ESXi Hypervisor Hostname", category_key="hypervisor", site_filter=values.get("site", "")))
        display_reference_box("hypervisor", "NYCESX001.corp.internal\nLONESX001.corp.internal\nSYDESX01.corp.local", "Hypervisor", site_filter=values.get("site", ""))

    with col_b:
        st.markdown("#### Virtual Machine (VM) Hostname")
        pk = "vm_host"
        pat = naming_patterns.get(pk, "")
        if _edit_toggle(pk):
            render_edit_mode_ui(pk, pat, variables)
            st.stop()
            return
        values = render_token_widgets(pat, variables, "vm", defaults={"Seq": "01"})
        gen_raw = interpolate_pattern(pat, {k: v for k, v in values.items() if v})
        gen_raw = re.sub(r"\s*\([^)]*\)", "", gen_raw).strip()
        gen_raw = re.sub(r"\s+or\s+.*", "", gen_raw).strip()
        gen_raw = re.sub(r"<[^>]+>", "", gen_raw)
        gen = apply_case(gen_raw, case_mode)
        st.caption("Generated VM Hostname:")
        st.code(gen, language="text")
        if st.button("AI Verify VM Hostname", key="ai_chk_vm"):
            with st.spinner("Auditing..."):
                st.info(verify_and_suggest_with_ai(gen, active_model, asset_type="Virtual Machine (VM) Hostname", category_key="vm", site_filter=values.get("site", "")))
        display_reference_box("vm", "USNYCAPP01     (NYC Application Server 01)\nUKLONDB01\nAUSYDFS01", "Virtual Machine", site_filter=values.get("site", ""))


def _asset_class_3(case_mode, active_model, naming_patterns, variables):
    auto_correct = st.checkbox(
        "Auto-Correct VMware Syntax (vswitch1 -> vSwitch1, nic0 -> vmnic0)",
        value=True, key="esxi_auto_corr",
        help="When checked, automatically normalizes vSwitch and vmnic naming.",
    )
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("#### 1. Physical Uplink (PCIeX/PortX)")
        pk = "esxi_uplink"
        pat = naming_patterns.get(pk, "")
        if _edit_toggle(pk):
            render_edit_mode_ui(pk, pat, variables)
            st.stop()
            return
        vals = render_esxi_network_inputs(pat, variables, "uplink", auto_correct)
        gen = interpolate_pattern(pat, {k: v for k, v in vals.items() if v})
        if not any(vals.values()):
            gen = interpolate_pattern(pat, {k: f"<{k}>" for k in vals})
        st.caption("Generated Physical Uplink Description:")
        st.code(gen, language="text")
    with col_b:
        st.markdown("#### 2. Port Group Teaming (Network)")
        pk = "esxi_portgroup"
        pat = naming_patterns.get(pk, "")
        if _edit_toggle(pk):
            render_edit_mode_ui(pk, pat, variables)
            st.stop()
            return
        pg_net = st.text_input("Network", value="", placeholder="e.g. VM Network", key="pg_network_in", label_visibility="visible").strip()
        pg_net = normalize_network_name(pg_net) if pg_net else ""
        gen_prefix = f"PG-{pg_net}" if pg_net else "PG-<pg_network>"
        vsw = st.text_input("vSwitch Name", value="vSwitch", placeholder="e.g. vSwitch0", help="Port Group name (defaults to PG- prefix).", key="pg_vsw").strip()
        vals = render_esxi_network_inputs(pat, variables, "portgroup", auto_correct)
        vals["pg_network"] = pg_net
        vals["PortGroup"] = vsw or "<PortGroup>"
        clean = {k: v for k, v in vals.items() if v}
        gen_desc = interpolate_pattern(pat, clean) if clean else interpolate_pattern(pat, {k: f"<{k}>" for k in vals})
        st.caption("Generated Port Group Name:")
        st.code(gen_prefix, language="text")
        st.caption("Generated Port Group Description:")
        st.code(gen_desc, language="text")
    with col_c:
        st.markdown("#### 3. VMkernel Adapter (vmk)")
        pk = "esxi_vmkernel"
        pat = naming_patterns.get(pk, "")
        if _edit_toggle(pk):
            render_edit_mode_ui(pk, pat, variables)
            st.stop()
            return
        vmk_name = st.text_input("vmk Name", value="vmk", placeholder="e.g. vmk0, vmk1", key="vmk_name_in").strip()
        vals = render_esxi_network_inputs(pat, variables, "vmk", auto_correct)
        if vals.get("Purpose"):
            p = vals["Purpose"]
            vals["Purpose"] = f"{p} Network" if p.lower() in ("management", "vmotion", "storage", "iscsi") else p
        clean = {k: v for k, v in vals.items() if v}
        gen = interpolate_pattern(pat, clean) if clean else interpolate_pattern(pat, {k: f"<{k}>" for k in vals})
        st.caption("Generated vmk Name:")
        st.code(vmk_name or "<vmk>", language="text")
        st.caption("Generated VMkernel Description:")
        st.code(gen, language="text")