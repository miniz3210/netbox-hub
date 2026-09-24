import re

import streamlit as st
from config.naming_rules import (
    load_naming_rules, save_naming_rules, export_rules_as_prompt,
    load_history, restore_from_history, clear_history, add_to_history,
    get_pattern_variables, get_naming_patterns, get_custom_patterns,
    get_device_presets, get_interface_presets, get_host_vm_presets,
    get_esxi_network_presets, make_preset_key, default_presets_for,
    DEFAULT_PRESET_KEY_FIELD, DEFAULT_NAMING_PATTERNS,
)
from core.naming_engine import generate_naming_pattern, generate_autocorrect_rule
from utils.formatters import (
    load_auto_corrections, save_auto_corrections, reset_auto_corrections,
)

def _diff_rule_list(category: str, old_rules: list, new_rules: list) -> list:
    """Return granular diff rows for an auto-correction rule list.

    Compares individual rules by their description so rule edits surface as
    ``[Modified]`` rows (Previous replacement -> New replacement) instead of
    dumping the entire category array as a raw dict string.
    """
    rows = []
    old_by_desc = {r.get("description"): r for r in old_rules}
    new_by_desc = {r.get("description"): r for r in new_rules}

    for desc in sorted(set(list(old_by_desc.keys()) + list(new_by_desc.keys()))):
        old_rule = old_by_desc.get(desc)
        new_rule = new_by_desc.get(desc)
        if old_rule is not None and new_rule is not None:
            old_rep = str(old_rule.get("replacement", ""))
            new_rep = str(new_rule.get("replacement", ""))
            if old_rule == new_rule:
                continue
            rows.append((
                f"**[Modified]** `{category}`: {desc}",
                old_rep,
                new_rep,
            ))
        elif new_rule is not None:
            rows.append((
                f"**[Added]** `{category}`: {desc}",
                "—",
                str(new_rule.get("replacement", "")),
            ))
        elif old_rule is not None:
            rows.append((
                f"**[Removed]** `{category}`: {desc}",
                str(old_rule.get("replacement", "")),
                "—",
            ))

    return rows


def validate_regex_replacement(pattern_str: str, replacement_str: str) -> tuple:
    """Validate that *replacement_str*'s backreferences fit *pattern_str*'s groups.
    Returns ``(True, "")`` on success or ``(False, "error message")`` on failure.
    """
    if not pattern_str:
        return True, ""
    try:
        compiled = re.compile(pattern_str)
    except re.error as e:
        return False, f"Regex syntax error in pattern '{pattern_str}': {e}"

    num_groups = compiled.groups
    refs = re.findall(r"(?:\\(\d+)|\\g<(\d+)>)", replacement_str)
    for m in refs:
        raw = m[0] or m[1]
        if int(raw) > num_groups:
            return False, (
                f"Invalid group \\{raw} in replacement '{replacement_str}': "
                f"pattern only has {num_groups} capture group(s)!"
            )
    return True, ""


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
    if st.session_state.pop("autocorrect_saved", False):
        st.success("✅ Auto-correction rules saved & applied!")
    if st.session_state.pop("autocorrect_reset", False):
        st.success("✅ Auto-correction rules reset to factory defaults!")

    rules = load_auto_corrections()
    categories = list(rules.keys())

    if not categories:
        st.info("No auto-correction categories defined.")
        return

    for category in categories:
        category_title = {
            "port_shortening": "🔌 Port Abbreviation Rules (Interface Shortening)",
            "vmware": "☁️ VMware Syntax Rules",
        }.get(category, f"🛠️ Auto-Correction Rules — {category}")
        with st.expander(category_title, expanded=False):
            st.caption(
                "Each row is a regex pattern → replacement pair. Edit inline or use "
                "the AI generator below to create new rules. The **On** checkbox "
                "enables/disables a rule without deleting it."
            )
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
                    )
                with col_r:
                    r = st.text_input(
                        "Replacement",
                        value=rule.get("replacement", ""),
                        key=f"ac_{category}_r_{idx}",
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

            with st.expander(f"✨ AI Assistant: Generate Rule for {category.replace('_', ' ').title()}", expanded=False):
                ai_prompt = st.text_input(
                    "Describe rule in natural language:",
                    key=f"ac_ai_input_{category}",
                    placeholder="e.g., Shorten GigabitEthernet to Gi, or standardize nic0 to vmnic0",
                )
                if st.button("Generate Regex Rule", key=f"ac_ai_btn_{category}", use_container_width=True):
                    if ai_prompt.strip():
                        try:
                            with st.spinner(f"Generating rule using {active_model}..."):
                                result = generate_autocorrect_rule(ai_prompt.strip(), active_model)
                            st.session_state[f"ac_{category}_new_p"] = result["pattern"]
                            st.session_state[f"ac_{category}_new_r"] = result["replacement"]
                            st.session_state[f"ac_{category}_new_d"] = result["description"]
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
                    failed = []
                    for rule in updated:
                        pat = rule.get("pattern", "")
                        repl = rule.get("replacement", "")
                        ok, msg = validate_regex_replacement(pat, repl)
                        if not ok:
                            desc = rule.get("description", pat)
                            failed.append(f"- `{desc}`: {msg}")
                    if failed:
                        st.error("❌ Cannot save — invalid rule(s):\n" + "\n".join(failed))
                    else:
                        final = dict(rules)
                        final[category] = updated
                        _persist_auto_corrections(final)
            with col_add:
                if st.button("➕ Add Rule", key=f"ac_{category}_add", use_container_width=True):
                    if new_p.strip():
                        ok, msg = validate_regex_replacement(new_p, new_r)
                        if not ok:
                            st.error(f"❌ Cannot add rule — {msg}")
                        else:
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
                if st.button("🔄 Reset to Factory Defaults", key=f"ac_reset_factory_{category}", use_container_width=True):
                    reset_auto_corrections()
                    st.session_state["autocorrect_reset"] = True
                    st.rerun()

    st.markdown("---")


def _persist_auto_corrections(data: dict) -> None:
    """Save auto-correction rules to YAML and refresh the session cache."""
    save_auto_corrections(data, source="Management UI")
    add_to_history(data, source="Auto-Correction: Management UI")
    st.session_state["autocorrect_rules_cache"] = data
    st.session_state["autocorrect_saved"] = True
    st.rerun()


def _save_presets(rules: dict) -> None:
    """Persist an updated rules dict carrying device/interface presets and reload."""
    save_naming_rules(rules, source="Presets Manager")
    st.session_state["naming_rules"] = rules.copy()
    st.session_state["presets_saved"] = True
    st.rerun()


def _preset_type_editor(kind: str, presets: list, rules: dict, prefix: str) -> None:
    """CRUD editor for one preset type (device/interface/host_vm/esxi_network).

    Reads the structured preset list, lets the user edit code / label / pattern template
    inline, delete (with a minimum-of-one guard) or add new presets, then persists
    everything back to ``data/naming_rules.yaml`` via a single Save button.
    """
    patterns = dict(rules.get("naming_patterns") or {})
    key_field = "device_presets" if kind == "device" else (
        "interface_presets" if kind == "interface" else (
            "host_vm_presets" if kind == "host_vm" else "esxi_network_presets"
        )
    )
    updated = []
    pending_delete = None
    patterns_updates = {}

    if presets:
        for idx, p in enumerate(presets):
            code = p.get("code", "")
            label = p.get("label", "")
            pkey = p.get("pattern_key", "")
            tpl = patterns.get(pkey, "")

            c1, c2, c3, c4 = st.columns([1.1, 2.2, 3.2, 0.6])
            with c1:
                ncode = st.text_input("Code", value=code, key=f"{kind}_pre_code_{idx}").strip()
            with c2:
                nlbl = st.text_input("Label", value=label, key=f"{kind}_pre_lbl_{idx}").strip()
            with c3:
                ntpl = st.text_input("Pattern Template", value=tpl, key=f"{kind}_pre_tpl_{idx}").strip()
            with c4:
                st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
                if st.button("🗑️", key=f"{kind}_pre_del_{idx}"):
                    if len(presets) > 1:
                        pending_delete = idx
                    else:
                        st.session_state[f"{kind}_preset_min_one"] = True

            if pending_delete == idx:
                continue
            final_pkey = pkey or make_preset_key(ncode or label, prefix)
            if ntpl:
                patterns_updates[final_pkey] = ntpl
            if ncode and final_pkey:
                updated.append({
                    "code": ncode.upper(),
                    "label": nlbl or ncode.upper(),
                    "pattern_key": final_pkey,
                    "description": p.get("description", ""),
                })
    else:
        st.info("No presets defined. Add one below.")

    if st.session_state.pop(f"{kind}_preset_min_one", False):
        st.warning("⚠️ At least one preset must remain. Delete a different entry first.")

    st.markdown("**➕ Add New Preset**")
    ca1, ca2, ca3, ca4 = st.columns([1.1, 1.8, 2.6, 2.6])
    with ca1:
        new_code = st.text_input("Code", value="", placeholder="e.g. SAN", key=f"{kind}_new_code").strip()
    with ca2:
        new_lbl = st.text_input("Display Label", value="", placeholder="e.g. SAN Storage (SAN)", key=f"{kind}_new_lbl").strip()
    with ca3:
        new_tpl = st.text_input("Pattern Template", value="", placeholder="e.g. SAN<Country><Site><Seq>", key=f"{kind}_new_tpl").strip()
    with ca4:
        new_desc = st.text_input("Description (Optional)", value="", placeholder="Describe this preset", key=f"{kind}_new_desc").strip()

    col_save, col_reset = st.columns(2)
    with col_save:
        saved_presets = st.button(
            "💾 Save Presets", key=f"{kind}_preset_save", type="primary",
            use_container_width=True,
        )
    with col_reset:
        reset_presets = st.button(
            "🔄 Reset to Defaults", key=f"{kind}_preset_reset",
            use_container_width=True,
        )

    if reset_presets:
        _reset_presets(kind, rules)
        return

    if saved_presets:
        final_presets = list(updated)
        final_patterns = dict(patterns)
        final_patterns.update(patterns_updates)
        if new_code and new_tpl:
            nkey = make_preset_key(new_code, prefix)
            while nkey in final_patterns and nkey not in [p["pattern_key"] for p in final_presets]:
                nkey = f"{nkey}_x"
            final_patterns[nkey] = new_tpl
            final_presets.append({
                "code": new_code.upper(),
                "label": new_lbl or new_code.upper(),
                "pattern_key": nkey,
                "description": new_desc,
            })
        elif new_code and not new_tpl:
            st.error("⚠️ Provide a Pattern Template to add a new preset.")
            return
        if not final_presets:
            st.error("⚠️ At least one preset is required.")
            return
        rules["naming_patterns"] = final_patterns
        rules[key_field] = final_presets
        _save_presets(rules)


def _reset_presets(kind: str, rules: dict) -> None:
    """Restore one preset *kind* and its tied patterns to factory defaults."""
    key_field = DEFAULT_PRESET_KEY_FIELD.get(kind)
    if not key_field:
        return
    defaults = default_presets_for(kind)
    default_patterns = dict(DEFAULT_NAMING_PATTERNS)
    patterns = dict(rules.get("naming_patterns") or {})
    for p in defaults:
        pkey = p.get("pattern_key", "")
        if pkey in default_patterns:
            patterns[pkey] = default_patterns[pkey]
    rules["naming_patterns"] = patterns
    rules[key_field] = defaults
    _save_presets(rules)


def render_standards_tab(active_model):
    st.subheader("📖 Infrastructure Naming Standards Configuration")
    st.caption("Define and manage your organization's naming conventions. All patterns configured here are automatically applied in the Naming tab.")
    
    # Check for variable manager save success message
    if "variables_saved" in st.session_state and st.session_state["variables_saved"]:
        st.success("✅ Variables saved successfully!")
        st.session_state["variables_saved"] = False

    if "presets_saved" in st.session_state and st.session_state["presets_saved"]:
        st.success("✅ Presets saved successfully!")
        st.session_state["presets_saved"] = False

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
    tab_edit, tab_vars, tab_history = st.tabs(["📝 Edit Standards", "📘 Pattern Variables Reference", "📜 Change History"])
    
    # Tab 1: Editable Form Interface
    with tab_edit:
        with st.expander("🎛️ Infrastructure Naming Patterns & Presets", expanded=False):
            st.info("💡 Configure naming patterns and the dynamic presets powering the Naming tab's radio selectors. Changes are saved when you click 'Save Changes' / 'Save Presets', or reset per category with 'Reset to Defaults'. Use the **Pattern Variables Reference** tab to see all available variables.")
            with st.form("naming_standards_form"):
                with st.expander("NetBox Hardware YAML Schema", expanded=False):
                    netbox_server_yaml = st.text_area(
                        "NetBox Server YAML Guidelines",
                        value=current_rules.get("netbox_server_yaml", ""),
                        height=100,
                        key="form_yaml",
                    )
    
                with st.expander("✨ AI Assistant: Generate Custom Naming Pattern", expanded=False):
                    st.caption("Describe a naming convention in plain text and let AI build a template for you. Verify the Label / Key / Template below, then click **➕ Add to Standards**.")
                    ai_desc = st.text_input(
                        "Describe the naming convention",
                        key="custom_ai_desc",
                        placeholder="e.g. SAN storage naming: SAN + country + site + sequence",
                    )
                    if st.form_submit_button("🤖 Generate Pattern with AI", key="custom_ai_gen", use_container_width=True):
                        if ai_desc.strip():
                            with st.spinner(f"Generating pattern using {active_model}..."):
                                try:
                                    generated = generate_naming_pattern(ai_desc.strip(), active_model)
                                    prefix = re.sub(r"[^A-Za-z0-9].*$", "", generated) or "CUSTOM"
                                    st.session_state["custom_new_label"] = prefix.upper()
                                    st.session_state["custom_new_key"] = prefix.lower() + "_pattern"
                                    st.session_state["custom_new_tpl"] = generated
                                    st.session_state["custom_ai_ready"] = True
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"❌ AI pattern generation failed: {e}")
                        else:
                            st.warning("⚠️ Please describe the naming convention first.")

                col_new_l, col_new_k, col_new_t = st.columns([3, 3, 4])
                with col_new_l:
                    custom_new_label = st.text_input(
                        "Custom Pattern Label", value=st.session_state.get("custom_new_label", ""),
                        key="custom_new_label", placeholder="e.g. SAN Storage",
                    )
                with col_new_k:
                    custom_new_key = st.text_input(
                        "Custom Pattern Key", value=st.session_state.get("custom_new_key", ""),
                        key="custom_new_key", placeholder="e.g. san_pattern",
                    )
                with col_new_t:
                    custom_new_tpl = st.text_input(
                        "Custom Pattern Template", value=st.session_state.get("custom_new_tpl", ""),
                        key="custom_new_tpl", placeholder="e.g. SAN<Country><Site><Seq>",
                    )
                existing_customs = get_custom_patterns(current_rules)
                if existing_customs:
                    with st.expander("Existing Custom Patterns", expanded=False):
                        for ccp in existing_customs:
                            st.markdown(f"- **{ccp.get('key')}** (`{ccp.get('pattern')}`) — {ccp.get('label')}")

                col_save, col_add, col_reset = st.columns([2, 2, 2])
                with col_save:
                    submitted = st.form_submit_button("💾 Save Changes", type="primary", use_container_width=True)
                with col_add:
                    add_custom = st.form_submit_button("➕ Add Custom Pattern", use_container_width=True)
                with col_reset:
                    reset = st.form_submit_button("🔄 Reset to Defaults", use_container_width=True)

                if submitted:
                    # Preserve any user-defined pattern_variables added via Edit Mode
                    rules_session = st.session_state.get("naming_rules", {})
                    session_variables = rules_session.get("pattern_variables", {}) if isinstance(rules_session, dict) else {}
    
                    new_rules = {
                        "netbox_server_yaml": netbox_server_yaml,
                        "custom_patterns": get_custom_patterns(current_rules),
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
    
                if add_custom:
                    if custom_new_key.strip() and custom_new_tpl.strip():
                        try:
                            existing_customs = list(get_custom_patterns(current_rules))
                            existing_customs = [
                                c for c in existing_customs if c["key"] != custom_new_key.strip()
                            ]
                            existing_customs.append({
                                "label": custom_new_label.strip() or custom_new_key.strip(),
                                "key": custom_new_key.strip(),
                                "pattern": custom_new_tpl.strip(),
                            })
                            merged = {**current_rules, "custom_patterns": existing_customs}
                            save_naming_rules(merged, source="Custom Pattern")
                            st.session_state["naming_rules"] = merged
                            st.session_state["custom_ai_ready"] = False
                            st.session_state["standards_saved"] = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Failed to add custom pattern: {str(e)}")
                    else:
                        st.warning("⚠️ Provide both a Pattern Key and a Pattern Template.")
                
                if reset:
                    from config.naming_rules import DEFAULT_RULES
                    save_naming_rules(DEFAULT_RULES, source="Reset to Defaults")
                    st.session_state["naming_rules"] = DEFAULT_RULES.copy()
                    # Set flag for success message
                    st.session_state["standards_reset"] = True
                    # Force immediate rerun
                    st.rerun()

        with st.expander("🔧 Device Type Presets", expanded=False):
            _preset_type_editor("device", get_device_presets(current_rules), current_rules, prefix="branch")

        with st.expander("🔌 Interface Type Presets", expanded=False):
            _preset_type_editor("interface", get_interface_presets(current_rules), current_rules, prefix="iface")

        with st.expander("🖥️ Hosts & Virtual Machines Presets", expanded=False):
            _preset_type_editor("host_vm", get_host_vm_presets(current_rules), current_rules, prefix="hostvm")

        with st.expander("☁️ ESXi Network Description Presets", expanded=False):
            _preset_type_editor("esxi_network", get_esxi_network_presets(current_rules), current_rules, prefix="esxinet")

        with st.expander("🛠️ Manage Syntax Auto-Correction Rules", expanded=False):
            _render_auto_correction_manager(active_model)

        # Export full system prompt at the bottom of Edit Standards
        full_prompt_text = export_rules_as_prompt(current_rules)
        with st.expander("📋 Export Full System Prompt for External AI", expanded=False):
            st.caption("Copy this complete prompt directly into ChatGPT, Claude, or other external AI models to enforce your organization's naming standards.")
            st.code(full_prompt_text, language="markdown")
            st.download_button(
                label="💾 Download Prompt (.txt)",
                data=full_prompt_text,
                file_name="infrastructure_naming_standards_prompt.txt",
                mime="text/plain"
            )
    
    # Tab 3: Pattern Variables Reference
    with tab_vars:
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
                        var_def = st.text_input("Auto-Fill", value=meta.get("default", ""), key=f"var_def_{name}")
                    with c_opt:
                        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                        var_opt = st.checkbox("Optional", value=bool(meta.get("optional")), key=f"var_opt_{name}")
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
            new_def = st.text_input("Default Auto-Fill (Optional)", value="", placeholder="e.g. vmnic0", key="var_new_def")
            new_optional = st.checkbox(
                "Optional (default empty unless user inputs)", value=True,
                key="var_new_optional",
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
    
    # Tab 4: Change History
    with tab_history:
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
                        with st.expander("👁️ View Full Details", expanded=False):
                            st.json(rules)
