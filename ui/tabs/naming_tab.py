import re
import streamlit as st
from utils.formatters import compute_suggested_site_code, normalize_port_shortname
from core.naming_engine import verify_and_suggest_with_ai

def render_naming_tab(active_model):
    st.subheader("🏷️ Standardized Infrastructure Naming Generator")
    naming_cat = st.radio("Select Asset Class", [
        "1. Network & Security Devices (Switches, APs, Firewalls, Routers)",
        "2. Hosts & Virtual Machines (ESXi & VMs)",
        "3. ESXi Network Descriptions (vmnic, PortGroup, VMkernel)"
    ], horizontal=True)

    st.markdown("---")

    # 1. Unified Network & Security Devices
    if "1. Network" in naming_cat:
        st.markdown("##### 📍 Location & Site Code Assistant")
        loc_col1, loc_col2 = st.columns([2, 1])
        with loc_col1:
            input_location = st.text_input("Location / City Name", value="Bristol", key="loc_input_help")
        with loc_col2:
            auto_code = compute_suggested_site_code(input_location)
            st.info(f"Suggested Site Code: **`{auto_code}`**")

        st.markdown("---")
        col_a, col_b = st.columns([1, 1])
        
        with col_a:
            st.markdown("#### 🛠️ Universal Device Hostname Generator")
            
            dev_type_preset = st.selectbox(
                "Device Type / Prefix",
                [
                    "SW (Switch)",
                    "VS (Virtual Chassis / Stack)",
                    "OTSW (OT Switch)",
                    "WAP (Wireless Access Point)",
                    "FW (Firewall / Security Appliance)",
                    "ION (Prisma SD-WAN)",
                    "VA (Virtual Appliance)",
                    "RTR (Router)",
                    "(None / Blank)",
                    "✏️ Custom Prefix..."
                ],
                index=0,
                key="dev_prefix_sel"
            )

            if dev_type_preset == "✏️ Custom Prefix...":
                dev_prefix = st.text_input("Enter Custom Prefix", value="", placeholder="e.g. SVR, GW, AGG", key="dev_custom_pre").strip().upper()
            elif "(None / Blank)" in dev_type_preset:
                dev_prefix = ""
            else:
                dev_prefix = dev_type_preset.split()[0].strip()

            c_ctry = st.text_input("Country Code (2-letter)", value="UK", key="u_ctry")
            c_state = st.text_input("State / Region (e.g. SA, NSW, VIC or blank)", value="", key="u_state")
            c_site = st.text_input("Site Code", value=auto_code, key="u_site")
            c_zone = st.text_input("Zone / Role / Vendor (Optional)", value="", placeholder="e.g. PA, PANORAMA, BOT, CORE, WH1", key="u_zone")
            c_seq = st.text_input("Sequence Number", value="01", key="u_seq")
            c_stack = st.text_input("Stack / Member ID (Optional)", value="", placeholder="e.g. 0, 1 (Leave empty if standalone)", key="u_stk")

            base_name = f"{dev_prefix}{c_ctry.upper()}{c_state.strip().upper()}{c_site.upper()}{c_zone.strip().upper()}{c_seq.strip()}"
            final_device_name = f"{base_name}-{c_stack.strip()}" if c_stack.strip() else base_name

            st.caption("Generated Device Hostname:")
            st.code(final_device_name, language="text")

            if st.button("🤖 AI Verify / Suggest Device Hostname", key="ai_chk_dev"):
                with st.spinner("Auditing against standards..."):
                    st.info(verify_and_suggest_with_ai(final_device_name, active_model))

            st.markdown("💡 **Standard Examples:**")
            st.code(
                "SWUKBRIS01-0      (Switch Stack Bristol, Member 0)\n"
                "WAPUKBRIS01       (Access Point Bristol 01)\n"
                "FWUKBRISPA01      (Firewall Palo Alto Bristol 01)\n"
                "IONUKBRIS01       (Prisma SD-WAN Bristol 01)\n"
                "VAUKBRISPANORAMA01(Panorama Virtual Appliance)",
                language="text"
            )

        with col_b:
            st.markdown("#### 🔌 Switch & Firewall Interface Formatter")
            
            p_cat = st.radio("Interface Type", [
                "Switch Uplink (Inter-Switch)", 
                "Switch LAG Member Port (LACP)", 
                "Switch Port-Channel (Logical)", 
                "Switch Access Port (Endpoint)",
                "Firewall Security Zone Interface"
            ], key="p_cat_sel")
            
            if p_cat == "Switch Uplink (Inter-Switch)":
                l_dev = st.text_input("Local Device Hostname", value=final_device_name, key="up_ld").strip()
                l_port_raw = st.text_input("Local Port", value="Port 51", key="up_lp")
                r_dev = st.text_input("Remote Device Hostname", value="AGE-ENVASADO", key="up_rd").strip()
                r_port_raw = st.text_input("Remote Port", value="Port 26", key="up_rp")
                link_role = st.text_input("Role / Purpose", value="Uplink", key="up_lr").strip()

                l_port_short = normalize_port_shortname(l_port_raw)
                r_port_short = normalize_port_shortname(r_port_raw)
                role_suffix = f" [{link_role}]" if link_role else ""

                st.caption(f"On Local Device (`{l_dev}`):")
                st.code(f"to {r_dev}_{r_port_short}{role_suffix}", language="text")
                st.caption(f"On Remote Device (`{r_dev}`):")
                st.code(f"to {l_dev}_{l_port_short}{role_suffix}", language="text")

            elif p_cat == "Switch LAG Member Port (LACP)":
                l_port_raw = st.text_input("Local Port", value="Port 2", key="lagm_lp")
                loc_po = st.text_input("Port-Channel ID", value="1", key="lagm_lpo").strip()
                r_dev = st.text_input("Remote Hostname", value="LACP-AGE-HOSTS", key="lagm_rd").strip()
                r_port_raw = st.text_input("Remote Port", value="Port 2", key="lagm_rp")
                link_role = st.text_input("Link Purpose", value="", key="lagm_lr").strip()

                l_port_short = normalize_port_shortname(l_port_raw)
                r_port_short = normalize_port_shortname(r_port_raw)
                po_num = loc_po.replace('Po', '').replace('po', '').strip() or "1"
                role_suffix = f" [{link_role}]" if link_role else ""
                lag_desc = f"{l_port_short} [Po{po_num}] -> {r_dev}_{r_port_short}{role_suffix}"

                st.caption("LAG Member Port Description:")
                st.code(lag_desc, language="text")

            elif p_cat == "Switch Port-Channel (Logical)":
                loc_po = st.text_input("Local Port-Channel ID", value="1", key="pchan_lpo").strip()
                r_dev = st.text_input("Remote Device Hostname", value="LACP-AGE-HOSTS", key="pchan_rd").strip()
                rem_po = st.text_input("Remote Port-Channel ID", value="1", key="pchan_rpo").strip()
                vlan_info = st.text_input("Trunk Info", value="TRUNK CORE", key="pchan_vl").strip()

                l_po = f"Po{loc_po.replace('Po', '').replace('po', '').strip() or '1'}"
                r_po = f"Po{rem_po.replace('Po', '').replace('po', '').strip() or '1'}"
                vlan_suffix = f" [{vlan_info}]" if vlan_info else ""
                st.caption("Logical Port-Channel Description:")
                st.code(f"{l_po} -> {r_dev}_{r_po}{vlan_suffix}", language="text")

            elif p_cat == "Switch Access Port (Endpoint)":
                vlan_name = st.text_input("VLAN Name", value="VLAN10_Management", key="acc_vln").strip()
                host_port_raw = st.text_input("Connected Host/Port", value="roflesx01_vmnic0", key="acc_hp").strip()
                clean_host = re.sub(r"\s+", "", host_port_raw)
                st.caption("Access Port Description:")
                st.code(f"{vlan_name} - {clean_host}", language="text")

            else:
                fw_zone = st.text_input("Security Zone / Role", value="DMZ", key="fw_z_in").strip()
                fw_vlan = st.text_input("VLAN ID", value="100", key="fw_v_in").strip()
                clean_fw_if = f"{fw_zone}_{fw_vlan}" if fw_vlan else fw_zone
                st.caption("Firewall Interface Description:")
                st.code(clean_fw_if, language="text")

    # 2. Hosts & Virtual Machines
    elif "2. Hosts" in naming_cat:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**ESXi Hypervisor Hostname**")
            h_env = st.radio("Profile", ["Corporate (.eswine.adds)", "Industrial (.eswines.ot)", "Branch (.corp.local)"], horizontal=True)
            if "Corporate" in h_env:
                h_site = st.text_input("Site Prefix", value="pws", key="esx_site")
                h_num = st.text_input("Host Number", value="001", key="esx_num")
                gen_esx = f"{h_site.lower()}esx{h_num}.eswine.adds"
            elif "Industrial" in h_env:
                h_site = st.text_input("Site Prefix", value="age", key="esx_site")
                h_num = st.text_input("Host Number", value="1", key="esx_num")
                gen_esx = f"{h_site.lower()}otinfhost{h_num}.eswines.ot"
            else:
                h_site = st.text_input("Site Prefix", value="rofl", key="esx_site")
                h_num = st.text_input("Host Number", value="01", key="esx_num")
                gen_esx = f"{h_site.lower()}esx{h_num}.corp.local"
            st.caption("Generated ESXi Hostname:")
            st.code(gen_esx, language="text")

            if st.button("🤖 AI Verify ESXi Host", key="ai_chk_esx"):
                with st.spinner("Auditing..."):
                    st.info(verify_and_suggest_with_ai(gen_esx, active_model))

        with col_b:
            st.markdown("**Virtual Machine Hostname**")
            v_site = st.text_input("Site", value="rofl", key="vm_site")
            v_role = st.text_input("Role (e.g. cvi, afs, sani, vlab)", value="cvi", key="vm_role")
            v_seq = st.text_input("Sequence Number", value="01", key="vm_seq")
            gen_vm = f"{v_site.lower()}{v_role.strip().lower()}{v_seq}"
            st.caption("Generated VM Name:")
            st.code(gen_vm, language="text")

            if st.button("🤖 AI Verify VM Hostname", key="ai_chk_vm"):
                with st.spinner("Auditing..."):
                    st.info(verify_and_suggest_with_ai(gen_vm, active_model))

    # 3. ESXi Network Descriptions
    else:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**1. Physical Uplink (`vmnic`)**")
            vmnic = st.text_input("vmnic ID", value="vmnic0")
            vsw = st.text_input("Target vSwitch", value="vSwitch0", key="vsw1")
            status = st.radio("Status", ["Active Uplink", "Standby Uplink"], horizontal=True)
            st.caption("Generated Physical Uplink:")
            st.code(f"{vmnic} - {vsw} {status}", language="text")

        with col_b:
            st.markdown("**2. Port Group Teaming (`PG`)**")
            vsw_pg = st.text_input("vSwitch Name", value="vSwitch0", key="vsw2")
            act_nics = st.text_input("Active vmnics", value="vmnic0, vmnic1")
            stb_nics = st.text_input("Standby vmnics", value="")
            parts = [f"{act_nics.strip()} Active"] if act_nics.strip() else []
            if stb_nics.strip():
                parts.append(f"{stb_nics.strip()} Standby")
            st.caption("Generated Port Group:")
            st.code(f"{vsw_pg} [{' / '.join(parts)}]", language="text")

        with col_c:
            st.markdown("**3. VMkernel Adapter (`vmk`)**")
            vmk_purp = st.text_input("Purpose / Role", value="Management Network")
            vsw_vmk = st.text_input("vSwitch", value="vSwitch0", key="vsw3")
            st.caption("Generated VMkernel:")
            st.code(f"{vmk_purp} [{vsw_vmk}]", language="text")