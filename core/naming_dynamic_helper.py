"""Dynamic pattern rendering engine for the Naming tab.

Decouples the token-to-widget mapping from the Streamlit layout so the Naming tab
can drive all generator cards from ``pattern_variables`` + ``naming_patterns``.
"""
import re
import streamlit as st
from typing import Dict, List, Optional, Tuple

from config.naming_rules import extract_tokens, save_naming_rules
from utils.formatters import (
    normalize_vswitch,
    normalize_vmnic,
    normalize_vmnic_list,
    normalize_network_name,
)
from utils.pattern_formatter import apply_pattern


# ── Variable metadata helpers ─────────────────────────────────────────────


def token_label(variables: Dict, token: str) -> str:
    meta = variables.get(token)
    if isinstance(meta, dict) and meta.get("label"):
        return meta["label"]
    return " ".join(word.capitalize() for word in token.replace("_", " ").split())


def token_placeholder(variables: Dict, token: str) -> str:
    meta = variables.get(token)
    if isinstance(meta, dict) and meta.get("placeholder"):
        return meta["placeholder"]
    return f"e.g. {token}"


# ── Pattern interpolation ─────────────────────────────────────────────────


def interpolate_pattern(pattern: str, values: Dict[str, str]) -> str:
    """Replace ``<Token>`` with the value; keep ``<Token>`` when empty.

    Also handles combined tokens like ``<role/esx>`` by matching the base
    name ``role``.
    """
    if not pattern:
        return ""
    result = pattern
    for token, value in values.items():
        value = str(value) if value else f"<{token}>"
        result = re.sub(rf"<{re.escape(token)}(?:/[^>]*)?>", value, result)
    return result


# ── Variable / pattern helpers used by edit mode ──────────────────────────


def get_rules_from_session() -> Tuple[Dict, Dict, Dict]:
    """Load merged rules, patterns, and variables from session state."""
    from config.naming_rules import load_naming_rules, get_naming_patterns, get_pattern_variables

    rules = st.session_state.get("naming_rules")
    if not rules:
        rules = load_naming_rules()
        st.session_state["naming_rules"] = rules
    patterns = get_naming_patterns(rules)
    variables = get_pattern_variables(rules)
    return rules, patterns, variables


# ── Widget rendering ──────────────────────────────────────────────────────


def render_token_widgets(
    pattern: str,
    variables: Dict,
    prefix: str,
    defaults: Optional[Dict[str, str]] = None,
    extra_inputs: Optional[Dict[str, Tuple[str, str, str]]] = None,
) -> Dict[str, str]:
    """Render Streamlit inputs for every token in ``pattern``.

    Returns a dict mapping token → current widget value (or empty string).

    ``defaults`` maps a token to an initial value (used to auto-fill widgets).

    ``extra_inputs`` is a dict ``{token: (label, placeholder, default)}`` for
    tokens that are not mentioned in the pattern but should still get a widget
    (e.g. optional ones like ``Status``, ``vmk``).
    """
    defaults = defaults or {}
    values: Dict[str, str] = {}
    tokens = extract_tokens(pattern)
    for token in tokens:
        label = token_label(variables, token)
        ph = token_placeholder(variables, token)
        wk = f"{prefix}__{token}"
        meta = variables.get(token, {})
        is_optional = isinstance(meta, dict) and meta.get("optional")
        initial = defaults.get(token, "")
        if is_optional:
            initial = ""
        values[token] = st.text_input(label, value=initial, placeholder=ph, key=wk).strip()

    for token, (label, ph, default) in (extra_inputs or {}).items():
        if token not in values:
            wk = f"{prefix}__{token}"
            values[token] = st.text_input(label, value=default, placeholder=ph, key=wk).strip()

    return values


def preview_box(label: str, content: str):
    """Standard preview box used across all generator cards."""
    st.caption(label)
    st.code(content, language="text")


# ── Special input behaviour per token ────────────────────────────────────


def render_esxi_network_inputs(pattern: str, variables: Dict, prefix: str, auto_correct: bool):
    """Renders ESXi-specific inputs with normalization for ``vmnic``, ``vSwitch``,
    ``Purpose``, ``Status``, and vmnic lists.

    Returns a dict that can be fed directly into ``interpolate_pattern``.
    """
    values: Dict[str, str] = {}
    tokens = extract_tokens(pattern)
    for token in tokens:
        label = token_label(variables, token)
        ph = token_placeholder(variables, token)
        wk = f"{prefix}__{token}"

        if token == "Status":
            values[token] = st.radio(
                label, ["Active Uplink", "Standby Uplink"],
                horizontal=True, key=wk,
            )
            continue

        value = ""
        meta = variables.get(token, {})
        is_optional = isinstance(meta, dict) and meta.get("optional")
        if token == "Standby_vmnics" or is_optional:
            value = st.text_input(label, value="", placeholder=ph, key=wk,
                               help="Optional - Leave empty if no standby uplinks").strip()
        else:
            value = st.text_input(label, value="", placeholder=ph, key=wk).strip()

        if value:
            if token == "vSwitch":
                values[token] = normalize_vswitch(value)
            elif token == "vmnic":
                values[token] = normalize_vmnic(value) if auto_correct else value
            elif token in ("Active_vmnics", "Standby_vmnics"):
                values[token] = normalize_vmnic_list(value) if auto_correct else value
            elif token == "Purpose":
                values[token] = normalize_network_name(value)
            else:
                values[token] = value
        else:
            values[token] = ""

    return values


# ── Edit mode rendering ──────────────────────────────────────────────────


def render_edit_mode_ui(pattern_key: str, pattern_value: str, variables: Dict):
    """In-place Edit Mode: raw pattern textarea + add-new-field expander.

    On save, flushes to ``st.session_state["naming_rules"]`` and to the JSON
    file, then triggers a rerun.
    """
    edit_key = f"edit_mode_{pattern_key}"
    pending_key = f"_edit_pending_{pattern_key}"
    initial = st.session_state.pop(pending_key, pattern_value or "")

    edited = st.text_area(
        "Pattern Template",
        value=initial,
        height=120,
        key=f"edit_text_{pattern_key}",
        help="Edit the pattern using <Token> placeholders.",
    )

    var_name = ""
    with st.expander("➕ Add New Field / Variable", expanded=False):
        var_name = st.text_input(
            "Variable Key (e.g. Speed)", value="", key=f"nw_var_name_{pattern_key}"
        ).strip()
        var_label = st.text_input(
            "Display Label", value="", placeholder="e.g. Interface Speed",
            key=f"nw_var_label_{pattern_key}",
        ).strip()
        var_ph = st.text_input(
            "Placeholder Example", value="", placeholder="e.g. 10G, 25G",
            key=f"nw_var_ph_{pattern_key}",
        ).strip()
        var_optional = st.checkbox(
            "Optional (default empty unless user inputs)", value=False,
            key=f"nw_var_opt_{pattern_key}",
            help="When checked, this field starts blank and is omitted from the final output if not filled."
        )
        if st.button("➕ Add Field", key=f"nw_add_{pattern_key}"):
            if var_name:
                variables[var_name] = {
                    "label": var_label or var_name,
                    "placeholder": var_ph or f"e.g. {var_name}",
                }
                if var_optional:
                    variables[var_name]["optional"] = True
                token = f"<{var_name}>"
                if token not in edited:
                    st.session_state[pending_key] = edited + token
                st.rerun()

    if st.button("💾 Save to Standards", key=f"nw_save_{pattern_key}", type="primary"):
        if var_name:
            variables[var_name] = {
                "label": var_label or var_name,
                "placeholder": var_ph or f"e.g. {var_name}",
            }
            if var_optional:
                variables[var_name]["optional"] = True
            token = f"<{var_name}>"
            if token not in edited:
                edited = edited + token
        rules = st.session_state.get("naming_rules")
        patterns = rules.get("naming_patterns")
        if patterns is not None:
            patterns[pattern_key] = edited
            rules[pattern_key] = edited
        all_used = set()
        for p in (patterns or {}).values():
            all_used.update(extract_tokens(p))
        rules["pattern_variables"] = {k: v for k, v in variables.items() if k in all_used}
        st.session_state["naming_rules"] = rules
        save_naming_rules(rules, source=f"Edit Mode: {pattern_key}")
        st.session_state[edit_key] = False
        st.session_state.pop(f"edit_toggle_widget_{pattern_key}", None)
        st.success(f"✅ Pattern '{pattern_key}' saved. Re-rendering form...")
        st.rerun()


# ── Sub-pattern selection for multi-pattern strings ──────────────────────


def pick_sub_pattern(full_pattern: str, dev_type: str) -> str:
    """For patterns like ``SW<...> / VS<...>``, pick the branch matching *dev_type*."""
    if " / " not in full_pattern:
        return full_pattern
    parts = full_pattern.split(" / ")
    for part in parts:
        if dev_type.upper().split()[0] in part:
            return part
    return parts[0]


# ── AI verify shortcut ────────────────────────────────────────────────────


def ai_verify_button(
    label: str,
    value: str,
    active_model: str,
    asset_type: str,
    site_filter: str = "",
):
    from core.naming_engine import verify_and_suggest_with_ai

    if st.button(f"🤖 AI Verify {label}", key=f"ai_{hash(value) % 2 ** 31}"):
        with st.spinner("Auditing against Standards..."):
            st.info(
                verify_and_suggest_with_ai(
                    value,
                    active_model,
                    asset_type=asset_type,
                    category_key="device",
                    site_filter=site_filter,
                )
            )