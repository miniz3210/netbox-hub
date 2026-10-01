import os
import json
import re
import copy
import time

import streamlit as st
from config.constants import RULES_FILE
from config.naming_rules import (
    load_naming_rules, save_naming_rules, export_rules_as_prompt,
    load_history, restore_from_history, clear_history, add_to_history,
    get_pattern_variables, get_naming_patterns, get_custom_patterns,
    get_device_presets, get_interface_presets, get_host_vm_presets,
    get_vlan_presets, get_vlan_description_mappings, make_preset_key,
    get_esxi_network_presets,
    default_presets_for, DEFAULT_PRESET_KEY_FIELD, DEFAULT_NAMING_PATTERNS,
    DEFAULT_HOST_TYPE_PRESETS, DEFAULT_VM_PRESETS,
    DEFAULT_RULES, DEFAULT_VLAN_PRESETS, DEFAULT_VLAN_DESCRIPTION_MAPPINGS,
    get_ipam_role_mappings, DEFAULT_IPAM_ROLE_MAPPINGS,
)
from core.naming_engine import generate_naming_pattern, generate_autocorrect_rule
from core.session_manager import SessionStateManager as SSM
from utils.formatters import (
    load_auto_corrections, save_auto_corrections, reset_auto_corrections,
)

# Shared column width ratios enforced across preset table headers, all data rows,
# and the inline "add" row so every preset table lines up identically.
# Code, Label, Pattern Template, Action.
PRESET_COLS = [1.0, 2.2, 7.5, 1.4]
# Action cell sub-columns: Up, Down, Delete (equal thirds, right-aligned).
PRESET_ACTION_COLS = [1, 1, 1]
# VLAN allocation preset columns: VID, Role, Action (group-level patterns shown above).
PRESET_VLAN_COLS = [1.0, 4.0, 1.2]
# Manage Pattern Variables columns: Name, Label, Placeholder, Auto-Fill, Optional, Up, Down, Delete.
VARIABLE_COLS = [1.5, 2.5, 2.5, 1.5, 0.9, 0.45, 0.45, 0.45]
# Auto-Correction rule columns: Original Pattern, Replacement, Description, Action.
AUTOCORRECT_COLS = [3.5, 3.0, 4.0, 1.0]

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


def _check_and_render_banner(section_id: str, msg_template: str = "", duration_sec: int = 10) -> bool:
    """Check session state for a banner matching *section_id* and render it if still valid."""
    banner_data = st.session_state.get("card_saved_banner")
    if not banner_data or banner_data.get("section") != section_id:
        return False
    now = time.time()
    elapsed = now - banner_data.get("ts", 0)
    if elapsed <= duration_sec:
        msg = banner_data.get("msg", msg_template)
        if msg:
            st.markdown(
                """
                <style>
                @keyframes autoDismissFade {
                    0% { opacity: 1; max-height: 120px; margin-bottom: 1rem; }
                    75% { opacity: 1; max-height: 120px; margin-bottom: 1rem; }
                    100% { opacity: 0; max-height: 0; margin-bottom: 0; padding-top: 0; padding-bottom: 0; overflow: hidden; display: none; }
                }
                div[data-testid="stAlert"], div[data-baseweb="notification"] {
                    animation: autoDismissFade 10s forwards !important;
                }
                </style>
                """,
                unsafe_allow_html=True
            )
            st.success(msg)
        st.session_state.pop("card_saved_banner", None)
        return True
    st.session_state.pop("card_saved_banner", None)
    return False


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
    st.session_state["card_saved_banner"] = {
        "section": f"vars_{section_key}",
        "msg": f"✅ {section_key.title()} Variables saved & applied!",
        "ts": time.time()
    }
    st.rerun()


def _render_auto_correction_manager(active_model: str) -> None:
    _render_ipam_role_mapping_manager(active_model)

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

            # Side-by-side Save & Reset buttons above AI Assistant (matching Preset style)
            col_save, col_reset = st.columns([1.2, 1.0])
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
            with col_reset:
                if st.button("🔄 Reset to Defaults", key=f"ac_reset_factory_{category}", width='stretch'):
                    reset_auto_corrections()
                    _clear_session_state_prefixes(f"ac_{category}_")

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

            # Inline Add Rule row aligned with table columns (Pattern, Replacement, Description, Add button in Delete column)
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
                if st.button("➕ Add", key=f"ac_{category}_add", width='stretch', help="Add new rule"):
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
                            _persist_auto_corrections(final)
                    else:
                        st.warning("⚠️ Enter a regex pattern to add.")


    _render_site_code_mapping_manager(key_prefix="ac_scm")


def _render_ipam_role_mapping_manager(active_model: str) -> None:
    rules = load_naming_rules()
    role_rules = list(get_ipam_role_mappings(rules))

    with st.expander("🏷️ IPAM Role Mapping Rules (Alias to Canonical Role)", expanded=False):
        st.caption(
            "Each row is a regex pattern → canonical role pair. Edit inline or use "
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

        # Side-by-side Save & Reset buttons above the AI Assistant
        col_save, col_reset = st.columns([1.2, 1.0])
        with col_save:
            if st.button("💾 Save & Apply Changes", key="ipamrole_save", type="primary", width='stretch'):
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
        with col_reset:
            if st.button("🔄 Reset to Defaults", key="ipamrole_reset", width='stretch'):
                _reset_ipam_role_mappings()

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
    st.session_state["card_saved_banner"] = {"section": "auto_correction", "msg": "✅ IPAM Role Mapping Rules saved & applied!", "ts": time.time()}
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
    st.session_state["card_saved_banner"] = {"section": "auto_correction", "msg": "✅ IPAM Role Mapping Rules reset to defaults!", "ts": time.time()}
    st.rerun()


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

        m_col_p, m_col_r, m_col_del = st.columns([4.0, 3.5, 1.0], vertical_alignment="center")
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
            col_p, col_r, col_del = st.columns([4.0, 3.5, 1.0], vertical_alignment="center")
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
            with col_del:
                if _render_centered_del_btn(f"{key_prefix}_sitecode_{idx}_del", "Delete this mapping"):
                    pending_delete = idx

            if pending_delete == idx:
                key_to_del = pat.strip().lower()
                if key_to_del in exact:
                    del exact[key_to_del]
                st.session_state["site_code_mappings_modified"] = dict(exact)
                # Persist immediately after deletion (this helper reruns the app)
                rules = load_naming_rules()
                sr = get_site_code_rules(rules)
                final = dict(rules)
                final["site_code_rules"] = dict(sr)
                final["site_code_rules"]["exact_mappings"] = dict(exact)
                _persist_site_code_mappings(final)
                return
            key = np_.strip().lower()
            if key:
                updated[key] = nr_.strip().upper()

        col_save, col_reset = st.columns([1.2, 1.0])
        with col_save:
            if st.button("💾 Save & Apply Changes", key=f"{key_prefix}_sitecode_save", type="primary", width='stretch'):
                rules = load_naming_rules()
                sr = get_site_code_rules(rules)
                final = dict(rules)
                final["site_code_rules"] = dict(sr)
                final["site_code_rules"]["exact_mappings"] = updated
                _persist_site_code_mappings(final)
                st.session_state["site_code_mappings_modified"] = dict(updated)
        with col_reset:
            if st.button("🔄 Reset to Defaults", key=f"{key_prefix}_sitecode_reset", width='stretch'):
                _reset_site_code_mappings()

        with st.form(key=f"{key_prefix}_sitecode_add_form", clear_on_submit=True):
            col_city, col_code, col_add = st.columns([4.0, 3.5, 1.0], vertical_alignment="center")
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
    st.session_state["card_saved_banner"] = {"section": "auto_correction", "msg": "✅ Site Code Mapping Rules saved & applied!", "ts": time.time()}
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
    st.session_state["card_saved_banner"] = {"section": "auto_correction", "msg": "✅ Site Code Mapping Rules reset to defaults!", "ts": time.time()}
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
    st.session_state["card_saved_banner"] = {"section": "auto_correction", "msg": "✅ Syntax Auto-Correction Rules saved & applied!", "ts": time.time()}
    st.rerun()


def _save_presets(rules: dict, section: str = "presets", section_label: str = "Presets") -> None:
    save_naming_rules(rules, source="Presets Manager")
    # Reload the persisted rules fresh from disk so the in-memory copy and SSM cache stay in sync
    # with what's on disk, preventing stale-widget defaults on immediate rerender.
    fresh = load_naming_rules()
    rules.clear()
    rules.update(fresh)
    SSM.set_naming_rules(fresh.copy())
    SSM.refresh_naming_rules()
    st.session_state["card_saved_banner"] = {"section": section, "msg": f"✅ {section_label} saved & applied!", "ts": time.time()}
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
    st.session_state["card_saved_banner"] = {"section": "csv_schemas", "msg": "✅ NetBox Bulk Import CSV Schemas saved & applied!", "ts": time.time()}
    _clear_session_state_prefixes("csv_sch_")
    st.rerun()

def _reset_csv_schemas(rules: dict) -> None:
    from config.naming_rules import DEFAULT_CSV_SCHEMAS
    rules["csv_schemas"] = copy.deepcopy(DEFAULT_CSV_SCHEMAS)
    save_naming_rules(rules, source="CSV Schemas Reset")
    SSM.set_naming_rules(rules.copy())
    st.session_state["card_saved_banner"] = {"section": "csv_schemas", "msg": "✅ NetBox Bulk Import CSV Schemas reset to defaults!", "ts": time.time()}
    _clear_session_state_prefixes("csv_sch_")
    st.rerun()

def _render_csv_schemas_editor(rules: dict) -> None:
    from config.naming_rules import get_csv_schemas
    schemas = get_csv_schemas(rules)

    with st.expander("📊 NetBox Bulk Import CSV Schemas", expanded=False):
        _check_and_render_banner("csv_schemas")
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
            if st.button("💾 Save CSV Schemas", key="csv_schemas_save", type="primary", width="stretch"):
                rules["csv_schemas"] = edited_schemas
                _save_csv_schemas(rules)
        with col_reset:
            if st.button("🔄 Reset Schemas to Defaults", key="csv_schemas_reset", width="stretch"):
                _reset_csv_schemas(rules)

def _save_vlan_desc_mappings(rules: dict, section: str = "vlan_desc_mappings", section_label: str = "VLAN Description Mappings") -> None:
    save_naming_rules(rules, source="VLAN Description Mappings Manager")
    SSM.set_naming_rules(rules.copy())
    st.session_state["card_saved_banner"] = {"section": section, "msg": f"✅ {section_label} saved & applied!", "ts": time.time()}
    _clear_session_state_prefixes("vlandesc_")
    st.rerun()


def _preset_type_editor(kind: str, presets: list, rules: dict, prefix: str, card_title: str, card_caption: str,
                        section: str = "presets", section_label: str = "Presets") -> None:
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

    with st.container(border=True):
        _check_and_render_banner(section)
        col_t1, col_t2 = st.columns([3, 1])
        with col_t1:
            st.markdown(f"#### {card_title}")
        with col_t2:
            st.markdown(f"<div style='text-align: right;'><span style='background-color: #2b313e; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;'>{len(presets)} presets</span></div>", unsafe_allow_html=True)
        st.caption(card_caption)

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

        # Inline Add Row (with dedicated + Add button on far right)
        with st.form(key=f"{kind}_add_form", clear_on_submit=True):
            is_esxi = kind == "esxi_network"
            code_ph = "e.g. DSwitch" if is_esxi else "e.g. SAN"
            lbl_ph = "e.g. Distributed Switch Uplink" if is_esxi else "e.g. SAN Storage (SAN)"
            tpl_ph = "e.g. <vmnic> - <vds_name> (<status>)" if is_esxi else "e.g. SAN<country><site><seq>"
            ca1, ca2, ca3, ca4 = st.columns(PRESET_COLS, vertical_alignment="center")
            with ca1:
                new_code = st.text_input("Code", value="", placeholder=code_ph, key=f"{kind}_new_code", label_visibility="collapsed").strip()
            with ca2:
                new_lbl = st.text_input("Display Label", value="", placeholder=lbl_ph, key=f"{kind}_new_lbl", label_visibility="collapsed").strip()
            with ca3:
                new_tpl = st.text_input("Pattern Template", value="", placeholder=tpl_ph, key=f"{kind}_new_tpl", label_visibility="collapsed").strip()
            with ca4:
                add_preset = st.form_submit_button("➕ Add", width='stretch', help="Add new preset")

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

    with st.container(border=True):
        _check_and_render_banner("hosts")
        col_t1, col_t2 = st.columns([3, 1])
        with col_t1:
            st.markdown("#### 💻 HOSTS TYPE PRESETS")
        with col_t2:
            st.markdown(f"<div style='text-align: right;'><span style='background-color: #2b313e; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;'>{len(host_presets)} presets</span></div>", unsafe_allow_html=True)
        st.caption("Manage physical hypervisor host naming patterns and presets.")

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
                add_preset = st.form_submit_button("➕ Add", width='stretch', help="Add new preset")

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
        st.session_state["card_saved_banner"] = {"section": "hosts", "msg": "✅ Hosts Type Presets reset to defaults!", "ts": time.time()}
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

    with st.container(border=True):
        _check_and_render_banner("vm_roles")
        col_t1, col_t2 = st.columns([3, 1])
        with col_t1:
            st.markdown("#### 🖱️ VIRTUAL MACHINE PRESETS")
        with col_t2:
            st.markdown(f"<div style='text-align: right;'><span style='background-color: #2b313e; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;'>{len(vm_presets)} presets</span></div>", unsafe_allow_html=True)
        st.caption("Manage virtual machine roles (cvi, afs, sani, vlab) and their shared hostname template.")

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
        st.session_state["card_saved_banner"] = {"section": "vm_roles", "msg": "✅ VM Role Presets reset to defaults!", "ts": time.time()}
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


# Column widths for the VLAN Description Mappings editor: Role, Description, Action.
VLAND_MAPPINGS_COLS = [3.2, 4.8, 1.0]


def _render_vlan_description_mappings_editor(rules: dict) -> None:
    with st.expander("🏷️ VLAN Description Mappings (Role → Description)", expanded=True):
        _check_and_render_banner("vlan_desc_mappings")

        mappings = dict(get_vlan_description_mappings(rules))

        st.caption(
            "Map each VLAN Role to its NetBox VLAN Description tag. When a role "
            "matches, its mapped value is used; otherwise the Role name itself is "
            "returned. These mappings are consulted by the IPAM tab's dynamic "
            "resolution logic."
        )

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
            col_role, col_desc, col_del = st.columns(VLAND_MAPPINGS_COLS, vertical_alignment="center")
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
            with col_del:
                if _render_centered_del_btn(f"vlandesc_{idx}_del", "Delete this mapping"):
                    new_mappings = {r: d for i, (r, d) in enumerate(items) if i != idx}
                    rules_to_save = dict(rules)
                    rules_to_save["vlan_description_mappings"] = new_mappings
                    _save_vlan_desc_mappings(rules_to_save)
                    return

            role_key = nrole.strip()
            if role_key:
                updated[role_key] = ndesc.strip()

        col_save, col_reset = st.columns([1.2, 1.0])
        with col_save:
            if st.button("💾 Save & Apply Changes", key="vlandesc_save", type="primary", width='stretch'):
                if not updated:
                    st.warning("⚠️ At least one mapping is required.")
                else:
                    rules = dict(rules)
                    rules["vlan_description_mappings"] = updated
                    _save_vlan_desc_mappings(rules)
        with col_reset:
            if st.button("🔄 Reset to Defaults", key="vlandesc_reset", width='stretch'):
                rules = dict(rules)
                rules["vlan_description_mappings"] = dict(DEFAULT_VLAN_DESCRIPTION_MAPPINGS)
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

    with st.container(border=True):
        _check_and_render_banner("vlan_presets")
        col_t1, col_t2 = st.columns([3, 1])
        with col_t1:
            st.markdown("#### 🌐 VLAN ALLOCATION PRESETS")
        with col_t2:
            total_count = sum(len(g.get("items", [])) for g in vlan_presets.values())
            st.markdown(f"<div style='text-align: right;'><span style='background-color: #2b313e; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;'>{total_count} presets</span></div>", unsafe_allow_html=True)
        st.caption("Manage reusable VLAN allocation groups. Each group has default patterns applied to all its items. VLAN Description tags are configured in the dedicated mappings expander below.")

        _inject_preset_table_style()

        preset_names = list(vlan_presets.keys())
        if "Custom / Empty Preset" not in preset_names:
            preset_names.append("Custom / Empty Preset")
        selected_group = st.session_state.get("vlan_pre_selected_group", None)
        if selected_group is None or selected_group not in preset_names:
            selected_group = preset_names[0] if preset_names else None

        is_custom = selected_group == "+ Create New Preset Group"

        group_options = preset_names + ["+ Create New Preset Group"]
        group_index = group_options.index(selected_group) if selected_group in group_options else 0

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
        if is_custom:
            new_group_name = st.text_input(
                "New Preset Group Name",
                value="",
                placeholder="e.g. Campus VLAN Preset",
                key="vlan_pre_new_group",
                label_visibility="visible",
            )

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

        # ── Save / Reset buttons below the list ──────────────────────────────
        col_save, col_reset = st.columns(2)
        with col_save:
            saved_presets = st.button("💾 Save & Apply Changes", key="vlan_pre_save", type="primary", width='stretch')
        with col_reset:
            reset_presets = st.button("🔄 Reset to Defaults", key="vlan_pre_reset", width='stretch')

        # ── Add Row form ──────────────────────────────────────────────────────
        if group_name is not None:
            with st.form(key="vlan_pre_add_form", clear_on_submit=True):
                ca_vid, ca_role, ca_act = st.columns([1.0, 4.0, 1.2], vertical_alignment="center")
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
                st.session_state["vlan_pre_selected_group"] = group_key
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
            st.session_state["vlan_pre_selected_group"] = new_group_name.strip() if new_group_name and new_group_name.strip() else st.session_state.get("vlan_pre_selected_group")
        _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")

    _render_vlan_description_mappings_editor(rules)


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
        </style>
        """,
        unsafe_allow_html=True,
    )

    current_rules = load_naming_rules()
    SSM.set_naming_rules(current_rules)

    tab_edit, tab_vars, tab_history = st.tabs(["📝 Edit Standards", "📘 Pattern Variables Reference", "📜 Change History"])
    
    with tab_edit:
        with st.expander("🌐 Subnet & VLAN Allocation Presets", expanded=True):
            _vlan_presets_editor(current_rules)

        with st.expander("🔧 Network & Security Devices", expanded=False):
            _check_and_render_banner("network_devices")
            _preset_type_editor("device", get_device_presets(current_rules), current_rules, prefix="branch",
                                card_title="🔧 DEVICE TYPE PRESETS",
                                card_caption="Manage device naming patterns and presets (SW, VS, FW, ION, WAP, RTR, VA).",
                                section="network_devices", section_label="Network & Security Devices")
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
            _preset_type_editor("interface", get_interface_presets(current_rules), current_rules, prefix="iface",
                                card_title="🔌 INTERFACE TYPE PRESETS",
                                card_caption="Manage interface description presets (Uplink, LAG, Po, Access, FW Zone).",
                                section="network_devices", section_label="Network & Security Devices")

        with st.expander("🖥️ Hosts & Virtual Machines", expanded=False):
            _host_editor(current_rules)
            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
            _vm_editor(current_rules)

        with st.expander("☁️ Hypervisor Virtualization & Networking", expanded=False):
            _check_and_render_banner("esxi")
            _preset_type_editor("esxi_network", get_esxi_network_presets(current_rules), current_rules, prefix="esxinet",
                                card_title="☁️️ HYPERVISOR NETWORK DESCRIPTION PRESETS",
                                card_caption="Manage Hypervisor interface descriptions (Uplink, PortGroup, Bridge, VMkernel/Management). Quick Copy dynamically renders from these templates.",
                                section="esxi", section_label="ESXi Virtualization & Networking")

        with st.expander("🛠️ Manage Syntax Auto-Correction Rules", expanded=False):
            _check_and_render_banner("auto_correction")
            _render_auto_correction_manager(active_model)

        _render_csv_schemas_editor(current_rules)

        with st.expander("📋 NetBox Server & Hardware YAML Guidelines", expanded=False):
            _check_and_render_banner("yaml_guidelines")
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
                    SSM.set_naming_rules(rules.copy())
                    st.session_state["card_saved_banner"] = {"section": "yaml_guidelines", "msg": "✅ NetBox Server & Hardware YAML Guidelines saved & applied!", "ts": time.time()}
                    st.rerun()
            with col_reset_yaml:
                if st.button("🔄 Reset to Defaults", width='stretch'):
                    default_yaml = DEFAULT_RULES.get("netbox_server_yaml", DEFAULT_NAMING_PATTERNS.get("netbox_server_yaml", ""))
                    rules = load_naming_rules()
                    rules["netbox_server_yaml"] = default_yaml
                    save_naming_rules(rules, source="YAML Guidelines Reset")
                    SSM.set_naming_rules(rules.copy())
                    st.session_state["card_saved_banner"] = {"section": "yaml_guidelines", "msg": "✅ NetBox Server & Hardware YAML Guidelines reset to defaults!", "ts": time.time()}
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

        # Gather updated dictionary across all scope cards
        all_edited_vars = {}
        for _, _, _, s_key in scope_meta:
            all_edited_vars.update(grouped_vars.get(s_key, {}))

        for scope_code, title, desc, s_key in scope_meta:
            scope_items = grouped_vars.get(s_key, {})
            with st.container(border=True):
                _check_and_render_banner(f"vars_{s_key}")
                col_t1, col_t2 = st.columns([3, 1])
                with col_t1:
                    st.markdown(f"#### {title}")
                with col_t2:
                    st.markdown(f"<div style='text-align: right;'><span style='background-color: #2b313e; padding: 3px 8px; border-radius: 4px; font-size: 0.85em;'>{len(scope_items)} variables</span></div>", unsafe_allow_html=True)
                st.caption(desc)

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
                                all_edited_vars.pop(name, None)
                                _persist_variables(current_rules, all_edited_vars, section_key=s_key)
                                return

                        all_edited_vars[name] = {
                            "label": lbl or name,
                            "placeholder": ph or f"e.g. {name}",
                            "default": df_val,
                            "optional": bool(opt),
                            "scope": s_key
                        }

                # 1. Action Row: Save & Reset
                col_save, col_rst = st.columns([4, 1])
                with col_save:
                    if st.button(f"💾 Save & Apply Changes", key=f"save_scope_{s_key}", type="primary", width="stretch"):
                        _persist_variables(current_rules, all_edited_vars, section_key=s_key)
                with col_rst:
                    if st.button("🔄 Reset Scope", key=f"reset_scope_{s_key}", type="secondary", width="stretch"):
                        from config.naming_rules import PATTERN_VARIABLES
                        default_scope_vars = {k: v for k, v in PATTERN_VARIABLES.items() if v.get("scope") == s_key}
                        all_edited_vars.update(default_scope_vars)
                        st.session_state["card_saved_banner"] = {"section": f"vars_{s_key}", "msg": f"✅ {s_key.title()} Variables reset to defaults!", "ts": time.time()}
                        _persist_variables(current_rules, all_edited_vars, section_key=s_key)

                # 2. Bottom Inline Add Row (Seamless table extension matching Preset style)
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
                if st.button("Reset to Defaults", key="var_reset_all", type="secondary", width='stretch'):
                    _persist_variables(current_rules, dict(PATTERN_VARIABLES))

        with st.expander("🧩 Active Patterns", expanded=False):
            if patterns_now:
                for key, pat in patterns_now.items():
                    st.markdown(f"**{key}:** `{pat}`")

        st.divider()
        st.caption("All variables defined here automatically power input boxes and template resolution across Naming, IPAM, and CSV generators.")
    
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