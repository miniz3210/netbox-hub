import os
import json
import re

import streamlit as st
from config.constants import RULES_FILE
from config.naming_rules import (
    load_naming_rules, save_naming_rules, export_rules_as_prompt,
    load_history, restore_from_history, clear_history, add_to_history,
    get_pattern_variables, get_naming_patterns, get_custom_patterns,
    get_device_presets, get_interface_presets, get_host_vm_presets,
    get_esxi_network_presets, make_preset_key, default_presets_for,
    DEFAULT_PRESET_KEY_FIELD, DEFAULT_NAMING_PATTERNS, DEFAULT_RULES,
)
from core.naming_engine import generate_naming_pattern, generate_autocorrect_rule
from utils.formatters import (
    load_auto_corrections, save_auto_corrections, reset_auto_corrections,
)

# Shared column width ratios enforced across preset table headers and all data rows.
PRESET_COLS = [1.2, 2.2, 4.5, 0.6]
# Manage Pattern Variables columns: Name, Label, Placeholder, Auto-Fill, Optional, Up, Down, Delete.
VARIABLE_COLS = [1.5, 2.5, 2.5, 1.5, 0.9, 0.45, 0.45, 0.45]
# Auto-Correction rule columns: Original Pattern, Replacement, Description, Action.
AUTOCORRECT_COLS = [3.2, 2.3, 3.8, 0.7]

def _normalize_var_name(raw: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", raw.strip().lower().replace(" ", "_"))

def _render_centered_del_btn(key: str, help_text: str = "Delete this entry") -> bool:
    """Helper to render a perfectly centered square delete icon button."""
    _, c_btn, _ = st.columns([1, 2, 1])
    with c_btn:
        return st.button("🗑️", key=key, help=help_text)

def _diff_rule_list(category: str, old_rules: list, new_rules: list) -> list:
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
    rules["pattern_variables"] = variables
    save_naming_rules(rules, source="Variable Manager")
    st.session_state["naming_rules"] = rules.copy()
    st.session_state["variables_saved"] = True
    st.rerun()


def _render_auto_correction_manager(active_model: str) -> None:
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
                "the AI generator below to create new rules."
            )
            ch_p, ch_r, ch_d, ch_del = st.columns(AUTOCORRECT_COLS, vertical_alignment="center")
            with ch_p:
                st.markdown("**Original Pattern**")
            with ch_r:
                st.markdown("**Replacement**")
            with ch_d:
                st.markdown("**Description**")
            with ch_del:
                pass

            items = list(rules[category])
            updated = []
            pending_delete = None
            for idx, rule in enumerate(items):
                col_p, col_r, col_d, col_del = st.columns(AUTOCORRECT_COLS, vertical_alignment="center")
                with col_p:
                    p = st.text_input(
                        "Original Pattern",
                        value=rule.get("pattern", ""),
                        key=f"ac_{category}_p_{idx}",
                        label_visibility="collapsed",
                    )
                with col_r:
                    r = st.text_input(
                        "Replacement",
                        value=rule.get("replacement", ""),
                        key=f"ac_{category}_r_{idx}",
                        label_visibility="collapsed",
                    )
                with col_d:
                    d = st.text_input(
                        "Description",
                        value=rule.get("description", ""),
                        key=f"ac_{category}_d_{idx}",
                        label_visibility="collapsed",
                    )
                with col_del:
                    if _render_centered_del_btn(f"ac_{category}_del_{idx}", "Delete this rule"):
                        pending_delete = idx

                if pending_delete == idx:
                    continue
                if p.strip():
                    updated.append({
                        "pattern": p,
                        "replacement": r,
                        "description": d,
                        "enabled": True,
                    })

            with st.expander(f"✨ AI Assistant: Generate Rule for {category.replace('_', ' ').title()}", expanded=False):
                ai_prompt = st.text_input(
                    "Describe rule in natural language:",
                    key=f"ac_ai_input_{category}",
                    placeholder="e.g., Shorten GigabitEthernet to Gi, or standardize nic0 to vmnic0",
                )
                if st.button("Generate Regex Rule", key=f"ac_ai_btn_{category}", width='stretch'):
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
                if st.button("💾 Save & Apply Changes", key=f"ac_{category}_save", type="primary", width='stretch'):
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
                if st.button("➕ Add Rule", key=f"ac_{category}_add", width='stretch'):
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
                if st.button("🔄 Reset to Factory Defaults", key=f"ac_reset_factory_{category}", width='stretch'):
                    reset_auto_corrections()
                    st.session_state["autocorrect_reset"] = True
                    st.rerun()

    _render_site_code_mapping_manager()

    st.markdown("---")


def _render_site_code_mapping_manager() -> None:
    from config.naming_rules import get_site_code_rules

    with st.expander("📍 Site Code Mapping Rules (City / Location to Code)", expanded=False):
        st.caption(
            "Each row maps a city/location pattern → site code. The Naming tab's Site Code "
            "Assistant uses these exact mappings directly. A location that matches a pattern is "
            "resolved to its code before any algorithmic fallback."
        )
        rules = load_naming_rules()
        sr = get_site_code_rules(rules)
        exact = dict(sr.get("exact_mappings") or {})

        m_col_p, m_col_r, m_col_del = st.columns([3.0, 2.2, 0.7], vertical_alignment="center")
        with m_col_p:
            st.markdown("**Original Pattern (City / Location)**")
        with m_col_r:
            st.markdown("**Replacement (Site Code)**")
        with m_col_del:
            pass

        items = list(exact.items())
        updated = {}
        pending_delete = None
        for idx, (pat, code) in enumerate(items):
            col_p, col_r, col_del = st.columns([3.0, 2.2, 0.7], vertical_alignment="center")
            with col_p:
                np_ = st.text_input(
                    "Original Pattern", value=pat, key=f"sitecode_{idx}_p",
                    label_visibility="collapsed",
                )
            with col_r:
                nr_ = st.text_input(
                    "Replacement", value=code, key=f"sitecode_{idx}_r",
                    label_visibility="collapsed",
                )
            with col_del:
                if _render_centered_del_btn(f"sitecode_{idx}_del", "Delete this mapping"):
                    pending_delete = idx

            if pending_delete == idx:
                continue
            key = np_.strip().lower()
            if key:
                updated[key] = nr_.strip().upper()

        s_col_add, s_col_addcode = st.columns([3.0, 2.2])
        with s_col_add:
            new_p = st.text_input("New City / Location", value="", key="sitecode_new_p",
                                 placeholder="e.g. bristol")
        with s_col_addcode:
            new_code = st.text_input("New Site Code", value="", key="sitecode_new_code",
                                    placeholder="e.g. BRI")

        col_save, col_add = st.columns(2)
        with col_save:
            if st.button("💾 Save & Apply Changes", key="sitecode_save", type="primary", width='stretch'):
                final = dict(rules)
                final["site_code_rules"] = dict(sr)
                final["site_code_rules"]["exact_mappings"] = updated
                _persist_site_code_mappings(final)
        with col_add:
            if st.button("➕ Add Mapping", key="sitecode_add", width='stretch'):
                if new_p.strip() and new_code.strip():
                    final = dict(rules)
                    final["site_code_rules"] = dict(sr)
                    final["site_code_rules"]["exact_mappings"] = dict(updated)
                    final["site_code_rules"]["exact_mappings"][new_p.strip().lower()] = new_code.strip().upper()
                    _persist_site_code_mappings(final)
                else:
                    st.warning("⚠️ Enter both a city/location and a site code to add.")


def _persist_site_code_mappings(rules: dict) -> None:
    from config.naming_rules import compute_delta, add_to_history

    old_rules = load_naming_rules()
    save_naming_rules(rules, source="Site Code Mapping Rules: Management UI")
    delta = compute_delta(old_rules, rules)
    if not delta:
        delta = {"site_code_rules": {"old": dict(old_rules.get("site_code_rules") or {}),
                                    "new": dict(rules.get("site_code_rules") or {})}}
    add_to_history(delta, source="Site Code Mapping Rules: Management UI")
    st.session_state["naming_rules"] = load_naming_rules()
    st.session_state["site_code_saved"] = True
    st.rerun()


def _persist_auto_corrections(data: dict) -> None:
    from config.naming_rules import compute_delta
    save_auto_corrections(data, source="Management UI")
    old_rules = {}
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                old_rules = json.load(f)
        except Exception:
            old_rules = {}
    from config.naming_rules import load_naming_rules
    old_normalized = load_naming_rules() if old_rules else {}
    new_normalized = dict(old_normalized)
    new_normalized["autocorrect_rules"] = data
    delta = compute_delta(old_normalized, new_normalized)
    if not delta:
        delta = {"auto_correction_rules": {"old": None, "new": data}}
    add_to_history(delta, source="Auto-Correction: Management UI")
    st.session_state["autocorrect_rules_cache"] = data
    st.session_state["autocorrect_saved"] = True
    st.rerun()


def _save_presets(rules: dict) -> None:
    save_naming_rules(rules, source="Presets Manager")
    st.session_state["naming_rules"] = rules.copy()
    st.session_state["presets_saved"] = True
    st.rerun()


def _preset_type_editor(kind: str, presets: list, rules: dict, prefix: str) -> None:
    patterns = dict(rules.get("naming_patterns") or {})
    key_field = "device_presets" if kind == "device" else (
        "interface_presets" if kind == "interface" else (
            "host_vm_presets" if kind == "host_vm" else "esxi_network_presets"
        )
    )
    _pending_del_key = f"_pending_del_{kind}"
    stale_del = st.session_state.pop(_pending_del_key, None)
    if stale_del is not None and 0 <= stale_del < len(presets):
        rules[key_field] = [p for i, p in enumerate(presets) if i != stale_del]
        _save_presets(rules)
        return

    col_hdr_code, col_hdr_lbl, col_hdr_tpl, col_hdr_act = st.columns(PRESET_COLS, vertical_alignment="center")
    with col_hdr_code:
        st.markdown("**Code**")
    with col_hdr_lbl:
        st.markdown("**Label**")
    with col_hdr_tpl:
        st.markdown("**Pattern Template**")
    with col_hdr_act:
        pass

    updated = []
    patterns_updates = {}

    if presets:
        for idx, p in enumerate(presets):
            code = p.get("code", "")
            label = p.get("label", "")
            pkey = p.get("pattern_key", "")
            tpl = patterns.get(pkey, "")

            c1, c2, c3, c4 = st.columns(PRESET_COLS, vertical_alignment="center")
            with c1:
                ncode = st.text_input("Code", value=code, key=f"{kind}_pre_code_{idx}", label_visibility="collapsed").strip()
            with c2:
                nlbl = st.text_input("Label", value=label, key=f"{kind}_pre_lbl_{idx}", label_visibility="collapsed").strip()
            with c3:
                ntpl = st.text_input("Pattern Template", value=tpl, key=f"{kind}_pre_tpl_{idx}", label_visibility="collapsed").strip()
            with c4:
                if _render_centered_del_btn(f"{kind}_pre_del_{idx}"):
                    if len(presets) > 1:
                        st.session_state[_pending_del_key] = idx
                        st.rerun()
                    else:
                        st.session_state[f"{kind}_preset_min_one"] = True

            if stale_del == idx:
                continue
            final_pkey = pkey or make_preset_key(ncode or label, prefix)
            if ntpl:
                patterns_updates[final_pkey] = ntpl
            if ncode and final_pkey:
                updated.append({
                    "code": ncode,
                    "label": nlbl or ncode,
                    "pattern_key": final_pkey,
                    "description": p.get("description", ""),
                })
    else:
        st.info("No presets defined. Add one below.")

    if st.session_state.pop(f"{kind}_preset_min_one", False):
        st.warning("⚠️ At least one preset must remain. Delete a different entry first.")

    st.markdown("**➕ Add New Preset**")
    is_esxi = kind == "esxi_network"
    code_ph = "e.g. DSwitch" if is_esxi else "e.g. SAN"
    lbl_ph = "e.g. Distributed Switch Uplink" if is_esxi else "e.g. SAN Storage (SAN)"
    tpl_ph = "e.g. <vmnic> - <vds_name> (<status>)" if is_esxi else "e.g. SAN<country><site><seq>"
    ca1, ca2, ca3 = st.columns([1.5, 2.5, 4.5])
    with ca1:
        new_code = st.text_input("Code", value="", placeholder=code_ph, key=f"{kind}_new_code", label_visibility="collapsed").strip()
    with ca2:
        new_lbl = st.text_input("Display Label", value="", placeholder=lbl_ph, key=f"{kind}_new_lbl", label_visibility="collapsed").strip()
    with ca3:
        new_tpl = st.text_input("Pattern Template", value="", placeholder=tpl_ph, key=f"{kind}_new_tpl", label_visibility="collapsed").strip()

    col_save, col_reset = st.columns(2)
    with col_save:
        saved_presets = st.button(
            "💾 Save Presets", key=f"{kind}_preset_save", type="primary",
            width='stretch',
        )
    with col_reset:
        reset_presets = st.button(
            "🔄 Reset to Defaults", key=f"{kind}_preset_reset",
            width='stretch',
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
                "code": new_code,
                "label": new_lbl or new_code,
                "pattern_key": nkey,
                "description": "",
            })
        elif new_code and not new_tpl:
            st.error("⚠️ Provide a Pattern Template to add a new preset.")
            return
        if not final_presets:
            st.error("⚠️ At least one preset is required.")
            return
        # Synchronize esxi_network patterns to preset items and alias keys
        if kind == "esxi_network":
            for p_item in final_presets:
                pk = p_item.get("pattern_key", "")
                val = final_patterns.get(pk, "")
                if val:
                    p_item["pattern"] = val
                    p_item["pattern_template"] = val
            for pkey, val in list(final_patterns.items()):
                if pkey.startswith("esxinet_"):
                    legacy_key = pkey.replace("esxinet_", "esxi_")
                    final_patterns[legacy_key] = val
                    rules[legacy_key] = val
                elif pkey.startswith("esxi_"):
                    alt_key = pkey.replace("esxi_", "esxinet_")
                    final_patterns[alt_key] = val
                    rules[alt_key] = val

        rules["naming_patterns"] = final_patterns
        rules[key_field] = final_presets
        _save_presets(rules)


def _reset_presets(kind: str, rules: dict) -> None:
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


def _fmt_delta_val(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _render_delta_table(delta: dict) -> None:
    if not delta:
        st.info("No field-level changes recorded for this entry.")
        return

    rows = []
    for key, change in delta.items():
        if not isinstance(change, dict):
            continue
        rows.append({
            "Path / Field": key,
            "Previous Value": _fmt_delta_val(change.get("old")),
            "New Value": _fmt_delta_val(change.get("new")),
        })

    if not rows:
        st.info("No field-level changes recorded for this entry.")
        return

    st.dataframe(
        rows,
        width='stretch',
        hide_index=True,
    )


def _host_editor(rules: dict) -> None:
    all_presets = list(get_host_vm_presets(rules))
    host_presets = [p for p in all_presets if p.get("pattern_key") != "vm_host"]
    vm_presets = [p for p in all_presets if p.get("pattern_key") == "vm_host"]
    patterns = dict(rules.get("naming_patterns") or {})

    st.markdown(f"**🖥️ Hosts Type Presets** &nbsp;&nbsp;&nbsp;`{len(host_presets)} presets`")
    st.caption("Manage physical host naming patterns and presets.")

    col_hdr_code, col_hdr_lbl, col_hdr_tpl, col_hdr_act = st.columns(PRESET_COLS, vertical_alignment="center")
    with col_hdr_code:
        st.markdown("**Code**")
    with col_hdr_lbl:
        st.markdown("**Label**")
    with col_hdr_tpl:
        st.markdown("**Pattern Template**")
    with col_hdr_act:
        pass

    updated = []
    patterns_updates = {}
    stale_del = st.session_state.pop("_host_vm_del_idx", None)

    for idx, preset in enumerate(host_presets):
        code = preset.get("code", "")
        label = preset.get("label", "")
        pk = preset.get("pattern_key", "")
        tpl = patterns.get(pk, "")

        c1, c2, c3, c4 = st.columns(PRESET_COLS, vertical_alignment="center")
        with c1:
            if code == "ESXi":
                st.text_input("Code", value=code, key=f"host_{idx}_code", disabled=True)
            else:
                ncode = st.text_input("Code", value=code, key=f"host_{idx}_code", label_visibility="collapsed").strip()
        with c2:
            if code == "ESXi":
                st.text_input("Label", value=label, key=f"host_{idx}_lbl", disabled=True)
            else:
                nlbl = st.text_input("Label", value=label, key=f"host_{idx}_lbl", label_visibility="collapsed").strip()
        with c3:
            ntpl = st.text_input("Pattern Template", value=tpl, key=f"host_{idx}_tpl", label_visibility="collapsed").strip()
        with c4:
            if code != "ESXi":
                if _render_centered_del_btn(f"host_del_{idx}"):
                    if len(host_presets) > 1:
                        st.session_state["_host_vm_del_idx"] = idx
                        st.rerun()
                    else:
                        st.warning("⚠️ At least one preset must remain.")
            else:
                st.button("", key=f"ghost_host_{idx}", disabled=True)

        if stale_del == idx:
            continue
        final_pk = pk or make_preset_key(code or label, "host_vm")
        if ntpl:
            patterns_updates[final_pk] = ntpl
        if final_pk:
            updated.append({
                "code": code if code else "ESXi",
                "label": label or code or "ESXi Host",
                "pattern_key": final_pk,
                "description": preset.get("description", ""),
            })

    if stale_del is not None:
        rules["naming_patterns"] = {**patterns, **patterns_updates}
        rules["host_vm_presets"] = updated + vm_presets
        _save_presets(rules)
        return

    st.markdown("**➕ Add New Preset**")
    ca1, ca2, ca3 = st.columns(PRESET_COLS[:3])
    with ca1:
        new_code = st.text_input("New Code", value="", placeholder="e.g. HYPV", key="host_new_code", label_visibility="collapsed").strip()
    with ca2:
        new_lbl = st.text_input("New Label", value="", placeholder="e.g. Hyper-V Host", key="host_new_lbl", label_visibility="collapsed").strip()
    with ca3:
        new_tpl = st.text_input("New Pattern Template", value="", placeholder="<site_prefix>hyp<seq>.<domain>", key="host_new_tpl", label_visibility="collapsed").strip()

    col_save, col_reset = st.columns(2)
    with col_save:
        saved = st.button("💾 Save Hosts Presets", key="host_preset_save", type="primary", width='stretch')
    with col_reset:
        reset = st.button("🔄 Reset to Defaults", key="host_preset_reset", width='stretch')

    if reset:
        _reset_presets("host_vm", rules)
        return

    if saved:
        final_presets = list(updated)
        final_patterns = {**patterns, **patterns_updates}
        if new_code and new_tpl:
            nkey = make_preset_key(new_code, "host_vm")
            while nkey in final_patterns and nkey not in [p["pattern_key"] for p in final_presets]:
                nkey = f"{nkey}_x"
            final_patterns[nkey] = new_tpl
            final_presets.append({
                "code": new_code,
                "label": new_lbl or new_code,
                "pattern_key": nkey,
                "description": "",
            })
        elif new_code and not new_tpl:
            st.error("⚠️ Provide a Pattern Template to add a new preset.")
            return
        rules["naming_patterns"] = final_patterns
        rules["host_vm_presets"] = final_presets + vm_presets
        _save_presets(rules)


def _vm_editor(rules: dict) -> None:
    all_presets = list(get_host_vm_presets(rules))
    host_presets = [p for p in all_presets if str(p.get("pattern_key", "")) != "vm_host"]
    vm_presets = [p for p in all_presets if p.get("pattern_key") == "vm_host"]
    patterns = dict(rules.get("naming_patterns") or {})
    tpl = patterns.get("vm_host", "")

    st.markdown(f"**🖱️ Virtual Machine Presets** &nbsp;&nbsp;&nbsp;`{len(vm_presets)} presets`")
    st.caption("Manage the virtual machine role options and their shared pattern template.")

    col_hdr_code, col_hdr_lbl, col_hdr_tpl, col_hdr_act = st.columns(PRESET_COLS, vertical_alignment="center")
    with col_hdr_code:
        st.markdown("**Code**")
    with col_hdr_lbl:
        st.markdown("**Label**")
    with col_hdr_tpl:
        st.markdown("**Pattern Template**")
    with col_hdr_act:
        pass

    updated = []
    stale_del = st.session_state.pop("_del_vm_role_idx", None)

    for idx, p in enumerate(vm_presets):
        c1, c2, c3, c4 = st.columns(PRESET_COLS, vertical_alignment="center")
        with c1:
            ncode = st.text_input("Code", value=p.get("code", ""), key=f"vm_code_{idx}", label_visibility="collapsed").strip()
        with c2:
            nlbl = st.text_input("Label", value=p.get("label", ""), key=f"vm_lbl_{idx}", label_visibility="collapsed").strip()
        with c3:
            ntpl = st.text_input("Pattern Template", value=tpl, key=f"vm_tpl_{idx}", label_visibility="collapsed").strip()
        with c4:
            if _render_centered_del_btn(f"vm_role_del_{idx}"):
                if len(vm_presets) > 1:
                    st.session_state["_del_vm_role_idx"] = idx
                    st.rerun()
                else:
                    st.warning("⚠️ At least one role must remain.")

        if stale_del == idx:
            continue
        tpl = ntpl or tpl or "<country><site><role><seq>"
        p["code"] = ncode.lower() if ncode else p.get("code", "")
        p["label"] = nlbl or ncode or p.get("label", "")
        p["pattern_key"] = "vm_host"
        p["description"] = ""
        updated.append(dict(p))

    if stale_del is not None:
        patterns["vm_host"] = tpl
        rules["naming_patterns"] = patterns
        rules["host_vm_presets"] = host_presets + updated
        _save_presets(rules)
        return

    st.markdown("**➕ Add New Preset**")
    nc1, nc2, nc3 = st.columns(PRESET_COLS[:3])
    with nc1:
        new_code = st.text_input("New Code", value="", key="vm_new_code", placeholder="e.g. cvi", label_visibility="collapsed").strip()
    with nc2:
        new_label = st.text_input("New Label", value="", key="vm_new_lbl", placeholder="e.g. Core Virtualization (cvi)", label_visibility="collapsed").strip()
    with nc3:
        new_tpl = st.text_input("New Pattern Template", value="", key="vm_new_tpl", placeholder="<country><site><role><seq>", label_visibility="collapsed").strip()

    c_save, c_reset = st.columns(2)
    with c_save:
        saved = st.button("💾 Save VM Presets", key="vm_save", type="primary", width='stretch')
    with c_reset:
        reset = st.button("🔄 Reset to Defaults", key="vm_reset", width='stretch')

    if reset:
        _reset_presets("host_vm", rules)
        return

    if saved:
        final = list(updated)
        if new_code:
            if new_tpl:
                patterns["vm_host"] = new_tpl
            final.append({
                "code": new_code.lower(),
                "label": new_label or new_code,
                "pattern_key": "vm_host",
                "description": "",
            })
        elif not updated and not new_code:
            st.warning("⚠️ At least one preset must remain.")
        patterns["vm_host"] = tpl or "<country><site><role><seq>"
        rules["naming_patterns"] = patterns
        rules["host_vm_presets"] = host_presets + final
        _save_presets(rules)


def render_standards_tab(active_model):
    st.subheader("📖 Infrastructure Naming Standards Configuration")
    st.caption("Define and manage your organization's naming conventions. All patterns configured here are automatically applied in the Naming tab.")
    
    if "variables_saved" in st.session_state and st.session_state["variables_saved"]:
        st.success("✅ Variables saved successfully!")
        st.session_state["variables_saved"] = False

    if "presets_saved" in st.session_state and st.session_state["presets_saved"]:
        st.success("✅ Presets saved successfully!")
        st.session_state["presets_saved"] = False

    if "standards_saved" in st.session_state and st.session_state["standards_saved"]:
        st.success("✅ Naming standards saved successfully!")
        st.session_state["standards_saved"] = False
    
    if "standards_reset" in st.session_state and st.session_state["standards_reset"]:
        st.success("✅ Reset to default standards!")
        st.session_state["standards_reset"] = False

    if st.session_state.pop("site_code_saved", False):
        st.success("✅ Site code mapping rules saved & applied!")

    st.markdown(
        """
        <style>
        /* Target any button containing the ghost identifier or disabled empty/blank button */
        button[kind="secondary"]:disabled,
        div:has(> button:disabled) button {
            opacity: 0 !important;
            visibility: hidden !important;
            border: none !important;
            background: transparent !important;
            box-shadow: none !important;
            pointer-events: none !important;
        }
        /* Ensure active buttons (⬆️, ⬇️, 🗑️) remain visible and styled */
        button:not(:disabled) {
            opacity: 1 !important;
            visibility: visible !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    current_rules = load_naming_rules()
    st.session_state["naming_rules"] = current_rules
    
    tab_edit, tab_vars, tab_history = st.tabs(["📝 Edit Standards", "📘 Pattern Variables Reference", "📜 Change History"])
    
    with tab_edit:
        with st.expander("🔧 Network & Security Devices", expanded=False):
            with st.expander("🔧 Device Type Presets", expanded=False):
                _preset_type_editor("device", get_device_presets(current_rules), current_rules, prefix="branch")

            with st.expander("🔌 Interface Type Presets", expanded=False):
                _preset_type_editor("interface", get_interface_presets(current_rules), current_rules, prefix="iface")

        with st.expander("🖥️ Hosts & Virtual Machines", expanded=False):
            with st.expander("🖥️ Hosts Type Presets", expanded=False):
                _host_editor(current_rules)

            with st.expander("🖱️ Virtual Machine Presets", expanded=False):
                _vm_editor(current_rules)

        with st.expander("☁️ ESXi Network Description Presets", expanded=False):
            _preset_type_editor("esxi_network", get_esxi_network_presets(current_rules), current_rules, prefix="esxinet")

        with st.expander("🛠️ Manage Syntax Auto-Correction Rules", expanded=False):
            _render_auto_correction_manager(active_model)

        with st.expander("📋 NetBox Server & Hardware YAML Guidelines", expanded=False):
            st.caption("Document and enforce the NetBox server hardware YAML schema used across your environment.")
            with st.expander("✨ AI Assistant: Generate NetBox Server YAML Specs", expanded=False):
                ai_desc = st.text_input(
                    "Describe the NetBox server hardware YAML spec",
                    key="custom_ai_desc",
                    placeholder="e.g. Generate server hardware YAML for a Dell R740 with dual 25G NICs and 4x 2.5in drive bays",
                )
                if st.button("Generate NetBox Server YAML Specs with AI", key="custom_ai_gen", width='stretch'):
                    if ai_desc.strip():
                        with st.spinner(f"Generating spec using {active_model}..."):
                            try:
                                generated = generate_naming_pattern(ai_desc.strip(), active_model)
                                st.session_state["form_yaml"] = generated
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ AI spec generation failed: {e}")
                    else:
                        st.warning("⚠️ Please describe the server hardware YAML spec first.")
            st.text_area(
                "NetBox Server YAML Guidelines",
                value=current_rules.get("netbox_server_yaml", ""),
                height=120,
                key="form_yaml",
            )
            col_save_yaml, col_reset_yaml = st.columns(2)
            with col_save_yaml:
                if st.button("💾 Save Guidelines", type="primary", width='stretch'):
                    yaml_text = st.session_state.get("form_yaml", "")
                    rules = load_naming_rules()
                    rules["netbox_server_yaml"] = yaml_text
                    save_naming_rules(rules, source="YAML Guidelines Save")
                    st.session_state["naming_rules"] = rules.copy()
                    st.toast("Guidelines saved successfully!", icon="✅")
            with col_reset_yaml:
                if st.button("🔄 Reset to Default", width='stretch'):
                    default_yaml = DEFAULT_RULES.get("netbox_server_yaml", DEFAULT_NAMING_PATTERNS.get("netbox_server_yaml", ""))
                    rules = load_naming_rules()
                    rules["netbox_server_yaml"] = default_yaml
                    save_naming_rules(rules, source="YAML Guidelines Reset")
                    st.session_state["naming_rules"] = rules.copy()
                    st.rerun()

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
    
    with tab_vars:
        st.markdown("##### 📘 Pattern Variables Reference Guide")
        st.caption("All available pattern variables currently configured. These drive the dynamic input fields in the Naming tab.")
        variables_now = get_pattern_variables(current_rules)
        patterns_now = get_naming_patterns(current_rules)

        st.markdown("#### ✏️ Manage Pattern Variables")
        st.caption("Add, edit, or remove variables. New variables default to **optional** (shown empty; omitted from output unless filled). Use `<Name>` in your naming patterns.")

        with st.expander("🔧 Edit Existing Variables", expanded=True):
            var_names = list(variables_now.keys())

            if variables_now:
                c_nh_nm, c_nh_lb, c_nh_ph, c_nh_df, c_nh_opt, c_nh_up, c_nh_dn, c_nh_del = st.columns(VARIABLE_COLS, vertical_alignment="center")
                with c_nh_nm:
                    st.markdown("**Name**")
                with c_nh_lb:
                    st.markdown("**Label**")
                with c_nh_ph:
                    st.markdown("**Placeholder**")
                with c_nh_df:
                    st.markdown("**Auto-Fill**")
                with c_nh_opt:
                    st.markdown("**Optional**")
                with c_nh_up:
                    pass
                with c_nh_dn:
                    pass
                with c_nh_del:
                    pass

                edited_vars = {}
                total_vars = len(var_names)
                for idx, name in enumerate(var_names):
                    meta = variables_now.get(name) if isinstance(variables_now.get(name), dict) else {}
                    c_nm, c_lb, c_ph, c_df, c_opt, c_up, c_dn, c_del = st.columns(VARIABLE_COLS, vertical_alignment="center")
                    with c_nm:
                        var_key = st.text_input("Name", value=name, key=f"var_key_{name}", label_visibility="collapsed").strip()
                        var_key = _normalize_var_name(var_key)
                    with c_lb:
                        var_lbl = st.text_input("Label", value=meta.get("label", name), key=f"var_lbl_{name}", label_visibility="collapsed").strip()
                    with c_ph:
                        var_ph = st.text_input("Placeholder", value=meta.get("placeholder", ""), key=f"var_ph_{name}", label_visibility="collapsed").strip()
                    with c_df:
                        var_def = st.text_input("Auto-Fill", value=meta.get("default", ""), key=f"var_def_{name}", label_visibility="collapsed")
                    with c_opt:
                        var_opt = st.checkbox("Optional", value=bool(meta.get("optional")), key=f"var_opt_{name}", label_visibility="collapsed")
                    with c_up:
                        if idx > 0:
                            st.button("⬆️", key=f"var_up_{idx}", help=f"Move <{name}> up")
                            if st.session_state.get(f"var_up_{idx}"):
                                var_names[idx - 1], var_names[idx] = var_names[idx], var_names[idx - 1]
                                reordered = {k: variables_now[k] for k in var_names}
                                _persist_variables(current_rules, reordered)
                        else:
                            st.button("", key=f"ghost_up_{idx}", disabled=True)
                    with c_dn:
                        if idx < total_vars - 1:
                            st.button("⬇️", key=f"var_dn_{idx}", help=f"Move <{name}> down")
                            if st.session_state.get(f"var_dn_{idx}"):
                                var_names[idx], var_names[idx + 1] = var_names[idx + 1], var_names[idx]
                                reordered = {k: variables_now[k] for k in var_names}
                                _persist_variables(current_rules, reordered)
                        else:
                            st.button("", key=f"ghost_dn_{idx}", disabled=True)
                    with c_del:
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
            new_name = _normalize_var_name(new_name)
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
    
    with tab_history:
        st.markdown("##### 📜 Naming Standards Change History")
        st.caption("View and restore previous versions of your naming standards. The last 10 changes are saved automatically.")
        
        history = load_history()
        
        if not history:
            st.info("📭 No history available yet. Changes will be tracked once you save modifications.")
        else:
            st.success(f"📊 {len(history)} version(s) in history")
            
            col_info, col_clear = st.columns([3, 1])
            with col_clear:
                if st.button("🗑️ Clear History", width='stretch'):
                    clear_history()
                    st.success("✅ History cleared!")
                    st.rerun()
            
            st.markdown("---")
            
            for idx, entry in enumerate(history):
                timestamp = entry.get("timestamp", "Unknown")
                source = entry.get("source", "Unknown")
                delta = entry.get("delta") if isinstance(entry.get("delta"), dict) else {}
                rules = entry.get("rules", {})
                change_count = entry.get("change_count", len(delta))
                changed_keys = entry.get("changed_keys") or list(delta.keys())

                if delta:
                    header = f"🕐 **{timestamp}** - {source} — Modified {change_count} rule(s): {', '.join(str(k) for k in changed_keys)}"
                else:
                    header = f"🕐 **{timestamp}** - {source}"

                with st.expander(header, expanded=(idx == 0)):
                    if delta:
                        st.markdown("**Delta Changes (Previous → New):**")
                        _render_delta_table(delta)
                    else:
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
                    
                    col_restore, col_view = st.columns([1, 1])
                    
                    with col_restore:
                        if st.button(f"↩️ Restore This Version", key=f"restore_{idx}", width='stretch'):
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