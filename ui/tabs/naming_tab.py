import re
import streamlit as st
from utils.formatters import compute_suggested_site_code, normalize_port_shortname
from core.naming_engine import verify_and_suggest_with_ai

def apply_case(text: str, mode: str) -> str:
    return text.upper() if mode == "UPPERCASE" else text.lower()

def render_naming_tab(active_model):
    st.subheader("🏷️ Standardized Infrastructure Naming Generator")
    
    # Global casing selector
    case_mode = st.radio(
        "Letter Casing Mode",
        ["UPPERCASE", "lowercase"],
        index=0,
        horizontal=True,
        help="Choose whether generated hostnames are rendered in all UPPERCASE (recommended) or lowercase."
    )

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

    # 1. Unified Network & Security Devices
    if "1. Network" in naming_cat:
        st.markdown("##### 📍 Location & Site Code Assistant")
        loc_col1, loc_col2 = st.columns([2, 1])
        with loc_col1:
            input_location = st.text_input(
                "Location / City Name",
                value="",
                placeholder="e.g. Sydney, London, Dallas, Rowland Flat",
                help="Type a city or facility name to automatically calculate a standard 4-letter site code.",
                key="loc_input_help"
            )
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
                    "SW (Switch)",
                    "VS (Virtual Chassis / Stack)",
                    "OTSW (OT Switch)",
                    "WAP (Wireless Access Point)",
                    "FW (Firewall / Security Appliance)",
                    "ION (Prisma SD-WAN)",
                    "VA (Virtual Appliance)",
                    "RTR (Router)",
                    "✏️ Custom Prefix..."
                ],
                index=0,
                key="dev_prefix_sel",
                help="Select standard device classification or choose Custom Prefix to type your own / leave blank."
            )

            if "Custom Prefix" in dev_type_preset:
                dev_prefix = st.text_input(
                    "Enter Custom Prefix (e.g. leave empty for none)", 
                    value="", 
                    placeholder="e.g. SVR, GW, AGG (or leave empty)", 
                    help="Custom hardware abbreviation. Leave completely blank if your hostname formula has no leading prefix.",
                    key="dev_custom_pre"
                ).strip()
            else:
                dev_prefix = dev_type_preset.split()[0].strip()

            c_ctry = st.text_input(
                "Country Code (2-letter)",
                value="",
                placeholder="e.g. AU, UK, US, ES, NZ",
                help="ISO 2-letter country code.",
                key="u_ctry"
            ).strip()

            c_state = st.text_input(
                "State / Region (Optional)",
                value="",
                placeholder="e.g. SA, NSW, VIC, TX (or leave blank)",
                help="State, province, or regional code if applicable. Leave blank for small territories/countries.",
                key="u_state"
            ).strip()

            c_site = st.text_input(
                "Site Code",
                value=auto_code,
                placeholder="e.g. BRIS, ROFL, SYD, LON, MAD",
                help="3-4 character site/facility identifier.",
                key="u_site"
            ).strip()

            c_zone = st.text_input(
                "Zone / Role / Vendor (Optional)",
                value="",
                placeholder="e.g. CORE, DIST, ACC, PA, PANORAMA, BOT, WH1",
                help="Specific architectural role, building zone, or security vendor identifier.",
                key="u_zone"
            ).strip()

            c_seq = st.text_input(
                "Sequence Number",
                value="01",
                placeholder="e.g. 01, 02, 03",
                help="Two-digit numerical sequence identifier.",
                key="u_seq"
            ).strip()

            c_stack = st.text_input(
                "Stack / Member ID (Optional)",
                value="",
                placeholder="e.g. 0, 1 (Leave empty if standalone)",
                help="Stack member number appended with a hyphen. Leave blank for standalone devices.",
                key="u_stk"
            ).strip()

            raw_base = f"{dev_prefix}{c_ctry}{c_state}{c_site}{c_zone}{c_seq}"
            final_device_name = apply_case(f"{raw_base}-{c_stack}" if c_stack else raw_base, case_mode)

            st.caption("Generated Device Hostname:")
            st.code(final_device_name, language="text")

            if st.button("🤖 AI Verify / Suggest Device Hostname", key="ai_chk_dev"):
                with st.spinner("Auditing against standards..."):
                    st.info(verify_and_suggest_with_ai(final_device_name, active_model))

            with st.expander("💡 Click to view reference hostname examples"):
                st.code(
                    "SWUKBRIS01-0      (Switch Stack, Member 0)\n"
                    "WAPUKBRIS01       (Access Point 01)\n"
                    "FWUKBRISPA01      (Palo Alto Firewall 01)\n"
                    "IONUKBRIS01       (Prisma SD-WAN 01)\n"
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
                l_dev = st.text_input(
                    "Local Device Hostname",
                    value=final_device_name,
                    placeholder="e.g. SWUSNYC01-0",
                    help="Hostname of the source/local network switch.",
                    key="up_ld"
                ).strip()

                l_port_raw = st.text_input(
                    "Local Port",
                    value="",
                    placeholder="e.g. GigabitEthernet1/0/48, Port 51, Te1/1",
                    help="Physical port name on the local switch.",
                    key="up_lp"
                )

                r_dev = st.text_input(
                    "Remote Device Hostname",
                    value="",
                    placeholder="e.g. SWUSNYC02-0, CORE-AGG01",
                    help="Hostname of the destination/remote connected device.",
                    key="up_rd"
                ).strip()

                r_port_raw = st.text_input(
                    "Remote Port",
                    value="",
                    placeholder="e.g. GigabitEthernet1/0/48, Port 26, Te1/2",
                    help="Physical port on the destination/remote connected device.",
                    key="up_rp"
                )

                link_role = st.text_input(
                    "Role / Purpose",
                    value="Uplink",
                    placeholder="e.g. Uplink, Downlink, MLAG",
                    help="Optional description tag appended in brackets.",
                    key="up_lr"
                ).strip()

                l_port_short = normalize_port_shortname(l_port_raw)
                r_port_short = normalize_port_shortname(r_port_raw)
                role_suffix = f" [{link_role}]" if link_role else ""

                st.caption(f"On Local Device (`{l_dev or 'LOCAL'}`):")
                st.code(f"to {r_dev}_{r_port_short}{role_suffix}", language="text")
                st.caption(f"On Remote Device (`{r_dev}`):")
                st.code(f"to {l_dev}_{l_port_short}{role_suffix}", language="text")

            elif p_cat == "Switch LAG Member Port (LACP)":
                l_port_raw = st.text_input("Local Port", value="", placeholder="e.g. Te1/0/1, Port 2", help="Member physical interface on the local switch.", key="lagm_lp")
                loc_po = st.text_input("Port-Channel ID", value="", placeholder="e.g. 1, 10, 20", help="Numerical Port-Channel / LAG identifier.", key="lagm_lpo").strip()
                r_dev = st.text_input("Remote Hostname", value="", placeholder="e.g. LACP-HOST-CLUSTER01", help="Target remote host, switch, or SAN.", key="lagm_rd").strip()
                r_port_raw = st.text_input("Remote Port", value="", placeholder="e.g. Eth1/1, Port 2", help="Connected port on the target remote system.", key="lagm_rp")
                link_role = st.text_input("Link Purpose", value="", placeholder="e.g. ESXi Uplink (optional)", help="Optional purpose annotation.", key="lagm_lr").strip()

                l_port_short = normalize_port_shortname(l_port_raw)
                r_port_short = normalize_port_shortname(r_port_raw)
                po_num = loc_po.replace('Po', '').replace('po', '').strip() or "1"
                role_suffix = f" [{link_role}]" if link_role else ""
                lag_desc = f"{l_port_short} [Po{po_num}] -> {r_dev}_{r_port_short}{role_suffix}"

                st.caption("LAG Member Port Description:")
                st.code(lag_desc, language="text")

            elif p_cat == "Switch Port-Channel (Logical)":
                loc_po = st.text_input("Local Port-Channel ID", value="", placeholder="e.g. 1, 10, 20", help="Local logical LAG channel ID.", key="pchan_lpo").strip()
                r_dev = st.text_input("Remote Device Hostname", value="", placeholder="e.g. CORE-AGG01", help="Target aggregation or peer switch hostname.", key="pchan_rd").strip()
                rem_po = st.text_input("Remote Port-Channel ID", value="", placeholder="e.g. 1, 10, 20", help="Port-Channel ID configured on the peer device.", key="pchan_rpo").strip()
                vlan_info = st.text_input("Trunk Info", value="", placeholder="e.g. TRUNK CORE, VLAN10,20,30", help="VLAN trunk or role designation tag.", key="pchan_vl").strip()

                l_po = f"Po{loc_po.replace('Po', '').replace('po', '').strip() or '1'}"
                r_po = f"Po{rem_po.replace('Po', '').replace('po', '').strip() or '1'}"
                vlan_suffix = f" [{vlan_info}]" if vlan_info else ""
                st.caption("Logical Port-Channel Description:")
                st.code(f"{l_po} -> {r_dev}_{r_po}{vlan_suffix}", language="text")

            elif p_cat == "Switch Access Port (Endpoint)":
                vlan_name = st.text_input("VLAN Name", value="", placeholder="e.g. VLAN10_Management, VLAN50_Printers", help="Name or functional tag of the untagged access VLAN.", key="acc_vln").strip()
                host_port_raw = st.text_input("Connected Host/Port", value="", placeholder="e.g. ESXHOST01_vmnic0, PRINTER-FL1", help="Connected host and specific interface.", key="acc_hp").strip()
                clean_host = re.sub(r"\s+", "", host_port_raw)
                st.caption("Access Port Description:")
                st.code(f"{vlan_name} - {clean_host}", language="text")

            else:
                fw_zone = st.text_input("Security Zone / Role", value="", placeholder="e.g. DMZ, TRUST, UNTRUST, INSIDE, OUTSIDE", help="Security zone assignment for the firewall interface.", key="fw_z_in").strip()
                fw_vlan = st.text_input("VLAN ID", value="", placeholder="e.g. 100, 200, 999", help="Associated 802.1Q VLAN sub-interface ID.", key="fw_v_in").strip()
                clean_fw_if = f"{fw_zone}_{fw_vlan}" if fw_vlan else fw_zone
                st.caption("Firewall Interface Description:")
                st.code(clean_fw_if, language="text")

    # 2. Hosts & Virtual Machines
    elif "2. Hosts" in naming_cat:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("#### 🖥️ ESXi Hypervisor Hostname")
            h_site = st.text_input(
                "Site Prefix",
                value="",
                placeholder="e.g. nyc, syd, lon, pws, age",
                help="3-4 character site abbreviation.",
                key="esx_site"
            ).strip()

            h_role = st.text_input(
                "Host Role (Optional)",
                value="",
                placeholder="e.g. esx, otinfhost, infmgmt, node",
                help="Hypervisor role identifier (e.g. 'esx' for standard cluster, 'otinfhost' for OT cluster).",
                key="esx_role"
            ).strip()

            h_num = st.text_input(
                "Host Sequence Number",
                value="001",
                placeholder="e.g. 001, 01, 1",
                help="Sequential node number.",
                key="esx_num"
            ).strip()

            h_dom = st.text_input(
                "Domain Name (FQDN Suffix)",
                value="",
                placeholder="e.g. corp.local, adds.net, ot.local (leave blank for shortname)",
                help="Full domain suffix. You can type any domain manually or leave it blank.",
                key="esx_dom"
            ).strip()

            raw_host = f"{h_site}{h_role or 'esx'}{h_num}"
            host_formatted = apply_case(raw_host, case_mode)
            gen_esx = f"{host_formatted}.{h_dom.lower()}" if h_dom else host_formatted

            st.caption("Generated ESXi Hostname:")
            st.code(gen_esx, language="text")

            if st.button("🤖 AI Verify ESXi Host", key="ai_chk_esx"):
                with st.spinner("Auditing..."):
                    st.info(verify_and_suggest_with_ai(gen_esx, active_model))

            with st.expander("💡 Click to view reference hypervisor examples"):
                st.code(
                    "NYCESX001.corp.local  (Standard Enterprise ESXi Node 001)\n"
                    "SYDOTINFHOST1.ot.net  (Industrial OT Cluster Node 1)\n"
                    "LONESX01              (Branch Hypervisor Standalone)",
                    language="text"
                )

        with col_b:
            st.markdown("#### 🖲️ Virtual Machine (VM) Hostname")
            v_site = st.text_input(
                "Site Prefix",
                value="",
                placeholder="e.g. nyc, syd, rofl, mel, lon",
                help="3-4 letter site or location code.",
                key="vm_site"
            ).strip()

            v_role = st.text_input(
                "Role Code",
                value="",
                placeholder="e.g. cvi, afs, sani, vlab, app, db, dc",
                help="Functional workload code (e.g. cvi=Core Virt, afs=App/File, sani=Storage, dc=Domain Controller).",
                key="vm_role"
            ).strip()

            v_seq = st.text_input(
                "Sequence Number",
                value="01",
                placeholder="e.g. 01, 02, 001",
                help="Two or three digit sequential VM number.",
                key="vm_seq"
            ).strip()

            raw_vm = f"{v_site}{v_role}{v_seq}"
            gen_vm = apply_case(raw_vm, case_mode)

            st.caption("Generated VM Hostname:")
            st.code(gen_vm, language="text")

            if st.button("🤖 AI Verify VM Hostname", key="ai_chk_vm"):
                with st.spinner("Auditing..."):
                    st.info(verify_and_suggest_with_ai(gen_vm, active_model))

            with st.expander("💡 Click to view reference VM examples"):
                st.code(
                    "NYCCVI01  (NYC Core Virtualization VM 01)\n"
                    "NYCAFS01  (NYC Application / File Server 01)\n"
                    "NYCSANI01 (NYC SAN / Storage Management 01)\n"
                    "NYCVLAB01 (NYC Test & Validation Lab 01)",
                    language="text"
                )

    # 3. ESXi Network Descriptions
    else:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("#### 1. Physical Uplink (`vmnic`)")
            vmnic = st.text_input("vmnic ID", value="", placeholder="e.g. vmnic0, vmnic1", help="Physical hypervisor NIC identifier.", key="vmnic_in")
            vsw = st.text_input("Target vSwitch", value="", placeholder="e.g. vSwitch0, dvSwitch01", help="Virtual switch or Distributed Switch name.", key="vsw1")
            status = st.radio("Status", ["Active Uplink", "Standby Uplink"], horizontal=True)
            
            gen_vmnic = f"{vmnic} - {vsw} {status}" if vmnic and vsw else ""
            st.caption("Generated Physical Uplink:")
            st.code(gen_vmnic or "vmnic0 - vSwitch0 Active Uplink", language="text")

        with col_b:
            st.markdown("#### 2. Port Group Teaming (`PG`)")
            vsw_pg = st.text_input("vSwitch Name", value="", placeholder="e.g. vSwitch0, dvSwitch01", help="Target virtual switch.", key="vsw2")
            act_nics = st.text_input("Active vmnics", value="", placeholder="e.g. vmnic0, vmnic1", help="Comma-separated list of active physical uplinks.", key="act_nics_in")
            stb_nics = st.text_input("Standby vmnics", value="", placeholder="e.g. vmnic2 (optional)", help="Comma-separated standby physical uplinks.", key="stb_nics_in")
            
            parts = [f"{act_nics.strip()} Active"] if act_nics.strip() else []
            if stb_nics.strip():
                parts.append(f"{stb_nics.strip()} Standby")
            
            gen_pg = f"{vsw_pg} [{' / '.join(parts)}]" if vsw_pg and parts else ""
            st.caption("Generated Port Group Teaming:")
            st.code(gen_pg or "vSwitch0 [vmnic0, vmnic1 Active]", language="text")

        with col_c:
            st.markdown("#### 3. VMkernel Adapter (`vmk`)")
            vmk_purp = st.text_input("Purpose / Service", value="", placeholder="e.g. Management Network, vMotion, vSAN", help="Designated role for the VMkernel adapter.", key="vmk_p_in")
            vsw_vmk = st.text_input("vSwitch Name", value="", placeholder="e.g. vSwitch0, dvSwitch01", help="Target virtual switch for the VMkernel adapter.", key="vsw3")
            
            gen_vmk = f"{vmk_purp} [{vsw_vmk}]" if vmk_purp and vsw_vmk else ""
            st.caption("Generated VMkernel Description:")
            st.code(gen_vmk or "Management Network [vSwitch0]", language="text")