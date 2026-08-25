import streamlit as st
from utils.formatters import (
    compute_suggested_site_code, 
    normalize_port_shortname,
    normalize_vswitch,
    normalize_vmnic,
    normalize_vmnic_list
)
from core.naming_engine import verify_and_suggest_with_ai
from core.db_manager import save_csv_records, get_records_by_category, clear_records_by_category

def apply_case(text: str, mode: str) -> str:
    return text.upper() if mode == "UPPERCASE" else text.lower()

def render_reference_uploader(category_key: str, default_lines: str, label: str):
    with st.expander(f"💡 Click to view reference {label} examples"):
        c_up, c_reset = st.columns([3, 1])
        with c_up:
            up_file = st.file_uploader(f"Upload NetBox {label} CSV Export", type=["csv"], key=f"csv_up_{category_key}")
        with c_reset:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🗑️ Reset Data", key=f"btn_reset_{category_key}"):
                clear_records_by_category(category_key)
                st.success("Reset to defaults!")
                st.rerun()

        if up_file is not None:
            try:
                cnt = save_csv_records(up_file, category=category_key)
                st.success(f"Loaded {cnt} real {label} records!")
                st.rerun()
            except Exception as e:
                st.error(f"Error parsing CSV: {e}")

        real_items = get_records_by_category(category_key)
        if real_items:
            st.markdown(f"##### 🟢 Real Data Examples ({len(real_items)} records from NetBox CSV):")
            formatted = []
            for r in real_items[:10]:
                meta = f" - {r['description']}" if r['description'] else f" ({r['model_or_role'] or r['site']})"
                formatted.append(f"{r['name']}{meta}")
            st.code("\n".join(formatted), language="text")
        else:
            st.markdown("##### 🟡 Default Examples:")
            st.code(default_lines, language="text")

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

    # 1. Network & Security
    if "1. Network" in naming_cat:
        case_mode = st.radio("Letter Casing Mode", ["UPPERCASE", "lowercase"], index=0, horizontal=True)

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
            c_site = st.text_input("Site Code", value=auto_code, placeholder="e.g. NYC, LON, SYD, DAL", key="u_site").strip()
            c_zone = st.text_input("Zone / Role / Vendor (Optional)", value="", placeholder="e.g. CORE, DIST, EDGE, PA", key="u_zone").strip()
            c_seq = st.text_input("Sequence Number", value="01", placeholder="e.g. 01, 02", key="u_seq").strip()
            c_stack = st.text_input("Stack / Member ID (Optional)", value="", placeholder="e.g. 0, 1", key="u_stk").strip()

            raw_base = f"{dev_prefix}{c_ctry}{c_state}{c_site}{c_zone}{c_seq}"
            final_device_name = apply_case(f"{raw_base}-{c_stack}" if c_stack else raw_base, case_mode)

            st.caption("Generated Device Hostname:")
            st.code(final_device_name, language="text")

            if st.button("🤖 AI Verify / Suggest Device Hostname", key="ai_chk_dev"):
                with st.spinner("Auditing against standards & real inventory..."):
                    st.info(verify_and_suggest_with_ai(final_device_name, active_model, asset_type=f"Network/Security Device ({dev_type_preset})", category_key="device"))

            render_reference_uploader(
                category_key="device",
                default_lines="SWUSNYC01-0       (Switch Stack, Member 0)\nWAPUSNYC01        (Access Point 01)\nFWUSNYCPA01       (Firewall 01)",
                label="Device"
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

    # 2. ESXi Hosts & VMs
    elif "2. Hosts" in naming_cat:
        case_mode = st.radio("Letter Casing Mode", ["UPPERCASE", "lowercase"], index=0, horizontal=True)

        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### 🖥️ ESXi Hypervisor Hostname")
            h_site = st.text_input("Site Prefix", value="", placeholder="e.g. nyc, lon, syd, dal", key="esx_site").strip()
            h_role = st.text_input("Host Role (Optional)", value="", placeholder="e.g. esx, infhost", key="esx_role").strip()
            h_num = st.text_input("Host Sequence Number", value="001", placeholder="e.g. 001, 01", key="esx_num").strip()
            h_dom = st.text_input("Domain Name (FQDN Suffix)", value="", placeholder="e.g. corp.internal, corp.local, enterprise.net", key="esx_dom").strip()

            raw_host = f"{h_site}{h_role or 'esx'}{h_num}"
            host_formatted = apply_case(raw_host, case_mode)
            gen_esx = f"{host_formatted}.{h_dom.lower()}" if h_dom else host_formatted

            st.caption("Generated ESXi Hostname:")
            st.code(gen_esx, language="text")

            if st.button("🤖 AI Verify ESXi Host", key="ai_chk_esx"):
                with st.spinner("Auditing against standards & real inventory..."):
                    st.info(verify_and_suggest_with_ai(gen_esx, active_model, asset_type="ESXi Hypervisor Hostname", category_key="hypervisor"))

            render_reference_uploader(
                category_key="hypervisor",
                default_lines="NYCESX001.corp.internal  (Enterprise ESXi Node 001)\nLONESX001.corp.internal  (Enterprise ESXi Node 001)\nSYDESX01.corp.local      (Branch Hypervisor Standalone)",
                label="Hypervisor"
            )

        with col_b:
            st.markdown("#### 🖲️ Virtual Machine (VM) Hostname")
            v_site = st.text_input("Site Prefix / Country & Site", value="", placeholder="e.g. usnyc, uklon, ausyd", key="vm_site").strip()
            v_role = st.text_input("Role Code / Workload", value="", placeholder="e.g. app, web, db, fs, dc", key="vm_role").strip()
            v_seq = st.text_input("Sequence Number", value="01", placeholder="e.g. 01, 02", key="vm_seq").strip()

            raw_vm = f"{v_site}{v_role}{v_seq}"
            gen_vm = apply_case(raw_vm, case_mode)

            st.caption("Generated VM Hostname:")
            st.code(gen_vm, language="text")

            if st.button("🤖 AI Verify VM Hostname", key="ai_chk_vm"):
                with st.spinner("Auditing against standards & real inventory..."):
                    st.info(verify_and_suggest_with_ai(gen_vm, active_model, asset_type="Virtual Machine (VM) Hostname", category_key="vm"))

            render_reference_uploader(
                category_key="vm",
                default_lines="USNYCAPP01     (NYC Application Server 01)\nUKLONDB01      (London Database Server 01)\nAUSYDFS01      (Sydney File Server 01)",
                label="Virtual Machine"
            )

    # 3. ESXi Network Descriptions
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