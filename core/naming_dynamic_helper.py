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
        meta = meta if isinstance(meta, dict) else {}
        is_optional = meta.get("optional")
        initial = defaults.get(token, meta.get("default", ""))
        if is_optional:
            initial = "" if not meta.get("default") else meta.get("default", "")
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


def render_esxi_network_inputs(pattern: str, variables: Dict, prefix: str, auto_correct: bool,
                            extra_pattern: str = "",
                            token_order: list = None):
    """Renders ESXi-specific inputs with normalization for ``vmnic``, ``vSwitch``,
    ``Purpose``, ``Status``, and vmnic lists.

    ``extra_pattern`` is an optional second pattern (e.g. the *_name pattern) whose
    tokens should also receive widgets.

    ``token_order`` is an optional list of token names in the desired render order.
    When omitted, tokens are rendered in the order they appear in the combined patterns.

    Returns a dict that can be fed directly into ``interpolate_pattern``.
    """
    values: Dict[str, str] = {}
    tokens = extract_tokens(pattern) + extract_tokens(extra_pattern)
    # Deduplicate while preserving order.
    seen = set()
    ordered = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    if token_order:
        # Use token_order for rendering, but still populate from the full set.
        render_list = [t for t in token_order if t in seen]
        # Append any tokens not in token_order at the end.
        for t in ordered:
            if t not in token_order:
                render_list.append(t)
    else:
        render_list = ordered

    for token in render_list:
        label = token_label(variables, token)
        ph = token_placeholder(variables, token)
        wk = f"{prefix}__{token}"
        _meta = variables.get(token, {})
        _meta = _meta if isinstance(_meta, dict) else {}
        default_val = _meta.get("default", "")

        if token == "Status":
            ph = _meta.get("placeholder", "Active Uplink / Standby Uplink")
            label = token_label(variables, token)
            wk = f"{prefix}__Status"
            values[token] = st.text_input(label, value=default_val or "", placeholder=ph, key=wk,
                                        help="Enter the uplink status").strip()
            continue

        value = ""
        is_optional = _meta.get("optional")
        if is_optional:
            value = st.text_input(label, value=default_val or "", placeholder=ph, key=wk,
                               help="Optional - Leave empty if no standby uplinks").strip()
        else:
            value = st.text_input(label, value=default_val or "", placeholder=ph, key=wk).strip()

        if value:
            if token == "vSwitch":
                values[token] = normalize_vswitch(value)
            elif token == "vmnic":
                values[token] = normalize_vmnic(value) if auto_correct else value
            elif token in ("Active_vmnics", "Standby_vmnics"):
                values[token] = normalize_vmnic_list(value) if auto_correct else value
            elif token == "Purpose":
                values[token] = normalize_network_name(value)
            elif token in ("pg_network",):
                values[token] = normalize_network_name(value) if auto_correct else value.strip()
            elif token in ("vmk",):
                values[token] = normalize_vmnic(value) if auto_correct else value
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
        help="Edit the pattern using <Token> placeholders. Manage variables in the Standards tab > Pattern Variables Reference.",
    )

    if st.button("💾 Save to Standards", key=f"nw_save_{pattern_key}", type="primary"):
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
        # Explicitly disable the Edit Mode toggle and clear its widget state so the
        # rerun exits Edit Mode and shows the generated view.
        st.session_state[edit_key] = False
        toggle_key = f"edit_toggle_widget_{pattern_key}"
        st.session_state.pop(toggle_key, None)
        st.session_state.pop(pending_key, None)
        st.rerun()


def render_multi_edit_mode_ui(pattern_pairs: List[Tuple[str, str]], variables: Dict):
    """In-place Edit Mode for a set of related pattern templates.

    ``pattern_pairs`` is a list of ``(pattern_key, current_value)`` where each entry
    is rendered as an editable textarea. On save, all keys are persisted to
    ``st.session_state["naming_rules"]`` and to the JSON file, then reruns.

    Labels and fallback defaults mirror the canonical Asset Class 3 patterns so the UI
    stays useful even when a *_name pattern is missing from the loaded rules.
    """
    joiner = "__".join(k for k, _ in pattern_pairs)
    edit_key = f"edit_mode_{joiner}"
    defaults = {
        "esxi_portgroup_name": "PG-<pg_network>",
        "esxi_vmkernel_name": "<vmk>",
    }
    labels = {
        "esxi_portgroup_name": "Port Group Name Pattern Template",
        "esxi_portgroup": "Port Group Description Pattern Template",
        "esxi_vmkernel_name": "VMkernel Name Pattern Template",
        "esxi_vmkernel": "VMkernel Description Pattern Template",
    }
    edited_vals = {}
    for key, current in pattern_pairs:
        edited_vals[key] = st.text_area(
            labels.get(key, f"{key} Pattern Template"),
            value=current or defaults.get(key, ""),
            height=90,
            key=f"edit_text_{key}",
            help="Edit the pattern using <Token> placeholders. Manage variables in the Standards tab > Pattern Variables Reference.",
        )

    # ── Field Order Configuration ────────────────────────────────────────────
    # Collect the natural token sequence from the templates, then allow re-ordering
    # via Move Up / Down buttons. The chosen order is persisted on Save.
    order_key = f"field_order_{joiner}"
    tokens = []
    for key, current in pattern_pairs:
        for t in extract_tokens(current or defaults.get(key, "")):
            if t not in tokens:
                tokens.append(t)

    custom_order = st.session_state.get(order_key)
    if custom_order is None:
        custom_order = list(tokens)
    else:
        # Keep in sync with any tokens added/removed by template edits.
        for t in tokens:
            if t not in custom_order:
                custom_order.append(t)

    with st.expander("🎛️ Field Order Configuration", expanded=False):
        st.caption("Reorder the input fields. The saved order is used when rendering the generator.")
        for i in range(len(custom_order)):
            col_lbl, col_up, col_dn = st.columns([4, 1, 1])
            with col_lbl:
                st.markdown(f"`{i + 1}.` {token_label(variables, custom_order[i])}")
            with col_up:
                if i > 0 and st.button("⬆️", key=f"{order_key}_up_{i}", help="Move up"):
                    custom_order[i - 1], custom_order[i] = custom_order[i], custom_order[i - 1]
                    st.session_state[order_key] = list(custom_order)
                    st.rerun()
            with col_dn:
                if i < len(custom_order) - 1 and st.button("⬇️", key=f"{order_key}_dn_{i}", help="Move down"):
                    custom_order[i], custom_order[i + 1] = custom_order[i + 1], custom_order[i]
                    st.session_state[order_key] = list(custom_order)
                    st.rerun()

    if st.button("💾 Save to Standards", key=f"nw_save_{joiner}", type="primary"):
        rules = st.session_state.get("naming_rules")
        patterns = rules.get("naming_patterns")
        if patterns is None:
            patterns = {}
            rules["naming_patterns"] = patterns
        for key, val in edited_vals.items():
            patterns[key] = val
            rules[key] = val
        all_used = set()
        for p in (patterns or {}).values():
            all_used.update(extract_tokens(p))
        rules["pattern_variables"] = {k: v for k, v in variables.items() if k in all_used}
        # Persist the chosen token order per pattern key.
        token_order_map = rules.get("token_order")
        if not isinstance(token_order_map, dict):
            token_order_map = {}
        for key, _ in pattern_pairs:
            token_order_map[key] = list(custom_order)
        rules["token_order"] = token_order_map
        st.session_state["naming_rules"] = rules
        st.session_state.pop(order_key, None)
        save_naming_rules(rules, source=f"Edit Mode: {joiner}")
        # Explicitly disable each Edit Mode toggle and clear its widget state so the
        # rerun exits Edit Mode and shows the generated view.
        for key, _ in pattern_pairs:
            st.session_state[f"edit_mode_{key}"] = False
            st.session_state.pop(f"edit_toggle_widget_{key}", None)
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


# ── Optional-clause removal (fully token-driven, no hardcoded words) ──


def _is_optional_token(variables: Dict, token: str) -> bool:
    meta = variables.get(token, {})
    return isinstance(meta, dict) and bool(meta.get("optional"))


def remove_empty_optional_tokens(pattern: str, values: Dict, variables: Dict) -> str:
    """Remove optional tokens (and their attached fixed text) that have no value.

    Operates on the raw pattern BEFORE value substitution. Every token marked
    ``optional`` whose value is empty in ``values`` has its clause member removed,
    along with the fixed words and separators that belong to it. Delimiters like
    `` / ...``, ``[...]`` and ``(...)`` are cleaned up so remaining text renders
    cleanly.

    The algorithm:
    1. Locate each ``[...]`` or ``(...)`` bracket clause in the pattern.
    2. For each member separated by `` / ``, evaluate the member's optional token.
       If empty (and optional), drop the member entirely.
    3. If a bracket clause becomes empty, drop the whole block.
    4. Fixed words (``Active``, ``Standby``, ``Network``) are *not* hardcoded -
       they live entirely in the editable template.
    """
    if not pattern or not values:
        return pattern or ""

    empty_opt = {t for t in extract_tokens(pattern)
                 if _is_optional_token(variables, t) and not str(values.get(t, ""))}
    if not empty_opt:
        return pattern

    # Process bracket clauses first (the most complex case).
    result = _process_bracket_clauses(pattern, empty_opt)
    # Drop empty brackets produced above.
    result = _drop_empty_brackets(result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


BRACKET_RE = re.compile(r"\(([^()]+)\)|\[([^\[\]]+)\]")


def _process_bracket_clauses(pattern: str, empty_opt: set) -> str:
    """Inside each ``(...)`` or ``[...]``, split on `` / ``, drop any clause
    member whose token is in *empty_opt*, and reassemble."""
    result = []
    pos = 0
    for m in BRACKET_RE.finditer(pattern):
        result.append(pattern[pos:m.start()])
        inner = m.group(1) or m.group(2)
        opener = m.group(0)[0]     # ( or [
        closer = ")" if opener == "(" else "]"
        members = re.split(r"\s+/\s+", inner)
        kept = []
        for member in members:
            tokens = extract_tokens(member)
            # Drop this member if any of its tokens is an empty optional.
            if any(t in empty_opt for t in tokens):
                continue
            kept.append(member)
        if kept:
            result.append(f"{opener}{' / '.join(kept)}{closer}")
        # else: empty clause - skip it entirely
        pos = m.end()
    result.append(pattern[pos:])
    return "".join(result)


def _drop_empty_brackets(pattern: str) -> str:
    return re.sub(r"\(\s*\)", "", re.sub(r"\[\s*\]", "", pattern))