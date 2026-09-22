import streamlit as st
from config.naming_rules import (
    load_naming_rules, save_naming_rules, export_rules_as_prompt,
    load_history, restore_from_history, clear_history,
    get_pattern_variables, get_naming_patterns,
)
from core.naming_engine import parse_prompt_to_rules, generate_autocorrect_rule
from utils.formatters import (
    load_auto_corrections, save_auto_corrections, reset_auto_corrections,
)

def _persist_variables(rules: dict, variables: dict) -> None:
    """Save updated ``pattern_variables`` to file and session state."""
    rules["pattern_variables"] = variables
    save_naming_rules(rules, source="Variable Manager")
    st.session_state["naming_rules"] = rules.copy()
    st.session_state["variables_saved"] = True
    st.rerun()


def _render_auto_correction_manager(active_model: str) -> None:
    """CRUD manager for the externalized VMware syntax auto-correction rules.

    Reads/writes ``data/autocorrect_rules.yaml`` and refreshes the in-session
    cache immediately so the ESXi naming tab picks up changes without a restart.
    """
    st.markdown("---")
    st.markdown("##### 🛠️ Manage Syntax Auto-Correction Rules")
    st.caption(
        "Edit the pattern/replacement pairs that auto-correct VMware syntax "
        "(vswitch1 -> vSwitch1, nic0 -> vmnic0). Changes are written to "
        "`data/autocorrect_rules.yaml` and applied live to the ESXi naming tab."
    )

    if st.session_state.pop("autocorrect_saved", False):
        st.success("✅ Auto-correction rules saved & applied!")
    if st.session_state.pop("autocorrect_reset", False):
        st.success("✅ Auto-correction rules reset to factory defaults!")

    rules = load_auto_corrections()
    categories = list(rules.keys())

    if not categories:
        st.info("No auto-correction categories defined.")
        return

    with st.expander("💡 Quick User Guide: How Auto-Correction Works", expanded=False):
        st.markdown(
            """
Each **Original Pattern** is a regular expression that catches irregular syntax, and the
**Replacement** is the standardized format you want instead.

- **Original Pattern** — the regex target. E.g. `(?i)\\b(vswitch)(\\d+)\\b`
  catches `vswitch0`, `VSWITCH1`, etc. (the `(?i)` makes it case-insensitive).
- **Replacement** — the standard form, using capture groups. E.g. `vSwitch\\2`
  normalizes the prefix to `vSwitch` while keeping the original port/switch number.

**Common examples:**

| Input             | Result     |
|-------------------|------------|
| `vswitch0`       | `vSwitch0`|
| `dvswitch1`       | `dvSwitch1`|
| `VMNIC0` / `nic0` / `eth0` | `vmnic0` |
| `VMK0`            | `vmk0`     |

**Actions:**

- Edit any pattern/replacement inline, then click **💾 Save & Apply Changes** to
  update `data/autocorrect_rules.yaml`.
- Use the **On** checkbox to enable / disable a rule without deleting it.
- Use **➕ Add Rule** to append custom conventions.
- Use **🔄 Reset to Factory Defaults** to recover the original definitions.
"""
        )

    for category in categories:
        with st.expander("🛠️ Auto-Correction Rules", expanded=True):
            items = list(rules[category])
            updated = []
            pending_delete = None
            for idx, rule in enumerate(items):
                col_p, col_r, col_d, col_e, col_del = st.columns([3.0, 2.2, 3.0, 0.7, 0.7])
                with col_p:
                    p = st.text_input(
                        "Original Pattern",
                        value=rule.get("pattern", ""),
                        key=f"ac_{category}_p_{idx}",
                        help="Python regex applied to the raw input.",
                    )
                with col_r:
                    r = st.text_input(
                        "Replacement",
                        value=rule.get("replacement", ""),
                        key=f"ac_{category}_r_{idx}",
                        help="Replacement string (may reference regex groups, e.g. \\1).",
                    )
                with col_d:
                    d = st.text_input(
                        "Description",
                        value=rule.get("description", ""),
                        key=f"ac_{category}_d_{idx}",
                    )
                with col_e:
                    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                    enabled = st.checkbox(
                        "On",
                        value=bool(rule.get("enabled", True)),
                        key=f"ac_{category}_e_{idx}",
                        help="Toggle this rule on/off without deleting it.",
                    )
                with col_del:
                    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                    if st.button("🗑️", key=f"ac_{category}_del_{idx}", help="Delete this rule"):
                        pending_delete = idx

                if pending_delete == idx:
                    continue
                if p.strip():
                    updated.append({
                        "pattern": p,
                        "replacement": r,
                        "description": d,
                        "enabled": bool(enabled),
                    })

            with st.expander("✨ AI Assistant: Generate Rule from Natural Language", expanded=False):
                st.caption("Describe the formatting in plain text and let AI build the regex for you.")
                ai_desc = st.text_input(
                    "Describe the correction rule in plain text",
                    key=f"ac_{category}_ai_desc",
                    placeholder="e.g. Change vlan to uppercase VLAN, or change gigabitethernet to Gi",
                )
                if st.button("🤖 Generate Regex Rule", key=f"ac_{category}_ai_gen", use_container_width=True):
                    if ai_desc.strip():
                        try:
                            with st.spinner(f"Generating rule using {active_model}..."):
                                result = generate_autocorrect_rule(ai_desc.strip(), active_model)
                            st.session_state[f"ac_{category}_new_p"] = result["pattern"]
                            st.session_state[f"ac_{category}_new_r"] = result["replacement"]
                            st.session_state[f"ac_{category}_new_d"] = result["description"]
                            st.session_state["autocorrect_ai_generated"] = True
                            st.toast("Rule generated! Review and click '➕ Add Rule' to apply.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ AI rule generation failed: {e}")
                    else:
                        st.warning("⚠️ Please describe the correction first.")

            col_add_p, col_add_r, col_add_d = st.columns([3.0, 2.2, 3.0])
            with col_add_p:
                new_p = st.text_input("New Pattern", value="", key=f"ac_{category}_new_p",
                                     placeholder=r"(?i)\b(vswitch)(\d+)\b")
            with col_add_r:
                new_r = st.text_input("New Replacement", value="", key=f"ac_{category}_new_r",
                                     placeholder=r"vSwitch\2")
            with col_add_d:
                new_d = st.text_input("New Description", value="", key=f"ac_{category}_new_d",
                                     placeholder="Describe the rule")

            col_save, col_add, col_reset = st.columns([2, 2, 2])
            with col_save:
                if st.button("💾 Save & Apply Changes", key=f"ac_{category}_save", type="primary", use_container_width=True):
                    final = dict(rules)
                    final[category] = updated
                    _persist_auto_corrections(final)
            with col_add:
                if st.button("➕ Add Rule", key=f"ac_{category}_add", use_container_width=True):
                    if new_p.strip():
                        final = dict(rules)
                        final[category] = list(updated) + [{
                            "pattern": new_p,
                            "replacement": new_r,
                            "description": new_d,
                            "enabled": True,
                        }]
                        _persist_auto_corrections(final)
                    else:
                        st.warning("⚠️ Enter a regex pattern to add.")
            with col_reset:
                if st.button("🔄 Reset to Factory Defaults", key="ac_reset_factory", use_container_width=True):
                    reset_auto_corrections()
                    st.session_state["autocorrect_reset"] = True
                    st.rerun()

    st.markdown("---")


def _persist_auto_corrections(data: dict) -> None:
    """Save auto-correction rules to YAML and refresh the session cache."""
    save_auto_corrections(data, source="Management UI")
    st.session_state["autocorrect_rules_cache"] = data
    st.session_state["autocorrect_saved"] = True
    st.rerun()



def render_standards_tab(active_model):
    st.subheader("📖 Infrastructure Naming Standards Configuration")
    st.caption("Define and manage your organization's naming conventions. All patterns configured here are automatically applied in the Naming tab.")
    
    # Check for variable manager save success message
    if "variables_saved" in st.session_state and st.session_state["variables_saved"]:
        st.success("✅ Variables saved successfully!")
        st.session_state["variables_saved"] = False

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
    
    # Tab 1: Editable Form Interface
    with tab1:
        st.markdown("##### Edit Naming Patterns")
        st.info("💡 Modify the naming patterns below. Changes are saved when you click 'Save Changes'. Use the **Pattern Variables Reference** tab to see all available variables.")
        
        with st.form("naming_standards_form"):
            st.markdown("#### 1. Network & Security Devices")
            col1, col2 = st.columns(2)
            
            with col1:
                branch_switch = st.text_input(
                    "Switch Hostname Pattern (SW / SWI)",
                    value=current_rules.get("branch_switch", ""),
                    help="Format: SW<Country><State><Site><Zone><Seq>-<StackID>",
                    key="form_switch",
                    autocomplete="off"
                )
                branch_stack = st.text_input(
                    "Virtual Chassis / Stack Pattern (VS)",
                    value=current_rules.get("branch_stack", ""),
                    help="Format: VS<Country><State><Site><Seq>-<StackID>",
                    key="form_stack",
                    autocomplete="off"
                )
                branch_ap = st.text_input(
                    "Wireless AP Pattern (WAP)",
                    value=current_rules.get("branch_ap", ""),
                    help="Format: WAP<Country><State><Site><Seq>",
                    key="form_ap",
                    autocomplete="off"
                )
                branch_firewall = st.text_input(
                    "Firewall Pattern (FW)",
                    value=current_rules.get("branch_firewall", current_rules.get("branch_security", "")),
                    help="Format: FW<Country><State><Site><Vendor><Seq>",
                    key="form_fw",
                    autocomplete="off"
                )
                branch_ion = st.text_input(
                    "SD-WAN / Prisma Pattern (ION)",
                    value=current_rules.get("branch_ion", ""),
                    help="Format: ION<Country><State><Site><Seq>",
                    key="form_ion",
                    autocomplete="off"
                )
                branch_router = st.text_input(
                    "Router Pattern (RTR)",
                    value=current_rules.get("branch_router", ""),
                    help="Format: RTR<Country><State><Site><Zone><Seq>",
                    key="form_rtr",
                    autocomplete="off"
                )
                branch_va = st.text_input(
                    "Virtual Appliance Pattern (VA)",
                    value=current_rules.get("branch_va", ""),
                    help="Format: VA<Country><State><Site><Zone><Seq>",
                    key="form_va",
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
                esxi_portgroup_name = st.text_input(
                    "ESXi Port Group Name",
                    value=current_rules.get("esxi_portgroup_name", ""),
                    help="Available: <pg_network> (e.g. PG-<pg_network>)",
                    key="form_esxi_pg_name",
                    autocomplete="off"
                )
                esxi_portgroup = st.text_input(
                    "ESXi Port Group Description",
                    value=current_rules.get("esxi_portgroup", ""),
                    help="Available: <PortGroup>, <Active_vmnics>, <Standby_vmnics>",
                    key="form_esxi_pg",
                    autocomplete="off"
                )
                esxi_vmkernel_name = st.text_input(
                    "ESXi VMkernel Name",
                    value=current_rules.get("esxi_vmkernel_name", ""),
                    help="Available: <vmk> (e.g. <vmk>)",
                    key="form_esxi_vmk_name",
                    autocomplete="off"
                )
                esxi_vmkernel = st.text_input(
                    "ESXi VMkernel Description",
                    value=current_rules.get("esxi_vmkernel", ""),
                    help="Available: <Purpose>, <vSwitch>, <Active_vmnics>, <Standby_vmnics>",
                    key="form_esxi_vmk",
                    autocomplete="off"
                )
            
            col_save, col_reset = st.columns([3, 1])
            with col_save:
                submitted = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)
            with col_reset:
                reset = st.form_submit_button("🔄 Reset to Defaults", use_container_width=True)
            
            if submitted:
                # Preserve any user-defined pattern_variables added via Edit Mode
                rules_session = st.session_state.get("naming_rules", {})
                session_variables = rules_session.get("pattern_variables", {}) if isinstance(rules_session, dict) else {}

                new_rules = {
                    "branch_switch": branch_switch,
                    "branch_stack": branch_stack,
                    "branch_ap": branch_ap,
                    "branch_firewall": branch_firewall,
                    "branch_ion": branch_ion,
                    "branch_router": branch_router,
                    "branch_va": branch_va,
                    "branch_security": branch_firewall,
                    "switch_uplink_desc": switch_uplink_desc,
                    "switch_lag_member": switch_lag_member,
                    "switch_port_channel": switch_port_channel,
                    "switch_access_desc": switch_access_desc,
                    "firewall_interface": firewall_interface,
                    "esxi_host": esxi_host,
                    "vm_host": vm_host,
                    "esxi_uplink": esxi_uplink,
                    "esxi_portgroup_name": esxi_portgroup_name,
                    "esxi_portgroup": esxi_portgroup,
                    "esxi_vmkernel_name": esxi_vmkernel_name,
                    "esxi_vmkernel": esxi_vmkernel,
                    "netbox_server_yaml": current_rules.get("netbox_server_yaml", ""),
                    "pattern_variables": session_variables,
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
                except Exception as e:
                    st.error(f"❌ Failed to save: {str(e)}")
            
            if reset:
                from config.naming_rules import DEFAULT_RULES
                save_naming_rules(DEFAULT_RULES, source="Reset to Defaults")
                st.session_state["naming_rules"] = DEFAULT_RULES.copy()
                # Set flag for success message
                st.session_state["standards_reset"] = True
                # Force immediate rerun
                st.rerun()

        # ── Auto-Correction Rule Manager (externalized to YAML) ────────
        _render_auto_correction_manager(active_model)

        st.markdown("---")
        st.markdown("#### 3. NetBox Hardware YAML Schema")
        netbox_server_yaml = st.text_area(
            "NetBox Server YAML Guidelines",
            value=current_rules.get("netbox_server_yaml", ""),
            height=100,
            help="Guidelines for generating NetBox device-type YAML files",
            key="form_yaml",
        )
        if st.button("💾 Save NetBox YAML", type="primary", use_container_width=True, key="save_netbox_yaml"):
            rules = load_naming_rules()
            rules["netbox_server_yaml"] = netbox_server_yaml
            save_naming_rules(rules, source="NetBox YAML Schema")
            st.session_state["naming_rules"] = rules
            st.session_state["standards_saved"] = True
            st.rerun()

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
        st.caption("All available pattern variables currently configured. These drive the dynamic input fields in the Naming tab.")
        variables_now = get_pattern_variables(current_rules)
        patterns_now = get_naming_patterns(current_rules)

        # Editable variable manager
        st.markdown("#### ✏️ Manage Pattern Variables")
        st.caption("Add, edit, or remove variables. New variables default to **optional** (shown empty; omitted from output unless filled). Use `<Name>` in your naming patterns.")

        # In-place editable variable rows
        with st.expander("🔧 Edit Existing Variables", expanded=True):
            var_names = list(variables_now.keys())

            if variables_now:
                edited_vars = {}
                total_vars = len(var_names)
                for idx, name in enumerate(var_names):
                    meta = variables_now.get(name) if isinstance(variables_now.get(name), dict) else {}
                    # 8 fixed columns: Name, Label, Placeholder, Auto-Fill, Optional, ⬆️, ⬇️, 🗑️
                    c_nm, c_lb, c_ph, c_df, c_opt, c_up, c_dn, c_del = st.columns([1.5, 2.0, 2.0, 1.2, 0.8, 0.4, 0.4, 0.4])
                    with c_nm:
                        var_key = st.text_input("Name", value=name, key=f"var_key_{name}").strip()
                    with c_lb:
                        var_lbl = st.text_input("Label", value=meta.get("label", name), key=f"var_lbl_{name}").strip()
                    with c_ph:
                        var_ph = st.text_input("Placeholder", value=meta.get("placeholder", ""), key=f"var_ph_{name}").strip()
                    with c_df:
                        var_def = st.text_input("Auto-Fill", value=meta.get("default", ""), key=f"var_def_{name}",
                                               help="Default value the Naming tab input starts with.")
                    with c_opt:
                        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                        var_opt = st.checkbox("Optional", value=bool(meta.get("optional")), key=f"var_opt_{name}",
                                              help="Optional variables start empty and are omitted when not filled.")
                    with c_up:
                        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                        if idx > 0:
                            if st.button("⬆️", key=f"var_up_{idx}", help=f"Move <{name}> up"):
                                var_names[idx - 1], var_names[idx] = var_names[idx], var_names[idx - 1]
                                reordered = {k: variables_now[k] for k in var_names}
                                _persist_variables(current_rules, reordered)
                        else:
                            st.empty()
                    with c_dn:
                        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                        if idx < total_vars - 1:
                            if st.button("⬇️", key=f"var_dn_{idx}", help=f"Move <{name}> down"):
                                var_names[idx], var_names[idx + 1] = var_names[idx + 1], var_names[idx]
                                reordered = {k: variables_now[k] for k in var_names}
                                _persist_variables(current_rules, reordered)
                        else:
                            st.empty()
                    with c_del:
                        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                        st.button("🗑️", key=f"var_del_{name}", help=f"Remove <{name}>")

                    if st.session_state.get(f"var_del_{name}"):
                        edited_vars[name] = None
                        continue
                    var_key = var_key or name
                    entry = {
                        "label": var_lbl or var_key,
                        "placeholder": var_ph or f"e.g. {var_key}",
                    }
                    if var_def:
                        entry["default"] = var_def
                    entry["optional"] = bool(var_opt)
                    edited_vars[var_key] = entry

                if st.button("💾 Apply Variable Changes", key="var_apply"):
                    final_vars = {k: v for k, v in edited_vars.items() if v is not None}
                    _persist_variables(current_rules, final_vars)
            else:
                st.info("No variables defined yet. Add one below.")
                if st.button("💾 Apply Variable Changes", key="var_apply_empty"):
                    st.warning("Nothing to apply.")

        st.markdown("---")
        st.markdown("#### ➕ Add New Variable")
        with st.expander("➕ Add a New Variable", expanded=True):
            new_name = st.text_input("Variable Name (e.g. Speed, Standby_vmnics)", value="", key="var_new_name").strip()
            new_label = st.text_input("Display Label", value="", placeholder="e.g. Interface Speed", key="var_new_label").strip()
            new_ph = st.text_input("Placeholder Example", value="", placeholder="e.g. 10G, 25G", key="var_new_ph").strip()
            new_def = st.text_input("Default Auto-Fill (Optional)", value="", placeholder="e.g. vmnic0", key="var_new_def",
                                   help="Default value the Naming tab input starts with.")
            new_optional = st.checkbox(
                "Optional (default empty unless user inputs)", value=True,
                key="var_new_optional",
                help="Optional variables start blank and are omitted from output when not filled.",
            )
            if st.button("➕ Add Variable", key="var_new_add", type="primary"):
                if new_name:
                    entry = {
                        "label": new_label or new_name,
                        "placeholder": new_ph or f"e.g. {new_name}",
                    }
                    if new_def:
                        entry["default"] = new_def
                    if new_optional:
                        entry["optional"] = True
                    else:
                        entry["optional"] = False
                    final_vars = dict(variables_now)
                    final_vars[new_name] = entry
                    _persist_variables(current_rules, final_vars)
                else:
                    st.warning("⚠️ Please enter a variable name.")

        with st.expander("🧩 Active Patterns", expanded=False):
            if patterns_now:
                for key, pat in patterns_now.items():
                    st.markdown(f"**{key}:** `{pat}`")

        st.markdown("---")
        st.caption("Use these variables in your naming patterns. The Naming tab will automatically replace them with actual values; optional ones only appear when filled.")
    
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
                        st.code(f"Switch: {rules.get('branch_switch', 'N/A')[:60]}", language="text")
                        st.code(f"Stack: {rules.get('branch_stack', 'N/A')[:60]}", language="text")
                        st.code(f"AP: {rules.get('branch_ap', 'N/A')[:60]}", language="text")
                        st.code(f"Firewall: {rules.get('branch_firewall', rules.get('branch_security', 'N/A'))[:60]}", language="text")
                        st.code(f"ION: {rules.get('branch_ion', 'N/A')[:60]}", language="text")
                    
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
