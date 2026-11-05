import os
import json
import re
import copy
import uuid


import streamlit as st
from config.constants import RULES_FILE
from config.naming_rules import (
    load_naming_rules, save_naming_rules,
    load_history, restore_from_history, clear_history, add_to_history,
    get_naming_patterns,
    get_device_presets, get_interface_presets, get_host_vm_presets, is_vm_preset_key,
    get_vlan_presets, get_vlan_description_mappings, make_preset_key,
    get_site_code_rules,
    default_presets_for, DEFAULT_PRESET_KEY_FIELD, DEFAULT_NAMING_PATTERNS,
    DEFAULT_VM_PRESETS,
    DEFAULT_VLAN_PRESETS, DEFAULT_VLAN_DESCRIPTION_MAPPINGS,
    get_ipam_role_mappings, DEFAULT_IPAM_ROLE_MAPPINGS,
    get_hardware_baseline_standards, DEFAULT_HARDWARE_BASELINE_STANDARDS,
    DEFAULT_HYPERVISOR_PRESETS, get_azure_mappings, DEFAULT_AZURE_MAPPINGS,
    )
from core.extractors import PROCESSING_MODULES, required_template_codes_for
PROCESSING_MODULES_MAP = {m["display_name"]: mid for mid, m in PROCESSING_MODULES.items()}
from core.naming_engine import generate_autocorrect_rule
from core.session_manager import SessionStateManager as SSM
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
# Standardized 3-column ratio for Key-Value mappings: Key, Value, Action
VLAN_DESC_MAPPINGS_COLS = [4.0, 5.0, 1.2]
# Auto-Correction rule columns: Original Pattern, Replacement, Description, Action.
AUTOCORRECT_COLS = [3.5, 3.0, 4.0, 1.0]


def _normalize_var_name(raw: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", raw.strip().lower().replace(" ", "_"))

def find_variable_references(rules: dict, name: str) -> list[str]:
    """Scan naming_patterns and schemas for references to <name> or {name}."""
    refs = []
    patterns = rules.get("naming_patterns", {})
    for k, v in patterns.items():
        if f"<{name}>" in v or f"{{{name}}}" in v:
            refs.append(f"Naming Pattern: {k}")
    schemas = rules.get("csv_schemas", {})
    for k, v in schemas.items():
        row_tpl = " ".join(str(x) for x in v.get("row_template", []))
        sup_tpl = " ".join(str(x) for x in v.get("supernet_template", []))
        if f"<{name}>" in row_tpl or f"{{{name}}}" in row_tpl or f"<{name}>" in sup_tpl or f"{{{name}}}" in sup_tpl:
            refs.append(f"CSV Schema: {k}")
    return refs

def _render_centered_del_btn(key: str, help_text: str = "Delete this entry", disabled: bool = False) -> bool:
    """Helper to render a clean delete icon button."""
    return st.button("🗑️", key=key, help=help_text, width='stretch', disabled=disabled)


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


def _commit_fresh_rules(rules: dict, fresh: dict) -> None:
    """Replace rules in place with fresh, persist to session, refresh, and bump the widget nonce."""
    rules.clear()
    rules.update(fresh)
    SSM.set_naming_rules(fresh.copy())
    SSM.refresh_naming_rules()
    st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1


def _wc_rows(name: str, loader) -> list:
    """Working copy of a row table (unsaved, on-screen only). Built once per session from saved rules."""
    k = f"wc_{name}"
    if k not in st.session_state:
        st.session_state[k] = [dict(r, _uid=uuid.uuid4().hex[:8]) for r in loader()]
    return st.session_state[k]


def _wc_discard(name: str) -> None:
    """Drop the working copy and its widget values (after Save / Reset)."""
    st.session_state.pop(f"wc_{name}", None)
    _clear_session_state_prefixes(f"wc_{name}_")


def _wc_values(rows: list, fields: tuple) -> list:
    return [tuple(str(r.get(f, "")).strip() for f in fields) for r in rows]


def _wc_row_actions(name: str, rows: list, idx: int, min_rows: int = 0) -> None:
    """Up / Down / Delete on the working copy only; saved by Save & Apply Changes."""
    uid = rows[idx]["_uid"]
    c_up, c_dn, c_del = st.columns(PRESET_ACTION_COLS)
    with c_up:
        if idx > 0 and st.button("⬆️", key=f"wc_{name}_{uid}_up", help="Move up"):
            rows[idx - 1], rows[idx] = rows[idx], rows[idx - 1]
            st.rerun()
    with c_dn:
        if idx < len(rows) - 1 and st.button("⬇️", key=f"wc_{name}_{uid}_dn", help="Move down"):
            rows[idx + 1], rows[idx] = rows[idx], rows[idx + 1]
            st.rerun()
    with c_del:
        if st.button("🗑️", key=f"wc_{name}_{uid}_del", help="Delete", disabled=len(rows) <= min_rows):
            rows.pop(idx)
            st.rerun()
def _wc_rows_with_names(name: str, loader) -> tuple:
    rows = _wc_rows(name, loader)
    names = [r.get("name") for r in rows if r.get("name")]
    return rows, names


def _hyp_add_module_cb(selectbox_key: str, platform_key: str) -> None:
    sel_add = st.session_state.get(selectbox_key)
    if sel_add and sel_add != "Choose a module...":
        target_id = PROCESSING_MODULES_MAP.get(sel_add)
        if target_id:
            wc_key = f"wc_hyp_mod_{platform_key}"
            wc_list = st.session_state.get(wc_key, [])
            assigned_ids = [r["mid"] for r in wc_list]
            if target_id not in assigned_ids:
                wc_list.append({"mid": target_id, "_uid": uuid.uuid4().hex[:8]})
                st.session_state[wc_key] = wc_list
            st.session_state[selectbox_key] = "Choose a module..."


def _wc_add_row(name: str, rows: list, fields: list, cols: list) -> None:
    """Inline add row; appends to the working copy only."""
    with st.form(key=f"wc_{name}_addform", clear_on_submit=True):
        cs = st.columns(cols, vertical_alignment="center")
        vals = {}
        for (f, ph), c in zip(fields, cs):
            with c:
                vals[f] = st.text_input(f, value="", placeholder=ph, key=f"wc_{name}_new_{f}", label_visibility="collapsed")
        with cs[-1]:
            added = st.form_submit_button("➕ Add", width="stretch")
    if added:
        if str(vals[fields[0][0]]).strip():
            rows.append({**{f: str(v).strip() for f, v in vals.items()}, "_uid": uuid.uuid4().hex[:8]})
            st.rerun()
        else:
            st.warning("⚠️ Enter a value to add.")


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
        .action-header {
            white-space: nowrap !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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


def _persist_variables(rules: dict, variables: dict, section_key: str = "shared", clear_prefix: str = None) -> None:
    """Persist *variables* into rules["pattern_variables"] by MERGE, not replace.

    Only the entries supplied in *variables* are added/updated/deleted within the
    given *section_key* scope.  Variables belonging to other scopes are preserved
    intact so that editing one scope cannot accidentally wipe another.
    """
    from config.naming_rules import PATTERN_VARIABLES
    current = rules.get("pattern_variables")
    if current is None:
        current = copy.deepcopy(PATTERN_VARIABLES)
    else:
        current = dict(current)
    new_current = {}
    inserted = False
    has_section_key = False
    for name, meta in current.items():
        scope = meta.get("scope") or "shared"
        if scope == section_key:
            has_section_key = True
            if not inserted:
                for vname, vvalue in variables.items():
                    new_current[vname] = vvalue
                inserted = True
            continue
        new_current[name] = meta
    if not has_section_key:
        for vname, vvalue in variables.items():
            new_current[vname] = vvalue
    rules["pattern_variables"] = new_current
    save_naming_rules(rules, source="Variable Manager")
    SSM.set_naming_rules(rules.copy())
    st.session_state["variables_saved"] = True
    if clear_prefix:
        _clear_session_state_prefixes(clear_prefix)
    st.toast(f"✅ {section_key.title()} Variables saved & applied!", icon="💾")
    st.rerun()


def _render_auto_correction_manager(active_model: str) -> None:
    rules = load_auto_corrections()
    categories = list(rules.keys())

    if not categories:
        st.info("No auto-correction categories defined.")
        return

    for category in categories:
        name = f"autocorr_{category}"
        saved = list(rules.get(category, []))
        rows = _wc_rows(name, lambda: [dict(r, _uid=uuid.uuid4().hex[:8]) for r in saved])
        fields = ("pattern", "replacement", "description", "enabled")
        COLS = AUTOCORRECT_COLS
        category_title = {
            "port_shortening": "🔌 Port Abbreviation Rules (Interface Shortening)",
            "vmware": "🔍 OCR Text Cleaning & Syntax Rules",
        }.get(category, f"🛠️ Auto-Correction Rules — {category}")

        with st.expander(category_title, expanded=False):
            st.caption(
                "Each row is a regex pattern → replacement pair. Edit inline or use "
                "the AI generator below to create new rules."
            )

            ch_p, ch_r, ch_d, ch_act = st.columns(COLS, vertical_alignment="center")
            with ch_p:
                st.markdown("**Original Pattern**")
            with ch_r:
                st.markdown("**Replacement**")
            with ch_d:
                st.markdown("**Description**")
            with ch_act:
                pass

            for idx, row in enumerate(list(rows)):
                c1, c2, c3, c4 = st.columns(COLS, vertical_alignment="center")
                with c1:
                    row["pattern"] = st.text_input(
                        "Original Pattern",
                        value=row.get("pattern", ""),
                        key=f"wc_{name}_{row['_uid']}_pattern",
                        label_visibility="collapsed", placeholder=r"(?i)\b(vswitch)(\d+)\b"
                    )
                with c2:
                    row["replacement"] = st.text_input(
                        "Replacement",
                        value=row.get("replacement", ""),
                        key=f"wc_{name}_{row['_uid']}_replacement",
                        label_visibility="collapsed", placeholder=r"vSwitch\2"
                    )
                with c3:
                    row["description"] = st.text_input(
                        "Description",
                        value=row.get("description", ""),
                        key=f"wc_{name}_{row['_uid']}_description",
                        label_visibility="collapsed", placeholder="Describe the rule"
                    )
                with c4:
                    _wc_row_actions(name, rows, idx, min_rows=0)

            if _wc_values(rows, fields[:3]) != [(str(r.get("pattern", "")).strip(), str(r.get("replacement", "")).strip(), str(r.get("description", "")).strip()) for r in saved]:
                st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

            c_s, c_r = st.columns(2)
            with c_s:
                if st.button("💾 Save & Apply Changes", key=f"{name}_save", type="primary", width="stretch"):
                    new_rules = load_auto_corrections()
                    final = []
                    for r in rows:
                        if r["pattern"].strip():
                            ok, msg = validate_regex_replacement(r["pattern"], r["replacement"])
                            if not ok:
                                st.error(f"❌ Cannot save — invalid rule: {msg}")
                                return
                            final.append({
                                "pattern": r["pattern"].strip(),
                                "replacement": r["replacement"].strip(),
                                "description": r["description"].strip(),
                                "enabled": True,
                            })
                    new_rules[category] = final
                    _wc_discard(name)
                    _persist_auto_corrections(new_rules, category)
            with c_r:
                if st.button("🔄 Reset to Defaults", key=f"{name}_reset", width="stretch"):
                    _wc_discard(name)
                    reset_auto_corrections(category)
                    st.toast(f"✅ {category.replace('_', ' ').title()} auto-corrections reset to defaults!", icon="🔄")
                    st.rerun()

            _wc_add_row(name, rows, [("pattern", r"(?i)\b(vswitch)(\d+)\b"), ("replacement", r"vSwitch\2"), ("description", "Describe the rule")], COLS)

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
                            st.session_state[f"wc_{name}_new_pattern"] = result["pattern"]
                            st.session_state[f"wc_{name}_new_replacement"] = result["replacement"]
                            st.session_state[f"wc_{name}_new_description"] = result["description"]
                            st.toast("Rule generated! Review and click '➕ Add' to apply.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ AI rule generation failed: {e}")
                    else:
                        st.warning("⚠️ Please describe the correction first.")


def _render_ipam_role_mapping_manager(active_model: str) -> None:
    name = "ipam_role"
    rules = load_naming_rules()
    saved = list(get_ipam_role_mappings(rules))
    rows = _wc_rows(name, lambda: [dict(r, _uid=uuid.uuid4().hex[:8]) for r in saved])
    fields = ("pattern", "replacement", "description", "enabled")
    COLS = AUTOCORRECT_COLS

    with st.container():
        ch_p, ch_r, ch_d, ch_act = st.columns(COLS, vertical_alignment="center")
        with ch_p:
            st.markdown("**Original Pattern**")
        with ch_r:
            st.markdown("**Replacement**")
        with ch_d:
            st.markdown("**Description**")
        with ch_act:
            pass

        for idx, row in enumerate(list(rows)):
            c1, c2, c3, c4 = st.columns(COLS, vertical_alignment="center")
            with c1:
                row["pattern"] = st.text_input(
                    "Original Pattern",
                    value=row.get("pattern", ""),
                    key=f"wc_{name}_{row['_uid']}_pattern",
                    label_visibility="collapsed", placeholder=r"(?i)^cctv|ip[\s\-]?cam$"
                )
            with c2:
                row["replacement"] = st.text_input(
                    "Replacement",
                    value=row.get("replacement", ""),
                    key=f"wc_{name}_{row['_uid']}_replacement",
                    label_visibility="collapsed", placeholder="Surveillance"
                )
            with c3:
                row["description"] = st.text_input(
                    "Description",
                    value=row.get("description", ""),
                    key=f"wc_{name}_{row['_uid']}_description",
                    label_visibility="collapsed", placeholder="Normalize CCTV variations"
                )
            with c4:
                _wc_row_actions(name, rows, idx, min_rows=0)

        if _wc_values(rows, fields[:3]) != [(str(r.get("pattern", "")).strip(), str(r.get("replacement", "")).strip(), str(r.get("description", "")).strip()) for r in saved]:
            st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

        c_s, c_r = st.columns(2)
        with c_s:
            if st.button("💾 Save & Apply Changes", key=f"{name}_save", type="primary", width="stretch"):
                new_rules = load_naming_rules()
                final = []
                for r in rows:
                    if r["pattern"].strip():
                        ok, msg = validate_regex_replacement(r["pattern"], r["replacement"])
                        if not ok:
                            st.error(f"❌ Cannot save — invalid rule: {msg}")
                            return
                        final.append({
                            "pattern": r["pattern"].strip(),
                            "replacement": r["replacement"].strip(),
                            "description": r["description"].strip(),
                            "enabled": r.get("enabled", True),
                        })
                new_rules["ipam_role_mappings"] = final
                _wc_discard(name)
                _persist_ipam_role_mappings(new_rules)
        with c_r:
            if st.button("🔄 Reset to Defaults", key=f"{name}_reset", width="stretch"):
                _wc_discard(name)
                _reset_ipam_role_mappings()

        _wc_add_row(name, rows, [("pattern", r"(?i)^cctv|ip[\s\-]?cam$"), ("replacement", "Surveillance"), ("description", "Normalize CCTV variations")], COLS)


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


def _render_site_code_mapping_manager(key_prefix: str = "std_scm") -> None:
    from config.naming_rules import get_site_code_rules
    name = f"{key_prefix}_sitecode"
    saved = dict(get_site_code_rules(load_naming_rules()).get("exact_mappings") or {})
    rows = _wc_rows(name, lambda: [{"p": k, "r": v} for k, v in saved.items()])
    COLS = [4.4, 4.4, 1.2]
    with st.expander("📍 Site Code Mapping Rules (City / Location to Code)", expanded=False):
        st.caption(
            "Each row maps a city/location pattern → site code. The Naming tab's Site Code "
            "Assistant uses these exact mappings directly. A location that matches a pattern is "
            "resolved to its code before any algorithmic fallback."
        )
        a, b, _ = st.columns(COLS, vertical_alignment="center")
        a.markdown("**Original Pattern (City / Location)**")
        b.markdown("**Replacement (Site Code)**")
        for idx, row in enumerate(list(rows)):
            c1, c2, c3 = st.columns(COLS, vertical_alignment="center")
            with c1:
                row["p"] = st.text_input("Original Pattern", value=row["p"], key=f"wc_{name}_{row['_uid']}_p", label_visibility="collapsed")
            with c2:
                row["r"] = st.text_input("Replacement", value=row["r"], key=f"wc_{name}_{row['_uid']}_r", label_visibility="collapsed")
            with c3:
                _wc_row_actions(name, rows, idx)
        from config.naming_rules import DEFAULT_SITE_CODE_RULES as _DSR
        _sr = get_site_code_rules(load_naming_rules())

        def _n(v, d):
            try:
                return max(1, min(8, int(str(v).strip())))
            except (ValueError, TypeError):
                return int(d)

        _per_d = list(_DSR["multi_word_chars_per_word"])
        _per = list(_sr.get("multi_word_chars_per_word") or _per_d)
        _w1_s = _n(_per[0], _per_d[0])
        _w2_s = _n(_per[1] if len(_per) > 1 else _per_d[1], _per_d[1])
        _sg_s = _n(_sr.get("single_word_max_chars"), _DSR["single_word_max_chars"])
        _fb_s = str(_sr.get("fallback") or _DSR["fallback"])
        st.markdown("**Fallback when no mapping matches**")
        st.caption(
            "Used only when the location is not in the table above. Two or more words: first N letters of "
            "word 1 + first N letters of word 2. One word: its first N letters. Empty input: the fallback text."
        )
        f1, f2, f3, f4 = st.columns(4, vertical_alignment="bottom")
        with f1:
            _fb_w1 = st.number_input("Letters from word 1", min_value=1, max_value=8, value=_w1_s, step=1, key=f"{name}_fb_w1")
        with f2:
            _fb_w2 = st.number_input("Letters from word 2", min_value=1, max_value=8, value=_w2_s, step=1, key=f"{name}_fb_w2")
        with f3:
            _fb_sg = st.number_input("Letters for single word", min_value=1, max_value=8, value=_sg_s, step=1, key=f"{name}_fb_sg")
        with f4:
            _fb_txt = st.text_input("Empty-input text", value=_fb_s, key=f"{name}_fb_txt")
        _fb_dirty = (int(_fb_w1), int(_fb_w2), int(_fb_sg), str(_fb_txt).strip()) != (_w1_s, _w2_s, _sg_s, _fb_s)
        if _wc_values(rows, ("p", "r")) != [(str(k).strip(), str(v).strip()) for k, v in saved.items()] or _fb_dirty:
            st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")
        c_s, c_r = st.columns(2)
        with c_s:
            if st.button("💾 Save & Apply Changes", key=f"{name}_save", type="primary", width="stretch"):
                new_rules = load_naming_rules()
                new_rules["site_code_rules"] = dict(get_site_code_rules(new_rules))
                new_rules["site_code_rules"]["multi_word_chars_per_word"] = [int(_fb_w1), int(_fb_w2)]
                new_rules["site_code_rules"]["single_word_max_chars"] = int(_fb_sg)
                new_rules["site_code_rules"]["fallback"] = str(_fb_txt).strip() or _fb_s
                new_rules["site_code_rules"]["exact_mappings"] = {
                    r["p"].strip().lower(): r["r"].strip().upper() for r in rows if r["p"].strip()}
                _wc_discard(name)
                _persist_site_code_mappings(new_rules)
        with c_r:
            if st.button("🔄 Reset to Defaults", key=f"{name}_reset", width="stretch"):
                _wc_discard(name)
                _reset_site_code_mappings()
        _wc_add_row(name, rows, [("p", "e.g. bristol"), ("r", "e.g. BRI")], COLS)


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
    _clear_session_state_prefixes("sitecode_", "ac_scm_sitecode_")
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
    _commit_fresh_rules(rules, fresh)
    st.toast(f"✅ {section_label} saved & applied!", icon="💾")
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

    # Load custom order or fallback to first parsed platform (dictionary order)
    saved_order = rules.get("hypervisor_platform_order") or []
    parsing_key_set = set(parsing_keys) if isinstance(parsing_keys, list) else set()
    ordered_platforms = [p for p in saved_order if p in parsing_key_set]
    for p in parsing_keys:
        if p not in ordered_platforms:
            ordered_platforms.append(p)

    all_platforms = ordered_platforms if ordered_platforms else parsing_keys[:1]

    plat_rows, names = _wc_rows_with_names("hyp_platforms", lambda: [{"name": p} for p in all_platforms])

    CREATE_OPTION = "+ Create New Platform..."
    dropdown_options = names + [CREATE_OPTION]

    cur_sel = st.session_state.get("sel_unified_platform", names[0] if names else "")
    if cur_sel not in dropdown_options:
        cur_sel = names[0] if names else ""

    c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
    with c_hdr_left:
        st.caption("Manage hypervisor platforms, standardized NetBox description templates, token aliases, and AI parsing rules.")
    with c_hdr_right:
        st.markdown(
            f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
            f"{len(names)} platforms</div>",
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
            index=dropdown_options.index(cur_sel) if cur_sel in dropdown_options else 0,
            key="sel_hypervisor_platform_choice"
        )
        st.session_state["sel_unified_platform"] = sel_choice

    with col_plat_actions:
        c_up, c_dn, c_del = st.columns(3)
        if sel_choice in names and sel_choice != CREATE_OPTION:
            cur_idx = names.index(sel_choice)
            if c_up.button("⬆️", key="btn_plat_order_up", disabled=(cur_idx == 0), help="Move Platform Up", width="stretch"):
                plat_rows[cur_idx], plat_rows[cur_idx - 1] = plat_rows[cur_idx - 1], plat_rows[cur_idx]
                st.session_state["pending_hypervisor_platform"] = sel_choice
                st.session_state.pop(
                    "sel_hypervisor_platform_choice",
                    None,
                )
                st.rerun()

            if c_dn.button("⬇️", key="btn_plat_order_dn", disabled=(cur_idx == len(names) - 1), help="Move Platform Down", width="stretch"):
                plat_rows[cur_idx], plat_rows[cur_idx + 1] = plat_rows[cur_idx + 1], plat_rows[cur_idx]
                st.session_state["pending_hypervisor_platform"] = sel_choice
                st.session_state.pop(
                    "sel_hypervisor_platform_choice",
                    None,
                )
                st.rerun()

            if c_del.button("🗑️", key="btn_plat_del", disabled=(len(plat_rows) <= 1), help=f"Delete platform '{sel_choice}'. At least one platform must remain.", width="stretch"):
                plat_rows.pop(cur_idx)
                st.session_state["pending_hypervisor_platform"] = plat_rows[0]["name"] if plat_rows else ""
                st.rerun()

    if sel_choice == CREATE_OPTION:
        st.markdown("##### ➕ Create New Platform Preset")
        new_plat_name = st.text_input("New Platform Name", placeholder="e.g. Nutanix AHV, OpenStack").strip()
        st.selectbox("Copy templates and settings from", names, key="new_plat_copy_from", help="The new platform starts as a copy of this platform (description templates, processing modules, role options, teaming rules). Edit it afterwards.")
        c_add_btn, c_cancel_btn = st.columns([2, 2])
        with c_add_btn:
            if st.button("➕ Create Platform", type="primary", width="stretch"):
                if not new_plat_name:
                    st.error("Platform name cannot be empty.")
                elif new_plat_name in names:
                    st.warning(f"Platform '{new_plat_name}' already exists.")
                else:
                    _copy_src = st.session_state.get("new_plat_copy_from") or ""
                    hyp_presets[new_plat_name] = copy.deepcopy(hyp_presets.get(_copy_src) or [])
                    parsing_presets[new_plat_name] = {
                        "platform": new_plat_name,
                        "aliases": {},
                        "instructions": f"[PLATFORM ARCHITECTURE: {new_plat_name.upper()}]\n1. Extract network topology into atomic attributes.",
                        "export_prompt": f"You are an expert network engineer specializing in {new_plat_name}...",
                    }
                    _src_cfg = parsing_presets.get(_copy_src)
                    if isinstance(_src_cfg, dict):
                        for _k, _v in _src_cfg.items():
                            if _k not in ("platform", "aliases", "instructions", "export_prompt"):
                                parsing_presets[new_plat_name][_k] = copy.deepcopy(_v)
                    rules["hypervisor_presets"] = hyp_presets
                    rules["topology_parsing_presets"] = parsing_presets
                    save_naming_rules(rules, source=f"Create Platform {new_plat_name}")
                    plat_rows.append({"name": new_plat_name, "_uid": uuid.uuid4().hex[:8]})
                    SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                    st.session_state["sel_unified_platform"] = new_plat_name
                    st.toast(f"✅ Platform '{new_plat_name}' created!", icon="💾")
                    st.rerun()
        with c_cancel_btn:
            if st.button("❌ Cancel", width="stretch"):
                st.session_state["pending_hypervisor_platform"] = names[0] if names else ""
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
    # Only seed missing keys when the user has NOT explicitly saved a value
    # (empty string is a valid intentional choice).  Check session-state flags
    # written by save handlers to distinguish "first render" from "user cleared".
    _seeded_key = f"_plat_parsing_seeded_{active_plat}"
    if not st.session_state.get(_seeded_key, False):
        if not plat_parsing.get("instructions") and default_fallback.get("instructions"):
            plat_parsing["instructions"] = default_fallback["instructions"]
        if not plat_parsing.get("export_prompt") and default_fallback.get("export_prompt"):
            plat_parsing["export_prompt"] = default_fallback["export_prompt"]
        st.session_state[_seeded_key] = True

    # ──────────────────────────────────────────────────────────────────────
    san = re.sub(r"[^a-z0-9]+", "_", active_plat.lower()).strip("_")
    cur_modules = plat_parsing.get("modules")
    if not isinstance(cur_modules, list):
        cur_modules = []
    mod_rows = _wc_rows(f"hyp_mod_{san}", lambda: [{"mid": m} for m in cur_modules])
    assigned_ids = [r["mid"] for r in mod_rows]
    all_mod_ids = list(PROCESSING_MODULES.keys())
    available_ids = [mid for mid in all_mod_ids if mid not in assigned_ids]

    st.markdown("**Processing Modules**")

    if assigned_ids:
        for mi, mid in enumerate(assigned_ids):
            mod = PROCESSING_MODULES.get(mid, {})
            lbl = mod.get("display_name", mid)
            kind = mod.get("input_kind", "unknown")
            c_lbl, c_act = st.columns([7, 3], vertical_alignment="center")
            with c_lbl:
                st.markdown(f"**{mi + 1}. {lbl}** :gray[{kind}]")
            with c_act:
                _wc_row_actions(f"hyp_mod_{san}", mod_rows, mi, min_rows=0)

    if assigned_ids != list(cur_modules):
        st.caption("⚠️ Unsaved module changes — click Save & Apply Changes to keep them.")

    c_add, c_empty = st.columns([2, 8])
    with c_add:
        add_display_names = {PROCESSING_MODULES[mid]["display_name"]: mid for mid in available_ids}
        add_opts = ["Choose a module..."] + list(add_display_names.keys())
        sel_add = st.selectbox(
            "Add Module",
            options=add_opts,
            label_visibility="collapsed",
            key=f"plat_mod_add_{active_plat}",
            on_change=_hyp_add_module_cb,
            args=(f"plat_mod_add_{active_plat}", san),
        )

    mods_dirty = False
    for _p, _cfg in parsing_presets.items():
        _k = "wc_hyp_mod_" + re.sub(r"[^a-z0-9]+", "_", _p.lower()).strip("_")
        if _k in st.session_state:
            _saved = _cfg.get("modules") if isinstance(_cfg, dict) else None
            if [r["mid"] for r in st.session_state[_k]] != list(_saved or []):
                mods_dirty = True

    if names != all_platforms or mods_dirty:
        st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

    c_save, c_reset = st.columns([1, 1])
    with c_save:
        if st.button("💾 Save & Apply Changes", key="hyp_plat_save", type="primary", width="stretch"):
            new_order = names
            rules_to_save = dict(rules)
            rules_to_save["hypervisor_platform_order"] = new_order
            for plat in list(hyp_presets.keys()):
                if plat not in new_order:
                    hyp_presets.pop(plat, None)
            for plat in list(parsing_presets.keys()):
                if plat not in new_order:
                    parsing_presets.pop(plat, None)
            for plat in list(parsing_presets.keys()):
                if plat in new_order:
                    plat_san = re.sub(r"[^a-z0-9]+", "_", plat.lower()).strip("_")
                    wc_key = f"wc_hyp_mod_{plat_san}"
                    if wc_key in st.session_state:
                        parsing_presets.setdefault(plat, {})["modules"] = [r["mid"] for r in st.session_state[wc_key]]
            rules_to_save["hypervisor_presets"] = hyp_presets
            rules_to_save["topology_parsing_presets"] = parsing_presets
            save_naming_rules(rules_to_save, source="Save Hypervisor Platforms")
            fresh = SSM.get_cached_naming_rules() or load_naming_rules()
            _commit_fresh_rules(rules, fresh)
            _wc_discard("hyp_platforms")
            _clear_session_state_prefixes("wc_hyp_mod_")
            st.toast("Hypervisor platforms saved")
            st.rerun()
    with c_reset:
        if st.button("🔄 Reset to Defaults", key="hyp_plat_reset", width="stretch"):
            _wc_discard("hyp_platforms")
            _clear_session_state_prefixes("wc_hyp_mod_")
            default_platforms = [p for p in list(DEFAULT_HYPERVISOR_PRESETS.keys()) if p in parsing_key_set]
            remaining = [p for p in ordered_platforms if p not in default_platforms]
            final_order = default_platforms + remaining
            rules_to_save = dict(rules)
            rules_to_save["hypervisor_platform_order"] = final_order
            save_naming_rules(rules_to_save, source="Reset Hypervisor Platforms")
            fresh = SSM.get_cached_naming_rules() or load_naming_rules()
            _commit_fresh_rules(rules, fresh)
            st.toast("Hypervisor platforms reset to defaults")
            st.rerun()

    st.caption("Platform order, removal and processing modules apply after Save & Apply Changes.")

    # Section 1: NetBox Description Templates
    # ──────────────────────────────────────────────────────────────────────
    with st.expander(f"🏷️ 1. Description Templates ({len(plat_patterns)} templates)", expanded=False):
        name = f"hyp_tpl_{re.sub(r'[^a-z0-9]+', '_', active_plat.lower()).strip('_')}"
        rows = _wc_rows(name, lambda: [{"code": r.get("code",""), "label": r.get("label",""), "pattern": r.get("pattern",""),
                                         "hidden": bool(r.get("hidden", False))} for r in plat_patterns])
        fields = ("code", "label", "pattern", "hidden")
        HYP_TPL_COLS = [1.2, 2.2, 4.4, 0.8, 1.4]
        _have_codes = {str(r.get("code", "")).strip() for r in rows if not r.get("hidden")}
        _lack_codes = [c for c in required_template_codes_for(plat_parsing.get("modules", [])) if c not in _have_codes]
        if _lack_codes:
            st.caption(
                "⚠️ Missing or hidden required template(s): " + ", ".join(_lack_codes)
                + ". Naming > Screenshot Batch cannot generate output for this platform until they exist and are not hidden."
            )

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

        for idx, row in enumerate(list(rows)):
            c0, c1, c2, c3, c4 = st.columns(HYP_TPL_COLS, vertical_alignment="center")
            with c0:
                row["code"] = st.text_input("Code", value=row.get("code", ""), key=f"wc_{name}_{row['_uid']}_code", label_visibility="collapsed")
            with c1:
                row["label"] = st.text_input("Label", value=row.get("label", ""), key=f"wc_{name}_{row['_uid']}_label", label_visibility="collapsed")
            with c2:
                row["pattern"] = st.text_input("Pattern", value=row.get("pattern", ""), key=f"wc_{name}_{row['_uid']}_pattern", label_visibility="collapsed")
            with c3:
                hidden_val = st.checkbox("Hide", value=bool(row.get("hidden", False)), key=f"wc_{name}_{row['_uid']}_hidden", label_visibility="collapsed")
                row["hidden"] = bool(hidden_val)
            with c4:
                _wc_row_actions(name, rows, idx)

        saved_values = [(r.get("code",""), r.get("label",""), r.get("pattern",""), str(bool(r.get("hidden", False)))) for r in plat_patterns]
        if _wc_values(rows, fields) != saved_values:
            st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

        c_s, c_r = st.columns(2)
        with c_s:
            if st.button("💾 Save & Apply Changes", key=f"btn_save_s1_{active_plat}", type="primary", width="stretch"):
                new_list = []
                for r in rows:
                    if r.get("code","").strip():
                        new_list.append({
                            "code": r["code"].strip(),
                            "label": r.get("label","").strip() or r["code"].strip(),
                            "pattern": r.get("pattern","").strip(),
                            "hidden": bool(r.get("hidden", False))
                        })
                hyp_presets[active_plat] = new_list
                rules["hypervisor_presets"] = hyp_presets
                save_naming_rules(rules, source=f"Save Section 1 for {active_plat}")
                _wc_discard(name)
                fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                _commit_fresh_rules(rules, fresh)
                st.toast(f"✅ Section 1 (Description Templates) for '{active_plat}' saved & applied!", icon="💾")
                st.rerun()
        with c_r:
            if st.button("🔄 Reset to Defaults", key=f"btn_reset_s1_{active_plat}", width="stretch"):
                default_hyp = DEFAULT_HYPERVISOR_PRESETS.get(active_plat)
                hyp_presets[active_plat] = [dict(t) for t in (default_hyp if default_hyp is not None else plat_patterns)]
                rules["hypervisor_presets"] = hyp_presets
                save_naming_rules(rules, source=f"Reset Section 1 for {active_plat}")
                _wc_discard(name)
                SSM.set_naming_rules(SSM.get_cached_naming_rules() or load_naming_rules())
                st.toast(f"✅ Section 1 reset to defaults for '{active_plat}'!", icon="🔄")
                st.rerun()

        _wc_add_row(name, rows, [("code","e.g. Trunk"),("label","e.g. Trunk Adapter"),("pattern","<interface> (<vlan> VLAN)")], HYP_TPL_COLS)

    # ──────────────────────────────────────────────────────────────────────
    # Section 2: Token Normalizations (OCR Typo Corrections)
    # ──────────────────────────────────────────────────────────────────────
    norm_rules = plat_parsing.get("token_normalizations", [])
    if not isinstance(norm_rules, list):
        norm_rules = []

    with st.expander(f"🔤 2. Token Normalizations & OCR Typo Corrections ({len(norm_rules)})", expanded=False):
        st.caption("Auto-correct OCR character jitter, split spaces, and platform typos before topology clustering (Regex Pattern -> Replacement).")

        col_p, col_r, col_del = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
        with col_p:
            st.markdown("**Original Pattern (Regex)**")
        with col_r:
            st.markdown("**Replacement**")
        with col_del:
            pass

        updated_norms = []
        for idx, rule in enumerate(norm_rules):
            pat = rule.get("pattern", "")
            rep = rule.get("replace", "")

            cp, cr, cd = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
            with cp:
                n_pat = st.text_input("Pattern", value=pat, key=f"norm_pat_{active_plat}_{idx}", label_visibility="collapsed").strip()
            with cr:
                n_rep = st.text_input("Replace", value=rep, key=f"norm_rep_{active_plat}_{idx}", label_visibility="collapsed").strip()
            with cd:
                if _render_centered_del_btn(f"norm_del_{active_plat}_{idx}", "Delete normalization rule"):
                    continue

            if n_pat:
                updated_norms.append({"pattern": n_pat, "replace": n_rep})

        with st.form(key=f"norm_add_form_{active_plat}", clear_on_submit=True):
            ca1, ca2, ca3 = st.columns([4.4, 4.4, 1.2], vertical_alignment="center")
            with ca1:
                new_pat = st.text_input("New Pattern", value="", key=f"norm_new_p_{active_plat}", placeholder="e.g. (?i)Netwrork", label_visibility="collapsed")
            with ca2:
                new_rep = st.text_input("New Replace", value="", key=f"norm_new_r_{active_plat}", placeholder="e.g. Network", label_visibility="collapsed")
            with ca3:
                add_norm = st.form_submit_button("➕ Add", width="stretch")

            if add_norm and new_pat.strip():
                updated_norms.append({"pattern": new_pat.strip(), "replace": new_rep.strip()})
                plat_parsing["token_normalizations"] = updated_norms
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Add normalization to {active_plat}")
                SSM.set_naming_rules(rules.copy())
                st.toast("✅ Normalization rule added!", icon="💾")
                st.rerun()

        st.divider()
        col_s2, col_r2 = st.columns(2)
        with col_s2:
            if st.button("💾 Save & Apply Normalizations", key=f"btn_save_norms_{active_plat}", type="primary", width="stretch"):
                plat_parsing["token_normalizations"] = updated_norms
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Save Normalizations for {active_plat}")
                SSM.refresh_naming_rules()
                st.toast(f"✅ Normalizations for {active_plat} saved!", icon="💾")
                st.rerun()
        with col_r2:
            if st.button("🔄 Reset to Default Normalizations", key=f"btn_reset_norms_{active_plat}", width="stretch"):
                plat_parsing["token_normalizations"] = DEFAULT_PARSING_PRESETS.get(active_plat, {}).get("token_normalizations", [])
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Reset Normalizations for {active_plat}")
                SSM.refresh_naming_rules()
                st.toast(f"✅ Normalizations reset for {active_plat}!", icon="🔄")
                st.rerun()

    # ──────────────────────────────────────────────────────────────────────
    # Section 3: Topology Noise Filters (True noise_filters Binding)
    # ──────────────────────────────────────────────────────────────────────
    noise_filters = plat_parsing.get("noise_filters", [])
    if not isinstance(noise_filters, list):
        noise_filters = []

    with st.expander(f"🔇 3. Topology Noise Filters ({len(noise_filters)})", expanded=False):
        st.caption("Tokens or regex patterns matching unwanted UI chrome, button labels, and icon artifacts to silently discard.")
        noise_text = "\n".join(str(t).strip() for t in noise_filters if str(t).strip())
        edited_noise = st.text_area(
            "Noise Tokens / Patterns (one per line)",
            value=noise_text,
            height=200,
            key=f"txt_noise_filters_{active_plat}",
            help="One token or regex per line. Directly used by core/extractors during spatial reduction."
        )

        parsed_noise = [t.strip() for t in edited_noise.split("\n") if t.strip()]

        col_s3, col_r3 = st.columns(2)
        with col_s3:
            if st.button("💾 Save & Apply Noise Filters", key=f"btn_save_noise_{active_plat}", type="primary", width="stretch"):
                plat_parsing["noise_filters"] = parsed_noise
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Save Noise Filters for {active_plat}")
                SSM.refresh_naming_rules()
                st.toast(f"✅ Noise filters for {active_plat} saved!", icon="💾")
                st.rerun()
        with col_r3:
            if st.button("🔄 Reset Noise Filters", key=f"btn_reset_noise_{active_plat}", width="stretch"):
                plat_parsing["noise_filters"] = DEFAULT_PARSING_PRESETS.get(active_plat, {}).get("noise_filters", [])
                parsing_presets[active_plat] = plat_parsing
                rules["topology_parsing_presets"] = parsing_presets
                save_naming_rules(rules, source=f"Reset Noise Filters for {active_plat}")
                SSM.refresh_naming_rules()
                st.toast(f"✅ Noise filters reset for {active_plat}!", icon="🔄")
                st.rerun()

def _preset_type_editor(kind: str, presets: list, rules: dict, prefix: str, card_title: str, card_caption: str,
                          section: str = "presets", section_label: str = "Presets", expanded: bool = False,
                          wrap_expander: bool = True) -> None:
    name = f"preset_{kind}"
    key_field = DEFAULT_PRESET_KEY_FIELD[kind]
    saved = presets
    rows = _wc_rows(name, lambda: [dict(r, _uid=uuid.uuid4().hex[:8]) for r in saved])

    expander_context = st.expander(card_title, expanded=expanded) if wrap_expander else st.container()
    with expander_context:
        if wrap_expander:
            c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
            with c_hdr_left:
                if card_caption and card_caption.strip():
                    st.caption(card_caption)
            with c_hdr_right:
                st.markdown(
                    f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
                    f"{len(rows)} presets</div>",
                    unsafe_allow_html=True
                )

        _inject_preset_table_style()

        h_code, h_label, h_pattern, h_act = st.columns(PRESET_COLS, vertical_alignment="center")
        with h_code:
            st.markdown("**Code**")
        with h_label:
            st.markdown("**Label**")
        with h_pattern:
            st.markdown("**Pattern Template**")
        with h_act:
            st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

        patterns = dict(rules.get("naming_patterns") or {})
        patterns_updates = {}
        fields = ("code", "label", "tpl", "pattern_key", "description")
        cols = PRESET_COLS

        if rows:
            for idx, row in enumerate(list(rows)):
                uid = row["_uid"]
                orig_pkey = row.get("pattern_key", "")
                orig_tpl = patterns.get(orig_pkey, "")

                c_code, c_label, c_pattern, c_actions = st.columns(cols, vertical_alignment="center")
                with c_code:
                    row["code"] = st.text_input("Code", value=row.get("code", ""), key=f"wc_{name}_{uid}_code", label_visibility="collapsed").strip()
                with c_label:
                    row["label"] = st.text_input("Label", value=row.get("label", ""), key=f"wc_{name}_{uid}_label", label_visibility="collapsed").strip()
                with c_pattern:
                    row["tpl"] = st.text_input("Pattern Template", value=row.get("tpl", orig_tpl), key=f"wc_{name}_{uid}_tpl", label_visibility="collapsed").strip()
                with c_actions:
                    _wc_row_actions(name, rows, idx, min_rows=0)

                ncode = row.get("code", "")
                nlbl = row.get("label", "")
                ntpl = row.get("tpl", "")
                final_pkey = row.get("pattern_key", "") or make_preset_key(ncode or nlbl, prefix)
                if ntpl:
                    patterns_updates[final_pkey] = ntpl
                row["pattern_key"] = final_pkey
        else:
            st.info("No presets defined. Add one below.")

        if _wc_values(rows, ("code", "label", "tpl")) != [(str(r.get("code", "")).strip(), str(r.get("label", "")).strip(), str(patterns.get(r.get("pattern_key", ""), "")).strip()) for r in saved]:
            st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

        c_save, c_reset = st.columns(2)
        with c_save:
            saved_presets = st.button("💾 Save & Apply Changes", key=f"{name}_save", type="primary", width="stretch")
        with c_reset:
            reset_presets = st.button("🔄 Reset to Defaults", key=f"{name}_reset", width="stretch")

        _wc_add_row(name, rows, [
            ("code", "e.g. DSwitch"),
            ("label", "e.g. Distributed Switch Uplink"),
            ("tpl", "e.g. <interface> - <parent> <purpose>"),
        ], cols)

    if reset_presets:
        _wc_discard(name)
        _reset_presets(kind, rules, section=section, section_label=section_label)
        return

    if saved_presets:
        _missing = [str(r.get("code", "")).strip() or str(r.get("label", "")).strip() or "(blank)" for r in rows if not str(r.get("code", "")).strip() or not str(r.get("tpl", "")).strip()]
        if _missing:
            st.toast("Not saved: Code and Pattern Template are required for " + ", ".join(_missing), icon="⚠️")
            return
        final_presets = []
        for row in rows:
            ncode = row.get("code", "").strip()
            ntpl = row.get("tpl", "").strip()
            if not ncode or not ntpl:
                continue
            final_pkey = row.get("pattern_key", "") or make_preset_key(ncode or row.get("label", ""), prefix)
            final_presets.append({
                "code": ncode,
                "label": row.get("label", "").strip() or ncode,
                "pattern_key": final_pkey,
                "description": row.get("description", ""),
            })

        final_patterns = dict(patterns)
        final_patterns.update(patterns_updates)

        _wc_discard(name)
        rules["naming_patterns"] = final_patterns
        rules[key_field] = final_presets
        _save_presets(rules, section=section, section_label=section_label)
        return
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
    host_presets = [p for p in all_presets if not is_vm_preset_key(p.get("pattern_key"))]
    vm_presets = [p for p in all_presets if is_vm_preset_key(p.get("pattern_key"))]
    patterns = dict(rules.get("naming_patterns") or {})

    name = "host_presets"
    rows = _wc_rows(name, lambda: host_presets)

    c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
    with c_hdr_left:
        st.caption("Manage physical hypervisor host naming patterns and presets.")
    with c_hdr_right:
        st.markdown(
            f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
            f"{len(rows)} presets</div>",
            unsafe_allow_html=True
        )

    _inject_preset_table_style()

    h_code, h_label, h_pattern, h_act = st.columns(PRESET_COLS, vertical_alignment="center")
    with h_code:
        st.markdown("**Code**")
    with h_label:
        st.markdown("**Label**")
    with h_pattern:
        st.markdown("**Pattern Template**")
    with h_act:
        st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

    fields = ("code", "label", "tpl", "pattern_key", "description")
    for idx, row in enumerate(list(rows)):
        pk = row.get("pattern_key", "")
        tpl = patterns.get(pk, "")

        c_code, c_label, c_pattern, c_actions = st.columns(PRESET_COLS, vertical_alignment="center")
        with c_code:
            row["code"] = st.text_input("Code", value=row.get("code", ""), key=f"wc_{name}_{row['_uid']}_code", label_visibility="collapsed").strip()
        with c_label:
            row["label"] = st.text_input("Label", value=row.get("label", ""), key=f"wc_{name}_{row['_uid']}_label", label_visibility="collapsed").strip()
        with c_pattern:
            row["tpl"] = st.text_input("Pattern Template", value=row.get("tpl", tpl), key=f"wc_{name}_{row['_uid']}_tpl", label_visibility="collapsed").strip()
        with c_actions:
            _wc_row_actions(name, rows, idx, min_rows=1)

        pk = row.get("pattern_key", "")
        if not pk:
            ncode = row.get("code", "").strip()
            nlbl = row.get("label", "").strip()
            row["pattern_key"] = make_preset_key(ncode or nlbl, "host_vm")

    current_presets = [(r.get("code", ""), r.get("label", ""), r.get("tpl", ""), r.get("pattern_key", ""), r.get("description", "")) for r in rows]
    saved_presets = [(p.get("code", ""), p.get("label", ""), patterns.get(p.get("pattern_key", ""), ""), p.get("pattern_key", ""), p.get("description", "")) for p in host_presets]
    if current_presets != saved_presets:
        st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

    c_save, c_reset = st.columns(2)
    with c_save:
        saved = st.button("💾 Save & Apply Changes", key=f"{name}_save", type="primary", width='stretch')
    with c_reset:
        reset = st.button("🔄 Reset to Defaults", key=f"{name}_reset", width='stretch')

    _wc_add_row(name, rows, [("code", "e.g. HYPV"), ("label", "e.g. Hyper-V Host"), ("tpl", "e.g. <site_prefix>hyp<seq>.<domain>")], PRESET_COLS)

    if reset:
        _wc_discard(name)
        new_rules = load_naming_rules()
        new_patterns = dict(new_rules.get("naming_patterns") or {})
        keep_vm = [copy.deepcopy(p) for p in get_host_vm_presets(new_rules) if is_vm_preset_key(p.get("pattern_key"))]
        reset_hosts = [copy.deepcopy(p) for p in default_presets_for("host_vm") if not is_vm_preset_key(p.get("pattern_key"))]
        for p in reset_hosts:
            pk = p.get("pattern_key", "")
            if pk in DEFAULT_NAMING_PATTERNS:
                new_patterns[pk] = DEFAULT_NAMING_PATTERNS[pk]
        new_rules["naming_patterns"] = new_patterns
        new_rules["host_vm_presets"] = reset_hosts + keep_vm
        _save_presets(new_rules, section="hosts", section_label="Hosts Type Presets")
        return

    if saved:
        _missing = [str(r.get("code", "")).strip() or str(r.get("label", "")).strip() or "(blank)"
                    for r in rows if not str(r.get("code", "")).strip() or not str(r.get("tpl", "")).strip()]
        if _missing:
            st.toast("Not saved: Code and Pattern Template are required for " + ", ".join(_missing), icon="⚠️")
            return
        final_host = []
        final_patterns = dict(rules.get("naming_patterns") or {})
        for row in rows:
            ncode = row.get("code", "").strip()
            nlbl = row.get("label", "").strip()
            ntpl = row.get("tpl", "").strip()
            if not ncode or not ntpl:
                continue
            pk = row.get("pattern_key", "")
            if not pk:
                pk = make_preset_key(ncode, "host_vm")
            final_host.append({
                "code": ncode,
                "label": nlbl,
                "pattern_key": pk,
                "description": row.get("description", ""),
            })
            final_patterns[pk] = ntpl
        new_rules = load_naming_rules()
        saved_vm = [copy.deepcopy(p) for p in vm_presets]
        new_rules["host_vm_presets"] = final_host + saved_vm
        new_rules["naming_patterns"] = final_patterns
        _wc_discard(name)
        _save_presets(new_rules, section="hosts", section_label="Hosts Type Presets")
        return

    if st.session_state.pop("host_preset_min_one", False):
        st.warning("⚠️ At least one preset must remain. Delete a different entry first.")


def _vm_editor(rules: dict) -> None:
    import re as _re
    name = "vm_presets"
    patterns = dict(rules.get("naming_patterns") or {})
    saved_vm = [dict(p, tpl=str(patterns.get(p.get("pattern_key", ""), "")))
                for p in get_host_vm_presets(rules) if is_vm_preset_key(p.get("pattern_key"))]
    rows = _wc_rows(name, lambda: [dict(p) for p in saved_vm])
    cols = PRESET_COLS

    c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
    with c_hdr_left:
        st.caption(
            "Manage virtual machine roles (" + ", ".join(str(p.get("code", "")) for p in saved_vm)
            + "). Each role has its own hostname template."
        )
    with c_hdr_right:
        st.markdown(
            f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>"
            f"{len(rows)} presets</div>",
            unsafe_allow_html=True
        )

    _inject_preset_table_style()

    h_code, h_label, h_tpl, h_act = st.columns(cols, vertical_alignment="center")
    with h_code:
        st.markdown("**Code**")
    with h_label:
        st.markdown("**Label**")
    with h_tpl:
        st.markdown("**Pattern Template**")
    with h_act:
        st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)

    for idx, row in enumerate(list(rows)):
        uid = row["_uid"]
        c1, c2, c3, c4 = st.columns(cols, vertical_alignment="center")
        with c1:
            row["code"] = st.text_input("Code", value=row.get("code", ""), key=f"wc_{name}_{uid}_code", label_visibility="collapsed").strip().lower()
        with c2:
            row["label"] = st.text_input("Label", value=row.get("label", ""), key=f"wc_{name}_{uid}_label", label_visibility="collapsed").strip()
        with c3:
            row["tpl"] = st.text_input("Pattern Template", value=row.get("tpl", ""), key=f"wc_{name}_{uid}_tpl", label_visibility="collapsed").strip()
        with c4:
            _wc_row_actions(name, rows, idx, min_rows=1)

    if _wc_values(rows, ("code", "label", "tpl")) != [(str(r.get("code", "")).strip().lower(), str(r.get("label", "")).strip(), str(r.get("tpl", "")).strip()) for r in saved_vm]:
        st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

    c_s, c_r = st.columns(2)
    with c_s:
        if st.button("💾 Save & Apply Changes", key=f"{name}_save", type="primary", width="stretch"):
            _missing = [str(r.get("code", "")).strip() or str(r.get("label", "")).strip() or "(blank)"
                        for r in rows if not str(r.get("code", "")).strip() or not str(r.get("tpl", "")).strip()]
            if _missing:
                st.toast("Not saved: Code and Pattern Template are required for " + ", ".join(_missing), icon="⚠️")
                return
            _codes = [str(r.get("code", "")).strip().lower() for r in rows]
            _dups = sorted({c for c in _codes if _codes.count(c) > 1})
            if _dups:
                st.toast("Not saved: duplicate VM code " + ", ".join(_dups), icon="⚠️")
                return
            new_rules = load_naming_rules()
            new_patterns = dict(new_rules.get("naming_patterns") or {})
            host_presets = [p for p in get_host_vm_presets(new_rules) if not is_vm_preset_key(p.get("pattern_key"))]
            # "vm_host" is the legacy shared key: reserved so no new role reuses it.
            used = {p.get("pattern_key") for p in host_presets} | {"vm_host"}
            final_vm = []
            for r in rows:
                code = str(r.get("code", "")).strip().lower()
                pk = str(r.get("pattern_key", "") or "")
                if not is_vm_preset_key(pk) or pk in used:
                    base = "vm_" + (_re.sub(r"[^a-z0-9]+", "_", code).strip("_") or "role")
                    pk, n = base, 2
                    while pk in used:
                        pk = f"{base}_{n}"
                        n += 1
                used.add(pk)
                new_patterns[pk] = str(r.get("tpl", "")).strip()
                final_vm.append({
                    "code": code,
                    "label": str(r.get("label", "")).strip() or code,
                    "pattern_key": pk,
                    "description": str(r.get("description", "")),
                })
            new_rules["naming_patterns"] = new_patterns
            new_rules["host_vm_presets"] = host_presets + final_vm
            _wc_discard(name)
            _save_presets(new_rules, section="vm_roles", section_label="VM Role Presets")
            return
    with c_r:
        if st.button("🔄 Reset to Defaults", key=f"{name}_reset", width="stretch"):
            _wc_discard(name)
            from config.naming_rules import DEFAULT_VM_PRESETS
            new_rules = load_naming_rules()
            new_patterns = dict(new_rules.get("naming_patterns") or {})
            keep_hosts = [p for p in get_host_vm_presets(new_rules) if not is_vm_preset_key(p.get("pattern_key"))]
            shared_tpl = str(DEFAULT_NAMING_PATTERNS.get("vm_host", ""))
            reset_vm = []
            for p in DEFAULT_VM_PRESETS:
                pk = "vm_" + (re.sub(r"[^a-z0-9]+", "_", str(p.get("code", "")).lower()).strip("_") or "role")
                new_patterns[pk] = shared_tpl
                reset_vm.append(dict(p, pattern_key=pk))
            new_rules["naming_patterns"] = new_patterns
            new_rules["host_vm_presets"] = keep_hosts + reset_vm
            _save_presets(new_rules, section="vm_roles", section_label="VM Role Presets")
            return

    _wc_add_row(name, rows, [
        ("code", "e.g. cvi"),
        ("label", "e.g. Core Virtualization"),
        ("tpl", "e.g. <country><site><role><seq>"),
    ], cols)


def _render_vlan_description_mappings_editor(rules: dict) -> None:
    name = "vlan_desc"
    saved = dict(get_vlan_description_mappings(rules))
    rows = _wc_rows(name, lambda: [{"role": k, "desc": v} for k, v in saved.items()])
    fields = ("role", "desc")
    COLS = VLAN_DESC_MAPPINGS_COLS

    with st.container():
        h_role, h_desc, h_act = st.columns(COLS, vertical_alignment="center")
        with h_role:
            st.markdown("**Role**")
        with h_desc:
            st.markdown("**VLAN Description**")
        with h_act:
            pass

        for idx, row in enumerate(list(rows)):
            c1, c2, c3 = st.columns(COLS, vertical_alignment="center")
            with c1:
                row["role"] = st.text_input(
                    "Role", value=row["role"], key=f"wc_{name}_{row['_uid']}_role",
                    label_visibility="collapsed", placeholder="e.g. Corporate WiFi"
                )
            with c2:
                row["desc"] = st.text_input(
                    "VLAN Description", value=row["desc"], key=f"wc_{name}_{row['_uid']}_desc",
                    label_visibility="collapsed", placeholder="e.g. VIN_Corp"
                )
            with c3:
                _wc_row_actions(name, rows, idx, min_rows=0)

        if _wc_values(rows, fields) != [(str(k).strip(), str(v).strip()) for k, v in saved.items()]:
            st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

        c_s, c_r = st.columns(2)
        with c_s:
            if st.button("💾 Save & Apply Changes", key=f"{name}_save", type="primary", width="stretch"):
                new_rules = load_naming_rules()
                non_empty = [r for r in rows if r["role"].strip()]
                if not non_empty:
                    st.warning("⚠️ At least one mapping is required.")
                else:
                    new_rules["vlan_description_mappings"] = {r["role"].strip(): r["desc"].strip() for r in non_empty}
                    _wc_discard(name)
                    _save_vlan_desc_mappings(new_rules)
        with c_r:
            if st.button("🔄 Reset to Defaults", key=f"{name}_reset", width="stretch"):
                _wc_discard(name)
                rules = dict(rules)
                rules["vlan_description_mappings"] = dict(DEFAULT_VLAN_DESCRIPTION_MAPPINGS)
                _save_vlan_desc_mappings(rules)

        _wc_add_row(name, rows, [("role", "e.g. Corporate WiFi"), ("desc", "e.g. VIN_Corp")], COLS)


def _vlan_items_name(group_name: str) -> str:
    return f"vlan_items_{group_name.lower().replace(' ', '_').replace('/', '_').replace('-', '_')}"


def _vlan_group_skeleton() -> dict:
    return {"vlan_name_pattern": "<role>", "prefix_pattern": "<site> <role> -- VLAN <vid>", "items": []}


def _vlan_presets_editor(rules: dict) -> None:
    vlan_presets = get_vlan_presets(rules)

    total_count = sum(len(g.get("items", [])) for g in vlan_presets.values())

    with st.expander("🌐 VLAN Allocation Presets", expanded=True):
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

        group_wc = _wc_rows("vlan_groups", lambda: [{"name": k} for k in vlan_presets.keys()])
        wc_names = [r["name"] for r in group_wc]
        fresh_names = {r["name"] for r in group_wc if r.get("fresh")}
        preset_names = list(wc_names)
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
                    _commit_fresh_rules(rules, fresh_rules)
                    st.rerun()

                if c_dn.button("⬇️", key="btn_vlan_grp_dn", disabled=(cur_idx == len(preset_names) - 1), help="Move Group Down", width="stretch"):
                    preset_names[cur_idx + 1], preset_names[cur_idx] = preset_names[cur_idx], preset_names[cur_idx + 1]
                    rules["vlan_presets"] = {k: rules["vlan_presets"][k] for k in preset_names}
                    save_naming_rules(rules, source="Reorder VLAN Preset Groups")
                    fresh_rules = SSM.get_cached_naming_rules() or load_naming_rules()
                    _commit_fresh_rules(rules, fresh_rules)
                    st.rerun()

                if c_del.button("🗑️", key="btn_vlan_grp_del", disabled=(len(wc_names) <= 1 or selected_group == "Custom / Empty Preset"), help=f"Delete preset group '{selected_group}'", width="stretch"):
                    cur_idx = wc_names.index(selected_group)
                    group_wc.pop(cur_idx)
                    _wc_discard(_vlan_items_name(selected_group))
                    remaining = [p for p in preset_names if p != selected_group]
                    st.session_state["pending_vlan_group"] = remaining[0] if remaining else "+ Create New Preset Group"
                    _clear_session_state_prefixes("vlan_pre_vid_", "vlan_pre_role_", "vlan_pre_pat_", "vlan_pre_gnpat_", "vlan_pre_gppat_")
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
                        group_wc.append({"name": new_grp_name, "_uid": uuid.uuid4().hex[:8], "fresh": True})
                        _wc_discard(_vlan_items_name(new_grp_name))
                        st.session_state["pending_vlan_group"] = new_grp_name
                        st.toast(f"VLAN Group '{new_grp_name}' added (unsaved). Click Save & Apply Changes to keep it.", icon="📝")
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
        if not is_custom:
            group_name = sel
            if group_name in fresh_names:
                group = dict(_vlan_group_skeleton())
            elif group_name in wc_names and group_name not in vlan_presets:
                group = dict(_vlan_group_skeleton())
            else:
                group = dict(vlan_presets.get(group_name, {"vlan_name_pattern": "<role>", "prefix_pattern": "", "items": []}))
            items = list(group.get("items", []))

        new_group_name = None

        if group_name is not None:
            items_name = f"vlan_items_{group_name.lower().replace(' ', '_').replace('/', '_').replace('-', '_')}"
            items_wc = _wc_rows(items_name, lambda: [dict(r, _uid=uuid.uuid4().hex[:8]) for r in items])
            fields = ("vid", "role")
            cols = PRESET_VLAN_COLS
            h_vid, h_role, h_act = st.columns(cols, vertical_alignment="center")
            with h_vid:
                st.markdown("**VID**")
            with h_role:
                st.markdown("**Role**")
            with h_act:
                st.markdown("<span class='action-header'>**Action**</span>", unsafe_allow_html=True)
            for idx, row in enumerate(list(items_wc)):
                with st.container():
                    c1, c2, c3 = st.columns(cols, vertical_alignment="center")
                    with c1:
                        row["vid"] = st.text_input("VID", value=str(row.get("vid", "")) if row.get("vid", "") not in (None, "") else "", key=f"wc_{items_name}_{row['_uid']}_vid", label_visibility="collapsed").strip()
                    with c2:
                        row["role"] = st.text_input("Role", value=str(row.get("role", "")), key=f"wc_{items_name}_{row['_uid']}_role", label_visibility="collapsed").strip()
                    with c3:
                        _wc_row_actions(items_name, items_wc, idx)
            _wc_add_row(items_name, items_wc, [("vid", "900"), ("role", "e.g. Surveillance / CCTV")], cols)
            if not items_wc:
                st.info("ℹ️ No preset VLAN items defined in this group. It will load as a blank table in IPAM, or you can add items below.")
            dirty = (_wc_values(items_wc, fields) != [(str(r.get("vid", "")).strip() if r.get("vid", "") not in (None, "") else "", str(r.get("role", "")).strip()) for r in items])
            if dirty:
                st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")
        else:
            items_wc = []
            fields = ()
            cols = ()
            dirty = False

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


        col_save, col_reset = st.columns(2, vertical_alignment="center")
        with col_save:
            saved_presets = st.button("💾 Save & Apply Changes", key="vlan_pre_save", type="primary", width='stretch')
        with col_reset:
            reset_presets = st.button("🔄 Reset to Defaults", key="vlan_pre_reset", width='stretch')

    # Collect group-level pattern values when not custom
    gnpat = group.get("vlan_name_pattern", "<role>")
    gppat = group.get("prefix_pattern", "")
    if group_name is not None:
        gnpat = st.session_state.get(f"vlan_pre_gnpat_{group_name}", gnpat).strip()
        gppat = st.session_state.get(f"vlan_pre_gppat_{group_name}", gppat).strip()

    if reset_presets:
        if group_name is not None:
            _wc_discard(items_name)
        _clear_session_state_prefixes("vlan_pre")
        rules["vlan_presets"] = copy.deepcopy(dict(DEFAULT_VLAN_PRESETS))
        _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")
        return

    if saved_presets:
        if group_name is not None:
            _wc_discard(items_name)
        if is_custom:
            if not new_group_name or not new_group_name.strip():
                st.error("⚠️ Provide a name for the new preset group.")
                return
            group_key = new_group_name.strip()
            items_list = [{k: v for k, v in r.items() if k != "_uid"} for r in items_wc if r.get("vid", "") or r.get("role", "")]
            vlan_presets[group_key] = {
                "vlan_name_pattern": "<role>",
                "prefix_pattern": "<site> <role> -- VLAN <vid>",
                "items": items_list,
            }
        else:
            if group_name is None:
                return
            items_list = [{k: v for k, v in r.items() if k != "_uid"} for r in items_wc if r.get("vid", "") or r.get("role", "")]
            final_group = dict(group)
            final_group["items"] = items_list
            final_group["vlan_name_pattern"] = gnpat
            final_group["prefix_pattern"] = gppat
            new_presets = {}
            for r in group_wc:
                n = r["name"]
                if n == group_name:
                    new_presets[n] = final_group
                elif n in fresh_names:
                    new_presets[n] = _vlan_group_skeleton()
                elif n in vlan_presets:
                    new_presets[n] = copy.deepcopy(vlan_presets[n])
                else:
                    new_presets[n] = _vlan_group_skeleton()
            if group_name not in new_presets:
                new_presets[group_name] = final_group
            vlan_presets = new_presets
        rules["vlan_presets"] = vlan_presets
        if is_custom:
            st.session_state["pending_vlan_group"] = new_group_name.strip() if new_group_name and new_group_name.strip() else st.session_state.get("vlan_pre_selected_group")
        _save_presets(rules, section="vlan_presets", section_label="VLAN Allocation Presets")


def _render_azure_mappings_editor(rules: dict):
    from config.naming_rules import get_azure_mappings, DEFAULT_AZURE_MAPPINGS
    saved = get_azure_mappings(rules)
    reg = _wc_rows("azure_region", lambda: [{"k": k, "v": v} for k, v in (saved.get("region_site") or {}).items()])
    plat = _wc_rows("azure_platform", lambda: [{"k": k, "v": v} for k, v in (saved.get("platform") or {}).items()])
    COLS = [4.4, 4.4, 1.2]
    with st.expander("☁️ Azure Import Mappings", expanded=False):
        st.caption("Used by Azure VM CSV import. KQL exports raw values; these tables map them to NetBox.")
        for name, rows, title, h1, h2, ph1, ph2 in (
            ("azure_region", reg, "Region → Site", "Azure Region", "NetBox Site", "e.g. westeurope", "e.g. Azure - West Europe"),
            ("azure_platform", plat, "OS Type → Platform", "Azure OS Type", "NetBox Platform", "e.g. Linux", "e.g. Ubuntu"),
        ):
            st.markdown(f"**{title}**")
            a, b, _ = st.columns(COLS, vertical_alignment="center")
            a.markdown(f"**{h1}**")
            b.markdown(f"**{h2}**")
            for idx, row in enumerate(list(rows)):
                c1, c2, c3 = st.columns(COLS, vertical_alignment="center")
                with c1:
                    row["k"] = st.text_input(h1, value=row["k"], key=f"wc_{name}_{row['_uid']}_k", label_visibility="collapsed")
                with c2:
                    row["v"] = st.text_input(h2, value=row["v"], key=f"wc_{name}_{row['_uid']}_v", label_visibility="collapsed")
                with c3:
                    _wc_row_actions(name, rows, idx)
            _wc_add_row(name, rows, [("k", ph1), ("v", ph2)], COLS)
        role = st.text_input("Role fallback (empty = leave blank)", value=saved.get("role_fallback", ""), key="wc_azure_role_fallback")
        dirty = (_wc_values(reg, ("k", "v")) != [(str(k).strip(), str(v).strip()) for k, v in (saved.get("region_site") or {}).items()]
                 or _wc_values(plat, ("k", "v")) != [(str(k).strip(), str(v).strip()) for k, v in (saved.get("platform") or {}).items()]
                 or role.strip() != str(saved.get("role_fallback", "")).strip())
        if dirty:
            st.caption("⚠️ Unsaved changes — click Save & Apply Changes to keep them.")

        def _done(msg):
            _wc_discard("azure_region")
            _wc_discard("azure_platform")
            st.session_state.pop("wc_azure_role_fallback", None)
            SSM.refresh_naming_rules()
            st.toast(msg)
            st.rerun()

        c_s, c_r = st.columns(2)
        with c_s:
            if st.button("💾 Save & Apply Changes", key="azure_map_save", type="primary", width="stretch"):
                new_rules = load_naming_rules()
                new_rules["azure_mappings"] = {
                    "region_site": {r["k"].strip().lower(): r["v"].strip() for r in reg if r["k"].strip() and r["v"].strip()},
                    "platform": {r["k"].strip(): r["v"].strip() for r in plat if r["k"].strip() and r["v"].strip()},
                    "role_fallback": role.strip(),
                }
                save_naming_rules(new_rules, source="Azure Mappings")
                _done("Azure mappings saved")
        with c_r:
            if st.button("🔄 Reset to Defaults", key="azure_map_reset", width="stretch"):
                new_rules = load_naming_rules()
                new_rules["azure_mappings"] = copy.deepcopy(DEFAULT_AZURE_MAPPINGS)
                save_naming_rules(new_rules, source="Azure Mappings Reset")
                _done("Azure mappings reset")


def _render_hardware_baseline_editor(rules: dict, active_model: str | None = None):
    """Render the structured Hardware Baseline Standards editor.

    Replaces the legacy single-string ``netbox_server_yaml`` text area with a
    category-based editor that supports server, module_nic, storage_san, and
    custom categories. Each category has its own save/reset buttons to avoid
    cross-card banner leakage.
    """
    active_model = active_model or st.session_state.get("active_model", "")
    nonce = st.session_state.get("standards_nonce", 0)

    hb = get_hardware_baseline_standards(rules)
    with st.expander("📋 Hardware Baseline Templates", expanded=False):
        c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
        with c_hdr_left:
            st.caption("Define per-category hardware YAML schema and interface naming patterns. These standards are enforced by the Hardware Catalog Generator when producing Module and Device YAMLs.")
        with c_hdr_right:
            st.markdown(f"<div style='text-align:right;color:#94a3b8;font-size:0.85rem;padding-top:2px;'>{len(hb)} categories</div>", unsafe_allow_html=True)

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
                        key=f"hb_{nonce}_{cat_key}_console",
                        help="Console port description (e.g. Serial (de-9))",
                    )
                    cp_module_bays = st.text_input(
                        "Module Bays",
                        value=current_cfg.get("module_bays", ""),
                        key=f"hb_{nonce}_{cat_key}_module_bays",
                        help="Comma-separated module bay names (e.g. PSU1, PSU2, PCIe1)",
                    )
                    cp_interfaces = st.text_input(
                        "Interfaces",
                        value=current_cfg.get("interfaces", ""),
                        key=f"hb_{nonce}_{cat_key}_interfaces",
                        help="Interface description (e.g. OOB Management ONLY (1000base-t, mgmt_only: true))",
                    )
                    edited_hb[cat_key] = {
                        **edited_hb.get(cat_key, current_cfg),
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
                        key=f"hb_{nonce}_{cat_key}_ipat",
                        help="Template for interface names. Use {module} for the model name and {index} for sequential port numbers. Default: {module}/Port{index}",
                    )
                    ip_port_count = st.number_input(
                        "Default Port Count",
                        min_value=1,
                        max_value=64,
                        value=int(current_cfg.get("default_port_count", 2)),
                        key=f"hb_{nonce}_{cat_key}_portcount",
                        help="Number of ports to generate by default",
                    )
                    ip_port_start = st.number_input(
                        "Port Index Start",
                        min_value=0,
                        max_value=99,
                        value=int(current_cfg.get("port_start_index", 1)),
                        key=f"hb_{nonce}_{cat_key}_portstart",
                        help="Starting index for port numbering",
                    )
                    edited_hb[cat_key] = {
                        **edited_hb.get(cat_key, current_cfg),
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
                        _new_hb = copy.deepcopy(get_hardware_baseline_standards(rules))
                        _new_hb[cat_key] = edited_hb[cat_key]
                        rules["hardware_baseline_standards"] = _new_hb
                        save_naming_rules(rules, source=f"Hardware Baseline: {cat_key}")
                        fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                        _commit_fresh_rules(rules, fresh)
                        st.toast(f"✅ {display_name} category saved & applied!", icon="💾")
                        st.rerun()
                with col_r:
                    if st.button("🔄 Reset to Defaults", key=f"hb_reset_{cat_key}", width="stretch"):
                        _new_hb = copy.deepcopy(get_hardware_baseline_standards(rules))
                        _new_hb[cat_key] = copy.deepcopy(DEFAULT_HARDWARE_BASELINE_STANDARDS[cat_key])
                        rules["hardware_baseline_standards"] = _new_hb
                        save_naming_rules(rules, source=f"Hardware Baseline: Reset {cat_key}")
                        fresh = SSM.get_cached_naming_rules() or load_naming_rules()
                        _commit_fresh_rules(rules, fresh)
                        _clear_session_state_prefixes(f"hb_{cat_key}_")
                        st.toast(f"✅ {display_name} reset to defaults!", icon="🔄")
                        st.rerun()


def render_standards_tab(active_model):
    st.subheader("📖 Infrastructure Naming Standards Configuration")
    st.caption("Define and manage your organization's naming conventions. All patterns configured here are automatically applied in the Naming tab.")

    st.markdown(
        """
        <style>
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
            with st.expander("🔧 Device Type Presets", expanded=False):
                c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
                with c_hdr_left:
                    st.caption(f"Manage device naming patterns and presets ({', '.join(p.get('code', '') for p in get_device_presets(current_rules))}).")
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
            with st.expander("🔌 Interface Type Presets", expanded=False):
                c_hdr_left, c_hdr_right = st.columns([0.82, 0.18])
                with c_hdr_left:
                    st.caption(f"Manage interface description presets ({', '.join(p.get('code', '') for p in get_interface_presets(current_rules))}).")
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
            with st.expander("💻 Host Type Presets", expanded=False):
                _host_editor(current_rules)
            with st.expander("🖱️ Virtual Machine Presets", expanded=False):
                _vm_editor(current_rules)
            with st.expander("☁️ Hypervisor Platform Presets", expanded=False):
                _render_hypervisor_platform_presets_editor(current_rules, active_model)

        elif _active_subtab == 2:
            _render_hardware_baseline_editor(current_rules, active_model)
            _render_azure_mappings_editor(current_rules)
            _render_auto_correction_manager(active_model)
            _render_site_code_mapping_manager(key_prefix="ac_scm")

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
                        st.toast("Current configuration solidified as new baseline!", icon="✅")
                        st.rerun()
                with col_b2:
                    if st.button("🔄 Restore All Standards to Baseline", width="stretch"):
                        standards_mgr.restore_from_baseline()
                        st.toast("All standards restored to baseline snapshot.", icon="⚠️")
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
        variable_cols = [1.5, 2.8, 3.2, 1.2, 0.8, 0.8]

        def _row_from_meta(token, meta, scope):
            meta = meta if isinstance(meta, dict) else {}
            return {
                "name": token, "label": meta.get("label", token), "placeholder": meta.get("placeholder", ""),
                "default": meta.get("default", ""), "optional": bool(meta.get("optional", False)),
                "scope": scope, "required": bool(meta.get("required", False)),
                "type": str(meta.get("type") or ""), "source": str(meta.get("source") or ""),
                "source_field": str(meta.get("source_field") or ""), "filter_field": str(meta.get("filter_field") or ""),
                "filter_value": str(meta.get("filter_value") or ""),
                "join": str(meta.get("join") if meta.get("join") is not None else ""),
                "binding_type": "Derived" if meta.get("type") == "derived" else ("Source" if meta.get("source") else "Manual"),
            }

        def _meta_from_row(row, scope):
            meta = {
                "label": row.get("label") or row["name"],
                "placeholder": row.get("placeholder") or f"e.g. {row['name']}",
                "default": row.get("default", ""), "optional": bool(row.get("optional", False)), "scope": scope,
            }
            if row.get("required"):
                meta["required"] = True
            if row.get("binding_type") == "Source" and row.get("source"):
                meta["source"] = row["source"]
            elif row.get("binding_type") == "Derived":
                meta.update({"type": "derived", "source_field": row.get("source_field", ""),
                             "filter_field": row.get("filter_field", ""), "filter_value": row.get("filter_value", ""),
                             "join": row.get("join", "")})
            return meta

        for scope_code, title, desc, s_key in scope_meta:
            scope_items = grouped_vars.get(s_key, {})
            with st.expander(f"{title} ({len(scope_items)} variables)", expanded=False):
                h1, h2 = st.columns([3, 1])
                h1.markdown(f"##### {title}")
                h2.markdown(f"<div style='text-align:right'><span style='background:#2b313e;padding:3px 8px;border-radius:4px;font-size:.85em'>{len(scope_items)} variables</span></div>", unsafe_allow_html=True)
                st.caption(desc)
                name = f"vars_{s_key}"
                rows = _wc_rows(name, lambda: [_row_from_meta(n, m, s_key) for n, m in scope_items.items()])
                for row in rows:
                    row.setdefault("_uid", uuid.uuid4().hex[:8])
                    row.setdefault("binding_type", "Derived" if row.get("type") == "derived" else ("Source" if row.get("source") else "Manual"))

                pending_name = st.session_state.get("pv_pending_delete")
                if pending_name and any(r["name"] == pending_name for r in rows):
                    st.warning(f"⚠️ Variable `<{pending_name}>` is used in: {', '.join(find_variable_references(current_rules, pending_name))}")
                    cc, cd = st.columns(2)
                    if cc.button("Delete anyway", key=f"pv_del_do_{pending_name}"):
                        rows[:] = [r for r in rows if r["name"] != pending_name]
                        st.session_state.pop("pv_pending_delete", None); st.rerun()
                    if cd.button("Cancel", key=f"pv_del_cancel_{pending_name}"):
                        st.session_state.pop("pv_pending_delete", None); st.rerun()

                headers = st.columns(variable_cols)
                for col, text in zip(headers, ("Token", "Label", "Placeholder", "Default", "Optional", "Action")):
                    col.markdown(f"**{text}**")
                for idx, row in enumerate(list(rows)):
                    uid = row["_uid"]
                    c1,c2,c3,c4,c5,c6 = st.columns(variable_cols, vertical_alignment="center")
                    c1.markdown(f"`<{row['name']}>`")
                    with c2: row["label"] = st.text_input("Label", value=row.get("label", ""), key=f"wc_{name}_{uid}_label", label_visibility="collapsed").strip()
                    with c3: row["placeholder"] = st.text_input("Placeholder", value=row.get("placeholder", ""), key=f"wc_{name}_{uid}_placeholder", label_visibility="collapsed").strip()
                    with c4: row["default"] = st.text_input("Default", value=row.get("default", ""), key=f"wc_{name}_{uid}_default", label_visibility="collapsed")
                    with c5: row["optional"] = st.checkbox("Optional", value=bool(row.get("optional", False)), key=f"wc_{name}_{uid}_optional", label_visibility="collapsed")
                    with c6:
                        up,dn,dele = st.columns(3)
                        if idx and up.button("⬆️", key=f"wc_{name}_{uid}_up"):
                            rows[idx-1],rows[idx]=rows[idx],rows[idx-1]; st.rerun()
                        if idx < len(rows)-1 and dn.button("⬇️", key=f"wc_{name}_{uid}_dn"):
                            rows[idx+1],rows[idx]=rows[idx],rows[idx+1]; st.rerun()
                        if dele.button("🗑️", key=f"wc_{name}_{uid}_del"):
                            if find_variable_references(current_rules,row["name"]):
                                st.session_state["pv_pending_delete"] = row["name"]
                            else: rows.pop(idx)
                            st.rerun()

                if s_key == "hypervisor":
                    st.divider(); st.markdown("**Data Bindings**")
                    st.caption("Bindings control token values. Multiple Values joins matches inside one token; text and separators between tokens, such as `/`, are controlled by Pattern Templates.")
                    bound = [r for r in rows if r.get("binding_type") in ("Source","Derived")]
                    widths=[1.6,1.0,1.5,1.3,2.0,1.0,.5]
                    if bound:
                        hs=st.columns(widths)
                        for col,text in zip(hs,("Token","Type","Value / Collect","Where","Equals","Multiple Values","Action")): col.markdown(f"**{text}**")
                    source_opts=["service_name","switch_name"]
                    collect_opts=["interface","purpose","switch","role","vlan","ip"]
                    filter_opts=["role","type","interface","purpose","switch"]
                    role_opts=["Active Uplink","Standby Uplink","None"]
                    for row in bound:
                        uid=row["_uid"]; cols=st.columns(widths,vertical_alignment="center")
                        cols[0].markdown(f"`<{row['name']}>`")
                        with cols[1]: row["binding_type"]=st.selectbox("Type",["Source","Derived"],index=0 if row["binding_type"]=="Source" else 1,key=f"wc_{name}_{uid}_bt",label_visibility="collapsed")
                        if row["binding_type"]=="Source":
                            row["type"]=""; row["source_field"]=row["filter_field"]=row["filter_value"]=row["join"]=""
                            current=row.get("source") if row.get("source") in source_opts else source_opts[0]
                            with cols[2]: row["source"]=st.selectbox("Fact",source_opts,index=source_opts.index(current),key=f"wc_{name}_{uid}_src",label_visibility="collapsed")
                        else:
                            row["type"]="derived"; row["source"]=""
                            cur=row.get("source_field") if row.get("source_field") in collect_opts else collect_opts[0]
                            with cols[2]: row["source_field"]=st.selectbox("Collect",collect_opts,index=collect_opts.index(cur),key=f"wc_{name}_{uid}_collect",label_visibility="collapsed")
                            cur=row.get("filter_field") if row.get("filter_field") in filter_opts else filter_opts[0]
                            with cols[3]: row["filter_field"]=st.selectbox("Where",filter_opts,index=filter_opts.index(cur),key=f"wc_{name}_{uid}_where",label_visibility="collapsed")
                            with cols[4]:
                                if row["filter_field"]=="role":
                                    cur=row.get("filter_value") if row.get("filter_value") in role_opts else role_opts[0]
                                    row["filter_value"]=st.selectbox("Equals",role_opts,index=role_opts.index(cur),key=f"wc_{name}_{uid}_eq",label_visibility="collapsed")
                                else: row["filter_value"]=st.text_input("Equals",value=row.get("filter_value",""),key=f"wc_{name}_{uid}_eqtxt",label_visibility="collapsed").strip()
                            with cols[5]: row["join"]=st.text_input("Multiple Values",value=row.get("join",""),key=f"wc_{name}_{uid}_multi",label_visibility="collapsed",help="Joins matches inside this token. Separators such as / belong in Pattern Templates.")
                        with cols[6]:
                            if st.button("✕",key=f"wc_{name}_{uid}_unbind"):
                                row.update({"binding_type":"Manual","type":"","source":"","source_field":"","filter_field":"","filter_value":"","join":""}); st.rerun()
                    available=[r for r in rows if r.get("binding_type")=="Manual"]
                    if available:
                        a,b,c=st.columns([5,2,1.5],vertical_alignment="bottom"); names=[r["name"] for r in available]
                        with a: add_name=st.selectbox("Add binding for token",names,format_func=lambda x:f"<{x}>",key=f"wc_{name}_addbind_name")
                        with b: add_type=st.selectbox("Binding type",["Source","Derived"],key=f"wc_{name}_addbind_type")
                        with c:
                            if st.button("➕ Add Binding",key=f"wc_{name}_addbind"):
                                target=next(r for r in rows if r["name"]==add_name); target["binding_type"]=add_type
                                target.update({"source":"service_name","type":""} if add_type=="Source" else {"source":"","type":"derived","source_field":"interface","filter_field":"role","filter_value":"Active Uplink","join":", "}); st.rerun()

                built={r["name"]:_meta_from_row(r,s_key) for r in rows if r.get("name")}
                save_col,reset_col=st.columns([4,1])
                with save_col: save=st.button("Save & Apply Changes",key=f"vars_save_{s_key}",type="primary",width="stretch")
                with reset_col:
                    if st.button("🔄 Reset to Defaults",key=f"reset_scope_{s_key}",type="secondary",width="stretch"):
                        _wc_discard(name); _persist_variables(current_rules,{k:copy.deepcopy(v) for k,v in PATTERN_VARIABLES.items() if (v.get("scope") or "shared")==s_key},section_key=s_key)
                if save:
                    invalid=[f"<{r['name']}>" for r in rows if r.get("binding_type")=="Derived" and not all(str(r.get(k) or "").strip() for k in ("source_field","filter_field","filter_value"))]
                    if invalid: st.error("Derived variables require Value / Collect, Where and Equals: "+", ".join(invalid))
                    else: _persist_variables(current_rules,built,section_key=s_key); _wc_discard(name)

                with st.form(key=f"wc_{name}_addform", clear_on_submit=True):
                    ca1,ca2,ca3,ca4,ca5,ca6=st.columns(variable_cols)
                    with ca1: new_name=_normalize_var_name(st.text_input("New Token",placeholder="e.g. tier",key=f"wc_{name}_new_name",label_visibility="collapsed").strip())
                    with ca2: new_lbl=st.text_input("New Label",placeholder="e.g. Service Tier",key=f"wc_{name}_new_label",label_visibility="collapsed").strip()
                    with ca3: new_ph=st.text_input("New Placeholder",placeholder="e.g. Tier-1",key=f"wc_{name}_new_placeholder",label_visibility="collapsed").strip()
                    with ca4: new_df=st.text_input("Default",key=f"wc_{name}_new_default",label_visibility="collapsed")
                    with ca5: st.markdown("")
                    with ca6: add=st.form_submit_button("➕ Add",width="stretch")
                if add:
                    if not new_name: st.warning("Enter a token name to add.")
                    elif new_name in {r["name"] for r in rows}: st.warning("Variable name already exists.")
                    else:
                        rows.append({"name":new_name,"label":new_lbl or new_name,"placeholder":new_ph or f"e.g. {new_name}","default":new_df,"optional":False,"scope":s_key,"required":False,"type":"","source":"","source_field":"","filter_field":"","filter_value":"","join":"","binding_type":"Manual","_uid":uuid.uuid4().hex[:8]}); st.rerun()

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
                    st.toast("History cleared!", icon="✅")
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
                                st.toast(f"Restored version from {timestamp}", icon="✅")
                                st.rerun()
                            except Exception as e:
                                st.error(f"❌ Failed to restore: {str(e)}")
                    
                    with col_view:
                        with st.expander("👁️ View Full Details", expanded=False):
                            st.json(rules)

        st.divider()
        st.markdown("##### Privacy Vault")

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
            st.markdown(f"**Active Session:** `{_active_sid}` — {len(_map) if _map else 0} token(s)")
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