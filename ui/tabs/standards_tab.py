import re
import streamlit as st
from config.naming_rules import (
    load_naming_rules, save_naming_rules, export_rules_as_prompt,
    load_history, restore_from_history, clear_history, CATEGORIES,
    PATTERN_KEYS, DEFAULT_PATTERN_VARIABLES, DEFAULT_RULES
)
from core.naming_engine import parse_prompt_to_rules

TOKEN_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_]*)>")


def _records(value):
    """Convert data_editor output to records across Streamlit versions."""
    if hasattr(value, "to_dict"):
        return value.to_dict("records")
    return value or []


def _structured_rules(raw):
    """Return the editor model while accepting legacy flat rules during migration."""
    raw = raw if isinstance(raw, dict) else {}
    patterns = raw.get("naming_patterns") if isinstance(raw.get("naming_patterns"), dict) else raw.get("patterns")
    patterns = patterns if isinstance(patterns, dict) else {}
    if not patterns:
        patterns = {key: raw.get(key, DEFAULT_RULES.get(key, "")) for key in PATTERN_KEYS}
    patterns = {key: str(patterns.get(key, "")) for key in PATTERN_KEYS}

    variables = raw.get("pattern_variables")
    if isinstance(variables, dict):
        if all(isinstance(value, dict) and "label" in value for value in variables.values()):
            grouped = {}
            for token, metadata in variables.items():
                grouped.setdefault(str(metadata.get("category", "custom")), []).append(str(token))
            variables = grouped
        else:
            variables = {str(category): [str(token) for token in values]
                         for category, values in variables.items() if isinstance(values, (list, tuple))}
    else:
        variables = {category: list(tokens) for category, tokens in DEFAULT_PATTERN_VARIABLES.items()}

    categories = {category: {} for category in CATEGORIES}
    stored_categories = raw.get("categories")
    if isinstance(stored_categories, dict):
        for category, values in stored_categories.items():
            if category in categories and isinstance(values, dict):
                categories[category].update({str(key): str(value) for key, value in values.items()})
    for key, value in patterns.items():
        category = _PATTERN_CATEGORY.get(key, "netbox_hardware")
        categories[category].setdefault(key, value)
    metadata = raw.get("pattern_variables") if isinstance(raw.get("pattern_variables"), dict) else {}
    if not all(isinstance(value, dict) for value in metadata.values()):
        metadata = {}
    return {"categories": categories, "pattern_variables": variables,
            "variable_metadata": metadata, "naming_patterns": patterns, "patterns": patterns}


_PATTERN_CATEGORY = {
    "branch_switch": "network_security", "branch_ap": "network_security", "branch_security": "network_security",
    "switch_uplink_desc": "interface_descriptions", "switch_uplink_local": "interface_descriptions", "switch_uplink_remote": "interface_descriptions", "switch_lag_member": "interface_descriptions",
    "switch_port_channel": "interface_descriptions", "switch_access_desc": "interface_descriptions",
    "firewall_interface": "interface_descriptions", "esxi_host": "hypervisors_virtual_machines",
    "vm_host": "hypervisors_virtual_machines", "esxi_uplink": "esxi_network",
    "esxi_portgroup": "esxi_network", "esxi_portgroup_name": "esxi_network", "esxi_portgroup_desc": "esxi_network", "esxi_vmkernel": "esxi_network", "esxi_vmkernel_name": "esxi_network", "esxi_vmkernel_desc": "esxi_network", "netbox_server_yaml": "netbox_hardware",
}


def _editor_model(raw):
    model = _structured_rules(raw)
    # Flatten category values into the canonical pattern map for editable rows.
    for category, values in model["categories"].items():
        if isinstance(values, dict):
            for key, value in values.items():
                if key in PATTERN_KEYS:
                    model["patterns"][key] = str(value)
                model["naming_patterns"][key] = str(value)
    return model


def _token_warnings(model):
    known = set(model.get("variable_metadata", {})) or {token for values in model["pattern_variables"].values() for token in values}
    warnings = {}
    for key, pattern in model["patterns"].items():
        undefined = sorted(set(TOKEN_RE.findall(pattern)) - known)
        if undefined:
            warnings[key] = undefined
    return warnings


def _render_editor(current_rules):
    model = _editor_model(current_rules)
    registered = st.session_state.get("standards_registered_tokens", [])
    if registered:
        model["pattern_variables"].setdefault("custom", [])
        model["pattern_variables"]["custom"] = list(dict.fromkeys(
            model["pattern_variables"]["custom"] + registered
        ))
    st.markdown("##### Structured Naming Rules")
    st.caption("Edit reusable pattern tokens and category-specific naming patterns. Changes are saved explicitly.")

    st.markdown("#### Pattern Variables")
    variable_rows = []
    for category, tokens in model["pattern_variables"].items():
        for token in tokens:
            metadata = model.get("variable_metadata", {}).get(token, {})
            variable_rows.append({"category": metadata.get("category", category), "token": token,
                                  "label": metadata.get("label", token.replace("_", " ")),
                                  "placeholder": metadata.get("placeholder", "")})
    edited_variables = st.data_editor(
        variable_rows, num_rows="dynamic", hide_index=True, use_container_width=True,
         column_config={"category": st.column_config.TextColumn("Category", required=True),
                        "token": st.column_config.TextColumn("Token (without angle brackets)", required=True),
                        "label": st.column_config.TextColumn("UI Label", required=True),
                        "placeholder": st.column_config.TextColumn("UI Placeholder")},
        key="standards_pattern_variables",
    )
    variables = {}
    variable_metadata = {}
    variable_errors = []
    for row in _records(edited_variables):
        category, token = str(row.get("category", "")).strip(), str(row.get("token", "")).strip()
        if not category and not token:
            continue
        if not category or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", token):
            variable_errors.append(f"Invalid variable row: {category or '(missing category)'} / {token or '(missing token)'}")
        else:
            variables.setdefault(category, []).append(token)
            variable_metadata[token] = {
                "label": str(row.get("label", "") or token.replace("_", " ")).strip(),
                "placeholder": str(row.get("placeholder", "") or "").strip(),
                "category": category,
            }

    st.markdown("#### Naming Patterns")
    patterns = {}
    for category, title in CATEGORIES.items():
        st.markdown(f"**{title}**")
        keys = [key for key in model["patterns"] if _PATTERN_CATEGORY.get(key) == category]
        rows = [{"name": key, "pattern": model["patterns"].get(key, "")} for key in keys]
        rows = st.data_editor(
            rows, num_rows="dynamic", hide_index=True, use_container_width=True,
            column_config={"name": st.column_config.TextColumn("Pattern name", required=True),
                           "pattern": st.column_config.TextColumn("Pattern", required=True)},
            key=f"standards_patterns_{category}",
        )
        for row in _records(rows):
            name = str(row.get("name", "")).strip()
            if name:
                patterns[name] = str(row.get("pattern", ""))

    candidate = {"categories": {category: {key: value for key, value in patterns.items()
                                             if _PATTERN_CATEGORY.get(key) == category}
                                  for category in CATEGORIES},
                 "pattern_variables": variable_metadata, "naming_patterns": patterns, "patterns": patterns}
    warnings = _token_warnings(candidate)
    if warnings:
        st.warning("Undefined tokens are referenced by active patterns.")
        for key, tokens in warnings.items():
            st.write(f"`{key}`: " + ", ".join(f"`<{token}>`" for token in tokens))
            for token in tokens:
                if st.button(f"Register <{token}>", key=f"register_token_{key}_{token}"):
                    registered = st.session_state.setdefault("standards_registered_tokens", [])
                    if token not in registered:
                        registered.append(token)
                    st.rerun()
    for error in variable_errors:
        st.error(error)

    col_save, col_reset = st.columns([3, 1])
    with col_save:
        if st.button("Save Changes", type="primary", use_container_width=True, key="save_structured_standards"):
            if variable_errors:
                st.error("Fix invalid pattern variable rows before saving.")
            else:
                save_naming_rules(candidate, source="Manual Edit")
                st.session_state["naming_rules"] = candidate
                st.session_state["standards_saved"] = True
                st.rerun()
    with col_reset:
        if st.button("Reset to Defaults", use_container_width=True, key="reset_structured_standards"):
            defaults = _editor_model(DEFAULT_RULES)
            save_naming_rules(defaults, source="Reset to Defaults")
            st.session_state["naming_rules"] = defaults
            st.session_state["standards_reset"] = True
            st.rerun()


def render_standards_tab(active_model):
    st.subheader("📖 Infrastructure Naming Standards Configuration")
    st.caption("Define and manage your organization's naming conventions. All patterns configured here are automatically applied in the Naming tab.")
    
    # Check for save success message
    if "standards_saved" in st.session_state and st.session_state["standards_saved"]:
        st.success("✅ Naming standards saved successfully!")
        st.session_state["standards_saved"] = False
    
    # Check for reset success message
    if "standards_reset" in st.session_state and st.session_state["standards_reset"]:
        st.success("✅ Reset to default standards!")
        st.session_state["standards_reset"] = False
    
    # Always reload rules from file to ensure fresh data after save
    current_rules = load_naming_rules()
    
    # Update session state with fresh rules
    st.session_state["naming_rules"] = current_rules
    
    # Create tabs for different editing modes
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📝 Edit Standards", "📄 View Full Prompt", "📘 Pattern Variables Reference", "🤖 AI Import", "📜 Change History"])
    
    # Tab 1: Structured editable model
    with tab1:
        _render_editor(current_rules)
        '''
            st.markdown("#### 1. Network & Security Devices")
            col1, col2 = st.columns(2)
            
            with col1:
                branch_switch = st.text_input(
                    "Switch Hostname Pattern",
                    value=current_rules.get("branch_switch", ""),
                    help="Format: SW<Country><State><Site><Zone><Seq>-<StackID>",
                    key="form_switch",
                    autocomplete="off"
                )
                branch_ap = st.text_input(
                    "Wireless AP Pattern",
                    value=current_rules.get("branch_ap", ""),
                    help="Format: WAP<Country><State><Site><Seq>",
                    key="form_ap",
                    autocomplete="off"
                )
                branch_security = st.text_input(
                    "Firewall/Security Pattern",
                    value=current_rules.get("branch_security", ""),
                    help="Format: FW<Country><State><Site><Vendor><Seq>",
                    key="form_fw",
                    autocomplete="off"
                )
            
            with col2:
                switch_uplink_desc = st.text_input(
                    "Switch Uplink Description",
                    value=current_rules.get("switch_uplink_desc", ""),
                    help="Available: <Local_Device>, <Local_Port>, <Local_Port_Short>, <Remote_Device>, <Remote_Port>, <Remote_Port_Short>",
                    key="form_uplink",
                    autocomplete="off"
                )
                switch_lag_member = st.text_input(
                    "LAG Member Description",
                    value=current_rules.get("switch_lag_member", ""),
                    help="Available: <Remote_Device>, <Remote_Port>, <Remote_Port_Short>",
                    key="form_lag",
                    autocomplete="off"
                )
                switch_port_channel = st.text_input(
                    "Port-Channel Description",
                    value=current_rules.get("switch_port_channel", ""),
                    help="Available: <Local_Po_ID>, <Remote_Device>",
                    key="form_po",
                    autocomplete="off"
                )
            
            col3, col4 = st.columns(2)
            with col3:
                switch_access_desc = st.text_input(
                    "Access Port Description",
                    value=current_rules.get("switch_access_desc", ""),
                    help="Available: <VLAN_ID>, <VLAN_Name>, <Device>, <Port>",
                    key="form_access",
                    autocomplete="off"
                )
            with col4:
                firewall_interface = st.text_input(
                    "Firewall Interface Description",
                    value=current_rules.get("firewall_interface", ""),
                    help="Available: <Role_Zone>, <VLAN_ID>",
                    key="form_fw_int",
                    autocomplete="off"
                )
            
            st.markdown("#### 2. Hypervisors & Virtual Machines")
            col5, col6 = st.columns(2)
            
            with col5:
                esxi_host = st.text_area(
                    "ESXi Hypervisor Pattern",
                    value=current_rules.get("esxi_host", ""),
                    height=80,
                    help="Format: <Site><Role><Seq>.<Domain> (e.g., ageesx001.example.corp, nycotinfhost1.example.ot)",
                    key="form_esxi"
                )
                vm_host = st.text_area(
                    "Virtual Machine Pattern",
                    value=current_rules.get("vm_host", ""),
                    height=80,
                    help="Format: <Site><Role><Seq> (e.g., ageapp01, nycdb02, sydfs001)",
                    key="form_vm"
                )
            
            with col6:
                esxi_uplink = st.text_input(
                    "ESXi Physical Uplink",
                    value=current_rules.get("esxi_uplink", ""),
                    help="Available: <vmnic>, <vSwitch>, <Purpose>, <Status>",
                    key="form_esxi_uplink",
                    autocomplete="off"
                )
                esxi_portgroup = st.text_input(
                    "ESXi Port Group Description",
                    value=current_rules.get("esxi_portgroup", ""),
                    help="Available: <PortGroup>, <Active_vmnics>, <Standby_vmnics>",
                    key="form_esxi_pg",
                    autocomplete="off"
                )
                esxi_vmkernel = st.text_input(
                    "ESXi VMkernel Description",
                    value=current_rules.get("esxi_vmkernel", ""),
                    help="Available: <Purpose>, <vSwitch>, <Active_vmnics>, <Standby_vmnics>",
                    key="form_esxi_vmk",
                    autocomplete="off"
                )
            
            st.markdown("#### 3. NetBox Hardware YAML Schema")
            netbox_server_yaml = st.text_area(
                "NetBox Server YAML Guidelines",
                value=current_rules.get("netbox_server_yaml", ""),
                height=100,
                help="Guidelines for generating NetBox device-type YAML files",
                key="form_yaml"
            )
            
            col_save, col_reset = st.columns([3, 1])
            with col_save:
                submitted = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)
            with col_reset:
                reset = st.form_submit_button("🔄 Reset to Defaults", use_container_width=True)
            
            if submitted:
                new_rules = {
                    "branch_switch": branch_switch,
                    "branch_ap": branch_ap,
                    "branch_security": branch_security,
                    "switch_uplink_desc": switch_uplink_desc,
                    "switch_lag_member": switch_lag_member,
                    "switch_port_channel": switch_port_channel,
                    "switch_access_desc": switch_access_desc,
                    "firewall_interface": firewall_interface,
                    "esxi_host": esxi_host,
                    "vm_host": vm_host,
                    "esxi_uplink": esxi_uplink,
                    "esxi_portgroup": esxi_portgroup,
                    "esxi_vmkernel": esxi_vmkernel,
                    "netbox_server_yaml": netbox_server_yaml
                }
                try:
                    # Save to file first
                    save_naming_rules(new_rules, source="Manual Edit")
                    # Update session state with new rules
                    st.session_state["naming_rules"] = new_rules.copy()
                    # Set flag for success message
                    st.session_state["standards_saved"] = True
                    # Force immediate rerun
                    st.rerun()
        '''
    
    # Tab 2: View Full Prompt (Read-Only)
    with tab2:
        st.markdown("##### Active Infrastructure Guidelines")
        st.caption("This is the complete prompt that AI uses to validate naming conventions. It's automatically generated from your configured patterns.")
        
        # Always reload from session state or file to ensure latest updates
        if "naming_rules" in st.session_state:
            current_rules_for_view = st.session_state["naming_rules"]
        else:
            current_rules_for_view = load_naming_rules()
        
        prompt_rep = export_rules_as_prompt(current_rules_for_view)
        
        # Use a dynamic key that changes when rules are updated to force widget refresh
        text_area_key = f"standards_display_{hash(str(current_rules_for_view))}"
        st.text_area("System Context", value=prompt_rep, height=500, disabled=True, key=text_area_key)
        
        st.download_button(
            "📥 Download Guidelines Prompt (.txt)", 
            prompt_rep, 
            "naming_standards.txt", 
            "text/plain",
            use_container_width=True
        )
    
    # Tab 3: Pattern Variables Reference
    with tab3:
        st.markdown("##### 📘 Pattern Variables Reference Guide")
        st.caption("Use these variables in your naming patterns. The Naming tab will automatically replace them with actual values.")
        
        st.markdown("#### Network & Security Device Hostnames")
        
        with st.expander("🖥️ Device Hostname Variables", expanded=True):
            st.markdown("""
**Available Variables:**
- `<Country>` - 2-letter country code (e.g., US, UK, AU)
- `<State>` - State or region code (e.g., NY, CA, NSW)
- `<Site>` - Site code (e.g., NYC, LON, SYD, AGE)
- `<Zone>` - Zone, role, or vendor identifier (e.g., CORE, DIST, EDGE, PA)
- `<Vendor>` - Vendor identifier (same as Zone, interchangeable)
- `<Seq>` - Sequence number (e.g., 01, 02)
- `<StackID>` - Stack or member ID (e.g., 0, 1)

**Example Patterns:**
- `SW<Country><Site><Seq>-<StackID>` → `SWUSNYC01-0`
- `WAP<Country><Site><Seq>` → `WAPUSNYC01`
- `FW<Country><Site><Vendor><Seq>` → `FWUSNYCPA01`
- `ION<Country><Site><Seq>` → `IONUSNYC01`
            """)
        
        st.markdown("#### Switch & Firewall Interface Descriptions")
        
        with st.expander("🔗 Switch Uplink Description Variables"):
            st.markdown("""
**Available Variables:**
- `<Local_Device>` - The hostname of the local switch (e.g., CV-BARRICAS)
- `<Local_Port>` - The raw port name as entered (e.g., XGigabitEthernet0/0/31)
- `<Local_Port_Short>` - **Auto-shortened** port name (e.g., XGE0/0/31) ⭐
- `<Remote_Device>` - The hostname of the remote switch (e.g., CV-BARRICAS_PASILLO)
- `<Remote_Port>` - The raw port name as entered (e.g., Port24)
- `<Remote_Port_Short>` - **Auto-shortened** port name (e.g., Po24) ⭐

**Auto-Shortening Examples:**
- `XGigabitEthernet0/0/31` → `XGE0/0/31`
- `TenGigabitEthernet1/0/1` → `Te1/0/1`
- `GigabitEthernet1/0/24` → `Gi1/0/24`
- `Port-channel10` → `Po10`

**Example Patterns:**
- `Uplink_to_<Remote_Device>_<Remote_Port_Short>` → `Uplink_to_CV-BARRICAS_PASILLO_Po24`
- `Uplink_from_<Local_Device>_to_<Remote_Device>_<Remote_Port_Short>` → `Uplink_from_CV-BARRICAS_to_CV-BARRICAS_PASILLO_XGE0/0/31`
- `<Local_Device>_<Local_Port_Short>_to_<Remote_Device>_<Remote_Port_Short>` → `CV-BARRICAS_Gi1/0/1_to_CV-CPD_Gi1/0/1`
            """)
        
        with st.expander("🔗 LAG Member Port (LACP) Variables"):
            st.markdown("""
**Available Variables:**
- `<Remote_Device>` - The hostname of the remote device
- `<Remote_Port>` - The raw port name as entered
- `<Remote_Port_Short>` - **Auto-shortened** port name ⭐

**Example Patterns:**
- `LACP_to_<Remote_Device>_<Remote_Port_Short>` → `LACP_to_SWUSNYC02-0_Gi1/0/1`
- `LAG_member_to_<Remote_Device>_<Remote_Port_Short>` → `LAG_member_to_SWUSNYC02-0_Po1`
            """)
        
        with st.expander("🔗 Port-Channel (Logical) Variables"):
            st.markdown("""
**Available Variables:**
- `<Local_Po_ID>` - The Port-Channel ID (e.g., LAG1, Po1)
- `<Remote_Device>` - The hostname of the remote device

**Example Patterns:**
- `<Local_Po_ID>_to_<Remote_Device>` → `LAG1_to_SWUSNYC02-0`
- `PortChannel_<Local_Po_ID>_<Remote_Device>` → `PortChannel_LAG1_SWUSNYC02-0`
            """)
        
        with st.expander("🔗 Access Port (Endpoint) Variables"):
            st.markdown("""
**Available Variables:**
- `<VLAN_ID>` - The VLAN number (e.g., 10, 100)
- `<VLAN_Name>` - The VLAN name (e.g., Data, Voice, Guest)
- `<Device>` - The connected device/host name
- `<Port>` - The endpoint port identifier

**Example Patterns:**
- `<VLAN_Name> - <Device>_<Port>` → `Data - PC-001_eth0`
- `VLAN<VLAN_ID> - <Device>_<Port>` → `VLAN10 - PC-001_eth0`
- `<Device>_<Port>` → `pwsesx001_iLO` (VLAN optional)

**Note:** If VLAN fields are empty, only `<Device>_<Port>` is used.
            """)
        
        with st.expander("🔗 Firewall Interface Variables"):
            st.markdown("""
**Available Variables:**
- `<Role_Zone>` - The security zone or role (e.g., TRUST, UNTRUST, DMZ)
- `<VLAN_ID>` - The VLAN number

**Example Patterns:**
- `<Role_Zone>_<VLAN_ID>` → `TRUST_10`
- `<Role_Zone>_VLAN<VLAN_ID>` → `TRUST_VLAN10`
            """)
        
        st.markdown("#### Hypervisors & Virtual Machines")
        
        with st.expander("🖥️ ESXi Hypervisor Variables"):
            st.markdown("""
**Available Variables:**
- `<Site>` or `<site>` - Site code (uppercase or lowercase)
- `<Role>` or `<role>` - Host role (uppercase or lowercase, e.g., esx, otinfhost, infhost)
- `<Seq>` or `<seq>` - Sequence number
- `<Domain>` or `<domain>` - Domain name (uppercase or lowercase)

**Example Patterns:**
- `<site><role><seq>.<domain>` → `ageesx001.example.corp`
- `<Site><Role><Seq>.<Domain>` → `AGEESX001.EXAMPLE.CORP`
- `<site>-<role>-<seq>.<domain>` → `age-esx-001.corp.internal`
            """)
        
        with st.expander("🖲️ Virtual Machine Variables"):
            st.markdown("""
**Available Variables:**
- `<Site>` or `<site>` - Site code (uppercase or lowercase)
- `<Country>` - First 2 characters of site code (e.g., US from USNYC)
- `<Role>` or `<role>` - Workload role (uppercase or lowercase, e.g., app, web, db, fs, dc)
- `<Seq>` or `<seq>` - Sequence number

**Example Patterns:**
- `<site><role><seq>` → `ageapp01`
- `<Country><Site><Role><Seq>` → `USNYCAPP01`
- `<Site>-<Role>-<Seq>` → `AGE-APP-01`
            """)
        
        st.markdown("#### ESXi Network Descriptions")
        
        with st.expander("🔗 ESXi Physical Uplink Variables (PCIeX/PortX)"):
            st.markdown("""
**Available Variables:**
- `<vmnic>` - The vmnic identifier (e.g., vmnic0, vmnic1)
- `<vSwitch>` - The vSwitch name (e.g., vSwitch0, vSwitch1)
- `<Purpose>` - The service purpose (e.g., Management, vMotion, Storage)
- `<Status>` - The uplink status (Active Uplink or Standby Uplink)

**Auto-Normalization:**
- Input "nic0" or "vmnic0" → Output: `vmnic0`
- Input "vswitch0" or "vswtich0" → Output: `vSwitch0` ⭐
- Input "vmotion" → Output: `vMotion` ⭐
- Input "management" → Output: `Management` ⭐

**Example Patterns:**
- `<vmnic> - <vSwitch> <Purpose> <Status>` → `vmnic1 - vSwitch0 Management Active Uplink`
- `<vmnic> - <vSwitch> <Status>` → `vmnic0 - vSwitch0 Active Uplink`
- `<vmnic>_<vSwitch>_<Purpose>_<Status>` → `vmnic1_vSwitch0_Management_Active_Uplink`

**Note:** vSwitch and Purpose normalization is always applied to fix common typos and ensure proper capitalization.
            """)
        
        with st.expander("🔗 ESXi Port Group Variables"):
            st.markdown("""
**Available Variables:**
- `<pg_network>` - The network name (e.g., VM Network, vMotion)
- `<PortGroup>` - The vSwitch name (e.g., vSwitch0)
- `<Active_vmnics>` - Active vmnic list (e.g., vmnic0, vmnic1)
- `<Standby_vmnics>` - Standby vmnic list (optional)

**Auto-Normalization:**
- Input "vm network" → Output: `VM Network` ⭐
- Input "vmotion" → Output: `vMotion` ⭐
- Input "vswitch0" → Output: `vSwitch0` ⭐

**Output Format:**
- **Port Group Name:** `PG-<pg_network>` → `PG-VM Network`
- **Port Group Description:** `<PortGroup> (<Active_vmnics> Active)` → `vSwitch0 (vmnic0 Active)`
- **With Standby:** `<PortGroup> (<Active_vmnics> Active / <Standby_vmnics> Standby)` → `vSwitch0 (vmnic0 Active / vmnic1 Standby)`

**Note:** Network names and vSwitch are always normalized for proper capitalization.
            """)
        
        with st.expander("🔗 ESXi VMkernel Adapter Variables"):
            st.markdown("""
**Available Variables:**
- `<Purpose>` - The service purpose (e.g., Management, vMotion, Storage)
- `<vSwitch>` - The vSwitch name
- `<Active_vmnics>` - Active vmnic list (e.g., vmnic0, vmnic1)
- `<Standby_vmnics>` - Standby vmnic list (optional)

**Auto-Normalization:**
- Input "vmotion" → Output: `vMotion` ⭐
- Input "management" → Output: `Management` ⭐
- Input "iscsi" → Output: `iSCSI` ⭐
- Input "vswitch0" → Output: `vSwitch0` ⭐

**Output Format:**
- **With Teaming:** `<Purpose> Network - <vSwitch> (<Active_vmnics> Active / <Standby_vmnics> Standby)`
- **Active Only:** `<Purpose> Network - <vSwitch> (<Active_vmnics> Active)`
- **Without Teaming:** `<Purpose> (<vSwitch>)`

**Example Patterns:**
- `Management Network - vSwitch0 (vmnic0 Active / vmnic1 Standby)`
- `vMotion Network - vSwitch1 (vmnic2 Active)`
- `Storage Network - vSwitch2 (vmnic4 Active / vmnic5 Standby)`
- `Management (vSwitch0)` (without teaming details)

**Note:** Purpose and vSwitch are always normalized for proper capitalization. Common purposes like Management, vMotion, Storage, and iSCSI automatically append "Network" suffix.
            """)
        
        st.info("💡 **Tip:** Copy any example pattern and modify it in the Edit Standards tab. All changes take effect immediately in the Naming tab.")
    
    # Tab 4: AI-Powered Import
    with tab4:
        st.markdown("##### 📥 Import Standards from Natural Language")
        st.caption("Paste a natural language description of your naming standards and AI will parse it into structured patterns.")
        
        imported_text = st.text_area(
            "Paste Naming Guidelines", 
            placeholder="e.g., 'Switch naming should be SW followed by country code, site code, and sequence number...'\n\nDescribe your complete naming conventions in natural language.",
            height=300, 
            key="import_prompt_text"
        )
        
        col_parse, col_example = st.columns([2, 1])
        with col_parse:
            if st.button("🤖 Parse & Apply with AI", type="primary", use_container_width=True):
                if imported_text.strip():
                    with st.spinner(f"Parsing naming standards using {active_model}..."):
                        try:
                            extracted = parse_prompt_to_rules(imported_text, active_model)
                            save_naming_rules(extracted, source="AI Import")
                            st.session_state["naming_rules"] = extracted
                            st.success("✅ Standards updated successfully from AI parsing!")
                            st.rerun()
                except Exception as e:
                            st.error(f"❌ Failed to parse prompt: {str(e)}")
                            st.info("💡 Try providing more detailed descriptions of your naming patterns.")
                else:
                    st.warning("⚠️ Please paste your naming guidelines text first.")
        
        with col_example:
            if st.button("📋 Show Example", use_container_width=True):
                example = """Network Device Naming:
- Switches: SW + 2-letter country + site code + sequence (e.g., SWUSNYC01)
- Access Points: WAP + country + site + number
- Firewalls: FW + country + site + vendor + sequence

Interface Descriptions:
- Uplinks should show: Uplink_to_<remote device>_<port>
- LAG members: LACP_to_<remote device>_<port>
- Access ports: VLAN name - device name_port (VLAN optional)

ESXi Hosts:
- Format: <site><esx><number>.<domain>
- IT domain: .example.corp
- OT domain: .example.ot
"""
                st.code(example, language="text")
                st.info("💡 Copy this example and modify it with your own standards, then paste above and click 'Parse & Apply'.")
    
    # Tab 5: Change History
    with tab5:
        st.markdown("##### 📜 Naming Standards Change History")
        st.caption("View and restore previous versions of your naming standards. The last 10 changes are saved automatically.")
        
        history = load_history()
        
        if not history:
            st.info("📭 No history available yet. Changes will be tracked once you save modifications.")
        else:
            st.success(f"📊 {len(history)} version(s) in history")
            
            # Add clear history button
            col_info, col_clear = st.columns([3, 1])
            with col_clear:
                if st.button("🗑️ Clear History", use_container_width=True):
                    clear_history()
                    st.success("✅ History cleared!")
                st.rerun()
        """
            
            st.markdown("---")
            
            # Display each history entry
            for idx, entry in enumerate(history):
                timestamp = entry.get("timestamp", "Unknown")
                source = entry.get("source", "Unknown")
                rules = entry.get("rules", {})
                
                with st.expander(f"🕐 **{timestamp}** - {source}", expanded=(idx == 0)):
                    # Show summary of changes
                    st.markdown("**Pattern Summary:**")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("##### Network Devices")
                        st.code(f"Switch: {rules.get('branch_switch', 'N/A')[:60]}...", language="text")
                        st.code(f"AP: {rules.get('branch_ap', 'N/A')[:60]}...", language="text")
                        st.code(f"Firewall: {rules.get('branch_security', 'N/A')[:60]}...", language="text")
                    
                    with col2:
                        st.markdown("##### Interface Descriptions")
                        st.code(f"Uplink: {rules.get('switch_uplink_desc', 'N/A')[:60]}...", language="text")
                        st.code(f"LAG: {rules.get('switch_lag_member', 'N/A')[:60]}...", language="text")
                        st.code(f"Access: {rules.get('switch_access_desc', 'N/A')[:60]}...", language="text")
                    
                    # Action buttons
                    col_restore, col_view = st.columns([1, 1])
                    
                    with col_restore:
                        if st.button(f"↩️ Restore This Version", key=f"restore_{idx}", use_container_width=True):
                            try:
                                restored_rules = restore_from_history(idx)
                                save_naming_rules(restored_rules, source=f"Restored from {timestamp}")
                                st.session_state["naming_rules"] = restored_rules
                                st.success(f"✅ Restored version from {timestamp}")
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Failed to restore: {str(e)}")
                    
                    with col_view:
                        if st.button(f"👁️ View Full Details", key=f"view_{idx}", use_container_width=True):
                            st.session_state[f"show_details_{idx}"] = not st.session_state.get(f"show_details_{idx}", False)
                            st.rerun()
                    
                    # Show full details if toggled
                    if st.session_state.get(f"show_details_{idx}", False):
                        st.markdown("---")
                        st.markdown("**Complete Pattern Configuration:**")
                        st.json(rules)
