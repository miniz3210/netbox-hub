import os
import json
import re
import copy


import streamlit as st
from config.constants import RULES_FILE
from config.naming_rules import (
    load_naming_rules, save_naming_rules, export_rules_as_prompt,
    load_history, restore_from_history, clear_history, add_to_history,
    get_pattern_variables, get_naming_patterns, get_custom_patterns,
    get_device_presets, get_interface_presets, get_host_vm_presets,
    get_vlan_presets, get_vlan_description_mappings, make_preset_key,
    get_esxi_network_presets, get_site_code_rules,
    default_presets_for, DEFAULT_PRESET_KEY_FIELD, DEFAULT_NAMING_PATTERNS,
    DEFAULT_HOST_TYPE_PRESETS, DEFAULT_VM_PRESETS,
    DEFAULT_RULES, DEFAULT_VLAN_PRESETS, DEFAULT_VLAN_DESCRIPTION_MAPPINGS,
    get_ipam_role_mappings, DEFAULT_IPAM_ROLE_MAPPINGS,
    get_hardware_baseline_standards, DEFAULT_HARDWARE_BASELINE_STANDARDS,
)
from core.naming_engine import generate_naming_pattern, generate_autocorrect_rule
from core.session_manager import SessionStateManager as SSM
from core.ai_client import call_ai
from utils.formatters import (
    load_auto_corrections, save_auto_corrections, reset_auto_corrections,
)
from data.standards_manager import StandardsManager, DEFAULT_PARSING_PRESETS

# Shared column width ratios enforced across preset table headers, all data rows,
# and the inline "add" row so every preset table lines up identically.
# Code, Label, Pattern Template, Action.
PRESET_COLS = [1.2, 2.2, 5.4, 1.2]
# Action cell sub-columns: Up, Down, Delete (equal thirds, right-aligned).
PRESET_ACTION_COLS = [1, 1, 1]
# VLAN allocation preset columns: VID, Role, Action (sum = 10.0)
PRESET_VLAN_COLS = [1.5, 7.3, 1.2]
# Standardized 3-column ratio for Key-Value mappings: Key (4.4), Value (4.4), Action (1.2) (sum = 10.0)
VLAND_MAPPINGS_COLS = [4.4, 4.4, 1.2]
# Manage Pattern Variables columns: Name, Label, Placeholder, Auto-Fill, Optional, Up, Down, Delete.
VARIABLE_COLS = [1.5, 2.5, 2.5, 1.5, 0.9, 0.45, 0.45, 0.45]
# Auto-Correction rule columns: Original Pattern, Replacement, Description, Action.
AUTOCORRECT_COLS = [3.5, 3.0, 4.0, 1.0]

def _render_preset_row_actions(
    idx: int,
    total: int,
    items: list,
    key_prefix: str,
    on_reorder=None,
    on_delete=None,
    swap_key: str = None,
) -> None:
    """Render Up / Down / Delete buttons matching the preset table style.

    ``on_reorder(new_items)`` is called when a reorder is requested.
    ``on_delete(del_idx)`` is called when delete is requested (caller handles pending).
    If ``swap_key`` is given, reorders are deferred via session state (preserving
    inline edits) instead of firing ``on_reorder`` immediately.
    """
    col_up, col_down, col_del = st.columns(PRESET_ACTION_COLS)
    with col_up:
        if idx > 0:
            if st.button("⬆️", key=f"{key_prefix}_{idx}_up", help="Move up"):
                if swap_key:
                    st.session_state[swap_key] = (idx, idx - 1)
                    st.rerun()
                elif on_reorder:
                    new_items = list(items)
                    new_items[idx], new_items[idx - 1] = new_items[idx - 1], new_items[idx]
                    on_reorder(new_items)
        else:
            st.empty()
    with col_down:
        if idx < total - 1:
            if st.button("⬇️", key=f"{key_prefix}_{idx}_dn", help="Move down"):
                if swap_key:
                    st.session_state[swap_key] = (idx, idx + 1)
                    st.rerun()
                elif on_reorder:
                    new_items = list(items)
                    new_items[idx], new_items[idx + 1] = new_items[idx + 1], new_items[idx]
                    on_reorder(new_items)
        else:
            st.empty()
    with col_del:
        if on_delete is not None:
            if st.button("🗑️", key=f"{key_prefix}_{idx}_del", help="Delete this entry"):
                on_delete(idx)


def _normalize_var_name(raw: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", raw.strip().lower().replace(" ", "_"))

def _render_centered_del_btn(key: str, help_text: str = "Delete this entry") -> bool:
    """Helper to render a clean delete icon button."""
    return st.button("🗑️", key=key, help=help_text, width='stretch')


def _clear_session_state_prefixes(*prefixes: str) -> None:
    """Drop every cached widget value whose key starts with one of *prefixes*.

    Called from Reset handlers so that stale in-row values (text inputs, moved
    indices, etc.) are wiped before the next render, guaranteeing the tables
    rebuild from the freshly loaded defaults rather than the old session values.
    """
    for k in list(st.session_state.keys()):
        sk = str(k)
        if any(sk.startswith(p) for p in prefixes):
            st.session_state.pop(k, None)




def _inject_preset_table_style() -> None:
    """Inject the shared preset-table CSS.

    The Action cell is the last column of every preset table, so its three icon
    buttons (Up / Down / Delete) must render flush against the right margin with
    equal widths and no awkward inner gap. Streamlit's default nested-column gap
    and button padding are what previously broke that alignment, so both are
    overridden here. Injected once per table; duplicate rules are harmless.
    """
    st.markdown(
        """
        <style>
        /* Equalize the nested Up/Down/Delete sub-columns inside an Action cell:
           collapse the default gap and strip the button padding so the three
           icons share the cell width evenly and sit flush to the right margin. */
        div[data-testid="column"] [data-testid="horizontalBlock"] {
            gap: 0.25rem;
        }
        div[data-testid="column"] [data-testid="horizontalBlock"] button {
            padding-left: 0.1rem !important;
            padding-right: 0.1rem !important;
            padding-top: 0.1rem !important;
            padding-bottom: 0.1rem !important;
            min-width: 0 !important;
            width: 100% !important;
        }
        /* Defend the Action cell against the Pattern Variables stylesheet, which
           force-flushes every trailing column on the page. A preset Action cell is
           the trailing column that itself holds sub-columns, so it is reset back
           to a plain block and its sub-columns are un-pinned. These selectors
           out-specify the generic `:last-child` rules regardless of order. */
        div[data-testid="column"]:last-child:has([data-testid="horizontalBlock"]) {
            display: flex !important;
            justify-content: flex-end !important;
            align-items: center !important;
        }
        div[data-testid="column"]:last-child [data-testid="horizontalBlock"] {
            display: flex !important;
            justify-content: flex-end !important;
            width: 100% !important;
            margin-left: auto !important;
        }
        div[data-testid="column"] [data-testid="column"]:last-child button {
            padding-left: 0.1rem !important;
            padding-right: 0.1rem !important;
            min-width: 0 !important;
            width: 100% !important;
        }
        .action-header {
            white-space: nowrap !important;
        }
        div[data-testid="column"]:last-child {
            display: flex !important;
            justify-content: flex-end !important;
            align-items: center !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

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


def _persist_variables(rules: dict, variables: dict, section_key: str = "shared") -> None:
    rules["pattern_variables"] = variables
    save_naming_rules(rules, source="Variable Manager")
    SSM.set_naming_rules(rules.copy())
    st.session_state["variables_saved"] = True
    st.toast(f"✅ {section_key.title()} Variables saved & applied!", icon="💾")
    st.rerun()


def _render_auto_correction_manager(active_model: str) -> None:
    if "rules_version_counter" not in st.session_state:
        st.session_state["rules_version_counter"] = 0
    rules = load_auto_corrections()
    categories = list(rules.keys())

    if not categories:
        st.info("No auto-correction categories defined.")
        return

    for category in categories:
        category_title = {
            "port_shortening": "🔌 Port Abbreviation Rules (Interface Shortening)",
            "vmware": "🔍 OCR Text Cleaning & Syntax Rules",
        }.get(category, f"🛠️ Auto-Correction Rules — {category}")
        with st.expander(category_title, expanded=False):
            st.caption(
                "Each row is a regex pattern → replacement pair. Edit inline or use "
                "the AI generator below to create new rules."
            )
            version = st.session_state["rules_version_counter"]
            edit_form_key = f"ac_edit_form_{category}_{version}"
            with st.form(key=edit_form_key, clear_on_submit=False):
                pass  # placeholder removed

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
            for idx, rule in enumerate(items):
                col_p, col_r, col_d, col_del = st.columns(AUTOCORRECT_COLS, vertical_alignment="center")
                with col_p:
                    p = st.text_input(
                        "Original Pattern",
                        value=rule.get("pattern", ""),
                        key=f"ac_{category}_p_{idx}_{version}",
                        label_visibility="collapsed",
                    )
                with col_r:
                    r = st.text_input(
                        "Replacement",
                        value=rule.get("replacement", ""),
                        key=f"ac_{category}_r_{idx}_{version}",
                        label_visibility="collapsed",
                    )
                with col_d:
                    d = st.text_input(
                        "Description",
                        value=rule.get("description", ""),
                        key=f"ac_{category}_d_{idx}_{version}",
                        label_visibility="collapsed",
                    )
                with col_del:
                    if _render_centered_del_btn(f"ac_{category}_del_{idx}_{version}", "Delete this rule"):
                        items.pop(idx)
                        rules[category] = items
                        save_auto_corrections(rules, source="Delete auto-correction rule")
                        st.session_state["rules_version_counter"] = version + 1
                        st.rerun()

                if p.strip():
                    updated.append({
                        "pattern": p,
                        "replacement": r,
                        "description": d,
                        "enabled": True,
                    })

            col_save, col_reset = st.columns([1.2, 1.0])
            with col_save:
                saved_ac = st.button("💾 Save & Apply Changes", key=f"ac_{category}_save", type="primary", width="stretch")
            with col_reset:
                if st.button("🔄 Reset to Defaults", key=f"ac_reset_factory_{category}", width='stretch'):
                    reset_auto_corrections()
                    _clear_session_state_prefixes(f"ac_{category}_")

            if saved_ac:
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
                    _persist_auto_corrections(final, category)

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

            with st.form(key=f"ac_add_form_{category}", clear_on_submit=True):
                col_add_p, col_add_r, col_add_d, col_add_btn = st.columns(AUTOCORRECT_COLS, vertical_alignment="center")
                with col_add_p:
                    new_p = st.text_input("New Pattern", value="", key=f"ac_{category}_new_p",
                                         placeholder=r"(?i)\b(vswitch)(\d+)\b", label_visibility="collapsed")
                with col_add_r:
                    new_r = st.text_input("New Replacement", value="", key=f"ac_{category}_new_r",
                                         placeholder=r"vSwitch\2", label_visibility="collapsed")
                with col_add_d:
                    new_d = st.text_input("New Description", value="", key=f"ac_{category}_new_d",
                                         placeholder="Describe the rule", label_visibility="collapsed")
                with col_add_btn:
                    add_submitted = st.form_submit_button("➕ Add", width='stretch', help="Add new rule")
                if add_submitted:
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
                            _clear_session_state_prefixes(f"ac_{category}_new_p", f"ac_{category}_new_r", f"ac_{category}_new_d")
                            _persist_auto_corrections(final, category)
                    else:
                        st.warning("⚠️ Enter a regex pattern to add.")


    _render_site_code_mapping_manager(key_prefix="ac_scm")


def _render_ipam_role_mapping_manager(active_model: str) -> None:
    rules = load_naming_rules()
    role_rules = list(get_ipam_role_mappings(rules))

    st.caption(
        "Each row is a regex pattern → canonical role pair. Edit inline or use "
        "the AI generator below to create new rules."
    )

    with st.form(key="ipam_role_edit_form", clear_on_submit=False):
        pass  # placeholder removed

    ch_p, ch_r, ch_d, ch_del = st.columns(AUTOCORRECT_COLS, vertical_alignment="center")
    with ch_p:
        st.markdown("**Original Pattern**")
    with ch_r:
        st.markdown("**Replacement**")
    with ch_d:
        st.markdown("**Description**")
    with ch_del:
        pass

    items = list(role_rules)
    updated = []
    pending_delete = None
    for idx, rule in enumerate(items):
        col_p, col_r, col_d, col_del = st.columns(AUTOCORRECT_COLS, vertical_alignment="center")
        with col_p:
            p = st.text_input(
                "Original Pattern",
                value=rule.get("pattern", ""),
                key=f"ipamrole_{idx}_p",
                label_visibility="collapsed",
            )
        with col_r:
            r = st.text_input(
                "Replacement",
                value=rule.get("replacement", ""),
                key=f"ipamrole_{idx}_r",
                label_visibility="collapsed",
            )
        with col_d:
            d = st.text_input(
                "Description",
                value=rule.get("description", ""),
                key=f"ipamrole_{idx}_d",
                label_visibility="collapsed",
            )
        with col_del:
            if _render_centered_del_btn(f"ipamrole_{idx}_del", "Delete this rule"):
                pending_delete = idx

        if pending_delete == idx:
            continue
        if p.strip():
            updated.append({
                "pattern": p,
                "replacement": r,
                "description": d,
                "enabled": rule.get("enabled", True),
            })

    col_save, col_reset = st.columns([1.2, 1.0])
    with col_save:
        saved_ipam = st.button("💾 Save & Apply Changes", key="ipamrole_save", type="primary", width="stretch")
    with col_reset:
        if st.button("🔄 Reset to Defaults", key="ipamrole_reset", width='stretch'):
            _reset_ipam_role_mappings()

    if saved_ipam:
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
            final["ipam_role_mappings"] = updated
            _persist_ipam_role_mappings(final)

    with st.expander("✨ AI Assistant: Generate Role Mapping Rule", expanded=False):
        ai_prompt = st.text_input(
            "Describe rule in natural language:",
            key="ipamrole_ai_input",
            placeholder="e.g. Map cctv or ip cam to Surveillance",
        )
        if st.button("Generate Regex Rule", key="ipamrole_ai_btn", width='stretch'):
            if ai_prompt.strip():
                try:
                    with st.spinner(f"Generating rule using {active_model}..."):
                        result = generate_autocorrect_rule(ai_prompt.strip(), active_model)
                    st.session_state["ipamrole_new_p"] = result["pattern"]
                    st.session_state["ipamrole_new_r"] = result["replacement"]
                    st.session_state["ipamrole_new_d"] = result["description"]
                    st.toast("Rule generated! Review and click '➕ Add' to apply.")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ AI rule generation failed: {e}")
            else:
                st.warning("⚠️ Please describe the rule first.")

    # Inline Add Rule row wrapped in a clear_on_submit form
    with st.form(key="ipam_role_add_form", clear_on_submit=True):
        col_add_p, col_add_r, col_add_d, col_add_btn = st.columns(AUTOCORRECT_COLS, vertical_alignment="center")
        with col_add_p:
            new_p = st.text_input("New Pattern", value="", key="ipamrole_new_p",
                                 placeholder=r"(?i)^cctv|ip[\s\-]?cam$", label_visibility="collapsed")
        with col_add_r:
            new_r = st.text_input("New Replacement", value="", key="ipamrole_new_r",
                                 placeholder="Surveillance", label_visibility="collapsed")
        with col_add_d:
            new_d = st.text_input("New Description", value="", key="ipamrole_new_d",
                                 placeholder="Normalize CCTV variations", label_visibility="collapsed")
        with col_add_btn:
            add_submitted = st.form_submit_button("➕ Add", width='stretch', help="Add new rule")
        if add_submitted:
            if new_p.strip():
                ok, msg = validate_regex_replacement(new_p, new_r)
                if not ok:
                    st.error(f"❌ Cannot add rule — {msg}")
                else:
                    final = dict(rules)
                    final["ipam_role_mappings"] = list(updated) + [{
                        "pattern": new_p,
                        "replacement": new_r,
                        "description": new_d,
                        "enabled": True,
                    }]
                    _persist_ipam_role_mappings(final)
            else:
                st.warning("⚠️ Enter a regex pattern to add.")


def _persist_ipam_role_mappings(rules: dict) -> None:
    from config.naming_rules import compute_delta, add_to_history

    old_rules = load_naming_rules()
    save_naming_rules(rules, source="IPAM Role Mapping Rules: Management UI")
    delta = compute_delta(old_rules, rules)
    if not delta:
        old_val = rules.get("ipam_role_mappings") or []
        new_val = old_rules.get("ipam_role_mappings") or []
        delta = {"ipam_role_mappings": {"old": list(old_val), "new": list(new_val)}}
    add_to_history(delta, source="IPAM Role Mapping Rules: Management UI")
    SSM.refresh_naming_rules()
    st.toast("✅ IPAM Role Mapping Rules saved & applied!", icon="💾")
    st.rerun()


def _reset_ipam_role_mappings() -> None:
    from config.naming_rules import compute_delta

    old_rules = load_naming_rules()
    new_rules = dict(old_rules)
    new_rules["ipam_role_mappings"] = list(DEFAULT_IPAM_ROLE_MAPPINGS)
    save_naming_rules(new_rules, source="IPAM Role Mapping Rules: Reset to Defaults")
    delta = compute_delta(old_rules, new_rules)
    if not delta:
        delta = {"ipam_role_mappings": {
            "old": list(old_rules.get("ipam_role_mappings") or []),
            "new": list(DEFAULT_IPAM_ROLE_MAPPINGS),
        }}
    add_to_history(delta, source="IPAM Role Mapping Rules: Reset to Defaults")
    _clear_session_state_prefixes("ipamrole_")
    SSM.refresh_naming_rules()
    st.toast("✅ IPAM Role Mapping Rules reset to defaults!", icon="🔄")
    st.rerun()


def _do_sitecode_reorder(new_items, key_prefix):
    """Reorder site code mappings and persist."""
    st.session_state["site_code_mappings_modified"] = dict(new_items)
    rules = load_naming_rules()
    sr = get_site_code_rules(rules)
    final = dict(rules)
    final["site_code_rules"] = dict(sr)
    final["site_code_rules"]["exact_mappings"] = dict(new_items)
    _persist_site_code_mappings(final)

def _do_sitecode_delete(del_idx, key_prefix, items, exact):
    """Delete a site code mapping and persist."""
    key_to_del = items[del_idx][0].strip().lower()
    exact.pop(key_to_del, None)
    st.session_state["site_code_mappings_modified"] = dict(exact)
    rules = load_naming_rules()
    sr = get_site_code_rules(rules)
    final = dict(rules)
    final["site_code_rules"] = dict(sr)
    final["site_code_rules"]["exact_mappings"] = dict(exact)
    _persist_site_code_mappings(final)

def _render_site_code_mapping_manager(key_prefix: str = "std_scm") -> None:
    from config.naming_rules import get_site_code_rules

    with st.expander("📍 Site Code Mapping Rules (City / Location to Code)", expanded=False):
        st.caption(
            "Each row maps a city/location pattern → site code. The Naming tab's Site Code "
            "Assistant uses these exact mappings directly. A location that matches a pattern is "
            "resolved to its code before any algorithmic fallback."
        )

        # Use session-state copy if available to avoid reloading stale disk data after edits
        if "site_code_mappings_modified" in st.session_state:
            exact = dict(st.session_state["site_code_mappings_modified"])
        else:
            rules = load_naming_rules()
            sr = get_site_code_rules(rules)
            exact = dict(sr.get("exact_mappings") or {})

        with st.form(key=f"{key_prefix}_sitecode_edit_form", clear_on_submit=False):
            pass  # placeholder removed

        m_col_p, m_col_r, m_col_del = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
        with m_col_p:
            st.markdown("**Original Pattern (City / Location)**")
        with m_col_r:
            st.markdown("**Replacement (Site Code)**")
        with m_col_del:
            pass

        items = list(exact.items())
        updated = {}

        for idx, (pat, code) in enumerate(items):
            col_p, col_r, col_act = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
            with col_p:
                np_ = st.text_input(
                    "Original Pattern", value=pat, key=f"{key_prefix}_sitecode_{idx}_p",
                    label_visibility="collapsed",
                )
            with col_r:
                nr_ = st.text_input(
                    "Replacement", value=code, key=f"{key_prefix}_sitecode_{idx}_r",
                    label_visibility="collapsed",
                )
            with col_act:
                _render_preset_row_actions(
                    idx=idx,
                    total=len(items),
                    items=items,
                    key_prefix=f"{key_prefix}_sitecode",
                    on_reorder=lambda new_items: (_do_sitecode_reorder(new_items, key_prefix) or None),
                    on_delete=lambda del_idx: (_do_sitecode_delete(del_idx, key_prefix, items, exact) or None)
                )

            key = np_.strip().lower()
            if key:
                updated[key] = nr_.strip().upper()

        col_save, col_reset = st.columns([1.2, 1.0])
        with col_save:
            saved_sitecode = st.button("💾 Save & Apply Changes", key=f"{key_prefix}_sitecode_save", type="primary", width="stretch")
        with col_reset:
            if st.button("🔄 Reset to Defaults", key=f"{key_prefix}_sitecode_reset", width='stretch'):
                _reset_site_code_mappings()

        if saved_sitecode:
            rules = load_naming_rules()
            sr = get_site_code_rules(rules)
            final = dict(rules)
            final["site_code_rules"] = dict(sr)
            final["site_code_rules"]["exact_mappings"] = updated
            _persist_site_code_mappings(final)
            st.session_state["site_code_mappings_modified"] = dict(updated)

        with st.form(key=f"{key_prefix}_sitecode_add_form", clear_on_submit=True):
            col_city, col_code, col_add = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
            with col_city:
                new_p = st.text_input(
                    "New City / Location", value="", key=f"{key_prefix}_sitecode_new_p",
                    placeholder="e.g. bristol", label_visibility="collapsed",
                )
            with col_code:
                new_code = st.text_input(
                    "New Site Code", value="", key=f"{key_prefix}_sitecode_new_code",
                    placeholder="e.g. BRI", label_visibility="collapsed",
                )
            with col_add:
                add_submitted = st.form_submit_button("➕ Add", width='stretch', help="Add new mapping")
            if add_submitted:
                if new_p.strip() and new_code.strip():
                    rules = load_naming_rules()
                    sr = get_site_code_rules(rules)
                    final = dict(rules)
                    final["site_code_rules"] = dict(sr)
                    final["site_code_rules"]["exact_mappings"] = dict(updated)
                    final["site_code_rules"]["exact_mappings"][new_p.strip().lower()] = new_code.strip().upper()
                    st.session_state["site_code_mappings_modified"] = dict(final["site_code_rules"]["exact_mappings"])
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
    SSM.refresh_naming_rules()
    st.toast("✅ Site Code Mapping Rules saved & applied!", icon="💾")
    st.rerun()


def _reset_site_code_mappings() -> None:
    """Restore factory-default Site Code mapping rules.

    Clears every cached ``sitecode_*`` widget value, reloads the default
    config from the factory settings, persists it to disk, then re-runs so the
    manager rebuilds from the defaults.
    """
    from config.naming_rules import DEFAULT_SITE_CODE_RULES, compute_delta

    FACTORY_SITE_CODE_MAPPINGS = {
        "new york": "NYC", "london": "LON", "sydney": "SYD",
        "singapore": "SIN", "tokyo": "TYO", "hong kong": "HKGSAR",
        "amsterdam": "AMS", "frankfurt": "FRA", "paris": "PAR",
        "chicago": "CHI", "los angeles": "LAX", "san francisco": "SFO",
        "dallas": "DFW", "seattle": "SEA", "boston": "BOS",
        "toronto": "YYZ", "bristol": "BRS", "age": "AGE",
    }

    factory_rules = DEFAULT_SITE_CODE_RULES
    factory_mappings = factory_rules.get("exact_mappings")
    if not isinstance(factory_mappings, dict) or not factory_mappings:
        factory_rules = dict(DEFAULT_SITE_CODE_RULES)
        factory_rules["exact_mappings"] = dict(FACTORY_SITE_CODE_MAPPINGS)

    old_rules = load_naming_rules()
    new_rules = dict(old_rules)
    new_rules["site_code_rules"] = dict(factory_rules)
    save_naming_rules(new_rules, source="Site Code Mapping Rules: Reset to Defaults")
    delta = compute_delta(old_rules, new_rules)
    if not delta:
        delta = {"site_code_rules": {
            "old": dict(old_rules.get("site_code_rules") or {}),
            "new": dict(factory_rules),
        }}
    add_to_history(delta, source="Site Code Mapping Rules: Reset to Defaults")
    _clear_session_state_prefixes("sitecode_")
    st.session_state.pop("site_code_mappings_modified", None)
    SSM.refresh_naming_rules()
    st.toast("✅ Site Code Mapping Rules reset to defaults!", icon="🔄")
    st.rerun()


def _persist_auto_corrections(data: dict, category: str = "") -> None:
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
    st.toast("✅ Syntax Auto-Correction Rules saved & applied!", icon="💾")
    st.rerun()


def _save_presets(rules: dict, section: str = "presets", section_label: str = "Presets") -> None:
    save_naming_rules(rules, source="Presets Manager")
    fresh = SSM.get_cached_naming_rules() or load_naming_rules()
    rules.clear()
    rules.update(fresh)
    SSM.set_naming_rules(fresh.copy())
    SSM.refresh_naming_rules()
    st.toast(f"✅ {section_label} saved & applied!", icon="💾")
    st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
    # Clear all preset widget input keys while safely preserving group selections and system flags
    _clear_session_state_prefixes("device_pre_", "interface_pre_", "host_", "vm_", "esxi_network_pre_",
        "vlan_pre_vid_", "vlan_pre_role_", "vlan_pre_pat_", "vlan_pre_gnpat_", "vlan_pre_gppat_",
        "vlan_pre_new_vid", "vlan_pre_new_role"
    )
    st.rerun()


def _save_csv_schemas(rules: dict) -> None:
    save_naming_rules(rules, source="CSV Schemas Manager")
    SSM.set_naming_rules(rules.copy())
    st.toast("✅ NetBox Bulk Import CSV Schemas saved & applied!", icon="💾")
    _clear_session_state_prefixes("csv_sch_")
    st.rerun()

def _reset_csv_schemas(rules: dict) -> None:
    from config.naming_rules import DEFAULT_CSV_SCHEMAS
    rules["csv_schemas"] = copy.deepcopy(DEFAULT_CSV_SCHEMAS)
    save_naming_rules(rules, source="CSV Schemas Reset")
    SSM.set_naming_rules(rules.copy())
    st.toast("✅ NetBox Bulk Import CSV Schemas reset to defaults!", icon="🔄")
    _clear_session_state_prefixes("csv_sch_")
    st.rerun()

def _render_csv_schemas_editor(rules: dict) -> None:
    from config.naming_rules import get_csv_schemas
    schemas = get_csv_schemas(rules)

    with st.expander("📊 NetBox Bulk Import CSV Schemas", expanded=False):
        st.caption("Customize headers and dynamic cell templates for the 4 offline NetBox bulk import CSVs (Site, VLAN Group, VLANs, Prefixes). Supports Universal Context tokens like `<site>`, `<vid>`, `<prefix>`, `<role>`, etc.")

        schema_meta = [
            ("import_site", "🏢 Import Site CSV Schema", "Headers and row template for creating dcim.site"),
            ("import_vlan_group", "🌐 Import VLAN Group CSV Schema", "Headers and row template for creating ipam.vlangroup"),
            ("import_vlans", "🏷️ Import VLANs CSV Schema", "Headers and row template for creating ipam.vlan"),
            ("import_prefixes", "📦 Import Prefixes CSV Schema", "Headers and row template for member prefixes and supernet container"),
        ]

        edited_schemas = copy.deepcopy(schemas)
        nonce = st.session_state.get("standards_nonce", 0)

        for s_key, s_title, s_desc in schema_meta:
            s_data = schemas.get(s_key, {})
            with st.container(border=True):
                st.markdown(f"##### {s_title}")
                st.caption(s_desc)

                headers_str = ", ".join(s_data.get("headers", []))
                row_tpl_str = ", ".join(s_data.get("row_template", []))

                new_headers = st.text_input(
                    "CSV Headers (Comma-separated)",
                    value=headers_str,
                    key=f"csv_sch_{nonce}_{s_key}_headers",
                    help="Column names appearing in row 1 of the generated CSV."
                )

                new_row_tpl = st.text_input(
                    "Row Template (Comma-separated tokens)",
                    value=row_tpl_str,
                    key=f"csv_sch_{nonce}_{s_key}_row",
                    help="Values for each record row. Use <token> for dynamic replacement."
                )

                parsed_headers = [h.strip() for h in new_headers.split(",") if h.strip()]
                parsed_row = [c.strip() for c in new_row_tpl.split(",") if c.strip()]

                edited_schemas[s_key] = {
                    "headers": parsed_headers,
                    "row_template": parsed_row
                }

                if s_key == "import_prefixes":
                    sup_tpl_str = ", ".join(s_data.get("supernet_template", []))
                    new_sup_tpl = st.text_input(
                        "Supernet Container Template (Comma-separated)",
                        value=sup_tpl_str,
                        key=f"csv_sch_{nonce}_{s_key}_sup",
                        help="Template for the top-level site supernet container row."
                    )
                    parsed_sup = [c.strip() for c in new_sup_tpl.split(",") if c.strip()]
                    edited_schemas[s_key]["supernet_template"] = parsed_sup

        col_save, col_reset = st.columns(2)
        with col_save:
            if st.button("💾 Save & Apply Changes", key="csv_schemas_save", type="primary", width="stretch"):
                rules["csv_schemas"] = edited_schemas
                _save_csv_schemas(rules)
        with col_reset:
            if st.button("🔄 Reset to Defaults", key="csv_schemas_reset", width="stretch"):
                _reset_csv_schemas(rules)

def _save_vlan_desc_mappings(rules: dict, section: str = "vlan_desc_mappings", section_label: str = "VLAN Description Mappings") -> None:
    save_naming_rules(rules, source="VLAN Description Mappings Manager")
    SSM.set_naming_rules(rules.copy())
    st.toast(f"✅ {section_label} saved & applied!", icon="💾")
    _clear_session_state_prefixes("vlandesc_")
    st.rerun()


DEFAULT_HYPERVISOR_PRESETS = {
    "VMware ESXi": [
        {
            "code": "Uplink",
            "label": "Physical Uplink",
            "pattern": "<vmnic> - <v_switch> <purpose> <uplink_role>",
        },
        {
            "code": "Uplink_Simple",
            "label": "Physical Uplink (No Purpose)",
            "pattern": "<vmnic> - <v_switch> <uplink_role>",
        },
        {
            "code": "PortGroup",
            "label": "Port Group",
            "pattern": "<v_switch> (<active_vmnics> Active / <standby_vmnics> Standby)",
        },
        {
            "code": "VMkernel",
            "label": "VMkernel",
            "pattern": "<purpose> (<v_switch>)",
        },
        {
            "code": "PG_Header",
            "label": "PortGroup Interface Name",
            "pattern": "PG-<port_group>",
        },
    ],
    "Proxmox VE": [
        {
            "code": "Bond",
            "label": "Linux Bond",
            "pattern": "<bond> (<slaves> Active)",
        },
        {
            "code": "Bridge",
            "label": "Linux Bridge",
            "pattern": "<bridge> - <purpose>",
        },
    ],
}


def _render_spatial_anchors_redaction_editor(
    plat_parsing: dict,
    active_plat: str,
) -> None:
    """Render Section 2.5: Relative Spatial Anchors & Local Privacy Redaction UI."""
    sp_cfg = plat_parsing.get("spatial_anchors_and_redaction")
    if not isinstance(sp_cfg, dict):
        sp_cfg = {}

    # Handle pending add from session state (deferred from clear_on_submit form)
    pending_add_key = f"_spat_pending_add_{active_plat}"
    if st.session_state.pop(pending_add_key, False):
        new_role = st.session_state.pop(f"spat_new_role_{active_plat}", "").strip()
        new_pat = st.session_state.pop(f"spat_new_pat_{active_plat}", "").strip()
        if new_role:
            pat_list = [p.strip() for p in new_pat.split(",") if p.strip()] if new_pat else []
            anchors_list = sp_cfg.get("anchors")
            if not isinstance(anchors_list, list):
                anchors_list = []
            anchors_list.append({"role": new_role, "patterns": pat_list})
            sp_cfg["anchors"] = anchors_list
            plat_parsing["spatial_anchors_and_redaction"] = sp_cfg

    enabled = sp_cfg.get("enabled", False)

    # Migrate legacy scalar fields into the new anchors list format on-the-fly
    anchors_list = sp_cfg.get("anchors")
    if not isinstance(anchors_list, list) or not anchors_list:
        legacy_map = [
            ("left_boundary_anchor", "left_boundary"),
            ("container_header", "container_header"),
            ("adapter_column_anchor", "adapter_column"),
        ]
        anchors_list = []
        for legacy_key, role in legacy_map:
            val = sp_cfg.get(legacy_key)
            if isinstance(val, str) and val.strip():
                anchors_list.append({"role": role, "patterns": [val.strip()]})
            elif isinstance(val, list) and val:
                anchors_list.append({"role": role, "patterns": list(val)})
        if not anchors_list:
            anchors_list = [
                {"role": "left_boundary", "patterns": ["Virtual switches"]},
                {"role": "container_header", "patterns": ["Standard Switch:"]},
                {"role": "adapter_column", "patterns": ["Physical Adapters"]},
            ]
        sp_cfg["anchors"] = anchors_list

    redact_ipv4 = sp_cfg.get("redact_ipv4", True)
    redact_domains = sp_cfg.get("redact_domains", True)
    redact_mac = sp_cfg.get("redact_mac", True)
    domain_pats = sp_cfg.get("domain_patterns", ["\\.adds$", "\\.local$", "\\.internal$", "\\.corp$"])
    if not isinstance(domain_pats, list):
        domain_pats = []
    domain_pats_str = ", ".join(domain_pats)

    st.caption(
        "Configure dynamic relative spatial boundaries (resolution-agnostic) and "
        "on-device deterministic data redaction. When enabled, OCR tokens are grouped "
        "by virtual-switch containers using anchor-based geometry instead of fixed "
        "pixel offsets, and sensitive values (IPs, domains, MACs) are replaced locally "
        "with deterministic tokens before the payload reaches any external LLM."
    )

    enabled_ui = st.checkbox(
        "Enable Relative Spatial Grouping & Local Redaction",
        value=bool(enabled),
        key=f"spat_enabled_{active_plat}",
        help="When enabled, OCR tokens are spatially grouped and sensitive data is redacted locally.",
    )

    sp_cfg_out = dict(sp_cfg)
    sp_cfg_out["enabled"] = enabled_ui

    if enabled_ui:
        # ── Dynamic Anchor Rows (identical UX to Section 2) ──────────────
        st.markdown(
            "<div style='display:flex; justify-content:space-between; align-items:center; margin-top:8px; margin-bottom:4px;'>"
            "<span style='font-size:0.95rem; font-weight:600;'>Anchor Roles &amp; Patterns</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption("Each row defines a spatial anchor role with one or more text patterns. Patterns are matched case-insensitively against OCR token text.")

        ANC_COLS = [2.0, 5.3, 1.2]
        ac_role, ac_pat, ac_act = st.columns(ANC_COLS, vertical_alignment="center")
        with ac_role:
            st.markdown("**Anchor Role**")
        with ac_pat:
            st.markdown("**Anchor Patterns (comma-separated)**")
        with ac_act:
            pass

        updated_anchors = []
        for idx, anchor_entry in enumerate(anchors_list):
            role = anchor_entry.get("role", "")
            patterns = anchor_entry.get("patterns", [])
            if not isinstance(patterns, list):
                patterns = [str(patterns)]
            pat_str = ", ".join(patterns)

            rc, rp, ra = st.columns(ANC_COLS, vertical_alignment="center")
            with rc:
                new_role = st.text_input(
                    "Anchor Role", value=role,
                    key=f"spat_role_{active_plat}_{idx}",
                    label_visibility="collapsed",
                ).strip()
            with rp:
                new_pat = st.text_input(
                    "Patterns", value=pat_str,
                    key=f"spat_pat_{active_plat}_{idx}",
                    label_visibility="collapsed",
                ).strip()
            with ra:
                if _render_centered_del_btn(f"spat_del_{active_plat}_{idx}", "Delete anchor row"):
                    continue  # skip re-adding this row
            if new_role:
                pat_list = [p.strip() for p in new_pat.split(",") if p.strip()] if new_pat else []
                updated_anchors.append({"role": new_role, "patterns": pat_list})

        # Inline add-row form (clear_on_submit avoids layout crashes)
        with st.form(key=f"spat_add_form_{active_plat}", clear_on_submit=True):
            ca1, ca2, ca3 = st.columns(ANC_COLS, vertical_alignment="center")
            with ca1:
                new_role = st.text_input(
                    "New Role", value="", key=f"spat_new_role_{active_plat}",
                    placeholder="e.g. container_header", label_visibility="collapsed",
                )
            with ca2:
                new_pat = st.text_input(
                    "New Patterns", value="", key=f"spat_new_pat_{active_plat}",
                    placeholder="e.g. Standard Switch:, DVSwitch:", label_visibility="collapsed",
                )
            with ca3:
                add_anchor = st.form_submit_button("➕ Add", width='stretch', help="Add new anchor row")
            if add_anchor:
                if new_role.strip():
                    pat_list = [p.strip() for p in new_pat.split(",") if p.strip()] if new_pat.strip() else []
                    updated_anchors.append({"role": new_role.strip(), "patterns": pat_list})
                    st.session_state[f"_spat_pending_add_{active_plat}"] = True
                    st.rerun()
                else:
                    st.warning("⚠️ Enter an anchor role name.")

        st.divider()

        # ── Redaction Controls ────────────────────────────────────────────
        redact_ipv4_ui = st.checkbox(
            "Redact IPv4 addresses",
            value=bool(redact_ipv4),
            key=f"spat_redact_ip_{active_plat}",
        )
        redact_domains_ui = st.checkbox(
            "Redact internal domains",
            value=bool(redact_domains),
            key=f"spat_redact_domain_{active_plat}",
        )
        redact_mac_ui = st.checkbox(
            "Redact MAC addresses",
            value=bool(redact_mac),
            key=f"spat_redact_mac_{active_plat}",
        )

        if redact_domains_ui:
            domain_pats_ui = st.text_input(
                "Domain Suffix Patterns (comma-separated regex)",
                value=domain_pats_str,
                key=f"spat_domain_pats_{active_plat}",
                help="Regex patterns matching internal domain suffixes to redact (e.g. '.adds, .local').",
            )
        else:
            domain_pats_ui = domain_pats_str

        sp_cfg_out.update({
            "redact_ipv4": redact_ipv4_ui,
            "redact_domains": redact_domains_ui,
            "redact_mac": redact_mac_ui,
            "domain_patterns": [
                p.strip() for p in domain_pats_ui.split(",") if p.strip()
            ] if redact_domains_ui else [],
        })
    else:
        # Preserve anchor settings while the feature is disabled.
        sp_cfg_out.setdefault("anchors", anchors_list)

    plat_parsing["spatial_anchors_and_redaction"] = sp_cfg_out


def _render_hypervisor_platform_presets_editor(rules: dict, active_model: str | None = None):
    """Unified editor for hypervisor platform presets.

    Each platform entry now lives in ``topology_parsing_presets`` with a single
    dict containing ``platform``, ``aliases``, ``instructions``, and
    ``export_prompt``.  The ``hypervisor_presets`` dict still holds the
    NetBox description templates (patterns) – these are saved together
    atomically with the aliases and instructions.

    Each logical section is wrapped in its own expander with independent
    ``💾 Save & Apply Changes`` / ``🔄 Reset`` buttons so banner leakage
    across sections is eliminated.
    """
    active_model = active_model or st.session_state.get("active_model", "")
    hyp_presets = rules.get("hypervisor_presets") or {}
    parsing_presets = rules.get("topology_parsing_presets") or {}

    # existing_set used only for ordering; aliases/instructions live in parsing_presets
    hyp_keys = list(hyp_presets.keys()) if isinstance(hyp_presets, dict) else []

    if isinstance(parsing_presets, dict):
        parsing_keys = list(parsing_presets.keys())
    elif isinstance(parsing_presets, list):
        parsing_keys = [
            item.get("platform") or item.get("name")
            for item in parsing_presets
            if isinstance(item, dict) and (item.get("platform") or item.get("name"))
        ]
    else:
        parsing_keys = []

    existing_set = set(hyp_keys + parsing_keys)

    # Load custom order or fallback with VMware ESXi first
    saved_order = rules.get("hypervisor_platform_order") or []
    ordered_platforms = [p for p in saved_order if p in existing_set]
    for p in ["VMware ESXi", "Proxmox VE"]:
        if p in existing_set and p not in ordered_platforms:
            ordered_platforms.append(p)
    for p in sorted(list(existing_set)):
        if p not in ordered_platforms:
            ordered_platforms.append(p)

    all_platforms = ordered_platforms if ordered_platforms else ["VMware ESXi"]

    CREATE_OPTION = "+ Create New Platform..."
    dropdown_options = all_platforms + [CREATE_OPTION]

    cur_sel = st.session_state.get("sel_unified_platform", all_platforms[0])
    if cur_sel not in dropdown_options:
        cur_sel = all_platforms[0]

    c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
    with c_hdr_left:
        st.caption("Manage hypervisor platforms, standardized NetBox description templates, token aliases, and AI parsing rules.")
    with c_hdr_right:
        st.markdown(
            f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
            f"{len(all_platforms)} platforms</div>",
            unsafe_allow_html=True
        )

    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)

    if "pending_hypervisor_platform" in st.session_state:
        cur_sel = st.session_state.pop("pending_hypervisor_platform")
        st.session_state["sel_unified_platform"] = cur_sel
        st.session_state["sel_hypervisor_platform_choice"] = cur_sel

    col_plat_sel, col_plat_actions = st.columns([7.8, 2.2], vertical_alignment="bottom")
    with col_plat_sel:
        sel_choice = st.selectbox(
            "Target Platform",
            dropdown_options,
            index=dropdown_options.index(cur_sel),
            key="sel_hypervisor_platform_choice"
        )
        st.session_state["sel_unified_platform"] = sel_choice

    with col_plat_actions:
        c_up, c_dn, c_del = st.columns(3)
        if sel_choice in all_platforms and sel_choice != CREATE_OPTION:
            cur_idx = all_platforms.index(sel_choice)
            if c_up.button("⬆️", key="btn_plat_order_up", disabled=(cur_idx == 0), help="Move Platform Up", width="stretch"):
                all_platforms[cur_idx - 1], all_platforms[cur_idx] = all_platforms[cur_idx], all_platforms[cur_idx - 1]
                rules["hypervisor_platform_order"] = all_platforms
                save_naming_rules(rules, source="Reorder Hypervisor Platforms")
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                rules.clear()
                rules.update(fresh)
                SSM.set_naming_rules(fresh.copy())
                SSM.refresh_naming_rules()
                st.session_state["sel_unified_platform"] = sel_choice
                st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                st.rerun()

            if c_dn.button("⬇️", key="btn_plat_order_dn", disabled=(cur_idx == len(all_platforms) - 1), help="Move Platform Down", width="stretch"):
                all_platforms[cur_idx + 1], all_platforms[cur_idx] = all_platforms[cur_idx], all_platforms[cur_idx + 1]
                rules["hypervisor_platform_order"] = all_platforms
                save_naming_rules(rules, source="Reorder Hypervisor Platforms")
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                rules.clear()
                rules.update(fresh)
                SSM.set_naming_rules(fresh.copy())
                SSM.refresh_naming_rules()
                st.session_state["sel_unified_platform"] = sel_choice
                st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                st.rerun()

            if c_del.button("🗑️", key="btn_plat_del", disabled=(len(all_platforms) <= 1), help=f"Delete platform '{sel_choice}'", width="stretch"):
                hyp_presets.pop(sel_choice, None)
                parsing_presets.pop(sel_choice, None)
                rules["hypervisor_presets"] = hyp_presets
                rules["topology_parsing_presets"] = parsing_presets
                rules["hypervisor_platform_order"] = [p for p in all_platforms if p != sel_choice]
                save_naming_rules(rules, source=f"Delete Platform {sel_choice}")
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                rules.clear()
                rules.update(fresh)
                SSM.set_naming_rules(fresh.copy())
                SSM.refresh_naming_rules()

                st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                st.session_state["pending_hypervisor_platform"] = all_platforms[0]
                st.toast(f"✅ Deleted platform '{sel_choice}'!", icon="🔄")
                st.rerun()

    if sel_choice == CREATE_OPTION:
        st.markdown("##### ➕ Create New Platform Preset")
        new_plat_name = st.text_input("New Platform Name", placeholder="e.g. Nutanix AHV, OpenStack").strip()
        c_add_btn, c_cancel_btn = st.columns([2, 2])
        with c_add_btn:
            if st.button("➕ Create Platform", type="primary", width="stretch"):
                if not new_plat_name:
                    st.error("Platform name cannot be empty.")
                elif new_plat_name in all_platforms:
                    st.warning(f"Platform '{new_plat_name}' already exists.")
                else:
                    hyp_presets[new_plat_name] = [
                        {"code": "Uplink", "label": "Physical Uplink", "pattern": "<interface> - <parent> <purpose>"},
                        {"code": "Default", "label": "Default Interface", "pattern": "<interface> (<purpose>)"}
                    ]
                    parsing_presets[new_plat_name] = {
                        "platform": new_plat_name,
                        "aliases": {},
                        "instructions": f"[PLATFORM ARCHITECTURE: {new_plat_name.upper()}]\n1. Extract network topology into atomic attributes.",
                        "export_prompt": f"You are an expert network engineer specializing in {new_plat_name}...",
                    }
                    rules["hypervisor_presets"] = hyp_presets
                    rules["topology_parsing_presets"] = parsing_presets
                    save_naming_rules(rules, source=f"Create Platform {new_plat_name}")
                    SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                    st.session_state["sel_unified_platform"] = new_plat_name
                    st.toast(f"✅ Platform '{new_plat_name}' created!", icon="💾")
                    st.rerun()
        with c_cancel_btn:
            if st.button("❌ Cancel", width="stretch"):
                st.session_state["pending_hypervisor_platform"] = all_platforms[0]
                st.rerun()
        return

    active_plat = sel_choice
    plat_patterns = hyp_presets.get(active_plat, [])
    raw_parsing = parsing_presets.get(active_plat, {})
    if isinstance(raw_parsing, str):
        plat_parsing = {"platform": active_plat, "aliases": {}, "instructions": raw_parsing, "export_prompt": ""}
    elif isinstance(raw_parsing, dict):
        plat_parsing = dict(raw_parsing)
    else:
        plat_parsing = {}

    # Ensure aliases key exists
    if "aliases" not in plat_parsing or not isinstance(plat_parsing.get("aliases"), dict):
        plat_parsing["aliases"] = {}

    default_fallback = DEFAULT_PARSING_PRESETS.get(active_plat, {})
    if not plat_parsing.get("instructions"):
        plat_parsing["instructions"] = default_fallback.get("instructions", "")
    if not plat_parsing.get("export_prompt"):
        plat_parsing["export_prompt"] = default_fallback.get("export_prompt", "")

    # ──────────────────────────────────────────────────────────────────────
    # Section 1: NetBox Description Templates
    # ──────────────────────────────────────────────────────────────────────
    with st.expander(f"🏷️ 1. Description Templates ({len(plat_patterns)} templates)", expanded=False):
        HYP_TPL_COLS = [1.2, 2.2, 4.4, 0.8, 1.4]
        c_h0, c_h1, c_h2, c_h3, c_h4 = st.columns(HYP_TPL_COLS, vertical_alignment="center")
        with c_h0:
            st.markdown("**Code**")
        with c_h1:
            st.markdown("**Label**")
        with c_h2:
            st.markdown("**Pattern Template**")
        with c_h3:
            st.markdown("**Hide**")
        with c_h4:
            st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

        updated_patterns = []
        rows_to_delete = []
        move_up_idx = None
        move_down_idx = None

        for idx, row in enumerate(plat_patterns):
            c0, c1, c2, c3, c4 = st.columns(HYP_TPL_COLS, vertical_alignment="center")
            c_code = c0.text_input("Code", value=row.get("code", ""), key=f"hyp_code_{active_plat}_{idx}", label_visibility="collapsed")
            c_lbl = c1.text_input("Label", value=row.get("label", ""), key=f"hyp_lbl_{active_plat}_{idx}", label_visibility="collapsed")
            c_pat = c2.text_input("Pattern", value=row.get("pattern", ""), key=f"hyp_pat_{active_plat}_{idx}", label_visibility="collapsed")
            c_hide = c3.checkbox(f"Hide {row.get('code', '')}", value=bool(row.get("hidden", False)), key=f"hyp_hide_{active_plat}_{idx}", label_visibility="collapsed")

            with c4:
                btn_c1, btn_c2, btn_c3 = st.columns(3)
                if idx > 0:
                    if btn_c1.button("⬆️", key=f"hyp_up_{active_plat}_{idx}", help="Move Up"):
                        move_up_idx = idx
                if idx < len(plat_patterns) - 1:
                    if btn_c2.button("⬇️", key=f"hyp_dn_{active_plat}_{idx}", help="Move Down"):
                        move_down_idx = idx
                if btn_c3.button("🗑️", key=f"hyp_del_{active_plat}_{idx}", help="Delete Template"):
                    rows_to_delete.append(idx)

            updated_patterns.append({"code": c_code, "label": c_lbl, "pattern": c_pat, "hidden": bool(c_hide)})

        # Immediate delete/reorder actions (they save themselves)
        if rows_to_delete:
            for r_idx in sorted(rows_to_delete, reverse=True):
                updated_patterns.pop(r_idx)
            plat_patterns = updated_patterns
            hyp_presets[active_plat] = plat_patterns
            rules["hypervisor_presets"] = hyp_presets
            save_naming_rules(rules, source=f"Delete template in {active_plat}")
            SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
            st.rerun()

        if move_up_idx is not None:
            updated_patterns[move_up_idx - 1], updated_patterns[move_up_idx] = updated_patterns[move_up_idx], updated_patterns[move_up_idx - 1]
            hyp_presets[active_plat] = updated_patterns
            rules["hypervisor_presets"] = hyp_presets
            save_naming_rules(rules, source=f"Reorder templates in {active_plat}")
            SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
            st.rerun()

        if move_down_idx is not None:
            updated_patterns[move_down_idx + 1], updated_patterns[move_down_idx] = updated_patterns[move_down_idx], updated_patterns[move_down_idx + 1]
            hyp_presets[active_plat] = updated_patterns
            rules["hypervisor_presets"] = hyp_presets
            save_naming_rules(rules, source=f"Reorder templates in {active_plat}")
            SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
            st.rerun()

        with st.form(key=f"hyp_tpl_add_{active_plat}", clear_on_submit=True):
            ac0, ac1, ac2, ac3, ac4 = st.columns(HYP_TPL_COLS, vertical_alignment="center")
            with ac0:
                new_t_code = st.text_input("New Code", placeholder="e.g. Trunk", key=f"new_hyp_code_{active_plat}", label_visibility="collapsed")
            with ac1:
                new_t_label = st.text_input("New Label", placeholder="e.g. Trunk Adapter", key=f"new_hyp_lbl_{active_plat}", label_visibility="collapsed")
            with ac2:
                new_t_pattern = st.text_input("New Pattern", placeholder="<interface> (<vlan> VLAN)", key=f"new_hyp_pat_{active_plat}", label_visibility="collapsed")
            with ac3:
                new_t_hide = st.checkbox("Hide new template", value=False, key=f"new_hyp_hide_{active_plat}", label_visibility="collapsed")
            with ac4:
                add_tpl = st.form_submit_button("➕ Add", width="stretch")

        if add_tpl:
            if new_t_code and new_t_pattern:
                updated_patterns.append({
                    "code": new_t_code,
                    "label": new_t_label or new_t_code,
                    "pattern": new_t_pattern,
                    "hidden": bool(new_t_hide),
                })
                hyp_presets[active_plat] = updated_patterns
                rules["hypervisor_presets"] = hyp_presets
                save_naming_rules(rules, source=f"Add template to {active_plat}")
                SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                st.rerun()

        st.divider()
        col_save_s1, col_reset_s1 = st.columns(2, vertical_alignment="center")
        with col_save_s1:
            if st.button("💾 Save & Apply Changes", key=f"btn_save_s1_{active_plat}", type="primary", width="stretch"):
                hyp_presets[active_plat] = updated_patterns
                rules["hypervisor_presets"] = hyp_presets
                save_naming_rules(rules, source=f"Save Section 1 for {active_plat}")
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                rules.clear()
                rules.update(fresh)
                SSM.set_naming_rules(fresh.copy())
                SSM.refresh_naming_rules()
                st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                st.toast(f"✅ Section 1 (Description Templates) for '{active_plat}' saved & applied!", icon="💾")
                st.rerun()
        with col_reset_s1:
            if st.button("🔄 Reset to Defaults", key=f"btn_reset_s1_{active_plat}", width="stretch"):
                default_hyp = DEFAULT_HYPERVISOR_PRESETS.get(active_plat, [])
                hyp_presets[active_plat] = [dict(t) for t in default_hyp]
                rules["hypervisor_presets"] = hyp_presets
                save_naming_rules(rules, source=f"Reset Section 1 for {active_plat}")
                SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                _clear_session_state_prefixes(
                    f"hyp_code_{active_plat}_", f"hyp_lbl_{active_plat}_",
                    f"hyp_pat_{active_plat}_", f"hyp_hide_{active_plat}_",
                    f"new_hyp_code_{active_plat}", f"new_hyp_lbl_{active_plat}",
                    f"new_hyp_pat_{active_plat}", f"new_hyp_hide_{active_plat}",
                )
                st.toast(f"✅ Section 1 reset to defaults for '{active_plat}'!", icon="🔄")
                st.rerun()

    # ──────────────────────────────────────────────────────────────────────
    # Section 2: Platform Token Aliases
    # ──────────────────────────────────────────────────────────────────────
    aliases = plat_parsing.get("aliases", {})
    if not isinstance(aliases, dict):
        aliases = {}
    alias_items = list(aliases.items())

    with st.expander(f"🔗 2. Platform Token Aliases ({len(alias_items)})", expanded=False):
        st.caption("Map canonical tokens to their platform-specific aliases. When a token or any of its aliases appears in OCR output, all mapped values are synchronized bidirectionally.")

        col_canon, col_alias, col_del = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
        with col_canon:
            st.markdown("**Canonical Token**")
        with col_alias:
            st.markdown("**Platform Aliases (comma-separated)**")
        with col_del:
            pass

        updated_aliases = {}
        for idx, (canonical, alias_list) in enumerate(alias_items):
            alias_str = ", ".join(alias_list) if isinstance(alias_list, list) else str(alias_list)
            c_col, a_col, d_col = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
            with c_col:
                new_canon = st.text_input(
                    "Canonical Token", value=canonical,
                    key=f"plat_alias_canon_{active_plat}_{idx}",
                    label_visibility="collapsed",
                ).strip()
            with a_col:
                new_alias = st.text_input(
                    "Aliases", value=alias_str,
                    key=f"plat_alias_val_{active_plat}_{idx}",
                    label_visibility="collapsed",
                ).strip()
            with d_col:
                if _render_centered_del_btn(f"plat_alias_del_{active_plat}_{idx}", "Delete alias mapping"):
                    updated_aliases.pop(canonical, None)
            if new_canon:
                alias_list_new = [a.strip() for a in new_alias.split(",") if a.strip()] if new_alias else []
                updated_aliases[new_canon] = alias_list_new

        # Inline add-row form for aliases (clear_on_submit avoids layout crashes)
        with st.form(key=f"plat_alias_add_{active_plat}", clear_on_submit=True):
            ca1, ca2, ca3 = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
            with ca1:
                new_canon = st.text_input("New Canonical", value="", key=f"plat_new_canon_{active_plat}", placeholder="e.g. parent", label_visibility="collapsed")
            with ca2:
                new_alias = st.text_input("New Aliases", value="", key=f"plat_new_alias_{active_plat}", placeholder="e.g. v_switch, bridge", label_visibility="collapsed")
            with ca3:
                add_alias = st.form_submit_button("➕ Add", width='stretch', help="Add new alias mapping")
            if add_alias:
                if new_canon.strip():
                    alias_list_new = [a.strip() for a in new_alias.split(",") if a.strip()] if new_alias.strip() else []
                    updated_aliases[new_canon.strip()] = alias_list_new
                    plat_parsing["aliases"] = updated_aliases
                    parsing_presets[active_plat] = plat_parsing
                    rules["topology_parsing_presets"] = parsing_presets
                    save_naming_rules(rules, source=f"Platform Presets: Add alias {active_plat}")
                    SSM.set_naming_rules(rules.copy())
                    st.toast("✅ Alias added & applied!", icon="💾")
                    st.rerun()
                else:
                    st.warning("⚠️ Enter a canonical token name.")

        st.divider()
        col_save_s2, col_reset_s2 = st.columns(2, vertical_alignment="center")
        with col_save_s2:
            if st.button("💾 Save & Apply Changes", key=f"btn_save_s2_{active_plat}", type="primary", width="stretch"):
                plat_parsing["aliases"] = updated_aliases
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Save Section 2 for {active_plat}")
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                rules.clear()
                rules.update(fresh)
                SSM.set_naming_rules(fresh.copy())
                SSM.refresh_naming_rules()
                st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                st.toast(f"✅ Section 2 (Token Aliases) for '{active_plat}' saved & applied!", icon="💾")
                st.rerun()
        with col_reset_s2:
            if st.button("🔄 Reset to Defaults", key=f"btn_reset_s2_{active_plat}", width="stretch"):
                default_pars = DEFAULT_PARSING_PRESETS.get(active_plat, {})
                if isinstance(default_pars, dict):
                    plat_parsing["aliases"] = dict(default_pars.get("aliases", {}))
                else:
                    plat_parsing["aliases"] = {}
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Reset Section 2 for {active_plat}")
                SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                _clear_session_state_prefixes(
                    f"plat_alias_canon_{active_plat}_", f"plat_alias_val_{active_plat}_",
                    f"plat_alias_del_{active_plat}_", f"plat_new_canon_{active_plat}",
                    f"plat_new_alias_{active_plat}",
                )
                st.toast(f"✅ Section 2 reset to defaults for '{active_plat}'!", icon="🔄")
                st.rerun()

    # ──────────────────────────────────────────────────────────────────────
    # Section 2.5: Relative Spatial Anchors & Local Privacy Redaction
    # ──────────────────────────────────────────────────────────────────────
    with st.expander("📐 2.5. Relative Spatial Anchors & Local Privacy Redaction", expanded=False):
        _render_spatial_anchors_redaction_editor(plat_parsing, active_plat)
        st.divider()
        col_save_s25, col_reset_s25 = st.columns(2, vertical_alignment="center")
        with col_save_s25:
            if st.button("💾 Save & Apply Changes", key=f"btn_save_s25_{active_plat}", type="primary", width="stretch"):
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Save Section 2.5 for {active_plat}")
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                rules.clear()
                rules.update(fresh)
                SSM.set_naming_rules(fresh.copy())
                SSM.refresh_naming_rules()
                st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                st.toast(f"✅ Section 2.5 (Spatial Anchors) for '{active_plat}' saved & applied!", icon="💾")
                st.rerun()
        with col_reset_s25:
            if st.button("🔄 Reset to Defaults", key=f"btn_reset_s25_{active_plat}", width="stretch"):
                default_pars = DEFAULT_PARSING_PRESETS.get(active_plat, {})
                if isinstance(default_pars, dict):
                    default_sp = default_pars.get("spatial_anchors_and_redaction", {})
                    if default_sp:
                        plat_parsing["spatial_anchors_and_redaction"] = dict(default_sp)
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Reset Section 2.5 for {active_plat}")
                SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                _clear_session_state_prefixes(f"spat_")
                st.toast(f"✅ Section 2.5 reset to defaults for '{active_plat}'!", icon="🔄")
                st.rerun()

    # ──────────────────────────────────────────────────────────────────────
    # Section 3: AI Parsing Rules & Invariants
    # ──────────────────────────────────────────────────────────────────────
    with st.expander("🧠 3. AI Parsing Rules & Invariants", expanded=False):
        st.caption("Complete system instructions injected into the LLM prompt during OCR topology parsing. Covers OCR normalization, token de-concatenation, topology inheritance, and mandatory atomic JSON attributes.")

        inst_val = plat_parsing.get("instructions", "")
        instructions_input = st.text_area(
            "Parsing Instructions (English System Prompt Rules)",
            value=inst_val,
            height=340,
            key=f"txt_instructions_{active_plat}",
            help="Platform-specific extraction and reconciliation rules injected during OCR topology parsing."
        )

        # ── AI Assistant: Generate Platform Rules ────────────────────────
        with st.expander("✨ AI Assistant: Generate Platform Rules", expanded=False):
            st.caption("Automatically generate parsing instructions and description patterns for new hypervisors.")
            c_ai_in, c_ai_btn = st.columns([4, 1.5], vertical_alignment="center")
            ai_plat_input = c_ai_in.text_input("Hypervisor / Platform", placeholder="e.g. Cisco NX-OS, OpenStack", label_visibility="collapsed", key="txt_ai_plat_gen")
            if c_ai_btn.button("Generate Platform Rules", key="btn_gen_ai_plat_rules", width="stretch"):
                target_p = ai_plat_input.strip()
                if not target_p:
                    st.warning("Please enter a platform name.")
                else:
                    with st.spinner(f"Generating rules for {target_p}..."):
                        try:
                            ai_prompt = (
                                f"Generate network topology parsing instructions and NetBox description patterns for hypervisor/platform: '{target_p}'.\n"
                                "Respond strictly with a JSON object with two keys:\n"
                                "1. 'instructions': A well-structured, multi-line English specification for OCR topology diagrams. "
                                "Must use clear headings, double newlines between sections, and clean bullet points. Follow this structure strictly:\n\n"
                                f"[PLATFORM ARCHITECTURE: {target_p.upper()}]\n\n"
                                "1. OCR TEXT & NORMALIZATION:\n"
                                "- Normalize adapter names and port labels...\n"
                                "- Strip vendor-specific generic prefixes...\n\n"
                                "2. TOPOLOGY INHERITANCE & ZERO ORPHAN POLICY:\n"
                                "- Topological parent-child mapping rules...\n"
                                "- Rules for active vs standby uplinks...\n\n"
                                "3. MANDATORY ATOMIC JSON ATTRIBUTES:\n"
                                "- interface: Clean interface identifier only...\n"
                                "- parent: Connected switch/bridge name...\n\n"
                                "2. 'export_prompt': A well-formatted, multi-line prompt template for external AI (ChatGPT/Claude), "
                                "using blank lines between bullet points and paragraphs.\n"
                                "Do not include markdown fences outside the JSON."
                            )
                            res_raw = call_ai(ai_prompt, active_model)
                            clean_json = re.sub(r"^```json\s*|^```\s*|```$", "", res_raw.strip(), flags=re.MULTILINE)
                            data = json.loads(clean_json)

                            gen_instructions = data.get("instructions", f"[PLATFORM ARCHITECTURE: {target_p.upper()}]")
                            gen_export_prompt = data.get("export_prompt", f"You are an expert engineer for {target_p}...")

                            if target_p not in hyp_presets:
                                hyp_presets[target_p] = [
                                    {"code": "Uplink", "label": "Physical Uplink", "pattern": "<interface> - <parent> <purpose>"},
                                    {"code": "Default", "label": "Default Interface", "pattern": "<interface> (<purpose>)"}
                                ]
                            parsing_presets[target_p] = {
                                "platform": target_p,
                                "instructions": gen_instructions,
                                "export_prompt": gen_export_prompt
                            }
                            rules["hypervisor_presets"] = hyp_presets
                            rules["topology_parsing_presets"] = parsing_presets

                            save_naming_rules(rules, source=f"AI Generated {target_p}")
                            fresh_rules = SSM.get_cached_naming_rules() or load_naming_rules()
                            rules.clear()
                            rules.update(fresh_rules)
                            SSM.set_naming_rules(fresh_rules.copy())
                            SSM.refresh_naming_rules()

                            st.session_state.pop(f"txt_instructions_{target_p}", None)
                            st.session_state.pop(f"txt_export_prompt_{target_p}", None)

                            st.session_state["pending_hypervisor_platform"] = target_p
                            st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                            st.toast(f"✅ Generated and loaded rules for '{target_p}'!", icon="💾")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to generate platform rules: {e}")

        st.divider()
        col_save_s3, col_reset_s3 = st.columns(2, vertical_alignment="center")
        with col_save_s3:
            if st.button("💾 Save & Apply Changes", key=f"btn_save_s3_{active_plat}", type="primary", width="stretch"):
                plat_parsing["instructions"] = instructions_input
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Save Section 3 for {active_plat}")
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                rules.clear()
                rules.update(fresh)
                SSM.set_naming_rules(fresh.copy())
                SSM.refresh_naming_rules()
                st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                st.toast(f"✅ Section 3 (AI Parsing Rules) for '{active_plat}' saved & applied!", icon="💾")
                st.rerun()
        with col_reset_s3:
            if st.button("🔄 Reset to Defaults", key=f"btn_reset_s3_{active_plat}", width="stretch"):
                default_pars = DEFAULT_PARSING_PRESETS.get(active_plat, {})
                if isinstance(default_pars, dict):
                    plat_parsing["instructions"] = default_pars.get("instructions", "")
                else:
                    plat_parsing["instructions"] = ""
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Reset Section 3 for {active_plat}")
                SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                _clear_session_state_prefixes(f"txt_instructions_{active_plat}")
                st.toast(f"✅ Section 3 reset to defaults for '{active_plat}'!", icon="🔄")
                st.rerun()

    # ──────────────────────────────────────────────────────────────────────
    # Section 4: AI Onboarding Blueprint (Read-Only Guide)
    # ──────────────────────────────────────────────────────────────────────
    with st.expander("📘 4. AI Onboarding Blueprint Guide", expanded=False):
        st.caption("A complete reference for onboarding any hypervisor platform into NetBox Hub. Use the blueprint prompt below to generate all 4 configuration sections for a new platform.")
        st.markdown("""
**The AI Onboarding Blueprint has 4 components:**

### 🏷️ 1. Description Templates
Interface naming patterns for NetBox objects. Each template maps a generic pattern to a specific network element type:
- **Uplink**: Physical uplink naming (e.g. `<vmnic> - <v_switch> <purpose>`)
- **Bridge**: Linux/virtual switch bridges (e.g. `<bridge> - <purpose>`)
- **PortGroup**: Port group or VLAN interface names (e.g. `PG-<port_group>`)
- **Default**: Fallback pattern for unclassified interfaces

### 🔗 2. Platform Token Aliases
Vendor-specific terminology mapped to canonical JSON keys so the parser normalizes diverse labels:
- `parent` → [switch, bridge, v_switch, bond]
- `interface` → [eth, nic, vmnic, port, bond]
- `speed` → [speed, link_speed, throughput]

### 📐 2.5. Spatial Anchors & Redaction
Screenshot container headers and local privacy masking for OCR-driven topology extraction:
- **Container headers** (`container_header`): Text that begins a logical network group (e.g. "Virtual Switch", "Network Bridge")
- **Adapter columns** (`adapter_column`): Column headers identifying physical adapter lists (e.g. "Physical Adapters", "Interfaces")
- **Redaction rules**: Deterministic masking of IPv4, MAC addresses, and internal domain names before data leaves the device

### 🧠 3. AI Parsing Rules & Invariants
LLM system instructions and atomic attribute requirements for topology parsing:
- OCR text cleaning and vendor prefix normalization
- Topology parent-child inheritance rules
- Mandatory JSON attributes: `interface`, `parent`, `purpose`, `uplink_role`, `slot`, `speed`
""", unsafe_allow_html=True)

        st.divider()

        col_blueprint, col_reset_s4 = st.columns([3, 1], vertical_alignment="center")
        with col_blueprint:
            if st.button(
                "📋 Copy AI Blueprint Prompt",
                key=f"btn_blueprint_{active_plat}",
                type="primary",
                width="stretch",
                help="Generate a complete prompt to paste into ChatGPT/Claude for onboarding a new platform.",
            ):
                blueprint_prompt = (
                    "You are an enterprise network virtualization architect. "
                    f"I need to onboard a new hypervisor/virtualization platform into NetBox Hub: \"{active_plat}\".\n\n"
                    "Please generate the complete configuration specification for this platform "
                    "strictly following the schema below.\n\n"
                    "Respond with a JSON object containing:\n\n"
                    "1. \"templates\": List of NetBox description patterns, e.g.:\n"
                    "  [\n"
                    '    {\"code\": \"Uplink\", \"label\": \"Physical Uplink\", '
                    '\"pattern\": \"<interface> - <parent> <purpose>\"},\n'
                    '    {\"code\": \"Default\", \"label\": \"Default Interface\", '
                    '\"pattern\": \"<interface> (<purpose>)\"}\n'
                    "  ]\n\n"
                    "2. \"aliases\": Dictionary mapping canonical keys "
                    "('parent', 'interface', 'speed') to vendor terms:\n"
                    "  {\n"
                    '    \"parent\": [\"switch\", \"bridge\"],\n'
                    '    \"interface\": [\"eth\", \"nic\"],\n'
                    '    \"speed\": [\"speed\", \"link_speed\"]\n'
                    "  }\n\n"
                    "3. \"spatial_anchors\": List of anchor definitions for UI screenshot grouping:\n"
                    "  [\n"
                    '    {\"role\": \"container_header\", '
                    '"patterns\": [\"Virtual Switch\", \"Network Bridge\"]},\n'
                    '    {\"role\": \"adapter_column\", '
                    '"patterns\": [\"Physical Adapters\", \"Interfaces\"]}\n'
                    "  ]\n\n"
                    "4. \"instructions\": Comprehensive English parsing invariants covering:\n"
                    "  - OCR text cleaning and vendor prefix normalization\n"
                    "  - Topology parent-child inheritance\n"
                    "  - Mandatory JSON attributes: interface, parent, purpose, "
                    "uplink_role, slot, speed\n"
                )
                st.code(blueprint_prompt, language="text")
                st.caption("*Copy the prompt above and paste it into ChatGPT, Claude, or any LLM to generate the full onboarding spec for* `" + active_plat + "`*.*")

        with col_reset_s4:
            if st.button("🔄 Reset to Defaults", key=f"btn_reset_s4_{active_plat}", width="stretch"):
                st.session_state[f"exp_s4_{active_plat}"] = False
                st.rerun()


def _preset_type_editor(kind: str, presets: list, rules: dict, prefix: str, card_title: str, card_caption: str,
                          section: str = "presets", section_label: str = "Presets", expanded: bool = False,
                          wrap_expander: bool = True) -> None:
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
        _save_presets(rules, section=section, section_label=section_label)
        return
    _pending_swap_key = f"_pending_swap_{kind}"

    expander_context = st.expander(card_title, expanded=expanded) if wrap_expander else st.container()
    with expander_context:
        # Preset count badge (right-aligned, same row as caption) — only when we own the expander
        if wrap_expander:
            c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
            with c_hdr_left:
                if card_caption and card_caption.strip():
                    st.caption(card_caption)
            with c_hdr_right:
                st.markdown(
                    f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
                    f"{len(presets)} presets</div>",
                    unsafe_allow_html=True
                )

        # Inject dynamic styling to guarantee Action buttons fit cleanly without squeezing
        _inject_preset_table_style()

        # Headers - balanced with compact, equalized Action column
        h_code, h_label, h_pattern, h_act = st.columns(PRESET_COLS, vertical_alignment="center")
        with h_code:
            st.markdown("**Code**")
        with h_label:
            st.markdown("**Label**")
        with h_pattern:
            st.markdown("**Pattern Template**")
        with h_act:
            st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

        updated = []
        patterns_updates = {}
        positions = {}

        nonce = st.session_state.get("standards_nonce", 0)
        if presets:
            for idx, p in enumerate(presets):
                code = p.get("code", "")
                label = p.get("label", "")
                pkey = p.get("pattern_key", "")
                tpl = patterns.get(pkey, "")

                c_code, c_label, c_pattern, c_actions = st.columns(PRESET_COLS, vertical_alignment="center")
                with c_code:
                    ncode = st.text_input("Code", value=code, key=f"{kind}_pre_{nonce}_code_{idx}", label_visibility="collapsed").strip()
                with c_label:
                    nlbl = st.text_input("Label", value=label, key=f"{kind}_pre_{nonce}_lbl_{idx}", label_visibility="collapsed").strip()
                with c_pattern:
                    ntpl = st.text_input("Pattern Template", value=tpl, key=f"{kind}_pre_{nonce}_tpl_{idx}", label_visibility="collapsed").strip()
                with c_actions:
                    col_up, col_down, col_del = st.columns(PRESET_ACTION_COLS)
                    with col_up:
                        if idx > 0:
                            if st.button("⬆️", key=f"{kind}_pre_up_{idx}", help="Move up"):
                                st.session_state[_pending_swap_key] = (idx, idx - 1)
                                st.rerun()
                        else:
                            st.empty()
                    with col_down:
                        if idx < len(presets) - 1:
                            if st.button("⬇️", key=f"{kind}_pre_down_{idx}", help="Move down"):
                                st.session_state[_pending_swap_key] = (idx, idx + 1)
                                st.rerun()
                        else:
                            st.empty()
                    with col_del:
                        if st.button("🗑️", key=f"{kind}_pre_del_{idx}", help="Delete item"):
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
                    positions[idx] = len(updated)
                    updated.append({
                        "code": ncode,
                        "label": nlbl or ncode,
                        "pattern_key": final_pkey,
                        "description": p.get("description", ""),
                    })
        else:
            st.info("No presets defined. Add one below.")

        # A pending reorder is applied to the fully-edited list so inline edits
        # made in the same interaction are preserved.
        pending_swap = st.session_state.pop(_pending_swap_key, None)
        if pending_swap is not None:
            src, dst = pending_swap
            src_pos = positions.get(src)
            dst_pos = positions.get(dst)
            if src_pos is not None and dst_pos is not None:
                updated[src_pos], updated[dst_pos] = updated[dst_pos], updated[src_pos]
            final_patterns = dict(patterns)
            final_patterns.update(patterns_updates)
            rules["naming_patterns"] = final_patterns
            rules[key_field] = list(updated)
            _save_presets(rules, section=section, section_label=section_label)
            return

        if st.session_state.pop(f"{kind}_preset_min_one", False):
            st.warning("⚠️ At least one preset must remain. Delete a different entry first.")

        col_save, col_reset = st.columns(2)
        with col_save:
            saved_presets = st.button(
                "💾 Save & Apply Changes", key=f"{kind}_preset_save", type="primary",
                width='stretch',
            )
        with col_reset:
            reset_presets = st.button(
                "🔄 Reset to Defaults", key=f"{kind}_preset_reset",
                width='stretch',
            )

        # Inline Add Row (Unified Card UI with standard '➕ Add' button)
        with st.form(key=f"{kind}_add_form", clear_on_submit=True):
            is_esxi = kind == "esxi_network"
            code_ph = "e.g. DSwitch" if is_esxi else "e.g. SAN"
            lbl_ph = "e.g. Distributed Switch Uplink" if is_esxi else "e.g. SAN Storage (SAN)"
            tpl_ph = "e.g. <vmnic> - <v_switch> (<status>)" if is_esxi else "e.g. SAN<country><site><seq>"
            ca1, ca2, ca3, ca4 = st.columns(PRESET_COLS, vertical_alignment="center")
            with ca1:
                new_code = st.text_input("Code", value="", placeholder=code_ph, key=f"{kind}_new_code", label_visibility="collapsed").strip()
            with ca2:
                new_lbl = st.text_input("Display Label", value="", placeholder=lbl_ph, key=f"{kind}_new_lbl", label_visibility="collapsed").strip()
            with ca3:
                new_tpl = st.text_input("Pattern Template", value="", placeholder=tpl_ph, key=f"{kind}_new_tpl", label_visibility="collapsed").strip()
            with ca4:
                add_preset = st.form_submit_button("➕ Add", width='stretch', help=f"Add new {kind} preset")

    if reset_presets:
        _clear_session_state_prefixes(f"{kind}_")
        _reset_presets(kind, rules, section=section, section_label=section_label)
        return

    if add_preset:
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
        if kind == "esxi_network":
            for p_item in final_presets:
                pk = p_item.get("pattern_key", "")
                tpl_val = final_patterns.get(pk, "")
                if pk.startswith("esxinet_"):
                    alt_pk = pk.replace("esxinet_", "esxi_")
                elif pk.startswith("esxi_"):
                    alt_pk = pk.replace("esxi_", "esxinet_")
                else:
                    alt_pk = f"esxi_{pk}"
                final_patterns[pk] = tpl_val
                final_patterns[alt_pk] = tpl_val
                rules[pk] = tpl_val
                rules[alt_pk] = tpl_val
                p_item["pattern"] = tpl_val
                p_item["pattern_template"] = tpl_val
        rules["naming_patterns"] = final_patterns
        rules[key_field] = final_presets
        _clear_session_state_prefixes(f"{kind}_new_code", f"{kind}_new_lbl", f"{kind}_new_tpl")
        _save_presets(rules, section=section, section_label=section_label)
        return

    if saved_presets:
        final_presets = list(updated)
        final_patterns = dict(patterns)
        final_patterns.update(patterns_updates)
        if not final_presets:
            st.error("⚠️ At least one preset is required.")
            return
        if kind == "esxi_network":
            for p_item in final_presets:
                pk = p_item.get("pattern_key", "")
                tpl_val = final_patterns.get(pk, "")
                if pk.startswith("esxinet_"):
                    alt_pk = pk.replace("esxinet_", "esxi_")
                elif pk.startswith("esxi_"):
                    alt_pk = pk.replace("esxi_", "esxinet_")
                else:
                    alt_pk = f"esxi_{pk}"
                final_patterns[pk] = tpl_val
                final_patterns[alt_pk] = tpl_val
                rules[pk] = tpl_val
                rules[alt_pk] = tpl_val
                p_item["pattern"] = tpl_val
                p_item["pattern_template"] = tpl_val
        rules["naming_patterns"] = final_patterns
        rules[key_field] = final_presets
        _save_presets(rules, section=section, section_label=section_label)
def _reset_presets(kind: str, rules: dict, section: str = "presets", section_label: str = "Presets") -> None:
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
    _save_presets(rules, section=section, section_label=section_label)


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

    c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
    with c_hdr_left:
        st.caption("Manage physical hypervisor host naming patterns and presets.")
    with c_hdr_right:
        st.markdown(
            f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
            f"{len(host_presets)} presets</div>",
            unsafe_allow_html=True
        )

    # Inject dynamic styling to guarantee Action buttons fit cleanly without squeezing
    _inject_preset_table_style()

    # Headers - balanced for dynamic scaling with protected Action width
    h_code, h_label, h_pattern, h_act = st.columns(PRESET_COLS, vertical_alignment="center")
    with h_code:
        st.markdown("**Code**")
    with h_label:
        st.markdown("**Label**")
    with h_pattern:
        st.markdown("**Pattern Template**")
    with h_act:
        st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

    updated = []
    patterns_updates = {}
    positions = {}
    stale_del = st.session_state.pop("_host_vm_del_idx", None)

    nonce = st.session_state.get("standards_nonce", 0)
    for idx, preset in enumerate(host_presets):
        pk = preset.get("pattern_key", "")
        tpl = patterns.get(pk, "")

        c_code, c_label, c_pattern, c_actions = st.columns(PRESET_COLS, vertical_alignment="center")
        with c_code:
            ncode = st.text_input("Code", value=preset.get("code") or "ESXi", key=f"host_{nonce}_{idx}_code", label_visibility="collapsed").strip()
        with c_label:
            nlbl = st.text_input("Label", value=preset.get("label") or "ESXi Host", key=f"host_{nonce}_{idx}_lbl", label_visibility="collapsed").strip()
        with c_pattern:
            ntpl = st.text_input("Pattern Template", value=tpl, key=f"host_{nonce}_{idx}_tpl", label_visibility="collapsed").strip()
        with c_actions:
            col_up, col_down, col_del = st.columns(PRESET_ACTION_COLS)
            with col_up:
                if idx > 0:
                    if st.button("⬆️", key=f"host_up_{idx}", help=f"Move {ncode or nlbl} up"):
                        st.session_state["_host_vm_swap"] = (idx, idx - 1)
                        st.rerun()
                else:
                    st.empty()
            with col_down:
                if idx < len(host_presets) - 1:
                    if st.button("⬇️", key=f"host_down_{idx}", help=f"Move {ncode or nlbl} down"):
                        st.session_state["_host_vm_swap"] = (idx, idx + 1)
                        st.rerun()
                else:
                    st.empty()
            with col_del:
                if st.button("🗑️", key=f"host_del_{idx}", help=f"Delete {ncode or nlbl}"):
                    if len(host_presets) > 1:
                        st.session_state["_host_vm_del_idx"] = idx
                        st.rerun()
                    else:
                        st.session_state["host_preset_min_one"] = True

        if stale_del == idx:
            continue
        final_pk = pk or make_preset_key(ncode or nlbl, "host_vm")
        if ntpl:
            patterns_updates[final_pk] = ntpl
        if final_pk:
            positions[idx] = len(updated)
            updated.append({
                "code": ncode or "ESXi",
                "label": nlbl or ncode or "ESXi Host",
                "pattern_key": final_pk,
                "description": preset.get("description", ""),
            })

    if st.session_state.pop("host_preset_min_one", False):
        st.warning("⚠️ At least one preset must remain. Delete a different entry first.")

    # A pending reorder is applied to the fully-edited list so inline edits
    # made in the same interaction are preserved.
    pending_swap = st.session_state.pop("_host_vm_swap", None)
    if pending_swap is not None or stale_del is not None:
        if pending_swap is not None:
            src, dst = pending_swap
            src_pos = positions.get(src)
            dst_pos = positions.get(dst)
            if src_pos is not None and dst_pos is not None:
                updated[src_pos], updated[dst_pos] = updated[dst_pos], updated[src_pos]
        rules["naming_patterns"] = {**patterns, **patterns_updates}
        rules["host_vm_presets"] = updated + vm_presets
        _save_presets(rules, section="hosts", section_label="Hosts Type Presets")
        return

    col_save, col_reset = st.columns(2)
    with col_save:
        saved = st.button("💾 Save & Apply Changes", key="host_preset_save", type="primary", width='stretch')
    with col_reset:
        reset = st.button("🔄 Reset to Defaults", key="host_preset_reset", width='stretch')

    with st.form(key="host_add_form", clear_on_submit=True):
        ca1, ca2, ca3, ca4 = st.columns(PRESET_COLS, vertical_alignment="center")
        with ca1:
            new_code = st.text_input("New Code", value="", placeholder="e.g. HYPV", key="host_new_code", label_visibility="collapsed").strip()
        with ca2:
            new_lbl = st.text_input("New Label", value="", placeholder="e.g. Hyper-V Host", key="host_new_lbl", label_visibility="collapsed").strip()
        with ca3:
            new_tpl = st.text_input("New Pattern Template", value="", placeholder="<site_prefix>hyp<seq>.<domain>", key="host_new_tpl", label_visibility="collapsed").strip()
        with ca4:
            add_preset = st.form_submit_button("➕ Add", width='stretch', help="Add new host preset")

    if reset:
        # Reset restores the canonical factory defaults for the host types
        # into the exact key this editor reads from ("host_vm_presets"),
        # persists them to disk immediately, and drops every cached host/preset
        # widget value so the table rebuilds from the defaults. VM presets
        # (pattern_key "vm_host") belong to the VM card and are preserved,
        # falling back to the canonical VM defaults when none exist.
        kept_vm = [copy.deepcopy(p) for p in vm_presets] or [
            copy.deepcopy(p) for p in DEFAULT_VM_PRESETS
        ]
        rules["host_vm_presets"] = copy.deepcopy(DEFAULT_HOST_TYPE_PRESETS) + kept_vm
        reset_patterns = dict(rules.get("naming_patterns") or {})
        for preset in DEFAULT_HOST_TYPE_PRESETS:
            pkey = preset.get("pattern_key", "")
            if pkey in DEFAULT_NAMING_PATTERNS:
                reset_patterns[pkey] = DEFAULT_NAMING_PATTERNS[pkey]
        rules["naming_patterns"] = reset_patterns
        save_naming_rules(rules, source="Hosts Type Presets: Reset to Defaults")
        SSM.set_naming_rules(rules.copy())
        st.toast("✅ Hosts Type Presets reset to defaults!", icon="🔄")
        st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
        st.session_state.pop("host_preset_min_one", None)
        st.session_state.pop("_host_vm_del_idx", None)
        st.session_state.pop("_host_vm_swap", None)
        _clear_session_state_prefixes("host_", "vm_", "preset_", "host_preset", "vm_preset")
        st.rerun()
        return

    if add_preset:
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
        _clear_session_state_prefixes("host_new_code", "host_new_lbl", "host_new_tpl")
        rules["naming_patterns"] = final_patterns
        rules["host_vm_presets"] = final_presets + vm_presets
        _save_presets(rules, section="hosts", section_label="Hosts Type Presets")
        return

    if saved:
        final_presets = list(updated)
        final_patterns = {**patterns, **patterns_updates}
        rules["naming_patterns"] = final_patterns
        rules["host_vm_presets"] = final_presets + vm_presets
        _save_presets(rules, section="hosts", section_label="Hosts Type Presets")


def _vm_editor(rules: dict) -> None:
    all_presets = list(get_host_vm_presets(rules))
    host_presets = [p for p in all_presets if str(p.get("pattern_key", "")) != "vm_host"]
    vm_presets = [p for p in all_presets if p.get("pattern_key") == "vm_host"]
    patterns = dict(rules.get("naming_patterns") or {})
    tpl = patterns.get("vm_host", "")

    c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
    with c_hdr_left:
        st.caption("Manage virtual machine roles (cvi, afs, sani, vlab) and their shared hostname template.")
    with c_hdr_right:
        st.markdown(
            f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
            f"{len(vm_presets)} presets</div>",
            unsafe_allow_html=True
        )

    # Inject dynamic styling to guarantee Action buttons fit cleanly without squeezing
    _inject_preset_table_style()

    # Headers - balanced for dynamic scaling with protected Action width
    h_code, h_label, h_pattern, h_act = st.columns(PRESET_COLS, vertical_alignment="center")
    with h_code:
        st.markdown("**Code**")
    with h_label:
        st.markdown("**Label**")
    with h_pattern:
        st.markdown("**Pattern Template**")
    with h_act:
        st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

    updated = []
    positions = {}
    stale_del = st.session_state.pop("_del_vm_role_idx", None)

    nonce = st.session_state.get("standards_nonce", 0)
    for idx, p in enumerate(vm_presets):
        c_code, c_label, c_pattern, c_actions = st.columns(PRESET_COLS, vertical_alignment="center")
        with c_code:
            ncode = st.text_input("Code", value=p.get("code", ""), key=f"vm_{nonce}_code_{idx}", label_visibility="collapsed").strip()
        with c_label:
            nlbl = st.text_input("Label", value=p.get("label", ""), key=f"vm_{nonce}_lbl_{idx}", label_visibility="collapsed").strip()
        with c_pattern:
            ntpl = st.text_input("Pattern Template", value=tpl, key=f"vm_{nonce}_tpl_{idx}", label_visibility="collapsed").strip()
        with c_actions:
            col_up, col_down, col_del = st.columns(PRESET_ACTION_COLS)
            with col_up:
                if idx > 0:
                    if st.button("⬆️", key=f"vm_role_up_{idx}", help=f"Move {p.get('code', '')} up"):
                        st.session_state["_vm_role_swap"] = (idx, idx - 1)
                        st.rerun()
                else:
                    st.empty()
            with col_down:
                if idx < len(vm_presets) - 1:
                    if st.button("⬇️", key=f"vm_role_down_{idx}", help=f"Move {p.get('code', '')} down"):
                        st.session_state["_vm_role_swap"] = (idx, idx + 1)
                        st.rerun()
                else:
                    st.empty()
            with col_del:
                if st.button("🗑️", key=f"vm_role_del_{idx}", help="Delete item"):
                    if len(vm_presets) > 1:
                        st.session_state["_del_vm_role_idx"] = idx
                        st.rerun()
                    else:
                        st.session_state["vm_preset_min_one"] = True

        if stale_del == idx:
            continue
        tpl = ntpl or tpl or "<country><site><role><seq>"
        p["code"] = ncode.lower() if ncode else p.get("code", "")
        p["label"] = nlbl or ncode or p.get("label", "")
        p["pattern_key"] = "vm_host"
        p["description"] = ""
        positions[idx] = len(updated)
        updated.append(dict(p))

    if st.session_state.pop("vm_preset_min_one", False):
        st.warning("⚠️ At least one role must remain. Delete a different entry first.")

    # A pending reorder is applied to the fully-edited list so inline edits
    # (including the shared pattern template) made in the same interaction
    # are preserved.
    pending_swap = st.session_state.pop("_vm_role_swap", None)
    if pending_swap is not None or stale_del is not None:
        if pending_swap is not None:
            src, dst = pending_swap
            src_pos = positions.get(src)
            dst_pos = positions.get(dst)
            if src_pos is not None and dst_pos is not None:
                updated[src_pos], updated[dst_pos] = updated[dst_pos], updated[src_pos]
        patterns["vm_host"] = tpl
        rules["naming_patterns"] = patterns
        rules["host_vm_presets"] = host_presets + updated
        _save_presets(rules, section="vm_roles", section_label="VM Role Presets")
        return

    c_save, c_reset = st.columns(2)
    with c_save:
        saved = st.button("💾 Save & Apply Changes", key="vm_save", type="primary", width='stretch')
    with c_reset:
        reset = st.button("🔄 Reset to Defaults", key="vm_reset", width='stretch')

    with st.form(key="vm_add_form", clear_on_submit=True):
        ca1, ca2, ca3, ca4 = st.columns(PRESET_COLS, vertical_alignment="center")
        with ca1:
            new_code = st.text_input("New Code", value="", key="vm_new_code", placeholder="e.g. cvi", label_visibility="collapsed").strip()
        with ca2:
            new_label = st.text_input("New Label", value="", key="vm_new_lbl", placeholder="e.g. Core Virtualization (cvi)", label_visibility="collapsed").strip()
        with ca3:
            new_tpl = st.text_input("New Pattern Template", value="", key="vm_new_tpl", placeholder="<country><site><role><seq>", label_visibility="collapsed").strip()
        with ca4:
            add_role = st.form_submit_button("➕ Add", width='stretch', help="Add new VM role")

    if reset:
        # Mirror of the HOSTS reset: only the VM presets are restored, the
        # host-type presets owned by the HOSTS card are left untouched.
        kept_hosts = [copy.deepcopy(p) for p in host_presets] or [
            copy.deepcopy(p) for p in DEFAULT_HOST_TYPE_PRESETS
        ]
        rules["host_vm_presets"] = kept_hosts + copy.deepcopy(DEFAULT_VM_PRESETS)
        vm_patterns = dict(rules.get("naming_patterns") or {})
        if "vm_host" in DEFAULT_NAMING_PATTERNS:
            vm_patterns["vm_host"] = DEFAULT_NAMING_PATTERNS["vm_host"]
        rules["naming_patterns"] = vm_patterns
        save_naming_rules(rules, source="VM Presets: Reset to Defaults")
        SSM.set_naming_rules(rules.copy())
        st.toast("✅ VM Role Presets reset to defaults!", icon="🔄")
        st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
        _clear_session_state_prefixes("host_", "vm_", "preset_", "host_preset", "vm_preset")
        st.rerun()
        return

    if add_role:
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
        rules["naming_patterns"] = patterns
        rules["host_vm_presets"] = host_presets + final
        _clear_session_state_prefixes("vm_new_code", "vm_new_lbl", "vm_new_tpl")
        _save_presets(rules, section="vm_roles", section_label="VM Role Presets")
        return

    if saved:
        final = list(updated)
        if not updated:
            st.warning("⚠️ At least one preset must remain.")
        patterns["vm_host"] = tpl or "<country><site><role><seq>"
        rules["naming_patterns"] = patterns
        rules["host_vm_presets"] = host_presets + final
        _save_presets(rules, section="vm_roles", section_label="VM Role Presets")


# Standardized 3-column ratio for Key-Value mappings: Key, Value, Action
VLAND_MAPPINGS_COLS = [4.0, 5.0, 1.2]

def _render_vlan_description_mappings_editor(rules: dict) -> None:

    mappings = dict(get_vlan_description_mappings(rules))

    st.caption(
        "Map each VLAN Role to its NetBox VLAN Description tag. When a role "
        "matches, its mapped value is used; otherwise the Role name itself is "
        "returned. These mappings are consulted by the IPAM tab's dynamic "
        "resolution logic."
    )

    with st.form(key="vlandesc_edit_form", clear_on_submit=False):
        pass  # placeholder removed — see below

    h_role, h_desc, h_del = st.columns(VLAND_MAPPINGS_COLS, vertical_alignment="center")
    with h_role:
        st.markdown("**Role**")
    with h_desc:
        st.markdown("**VLAN Description**")
    with h_del:
        pass

    items = list(mappings.items())
    updated = {}

    for idx, (role, desc) in enumerate(items):
        col_role, col_desc, col_act = st.columns(VLAND_MAPPINGS_COLS, vertical_alignment="center")
        with col_role:
            nrole = st.text_input(
                "Role", value=role, key=f"vlandesc_{idx}_role",
                label_visibility="collapsed",
            )
        with col_desc:
            ndesc = st.text_input(
                "VLAN Description", value=desc, key=f"vlandesc_{idx}_desc",
                label_visibility="collapsed",
            )
        with col_act:
            _render_preset_row_actions(
                idx=idx,
                total=len(items),
                items=items,
                key_prefix="vlandesc",
                on_reorder=lambda new_items: (
                    rules_to_save := dict(rules),
                    rules_to_save.update({"vlan_description_mappings": dict(new_items)}),
                    save_naming_rules(rules_to_save, source="VLAN Desc: Reorder"),
                    SSM.set_naming_rules(rules_to_save.copy()),
                    SSM.refresh_naming_rules(),
                    st.rerun()
                ),
                on_delete=lambda del_idx: (
                    new_mappings := {r: d for i, (r, d) in enumerate(items) if i != del_idx},
                    rules_to_save := dict(rules),
                    rules_to_save.update({"vlan_description_mappings": new_mappings}),
                    _save_vlan_desc_mappings(rules_to_save),
                    None
                )
            )

        role_key = nrole.strip()
        if role_key:
            updated[role_key] = ndesc.strip()

    col_save, col_reset = st.columns([1.2, 1.0])
    with col_save:
        saved = st.button("💾 Save & Apply Changes", key="vlandesc_save", type="primary", width="stretch")
    with col_reset:
        if st.button("🔄 Reset to Defaults", key="vlandesc_reset", width='stretch'):
            rules = dict(rules)
            rules["vlan_description_mappings"] = dict(DEFAULT_VLAN_DESCRIPTION_MAPPINGS)
            _save_vlan_desc_mappings(rules)

    if saved:
        if not updated:
            st.warning("⚠️ At least one mapping is required.")
        else:
            rules = dict(rules)
            rules["vlan_description_mappings"] = updated
            _save_vlan_desc_mappings(rules)

    with st.form(key="vlandesc_add_form", clear_on_submit=True):
        col_role_new, col_desc_new, col_add = st.columns(VLAND_MAPPINGS_COLS, vertical_alignment="center")
        with col_role_new:
            new_role = st.text_input(
                "New Role", value="", placeholder="e.g. Corporate WiFi",
                key="vlandesc_new_role", label_visibility="collapsed",
            )
        with col_desc_new:
            new_desc = st.text_input(
                "New Description", value="", placeholder="e.g. VIN_Corp",
                key="vlandesc_new_desc", label_visibility="collapsed",
            )
        with col_add:
            add_submitted = st.form_submit_button("➕ Add", width='stretch', help="Add new mapping")

        if add_submitted:
            if new_role.strip():
                rules = dict(rules)
                final_mappings = dict(updated)
                final_mappings[new_role.strip()] = new_desc.strip()
                rules["vlan_description_mappings"] = final_mappings
                _save_vlan_desc_mappings(rules)
            else:
                st.warning("⚠️ Enter a Role to add.")


def _vlan_presets_editor(rules: dict) -> None:
    vlan_presets = get_vlan_presets(rules)

    total_count = sum(len(g.get("items", [])) for g in vlan_presets.values())

    with st.expander(f"🌐 VLAN ALLOCATION PRESETS", expanded=True):
        c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
        with c_hdr_left:
            st.caption("Manage reusable VLAN allocation groups. Each group has default patterns applied to all its items. VLAN Description tags are configured in the dedicated mappings expander below.")
        with c_hdr_right:
            st.markdown(
                f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
                f"{total_count} presets</div>",
                unsafe_allow_html=True
            )

        _inject_preset_table_style()

        preset_names = list(vlan_presets.keys())
        if "Custom / Empty Preset" not in preset_names:
            preset_names.append("Custom / Empty Preset")
        if "pending_vlan_group" in st.session_state:
            selected_group = st.session_state.pop("pending_vlan_group")
            st.session_state["vlan_pre_selected_group"] = selected_group
        selected_group = st.session_state.get("vlan_pre_selected_group", None)
        if selected_group is None or selected_group not in preset_names:
            selected_group = preset_names[0] if preset_names else None

        is_custom = selected_group == "+ Create New Preset Group"

        group_options = preset_names + ["+ Create New Preset Group"]
        group_index = group_options.index(selected_group) if selected_group in group_options else 0

        col_grp_sel, col_grp_actions = st.columns([7.8, 2.2], vertical_alignment="bottom")
        with col_grp_sel:
            def _on_group_change():
                st.session_state["vlan_pre_group_created"] = False
            sel = st.selectbox(
                "Preset Group",
                options=group_options,
                index=group_index,
                key="vlan_pre_selected_group",
                help="Select a preset group to configure, or create a new one.",
                on_change=_on_group_change,
            )
        with col_grp_actions:
            c_up, c_dn, c_del = st.columns(3)
            if not is_custom and len(preset_names) > 1 and selected_group not in ("Custom / Empty Preset",):
                cur_idx = preset_names.index(selected_group)
                if c_up.button("⬆️", key="btn_vlan_grp_up", disabled=(cur_idx == 0), help="Move Group Up", width="stretch"):
                    preset_names[cur_idx - 1], preset_names[cur_idx] = preset_names[cur_idx], preset_names[cur_idx - 1]
                    rules["vlan_presets"] = {k: rules["vlan_presets"][k] for k in preset_names}
                    save_naming_rules(rules, source="Reorder VLAN Preset Groups")
                    fresh_rules = SSM.get_cached_naming_rules() or load_naming_rules()
                    rules.clear()
                    rules.update(fresh_rules)
                    SSM.set_naming_rules(fresh_rules.copy())
                    SSM.refresh_naming_rules()
                    st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                    st.rerun()

                if c_dn.button("⬇️", key="btn_vlan_grp_dn", disabled=(cur_idx == len(preset_names) - 1), help="Move Group Down", width="stretch"):
                    preset_names[cur_idx + 1], preset_names[cur_idx] = preset_names[cur_idx], preset_names[cur_idx + 1]
                    rules["vlan_presets"] = {k: rules["vlan_presets"][k] for k in preset_names}
                    save_naming_rules(rules, source="Reorder VLAN Preset Groups")
                    fresh_rules = SSM.get_cached_naming_rules() or load_naming_rules()
                    rules.clear()
                    rules.update(fresh_rules)
                    SSM.set_naming_rules(fresh_rules.copy())
                    SSM.refresh_naming_rules()
                    st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                    st.rerun()

                if c_del.button("🗑️", key="btn_vlan_grp_del", disabled=(len(preset_names) <= 1 or selected_group == "Custom / Empty Preset"), help=f"Delete preset group '{selected_group}'", width="stretch"):
                    vlan_presets.pop(selected_group, None)
                    rules["vlan_presets"] = vlan_presets
                    remaining = [p for p in preset_names if p != selected_group]
                    st.session_state["pending_vlan_group"] = remaining[0] if remaining else "+ Create New Preset Group"
                    _clear_session_state_prefixes("vlan_pre_vid_", "vlan_pre_role_", "vlan_pre_pat_", "vlan_pre_gnpat_", "vlan_pre_gppat_")
                    save_naming_rules(rules, source=f"Delete VLAN preset group {selected_group}")
                    st.rerun()

        if sel == "+ Create New Preset Group":
            st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
            st.markdown("##### ➕ Create New VLAN Preset Group")
            new_grp_name = st.text_input(
                "New Preset Group Name",
                placeholder="e.g. DataCenter VLAN Preset, Campus Network",
                key="vlan_new_group_name_input"
            ).strip()
            c_create, c_cancel = st.columns(2)
            with c_create:
                if st.button("➕ Create Group", type="primary", width="stretch"):
                    if not new_grp_name:
                        st.error("Preset group name cannot be empty.")
                    elif new_grp_name in preset_names:
                        st.warning(f"Group '{new_grp_name}' already exists.")
                    else:
                        vlan_presets[new_grp_name] = {
                            "vlan_name_pattern": "<role>",
                            "prefix_pattern": "<site> <role> -- VLAN <vid>",
                            "items": []
                        }
                        rules["vlan_presets"] = vlan_presets
                        save_naming_rules(rules, source=f"Create VLAN Group {new_grp_name}")
                        SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                        st.session_state["pending_vlan_group"] = new_grp_name
                        st.toast(f"✅ VLAN Group '{new_grp_name}' created!", icon="💾")
                        st.rerun()
            with c_cancel:
                if st.button("Cancel", width="stretch"):
                    valid_groups = [p for p in preset_names if p != "+ Create New Preset Group"]
                    fallback_grp = valid_groups[0] if valid_groups else "Branch Office VLAN Preset"
                    st.session_state["pending_vlan_group"] = fallback_grp
                    st.rerun()
            return

        group_name = None
        group = {"vlan_name_pattern": "<role>", "prefix_pattern": "", "items": []}
        items = []
        updated = []
        if not is_custom:
            group_name = sel
            group = dict(vlan_presets.get(group_name, {"vlan_name_pattern": "<role>", "prefix_pattern": "", "items": []}))
            items = list(group.get("items", []))

        _pending_del_key = "_vlan_pending_del"
        _pending_swap_key = "_vlan_pending_swap"

        if group_name is not None:
            stale_del = st.session_state.pop(_pending_del_key, None)
            if stale_del is not None and 0 <= stale_del < len(items):
                    items = [p for i, p in enumerate(items) if i != stale_del]
                    group["items"] = items
                    rules["vlan_presets"] = dict(vlan_presets)
                    rules["vlan_presets"][group_name] = group
                    _clear_session_state_prefixes("vlan_pre_vid_", "vlan_pre_role_", "vlan_pre_pat_")
                    _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")
                    return

            pending_swap = st.session_state.pop(_pending_swap_key, None)
            if pending_swap is not None:
                src, dst = pending_swap
                if 0 <= src < len(items) and 0 <= dst < len(items):
                    items[src], items[dst] = items[dst], items[src]
                    group["items"] = items
                    rules["vlan_presets"] = dict(vlan_presets)
                    rules["vlan_presets"][group_name] = group
                    _clear_session_state_prefixes("vlan_pre_vid_", "vlan_pre_role_", "vlan_pre_pat_")
                    _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")
                    return

        new_group_name = None

        # ── Group-level pattern card ──────────────────────────────────────────
        if group_name is not None:
            with st.container(border=True):
                st.markdown("**Group Default Patterns**")
                pc1, pc2 = st.columns(2)
                with pc1:
                    group_name_pattern = st.text_input(
                        "Group VLAN Name Pattern",
                        value=str(group.get("vlan_name_pattern", "<role>")),
                        placeholder="<role>",
                        key=f"vlan_pre_gnpat_{group_name}",
                        help="Default template for VLAN Name across all items in this group. Example: <role> or <site>_DC_<role>",
                    )
                with pc2:
                    group_prefix_pattern = st.text_input(
                        "Group Prefix Description Pattern",
                        value=str(group.get("prefix_pattern", "")),
                        placeholder="<site> <role> -- VLAN <vid>",
                        key=f"vlan_pre_gppat_{group_name}",
                        help="Default template for Prefix Description across all items in this group.",
                    )

        if group_name is not None and items:
            h_vid, h_role, h_act = st.columns(PRESET_VLAN_COLS, vertical_alignment="center")
            with h_vid:
                st.markdown("**VID**")
            with h_role:
                st.markdown("**Role**")
            with h_act:
                st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

            positions = {}

            nonce = st.session_state.get("standards_nonce", 0)
            for idx, p in enumerate(items):
                vid = p.get("vid", "")
                role_name = p.get("role", "")

                c_vid, c_role, c_actions = st.columns(PRESET_VLAN_COLS, vertical_alignment="center")
                with c_vid:
                    nvid = st.text_input("VID", value=str(vid) if vid not in (None, "") else "", key=f"vlan_pre_vid_{nonce}_{idx}", label_visibility="collapsed").strip()
                with c_role:
                    nrole = st.text_input("Role", value=str(role_name), key=f"vlan_pre_role_{nonce}_{idx}", label_visibility="collapsed").strip()
                with c_actions:
                    col_up, col_down, col_del = st.columns(PRESET_ACTION_COLS)
                    with col_up:
                        if idx > 0:
                            if st.button("⬆️", key=f"vlan_pre_up_{idx}", help="Move up"):
                                st.session_state[_pending_swap_key] = (idx, idx - 1)
                                st.rerun()
                        else:
                            st.empty()
                    with col_down:
                        if idx < len(items) - 1:
                            if st.button("⬇️", key=f"vlan_pre_down_{idx}", help="Move down"):
                                st.session_state[_pending_swap_key] = (idx, idx + 1)
                                st.rerun()
                        else:
                            st.empty()
                    with col_del:
                        if st.button("🗑️", key=f"vlan_pre_del_{idx}", help="Delete this entry"):
                            st.session_state[_pending_del_key] = idx
                            st.rerun()

                if stale_del == idx:
                    continue
                try:
                    nvid_int = int(nvid) if nvid else None
                except (ValueError, TypeError):
                    nvid_int = None
                if nrole or nvid:
                    updated.append({
                        "vid": nvid_int,
                        "role": nrole,
                    })
                positions[idx] = len(updated)

            if st.session_state.pop("vlan_pre_min_one", False):
                st.warning("⚠️ At least one VLAN entry must remain. Delete a different entry first.")
        # END of the `if group_name is not None` block for headers + editable list.

        if group_name is not None and not items:
            st.info("ℹ️ No preset VLAN items defined in this group. It will load as a blank table in IPAM, or you can add items below.")

        col_save, col_reset = st.columns(2, vertical_alignment="center")
        with col_save:
            saved_presets = st.button("💾 Save & Apply Changes", key="vlan_pre_save", type="primary", width='stretch')
        with col_reset:
            reset_presets = st.button("🔄 Reset to Defaults", key="vlan_pre_reset", width='stretch')

        # ── Add Row form ──────────────────────────────────────────────────────
        if group_name is not None:
            with st.form(key="vlan_pre_add_form", clear_on_submit=True):
                ca_vid, ca_role, ca_act = st.columns(PRESET_VLAN_COLS, vertical_alignment="center")
                with ca_vid:
                    new_vid = st.text_input("New VID", value="", placeholder="900", key="vlan_pre_new_vid", label_visibility="collapsed").strip()
                with ca_role:
                    new_role = st.text_input("New Role", value="", placeholder="e.g. Surveillance / CCTV", key="vlan_pre_new_role", label_visibility="collapsed").strip()
                with ca_act:
                    add_vlan = st.form_submit_button("➕ Add", width='stretch', help="Add new VLAN entry")
        else:
            new_vid = ""
            new_role = ""
            add_vlan = False

    # Collect group-level pattern values when not custom
    gnpat = group.get("vlan_name_pattern", "<role>")
    gppat = group.get("prefix_pattern", "")
    if group_name is not None:
        gnpat = st.session_state.get(f"vlan_pre_gnpat_{group_name}", gnpat).strip()
        gppat = st.session_state.get(f"vlan_pre_gppat_{group_name}", gppat).strip()

    if reset_presets:
        import copy
        _clear_session_state_prefixes("vlan_pre")
        rules["vlan_presets"] = copy.deepcopy(dict(DEFAULT_VLAN_PRESETS))
        _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")
        return

    if group_name is not None and add_vlan:
        if is_custom:
            if not new_group_name or not new_group_name.strip():
                st.error("⚠️ Provide a name for the new preset group.")
                return
            group_key = new_group_name.strip()
            try:
                new_vid_int = int(new_vid) if new_vid else None
            except (ValueError, TypeError):
                new_vid_int = None
            if new_role or new_vid:
                vlan_presets[group_key] = {
                    "vlan_name_pattern": "<role>",
                    "prefix_pattern": "<site> <role> -- VLAN <vid>",
                    "items": [{
                        "vid": new_vid_int,
                        "role": new_role,
                    }]
                }
                rules["vlan_presets"] = vlan_presets
                st.session_state["pending_vlan_group"] = group_key
                _clear_session_state_prefixes("vlan_pre_new_vid", "vlan_pre_new_role", "vlan_pre_new_group")
                _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")
            else:
                st.error("⚠️ Enter at least a Role or VID for the first entry.")
            return
        if new_role or new_vid:
            try:
                new_vid_int = int(new_vid) if new_vid else None
            except (ValueError, TypeError):
                new_vid_int = None
            items_added = list(items) + [{
                "vid": new_vid_int,
                "role": new_role,
            }]
            group["items"] = items_added
            group["vlan_name_pattern"] = gnpat
            group["prefix_pattern"] = gppat
            rules["vlan_presets"] = dict(vlan_presets)
            rules["vlan_presets"][group_name] = group
            _clear_session_state_prefixes("vlan_pre_vid_", "vlan_pre_role_", "vlan_pre_pat_")
            _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")
        else:
            st.warning("⚠️ Enter at least a Role or VID to add.")
        return

    if saved_presets:
        if is_custom:
            if not new_group_name or not new_group_name.strip():
                st.error("⚠️ Provide a name for the new preset group.")
                return
            group_key = new_group_name.strip()
            vlan_presets[group_key] = {
                "vlan_name_pattern": "<role>",
                "prefix_pattern": "<site> <role> -- VLAN <vid>",
                "items": updated,
            }
        else:
            if group_name is None:
                return
            final_group = dict(group)
            final_group["items"] = updated
            final_group["vlan_name_pattern"] = gnpat
            final_group["prefix_pattern"] = gppat
            vlan_presets[group_name] = final_group
        rules["vlan_presets"] = vlan_presets
        if is_custom:
            st.session_state["pending_vlan_group"] = new_group_name.strip() if new_group_name and new_group_name.strip() else st.session_state.get("vlan_pre_selected_group")
        _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")



def _notify_preset_changed() -> None:
    """Bump the cross-tab standards nonce so the Naming tab's Platform Preset
    dropdown re-reads the latest parsing presets."""
    st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1


def _render_hardware_baseline_editor(rules: dict, active_model: str | None = None):
    """Render the structured Hardware Baseline Standards editor.

    Replaces the legacy single-string ``netbox_server_yaml`` text area with a
    category-based editor that supports server, module_nic, storage_san, and
    custom categories. Each category has its own save/reset buttons to avoid
    cross-card banner leakage.
    """
    from config.naming_rules import compute_delta, add_to_history
    active_model = active_model or st.session_state.get("active_model", "")

    with st.container(border=True):
        col_t1, col_t2 = st.columns([3, 1], vertical_alignment="center")
        with col_t1:
            st.markdown("#### 📋 NetBox YAML / Hardware Templates & Guidelines")
        with col_t2:
            hb = get_hardware_baseline_standards(rules)
            st.markdown(f"<div style='text-align: right;'><span style='background-color: #2b313e; padding: 3px 8px; border-radius: 4px; font-size: 0.85em; color: #94a3b8;'>{len(hb)} categories</span></div>", unsafe_allow_html=True)
        st.caption("Define per-category hardware YAML schema and interface naming patterns. These standards are enforced by the Hardware Catalog Generator when producing Module and Device YAMLs.")

        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

        # ── Render each category editor ───────────────────────────────────────
        edited_hb = {}
        for cat_key, default_cfg in DEFAULT_HARDWARE_BASELINE_STANDARDS.items():
            current_cfg = dict(hb.get(cat_key, default_cfg))
            display_name = current_cfg.get("display_name", cat_key.replace("_", " ").title())

            with st.expander(f"🔹 {display_name} ({cat_key})", expanded=False):

                # ── Server / generic fields ───────────────────────────────
                if cat_key in ("server", "storage_san"):
                    cp_console = st.text_input(
                        "Console Ports",
                        value=current_cfg.get("console_ports", ""),
                        key=f"hb_{cat_key}_console",
                        help="Console port description (e.g. Serial (de-9))",
                    )
                    cp_module_bays = st.text_input(
                        "Module Bays",
                        value=current_cfg.get("module_bays", ""),
                        key=f"hb_{cat_key}_module_bays",
                        help="Comma-separated module bay names (e.g. PSU1, PSU2, PCIe1)",
                    )
                    cp_interfaces = st.text_input(
                        "Interfaces",
                        value=current_cfg.get("interfaces", ""),
                        key=f"hb_{cat_key}_interfaces",
                        help="Interface description (e.g. OOB Management ONLY (1000base-t, mgmt_only: true))",
                    )
                    edited_hb[cat_key] = {
                        **current_cfg,
                        "display_name": display_name,
                        "console_ports": cp_console,
                        "module_bays": cp_module_bays,
                        "interfaces": cp_interfaces,
                    }
                else:
                    # module_nic and other categories: focus on interface_pattern
                    pass

                # ── Module / NIC interface pattern ──────────────────────────
                if cat_key in ("module_nic", "storage_san"):
                    ip_pattern = st.text_input(
                        "Interface Naming Pattern",
                        value=current_cfg.get("interface_pattern", "{module}/Port{index}"),
                        key=f"hb_{cat_key}_ipat",
                        help="Template for interface names. Use {module} for the model name and {index} for sequential port numbers. Default: {module}/Port{index}",
                    )
                    ip_port_count = st.number_input(
                        "Default Port Count",
                        min_value=1,
                        max_value=64,
                        value=int(current_cfg.get("default_port_count", 2)),
                        key=f"hb_{cat_key}_portcount",
                        help="Number of ports to generate by default",
                    )
                    ip_port_start = st.number_input(
                        "Port Index Start",
                        min_value=0,
                        max_value=99,
                        value=int(current_cfg.get("port_start_index", 1)),
                        key=f"hb_{cat_key}_portstart",
                        help="Starting index for port numbering",
                    )
                    edited_hb[cat_key] = {
                        **current_cfg,
                        "display_name": display_name,
                        "interface_pattern": ip_pattern,
                        "default_port_count": ip_port_count,
                        "port_start_index": ip_port_start,
                    }

                # Show preview of generated names
                if cat_key in ("module_nic", "storage_san"):
                    try:
                        pattern = edited_hb[cat_key].get("interface_pattern", "{module}/Port{index}")
                        pname = st.session_state.get("m_model", "X550") or "TESTMODULE"
                        count = int(edited_hb[cat_key].get("default_port_count", 2))
                        start = int(edited_hb[cat_key].get("port_start_index", 1))
                        preview_names = []
                        for i in range(start, start + count):
                            preview_names.append(pattern.replace("{module}", pname).replace("{index}", str(i)))
                        st.caption(f"Preview (model='{pname}'): **{', '.join(preview_names)}**")
                    except Exception:
                        pass

                st.divider()
                col_s, col_r = st.columns(2)
                with col_s:
                    if st.button("💾 Save & Apply Changes", key=f"hb_save_{cat_key}", type="primary", width="stretch"):
                        old_hb = get_hardware_baseline_standards(rules)
                        rules["hardware_baseline_standards"] = edited_hb
                        save_naming_rules(rules, source=f"Hardware Baseline: {cat_key}")
                        fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                        rules.clear()
                        rules.update(fresh)
                        SSM.set_naming_rules(fresh.copy())
                        SSM.refresh_naming_rules()
                        st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1
                        st.toast(f"✅ {display_name} category saved & applied!", icon="💾")
                        st.rerun()
                with col_r:
                    if st.button("🔄 Reset to Defaults", key=f"hb_reset_{cat_key}", width="stretch"):
                        rules["hardware_baseline_standards"] = get_hardware_baseline_standards(load_naming_rules())
                        save_naming_rules(rules, source=f"Hardware Baseline: Reset {cat_key}")
                        fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                        rules.clear()
                        rules.update(fresh)
                        SSM.set_naming_rules(fresh.copy())
                        SSM.refresh_naming_rules()
                        st.toast(f"✅ {display_name} reset to defaults!", icon="🔄")
                        st.rerun()


def render_standards_tab(active_model):
    st.subheader("📖 Infrastructure Naming Standards Configuration")
    st.caption("Define and manage your organization's naming conventions. All patterns configured here are automatically applied in the Naming tab.")

    st.markdown(
        """
        <style>
        /* Only hide specifically marked ghost buttons without breaking active buttons */
        button[data-testid="baseButton-secondary"]:disabled:empty {
            display: none !important;
        }
        /* Hide Streamlit's "Press Enter to submit form" overlay text in text inputs */
        div[data-testid="InputInstructions"] {
            display: none !important;
        }
        /* Purge tooltip '?' icons from checkboxes */
        div[data-testid="stCheckbox"] [data-testid="stTooltipIcon"] {
            display: none !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    current_rules = load_naming_rules()
    SSM.set_naming_rules(current_rules)

    # ── Lazy-load outer tabs: only render the active one to avoid creating
    #    ~60+ widget objects on every run when the user is viewing a different tab.
    _STANDARDS_OUTER = ["edit", "vars", "history"]
    if "standards_outer_tab" not in st.session_state:
        st.session_state["standards_outer_tab"] = 0
    _active_outer = st.session_state.get("standards_outer_tab", 0)

    _outer_labels = ["📝 Edit Standards", "📘 Pattern Variables", "📜 Change History"]
    _outer_sel = st.radio(
        "Standards Navigation",
        options=_outer_labels,
        index=_active_outer,
        horizontal=True,
        key="standards_outer_radio",
        label_visibility="collapsed",
    )
    _new_outer_idx = _outer_labels.index(_outer_sel)
    if _new_outer_idx != _active_outer:
        st.session_state["standards_outer_tab"] = _new_outer_idx
        st.rerun()

    if _active_outer == 0:
        # ── Lazy-load inner sub-tabs: only render the active sub-section.
        _SUBTAB_KEYS = ("ipam", "naming", "infra")
        if "standards_subtab" not in st.session_state:
            st.session_state["standards_subtab"] = 0
        _active_subtab = st.session_state.get("standards_subtab", 0)

        _subtab_labels = ["🌐 IPAM Standards", "🏷️ Naming Standards", "⚙️ Infrastructure & Baseline"]
        _sub_sel = st.radio(
            "Standards Category",
            options=_subtab_labels,
            index=_active_subtab,
            horizontal=True,
            key="standards_subtab_radio",
            label_visibility="collapsed",
        )
        _new_sub_idx = _subtab_labels.index(_sub_sel)
        if _new_sub_idx != _active_subtab:
            st.session_state["standards_subtab"] = _new_sub_idx
            st.rerun()

        if _active_subtab == 0:
            _vlan_presets_editor(current_rules)
            with st.expander("🏷️ VLAN Description Mappings (Role → Description)", expanded=False):
                c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
                with c_hdr_left:
                    st.caption(
                        "Map each VLAN Role to its NetBox VLAN Description tag. When a role "
                        "matches, its mapped value is used; otherwise the Role name itself is "
                        "returned. These mappings are consulted by the IPAM tab's dynamic "
                        "resolution logic."
                    )
                with c_hdr_right:
                    mappings_count = len(dict(get_vlan_description_mappings(current_rules)))
                    st.markdown(
                        f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
                        f"{mappings_count} mappings</div>",
                        unsafe_allow_html=True
                    )
                _render_vlan_description_mappings_editor(current_rules)
            with st.expander("🏷️ IPAM Role Mapping Rules (Alias to Canonical Role)", expanded=False):
                c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
                with c_hdr_left:
                    st.caption(
                        "Each row is a regex pattern → canonical role pair. Edit inline or use "
                        "the AI generator below to create new rules."
                    )
                with c_hdr_right:
                    ipam_rules = list(get_ipam_role_mappings(current_rules))
                    st.markdown(
                        f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
                        f"{len(ipam_rules)} rules</div>",
                        unsafe_allow_html=True
                    )
                _render_ipam_role_mapping_manager(active_model)
            _render_csv_schemas_editor(current_rules)

        elif _active_subtab == 1:
            with st.expander("🔧 DEVICE TYPE PRESETS", expanded=False):
                c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
                with c_hdr_left:
                    st.caption("Manage device naming patterns and presets (SW, VS, FW, ION, WAP, RTR, VA).")
                with c_hdr_right:
                    st.markdown(
                        f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
                        f"{len(get_device_presets(current_rules))} presets</div>",
                        unsafe_allow_html=True
                    )
                _preset_type_editor("device", get_device_presets(current_rules), current_rules, prefix="branch",
                                     card_title="🔧 DEVICE TYPE PRESETS",
                                     card_caption="",
                                     section="device_presets", section_label="Device Type Presets", expanded=True,
                                     wrap_expander=False)
            with st.expander("🔌 INTERFACE TYPE PRESETS", expanded=False):
                c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
                with c_hdr_left:
                    st.caption("Manage interface description presets (Uplink, LAG, Po, Access, FW Zone).")
                with c_hdr_right:
                    st.markdown(
                        f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
                        f"{len(get_interface_presets(current_rules))} presets</div>",
                        unsafe_allow_html=True
                    )
                _preset_type_editor("interface", get_interface_presets(current_rules), current_rules, prefix="iface",
                                     card_title="🔌 INTERFACE TYPE PRESETS",
                                     card_caption="",
                                     section="interface_presets", section_label="Interface Type Presets",
                                     wrap_expander=False)
            with st.expander("💻 HOSTS TYPE PRESETS", expanded=False):
                _host_editor(current_rules)
            with st.expander("🖱️ VIRTUAL MACHINE PRESETS", expanded=False):
                _vm_editor(current_rules)
            with st.expander("☁️ Hypervisor Platform Presets", expanded=False):
                _render_hypervisor_platform_presets_editor(current_rules, active_model)

        elif _active_subtab == 2:
            _render_hardware_baseline_editor(current_rules, active_model)

            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

            with st.container(border=True):
                st.markdown("#### 🛠️ Auto-Correction & Syntax Rules")
                st.caption("Regex-based text cleaning rules for OCR noise removal and interface port shortening. Used for user-input error prevention.")
                _render_auto_correction_manager(active_model)

            with st.expander("⚙️ System Prompt Export & Baseline Management", expanded=False):
                standards_mgr = StandardsManager()
                st.markdown("#### 📦 Global Ruleset Baseline (Reset Anchor)")
                raw_ts = standards_mgr.get_baseline_timestamp()
                formatted_ts = raw_ts.replace("T", " ").split(".")[0] if "T" in raw_ts else raw_ts
                st.caption(f"Current Solidified Baseline: **🟢 {formatted_ts}**")

                col_b1, col_b2 = st.columns([1, 1])
                with col_b1:
                    if st.button("📌 Solidify Current Rules as Baseline", width="stretch"):
                        standards_mgr.solidify_baseline()
                        st.success("✅ Current configuration solidified as new baseline!")
                        st.rerun()
                with col_b2:
                    if st.button("🔄 Restore All Standards to Baseline", width="stretch"):
                        standards_mgr.restore_from_baseline()
                        st.warning("⚠️ All standards restored to baseline snapshot.")
                        st.rerun()

                st.divider()
                st.markdown("#### 📋 Full System Prompt for External AI")
                compiled_prompt = standards_mgr.compile_full_system_prompt()
                st.code(compiled_prompt, language="markdown")
    
    elif _active_outer == 1:
        with st.container():
            st.markdown("##### 📘 Pattern Variables Reference Guide")
            st.caption("Unified registry of all pattern variables. Scoped by domain to eliminate naming collision between IPAM, Naming, and ESXi templates.")
            
            from config.naming_rules import get_grouped_pattern_variables, PATTERN_VARIABLES
        grouped_vars = get_grouped_pattern_variables(current_rules)
        patterns_now = get_naming_patterns(current_rules)

        scope_meta = [
            ("shared", "🏢 GLOBAL & SHARED VARIABLES", "Variables shared across all infrastructure naming and IPAM provisioning.", "shared"),
            ("ipam", "🌐 IPAM & SUBNET VARIABLES", "Variables for VLANs, subnets, supernets, and NetBox bulk import schemas.", "ipam"),
            ("naming", "💻 DEVICE & VM NAMING VARIABLES", "Variables driving network device, router, firewall, and virtual machine hostnames.", "naming"),
            ("hypervisor", "☁️ Hypervisor Virtualization & Networking", "Variables for physical uplinks, vSwitches, Port Groups, and VMkernels.", "hypervisor"),
        ]

        VARIABLE_COLS_OPTIMIZED = [1.5, 3.2, 3.8, 1.2, 0.8, 0.6]

        for scope_code, title, desc, s_key in scope_meta:
            scope_items = grouped_vars.get(s_key, {})
            with st.expander(f"{title} ({len(scope_items)} variables)", expanded=False):
                col_t1, col_t2 = st.columns([3, 1])
                with col_t1:
                    st.markdown(f"#### {title}")
                with col_t2:
                    st.markdown(f"<div style='text-align: right;'><span style='background-color: #2b313e; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;'>{len(scope_items)} variables</span></div>", unsafe_allow_html=True)
                st.caption(desc)

                scope_edited_vars = {}
                if scope_items:
                    c_h1, c_h2, c_h3, c_h4, c_h5, c_h6 = st.columns(VARIABLE_COLS_OPTIMIZED, vertical_alignment="center")
                    with c_h1:
                        st.markdown("**Token**")
                    with c_h2:
                        st.markdown("**Label**")
                    with c_h3:
                        st.markdown("**Placeholder**")
                    with c_h4:
                        st.markdown("**Default**")
                    with c_h5:
                        st.markdown("**Optional**")
                    with c_h6:
                        st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

                with st.form(key=f"vars_edit_form_{s_key}", clear_on_submit=False):
                    pass  # placeholder removed

                all_edited_vars = {}
                for name, meta in list(scope_items.items()):
                    c1, c2, c3, c4, c5, c6 = st.columns(VARIABLE_COLS_OPTIMIZED, vertical_alignment="center")
                    with c1:
                        st.code(f"<{name}>", language="text")
                    with c2:
                        lbl = st.text_input("Label", value=meta.get("label", name), key=f"vscope_{s_key}_lbl_{name}", label_visibility="collapsed").strip()
                    with c3:
                        ph = st.text_input("Placeholder", value=meta.get("placeholder", ""), key=f"vscope_{s_key}_ph_{name}", label_visibility="collapsed").strip()
                    with c4:
                        df_val = st.text_input("Default", value=meta.get("default", ""), key=f"vscope_{s_key}_df_{name}", label_visibility="collapsed")
                    with c5:
                        opt = st.checkbox("Optional", value=bool(meta.get("optional", False)), key=f"vscope_{s_key}_opt_{name}", label_visibility="collapsed")
                    with c6:
                        if _render_centered_del_btn(f"vscope_del_{s_key}_{name}", f"Delete <{name}>"):
                            scope_edited_vars.pop(name, None)
                            all_edited_vars.pop(name, None)
                            _persist_variables(current_rules, all_edited_vars, section_key=s_key)
                            return

                    scope_edited_vars[name] = {
                        "label": lbl or name,
                        "placeholder": ph or f"e.g. {name}",
                        "default": df_val,
                        "optional": bool(opt),
                        "scope": s_key
                    }
                    all_edited_vars[name] = scope_edited_vars[name]

                col_save, col_rst = st.columns([4, 1])
                with col_save:
                    saved_vars = st.button(f"💾 Save & Apply Changes", key=f"vars_save_{s_key}", type="primary", width="stretch")
                with col_rst:
                    if st.button("🔄 Reset to Defaults", key=f"reset_scope_{s_key}", type="secondary", width="stretch"):
                        from config.naming_rules import PATTERN_VARIABLES
                        default_scope_vars = {k: v for k, v in PATTERN_VARIABLES.items() if v.get("scope") == s_key}
                        scope_edited_vars.update(default_scope_vars)
                        all_edited_vars.update(scope_edited_vars)
                        st.toast(f"✅ {s_key.title()} Variables reset to defaults!", icon="🔄")
                        _persist_variables(current_rules, all_edited_vars, section_key=s_key)

                if saved_vars:
                    _persist_variables(current_rules, scope_edited_vars, section_key=s_key)

                # 2. Bottom Inline Add Row (Standardized with '➕ Add' button)
                with st.form(key=f"var_add_form_{s_key}", clear_on_submit=True):
                    ca1, ca2, ca3, ca4, ca5, ca6 = st.columns(VARIABLE_COLS_OPTIMIZED, vertical_alignment="center")
                    with ca1:
                        new_name = st.text_input("New Token", value="", placeholder="e.g. tier", key=f"vnew_{s_key}_name", label_visibility="collapsed").strip()
                        new_name = _normalize_var_name(new_name)
                    with ca2:
                        new_lbl = st.text_input("New Label", value="", placeholder="e.g. Service Tier", key=f"vnew_{s_key}_lbl", label_visibility="collapsed").strip()
                    with ca3:
                        new_ph = st.text_input("New Placeholder", value="", placeholder="e.g. Tier-1, Tier-2", key=f"vnew_{s_key}_ph", label_visibility="collapsed").strip()
                    with ca4:
                        new_df = st.text_input("Default", value="", placeholder="", key=f"vnew_{s_key}_df", label_visibility="collapsed")
                    with ca5:
                        new_opt = st.checkbox("Optional", value=False, key=f"vnew_{s_key}_opt", label_visibility="collapsed")
                    with ca6:
                        add_tok = st.form_submit_button("➕ Add", width='stretch', help=f"Add variable to {title}")

                    if add_tok:
                        if new_name:
                            all_edited_vars[new_name] = {
                                "label": new_lbl or new_name,
                                "placeholder": new_ph or f"e.g. {new_name}",
                                "default": new_df,
                                "optional": bool(new_opt),
                                "scope": s_key
                            }
                            _persist_variables(current_rules, all_edited_vars, section_key=s_key)
                            return
                        else:
                            st.warning("⚠️ Enter a token name to add.")

        # Bottom Global Reset Option
        with st.container(border=True):
            col_r1, col_r2 = st.columns([3, 1])
            with col_r1:
                st.markdown("##### 🔄 Reset All Variables")
                st.caption("Restore all variables across all scopes back to system factory defaults.")
            with col_r2:
                if st.button("🔄 Reset to Defaults", key="var_reset_all", type="secondary", width='stretch'):
                    _persist_variables(current_rules, dict(PATTERN_VARIABLES))

        with st.expander("🧩 Active Patterns", expanded=False):
            if patterns_now:
                for key, pat in patterns_now.items():
                    st.markdown(f"**{key}:** `{pat}`")

        st.divider()
        st.caption("All variables defined here automatically power input boxes and template resolution across Naming, IPAM, and CSV generators.")
    
    elif _active_outer == 2:
        with st.container():
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
            
            st.divider()
            
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
                                SSM.set_naming_rules(restored_rules)
                                st.success(f"✅ Restored version from {timestamp}")
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Failed to restore: {str(e)}")
                    
                    with col_view:
                        with st.expander("👁️ View Full Details", expanded=False):
                            st.json(rules)

        # ── Privacy Vault Audit Console ────────────────────────────────────
        from core.vault import SanitizerVault
        _vault = SanitizerVault()

        _stats = _vault.get_vault_stats()
        _cols_m = st.columns(5)
        with _cols_m[0]:
            st.metric("Total Mappings", _stats["total"])
        with _cols_m[1]:
            st.metric("IPs", _stats["ips"])
        with _cols_m[2]:
            st.metric("NETs", _stats["nets"])
        with _cols_m[3]:
            st.metric("DOMAINs", _stats["domains"])
        _active_sid = st.session_state.get("latest_vault_session")
        if _active_sid:
            _map = _vault.get_session_token_map(_active_sid)
            st.markdown(f"**Active Session:** `{_active_sid}` — {_map and len(_map)} token(s)")
        else:
            st.caption("No active session tokens.")

        if st.button("🧹 Purge & Reset Vault Cache", key="std_clear_vault"):
            _vault.clear_vault()
            st.session_state.pop("latest_vault_session", None)
            st.toast("✅ Vault database purged and reset successfully!", icon="🧹")
            st.rerun()

        _records = _vault.get_recent_mappings(20)
        if _records:
            import pandas as pd
            _df = pd.DataFrame(_records)
            _display_cols = ["real_value", "token", "category", "created_at", "last_used_at"]
            _df = _df[_display_cols]
            _df.columns = ["Real Value", "Safe Token", "Category", "Created At", "Last Used At"]
            st.dataframe(_df, width="stretch", hide_index=True)
        else:
            st.caption(
                "Vault is currently empty. Tokens will appear here when text containing "
                "RFC 1918 IPs or internal domains is processed."
            )