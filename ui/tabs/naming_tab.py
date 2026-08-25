import re
import streamlit as st
from utils.formatters import compute_suggested_site_code, normalize_port_shortname
from core.naming_engine import verify_and_suggest_with_ai

def render_naming_tab(active_model):
    st.subheader("🏷️ Standardized Infrastructure Naming Generator")
    naming_cat = st.radio("Select Asset Class", [
        "1. Network Devices (Switches, APs & Firewalls)",
        "2. Hosts & Virtual Machines (ESXi & VMs)",
        "3. ESXi Network Descriptions (vmnic, PortGroup, VMkernel)"
    ], horizontal=True)

    st.markdown("---")

    if "1. Network" in naming_cat:
        st.markdown("##### 📍 Location & Site Code Assistant")
        loc_col1, loc_col2 = st.columns([2, 1])
        with loc_col1:
            input_location = st.text_input("Location / City Name", value="Bristol", key="loc_input_help")
        with loc_col2:
            auto_code = compute_suggested_site_code(input_location)
            st.info(f"Suggested Site Code: **`{auto_code}`**")

        st.markdown("---")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**Switch Hostname Generator**")
            dev_sw_type = st.selectbox("Switch Type", ["SW (Standalone Switch)", "VS (Virtual Chassis / Stack)", "OTSW (OT Field Switch)", "(None / Blank)"], index=0)
            prefix_sw = "" if "(None / Blank)" in dev_sw_type else dev_sw_type.split()[0]
            
            s_ctry = st.text_input("Country Code", value="UK", key="sw_ctry_g")
            s_state = st.text_input("State / Region", value="", key="sw_st_g")
            s_site = st.text_input("Site Code", value=auto_code, key="sw_site_g")
            s_zone = st.text_input("Zone / Role", value="", key="sw_zone_g")
            s_seq = st.text_input("Sequence", value="01", key="sw_seq_g")
            s_stack = st.text_input("Stack ID", value="0", key="sw_stk_g")
            
            base_sw = f"{prefix_sw}{s_ctry.upper()}{s_state.strip().upper()}{s_site.upper()}{s_zone.strip().upper()}{s_seq}"
            current_sw_name = f"{base_sw}-{s_stack.strip()}" if s_stack.strip() else base_sw
            
            st.caption("Generated Switch Hostname:")
            st.code(current_sw_name, language="text")

            if st.button("🤖 AI Verify Switch", key="ai_chk_sw"):
                with st.spinner("Auditing..."):
                    st.info(verify_and_suggest_with_ai(current_sw_name, active_model))

            st.markdown("💡 **Live Switch Reference Examples:**")
            st.code(
                "SWUKBRIS01-0      (Bristol Stack Switch 01, Member 0)\n"
                "SWUKBRIS01-1      (Bristol Stack Switch 01, Member 1)\n"
                "SWUKWEYCORE-0     (Weybridge Core Switch, Member 0)\n"
                "SWAUSAROFLWH1-0   (Rowland Flat WH1 Switch, Member 0)\n"
                "VSAUSAROFLCCORE-0 (Rowland Flat Core Virtual Chassis)\n"
                "SWAUSABRS01       (Banrock Station Standalone Switch)",
                language="text"
            )

            st.markdown("---")
            st.markdown("**Switch Port Description Formatter**")
            p_type = st.radio("Port Type", ["Uplink (Inter-Switch)", "LAG Member Port (LACP Uplink)", "Port-Channel (Logical Aggregate)", "Access (Host/Endpoint)"], key="p_sel")
            
            if p_type == "Uplink (Inter-Switch)":
                l_dev = st.text_input("Local Device Hostname", value="SWUKBRIS01-0", key="up_ld").strip()
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

            elif p_type == "LAG Member Port (LACP Uplink)":
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

            elif p_type == "Port-Channel (Logical Aggregate)":
                loc_po = st.text_input("Local Port-Channel ID", value="1", key="pchan_lpo").strip()
                r_dev = st.text_input("Remote Device Hostname", value="LACP-AGE-HOSTS", key="pchan_rd").strip()
                rem_po = st.text_input("Remote Port-Channel ID", value="1", key="pchan_rpo").strip()
                vlan_info = st.text_input("Trunk Info", value="TRUNK CORE", key="pchan_vl").strip()

                l_po = f"Po{loc_po.replace('Po', '').replace('po', '').strip() or '1'}"
                r_po = f"Po{rem_po.replace('Po', '').replace('po', '').strip() or '1'}"
                vlan_suffix = f" [{vlan_info}]" if vlan_info else ""
                st.caption("Logical Port-Channel Description:")
                st.code(f"{l_po} -> {r_dev}_{r_po}{vlan_suffix}", language="text")

            else:
                vlan_name = st.text_input("VLAN Name", value="VLAN10_Management", key="acc_vln").strip()
                host_port_raw = st.text_input("Connected Host/Port", value="roflesx01_vmnic0", key="acc_hp").strip()
                clean_host_port = re.sub(r"\s+", "", host_port_raw)
                st.caption("Access Port Description:")
                st.code(f"{vlan_name} - {clean_host_port}", language="text")

        with col_b:
            st.markdown("**Wireless AP Naming**")
            ap_ctry = st.text_input("Country Code", value="UK", key="ap_c")
            ap_state = st.text_input("State Code", value="", key="ap_st")
            ap_site = st.text_input("Site Code", value=auto_code, key="ap_s")
            ap_seq = st.text_input("Sequence", value="01", key="ap_seq")
            clean_ap = f"WAP{ap_ctry.upper()}{ap_state.upper()}{ap_site.upper()}{ap_seq}"
            st.caption("Generated AP Hostname:")
            st.code(clean_ap, language="text")

        with col_c:
            st.markdown("**Firewall & Security Appliances**")
            fw_arch = st.selectbox("Firewall Category", [
                "Prisma SD-WAN (ION<Country><State><Site><Seq>)",
                "Palo Alto / Fortinet Firewall (FW<Country><State><Site><Vendor><Seq>)",
                "Virtual Appliance Panorama (VA<Country><State><Site>PANORAMA<Seq>)",
                "(None / Blank)"
            ])
            fw_ctry = st.text_input("Country Code", value="UK", key="fw_c_gen")
            fw_state = st.text_input("State Code", value="", key="fw_st_gen")
            fw_site = st.text_input("Site Code", value=auto_code, key="fw_s_gen")
            
            if "Palo Alto" in fw_arch:
                fw_vendor = st.text_input("Vendor ID", value="PA", key="fw_vrole")
                fw_seq = st.text_input("Seq", value="01", key="fw_seq_pa")
                clean_sec = f"FW{fw_ctry.upper()}{fw_state.upper()}{fw_site.upper()}{fw_vendor.upper()}{fw_seq}"
            elif "Prisma" in fw_arch:
                fw_seq = st.text_input("Seq", value="01", key="fw_seq_ion2")
                clean_sec = f"ION{fw_ctry.upper()}{fw_state.upper()}{fw_site.upper()}{fw_seq}"
            elif "Panorama" in fw_arch:
                va_seq = st.text_input("Seq", value="01", key="va_sq")
                clean_sec = f"VA{fw_ctry.upper()}{fw_state.upper()}{fw_site.upper()}PANORAMA{va_seq}"
            else:
                fw_seq = st.text_input("Seq", value="01", key="fw_seq_blank")
                clean_sec = f"{fw_ctry.upper()}{fw_state.upper()}{fw_site.upper()}{fw_seq}"

            st.caption("Generated Appliance Hostname:")
            st.code(clean_sec, language="text")

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

        with col_b:
            st.markdown("**Virtual Machine Hostname**")
            v_site = st.text_input("Site", value="rofl", key="vm_site")
            v_role = st.text_input("Role (cvi, afs, sani)", value="cvi", key="vm_role")
            v_seq = st.text_input("Seq", value="01", key="vm_seq")
            st.caption("Generated VM Name:")
            st.code(f"{v_site.lower()}{v_role.strip().lower()}{v_seq}", language="text")

    else:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            vmnic = st.text_input("vmnic ID", value="vmnic0")
            vsw = st.text_input("Target vSwitch", value="vSwitch0", key="vsw1")
            status = st.radio("Status", ["Active Uplink", "Standby Uplink"], horizontal=True)
            st.caption("Generated Physical Uplink:")
            st.code(f"{vmnic} - {vsw} {status}", language="text")

        with col_b:
            vsw_pg = st.text_input("vSwitch Name", value="vSwitch0", key="vsw2")
            act_nics = st.text_input("Active vmnics", value="vmnic0, vmnic1")
            stb_nics = st.text_input("Standby vmnics", value="")
            parts = [f"{act_nics.strip()} Active"] if act_nics.strip() else []
            if stb_nics.strip():
                parts.append(f"{stb_nics.strip()} Standby")
            joined_parts = " / ".join(parts)
            st.caption("Generated Port Group:")
            st.code(f"{vsw_pg} [{joined_parts}]", language="text")

        with col_c:
            vmk_purp = st.text_input("Purpose", value="Management Network")
            vsw_vmk = st.text_input("vSwitch", value="vSwitch0", key="vsw3")
            st.caption("Generated VMkernel:")
            st.code(f"{vmk_purp} [{vsw_vmk}]", language="text")