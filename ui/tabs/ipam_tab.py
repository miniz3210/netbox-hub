def on_preset_change():
    selected = st.session_state.get("ipam_preset_selector")
    template_list = VLAN_PRESETS.get(selected, [])
    
    # Clear any leftover widget input state
    if "ipam_data_editor" in st.session_state:
        del st.session_state["ipam_data_editor"]
        
    if not template_list:
        st.session_state["ipam_persisted_rows"] = []
    else:
        new_rows = []
        for t in template_list:
            new_rows.append({
                "VLAN ID": t["vid"],
                "Role": t["role"],
                "VLAN Name": t.get("vlan_name", t["role"]),
                "VLAN Description": t.get("desc", ""),
                "Subnet (CIDR)": "",
                "fallback_subnet": ""
            })
        st.session_state["ipam_persisted_rows"] = new_rows