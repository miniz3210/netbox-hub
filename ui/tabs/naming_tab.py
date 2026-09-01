import streamlit as st
from utils.formatters import (
    compute_suggested_site_code, 
    normalize_port_shortname,
    normalize_vswitch,
    normalize_vmnic,
    normalize_vmnic_list
)
from core.naming_engine import verify_and_suggest_with_ai
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
from ui.tabs.ipam_tab import POWERSHELL_AGENT_CODE

def apply_case(text: str, mode: str) -> str:
    return text.upper() if mode == "UPPERCASE" else text.lower()

def handle_csv_upload():
    uploaded_files = st.session_state.get("global_netbox_csv")
    if not uploaded_files:
        return

    if not isinstance(uploaded_files, list):
        uploaded_files = [uploaded_files]

    total_devices = 0
    total_hypervisors = 0
    total_vms = 0
    errors = []

    for f in uploaded_files:
        try:
            counts = save_universal_csv(f, filename=f.name, clear_first=False)
            total_devices += counts.get("device", 0)
            total_hypervisors += counts.get("hypervisor", 0)
            total_vms += counts.get("vm", 0)
        except Exception as e:
            errors.append(f"• **{f.name}**: {str(e)}")

    if errors:
        for err in errors:
            st.error(err)

    if total_devices > 0 or total_hypervisors > 0 or total_vms > 0:
        st.toast(f"✅ Ingested: {total_devices} Devices, {total_hypervisors} Hypervisors, {total_vms} VMs!", icon="🚀")

def handle_csv_reset():
    clear_inventory_records()
    st.toast("🗑️ Database Cleared. Restored default examples.", icon="🧹")

def display_reference_box(category_key: str, default_lines: str, label: str, site_filter: str = ""):
    real_items = get_records_by_category(category_key, site_filter=site_filter)
    filter_hint = f" matching '{site_filter.upper()}'" if site_filter else ""
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

def render_compact_toolbar():
    total_recs = get_total_record_count()
    device_count = len(get_records_by_category("device")) + len(get_records_by_category("hypervisor"))
    vm_count = len(get_records_by_category("vm"))
    
    status_tag = f"🟢 ({device_count} Devices, {vm_count} VMs in DB)" if total_recs > 0 else "⚪ (Default Examples)"
    tick_devices = " ✅" if device_count > 0 else ""
    tick_vms = " ✅" if vm_count > 0 else ""

    with st.expander(f"📥 Ingest NetBox Data (CSV) {status_tag}", expanded=False):
        if total_recs > 0:
            # Get metadata for devices and VMs
            meta_devices = get_sync_metadata("netbox_devices")
            meta_vms = get_sync_metadata("netbox_virtual_machines")
            
            # Use the most recent source that's not "None"
            sources = [m['source'] for m in [meta_devices, meta_vms] if m['source'] != "None"]
            display_source = sources[0] if sources else "Manual CSV Upload"
            
            st.markdown(f"**DB Status:** `Source: {display_source}`")

        st.markdown("**Option A: Automated Push via PowerShell Agent (Recommended):**")
        st.code('.\\Sync-NetBoxHub.ps1 -NetBoxUrl "https://xxxx" -ApiToken "xxxx" -HubUrl "xxxx"', language="powershell")

        st.caption("💡 *Press **Refresh** after the upload is completed in PowerShell to reload the local data.*")

        c_dl, c_ref = st.columns([2, 1])
        with c_dl:
            st.download_button(
                "⬇️ Download Sync-NetBoxHub.ps1 Agent",
                POWERSHELL_AGENT_CODE,
                file_name="Sync-NetBoxHub.ps1",
                mime="text/plain",
                key="dl_ps1_naming"
            )
        with c_ref:
            if st.button("🔄 Refresh", key="ref_naming_btn", width="stretch"):
                st.rerun()

        st.markdown("**Option B: Manual CSV Export & Upload:**")

        # Get timestamps for each file
        meta_devices = get_sync_metadata("netbox_devices")
        meta_vms = get_sync_metadata("netbox_virtual_machines")
        
        devices_timestamp = f" `{meta_devices['updated_at']}`" if meta_devices['updated_at'] != "Never" else ""
        vms_timestamp = f" `{meta_vms['updated_at']}`" if meta_vms['updated_at'] != "Never" else ""

        col_l1, col_r1 = st.columns([12, 1])
        with col_l1:
            st.markdown(f"* **Devices / Servers / Switches:** Go to `Devices` ➔ `Devices` ➔ `Export` ➔ `All Data` (`netbox_devices.csv`){tick_devices}{devices_timestamp}")
        with col_r1:
            if device_count > 0:
                if st.button("🗑️", key="btn_clr_dev_inline", help="Clear netbox_devices.csv data"):
                    clear_device_records()
                    st.toast("🗑️ Cleared netbox_devices.csv data.", icon="🧹")
                    st.rerun()

        col_l2, col_r2 = st.columns([12, 1])
        with col_l2:
            st.markdown(f"* **Virtual Machines:** Go to `Virtualization` ➔ `Virtual Machines` ➔ `Export` ➔ `All Data` (`netbox_virtual_machines.csv`){tick_vms}{vms_timestamp}")
        with col_r2:
            if vm_count > 0:
                if st.button("🗑️", key="btn_clr_vm_inline", help="Clear netbox_virtual machines.csv data"):
                    clear_vm_records()
                    st.toast("🗑️ Cleared netbox_virtual machines.csv data.", icon="🧹")
                    st.rerun()

        c_up, c_rst = st.columns([3, 1])
        with c_up:
            st.file_uploader(
                "Upload NetBox CSV Export",
                type=["csv"],
                accept_multiple_files=True,
                key="global_netbox_csv",
                on_change=handle_csv_upload,
                label_visibility="collapsed"
            )
        with c_rst:
            if total_recs > 0:
                st.button("🗑️ Clear All DB", on_click=handle_csv_reset, width="stretch", key="rst_csv_btn")
            else:
                st.caption("No custom data loaded.")

    case_mode = st.radio(
        "Casing",
        ["UPPERCASE", "lowercase"],
        index=0,
        horizontal=True,
        help="Render output in UPPERCASE or lowercase."
    )

    return case_mode

def render_naming_tab(active_model):
    st.subheader("🏷️ Standardized Infrastructure Naming Generator")
    
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
        case_mode = render_compact_toolbar()

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

            display_reference_box(
                category_key="device",
                default_lines="SWUSNYC01-0       (Switch Stack, Member 0)\nWAPUSNYC01        (Access Point 01)\nFWUSNYCPA01       (Firewall 01)",
                label="Device",
                site_filter=c_site
            )

        with col_b:
            st.markdown("#### 🔌 Switch & Firewall Interface Formatter")
            p_cat = st.radio("Interface Type", [
                "Switch Uplink (Inter-Switch)", "Switch LAG Member Port (LACP)", 
                "Switch Port-Channel (Logical)", "Switch Access Port (Endpoint)", "Firewall Security Zone Interface"
            ], key="p_cat_sel")
            
            if p_cat == "Switch Uplink (Inter-Switch)":
                l_dev = st.text_input("Local Device Hostname", value=final_device_name, placeholder="e.g. SWUSNYC01-0", key="up_ld").strip()
                l_port_raw = st.text_input("Local Port", value="", placeholder="e.g. Gi1/0/48, Te1/0/1", key="up_lp")
                r_dev = st.text_input("Remote Device Hostname", value="", placeholder="e.g. SWUSNYC02-0", key="up_rd").strip()
                r_port_raw = st.text_input("Remote Port", value="", placeholder="e.g. Gi1/0/48, Te1/0/1", key="up_rp")
                link_role = st.text_input("Role / Purpose", value="Uplink", placeholder="e.g. Uplink", key="up_lr").strip()

                l_port_short = normalize_port_shortname(l_port_raw)
                r_port_short = normalize_port_shortname(r_port_raw)
                role_suffix = f" [{link_role}]" if link_role else ""

                st.caption(f"On Local Device (`{l_dev or 'LOCAL'}`):")
                st.code(f"to {r_dev}_{r_port_short}{role_suffix}", language="text")
                st.caption(f"On Remote Device (`{r_dev}`):")
                st.code(f"to {l_dev}_{l_port_short}{role_suffix}", language="text")
            
            elif p_cat == "Switch LAG Member Port (LACP)":
                st.markdown("### ⚙️ LAG Member Port Formatter")
                lag_id = st.text_input("LAG ID (e.g. Po1)", value="Po1", key="lag_id")
                port = st.text_input("Physical Port (e.g. Gi1/0/1)", value="", key="lag_port")
                st.code(f"channel-group {lag_id.replace('Po', '')} mode active", language="text")
                st.code(f"interface {normalize_port_shortname(port)}", language="text")
                st.code(f" description LAG Member: {lag_id}", language="text")
            
            elif p_cat == "Switch Port-Channel (Logical)":
                st.markdown("### 🏗️ Port-Channel Formatter")
                pc_id = st.text_input("Port-Channel ID (e.g. Po1)", value="Po1", key="pc_id")
                st.code(f"interface {pc_id}", language="text")
                st.code(f" description Uplink to ...", language="text")
            
            elif p_cat == "Switch Access Port (Endpoint)":
                st.markdown("### 💻 Access Port Formatter")
                vlan = st.text_input("Access VLAN", value="10", key="ac_vlan")
                st.code(f"switchport mode access", language="text")
                st.code(f"switchport access vlan {vlan}", language="text")
            
            elif p_cat == "Firewall Security Zone Interface":
                st.markdown("### 🛡️ Firewall Interface Formatter")
                zone = st.text_input("Security Zone", value="TRUST", key="fw_zone")
                st.code(f"nameif {zone.lower()}", language="text")

    # Hosts & VMs
    elif "2. Hosts" in naming_cat:
        case_mode = render_compact_toolbar()

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### 🖥️ ESXi Hypervisor Hostname")
            h_site = st.text_input("Site Prefix", value="", placeholder="e.g. age, nyc, lon, syd", key="esx_site").strip()
            h_role = st.text_input("Host Role (Optional)", value="", placeholder="e.g. esx, otinfhost, infhost", key="esx_role").strip()
            h_num = st.text_input("Host Sequence Number", value="001", placeholder="e.g. 001, 01, 1", key="esx_num").strip()
            h_dom = st.text_input("Domain Name (FQDN Suffix)", value="", placeholder="e.g. corp.internal, eswine.adds, enterprise.net", key="esx_dom").strip()

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
            st.markdown("#### 1. Physical Uplink (`vmnic`)")
            vmnic_raw = st.text_input("vmnic ID", value="vmnic", placeholder="e.g. vmnic0, nic1", key="vmnic_in")
            vsw1_raw = st.text_input("Target vSwitch", value="vSwitch", placeholder="e.g. vSwitch0, vswitch1", key="vsw1")
            status = st.radio("Status", ["Active Uplink", "Standby Uplink"], horizontal=True)
            
            clean_vmnic = normalize_vmnic(vmnic_raw) if auto_correct else vmnic_raw.strip()
            clean_vsw1 = normalize_vswitch(vsw1_raw) if auto_correct else vsw1_raw.strip()
            gen_vmnic = f"{clean_vmnic} - {clean_vsw1} {status}" if clean_vmnic and clean_vsw1 else ""
            
            st.caption("Generated Physical Uplink:")
            st.code(gen_vmnic or "vmnic - vSwitch Active Uplink", language="text")

        with col_b:
            st.markdown("#### 2. Port Group Teaming (`PG-`)")
            vsw_pg_raw = st.text_input("vSwitch Name", value="PG-", placeholder="e.g. PG-VM Network", help="Port Group name (defaults to PG- prefix).", key="vsw2")
            act_nics_raw = st.text_input("Active vmnics", value="vmnic", placeholder="e.g. vmnic0, vmnic1", key="act_nics_in")
            stb_nics_raw = st.text_input("Standby vmnics", value="", placeholder="e.g. vmnic2 (optional)", key="stb_nics_in")
            
            clean_vsw_pg = vsw_pg_raw.strip()
            clean_act = normalize_vmnic_list(act_nics_raw) if auto_correct else act_nics_raw.strip()
            clean_stb = normalize_vmnic_list(stb_nics_raw) if auto_correct else stb_nics_raw.strip()

            parts = [f"{clean_act} Active"] if clean_act else []
            if clean_stb:
                parts.append(f"{clean_stb} Standby")
            
            gen_pg = f"{clean_vsw_pg} [{' / '.join(parts)}]" if clean_vsw_pg and parts else ""
            st.caption("Generated Port Group Teaming:")
            st.code(gen_pg or "PG- [vmnic Active]", language="text")

        with col_c:
            st.markdown("#### 3. VMkernel Adapter (`vmk`)")
            vmk_purp = st.text_input("Purpose / Service", value="", placeholder="e.g. Management, vMotion, Storage", key="vmk_p_in").strip()
            vsw_vmk_raw = st.text_input("vSwitch Name", value="vSwitch", placeholder="e.g. vSwitch0", key="vsw3")
            
            clean_vsw_vmk = normalize_vswitch(vsw_vmk_raw) if auto_correct else vsw_vmk_raw.strip()
            
            if vmk_purp and clean_vsw_vmk:
                fallback_disp = f"{vmk_purp} [{clean_vsw_vmk}]"
            elif vmk_purp:
                fallback_disp = vmk_purp
            elif clean_vsw_vmk:
                fallback_disp = f"[{clean_vsw_vmk}]"
            else:
                fallback_disp = "[vSwitch]"

            st.caption("Generated VMkernel Description:")
            st.code(fallback_disp, language="text")