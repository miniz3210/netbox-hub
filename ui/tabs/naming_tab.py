import logging
import re
from typing import Dict, List, Tuple, Any, Set
import streamlit as st
from config.constants import APP_VERSION
import pandas as pd

logger = logging.getLogger(__name__)
from utils.formatters import normalize_port_shortname
from core.naming_engine import verify_and_suggest_with_ai
from core.db_manager import (
    get_records_by_category,
    get_total_record_count,
    get_sync_metadata,
)
from core.session_manager import SessionStateManager as SSM
from ui.components import render_ai_chat, render_backup_uploader
from core.naming_dynamic_helper import (
    render_dynamic_pattern,
    render_token_widgets,
    render_edit_mode_ui,
    extract_tokens,
    token_label,
)
from config.naming_rules import (
    load_naming_rules, get_naming_patterns, get_pattern_variables,
    get_device_presets, get_interface_presets, get_host_vm_presets,
    compute_suggested_site_code,
)
from core.topology_reducer import reduce_topology_pipeline

from core.extractors import run_topology_pipeline, preview_topology_text, PROCESSING_MODULES

# ---------------------------------------------------------------------------
# Progressive VLM rendering helpers
# ---------------------------------------------------------------------------

from ui.components import _cached_fetch_toolbar_stats


@st.cache_data(ttl=60, show_spinner=False)
def _cached_get_reference_records(category_key, site_filter="", name_filter=""):
    """Cache reference records to avoid full-table SQLite scans on every widget interaction."""
    items = get_records_by_category(category_key, site_filter=site_filter)
    if name_filter:
        nfu = name_filter.upper()
        items = [r for r in items if r.get('name', '').upper().startswith(nfu)]
    return items


# ---------------------------------------------------------------------------
# Switch name normalization
# ---------------------------------------------------------------------------

def _natsort_key(s: str):
    """Return a sort key that orders numeric substrings as integers,
    so vmnic1 < vmnic2 < vmnic10 instead of alphabetical ordering."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]



# ---------------------------------------------------------------------------
# Strict noise / junk-token filter for chip pool hygiene
# ---------------------------------------------------------------------------
UI_NAV_NOISE = {
    "true", "false", "null", "none", "is_uncertain",
}


def _is_junk_token(text: str, dynamic_filter: set | None = None) -> bool:
    t = text.strip()
    if not t or len(t) <= 1:
        return True
    low = t.lower()
    if low.startswith("--- screenshot") or low.startswith("==="):
        return True
    if low in ("false", "true", "null", "none", "is_uncertain"):
        return True
    if low in UI_NAV_NOISE:
        return True
    if dynamic_filter and low in dynamic_filter:
        return True
    if re.match(r"^virtual\s*machines\s*\(\d+\)", low, re.IGNORECASE):
        return True
    if re.match(r"^vmkernel\s*ports\s*\(\d+\)", low, re.IGNORECASE):
        return True
    if re.match(r"^vlan\s*id\s*:\s*(?:--|\d+)", low, re.IGNORECASE):
        return True
    return False


# ---------------------------------------------------------------------------
# Standards-driven noise set helper — reads ui_noise_blacklist from YAML
# ---------------------------------------------------------------------------
def _get_platform_noise_set(naming_rules: dict, platform_name: str) -> Tuple[Set[str], Set[str]]:
    """Return (exact_lower_set, nospace_alnum_set) from ui_noise_blacklist.

    exact_lower_set contains every blacklist entry lower-cased and stripped.
    nospace_alnum_set contains every entry with all spaces and trailing
    punctuation/ellipses removed, also lower-cased.  A token is noise if its
    lower-cased form matches exact_lower_set OR its space-stripped form matches
    nospace_alnum_set.
    """
    plat_cfg = (naming_rules.get("topology_parsing_presets") or {}).get(platform_name, {})
    if not isinstance(plat_cfg, dict):
        plat_cfg = {}
    raw_entries = plat_cfg.get("ui_noise_blacklist") or []
    exact_lower_set: Set[str] = set()
    nospace_alnum_set: Set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, str) or not entry.strip():
            continue
        low = entry.strip().lower()
        exact_lower_set.add(low)
        stripped = re.sub(r"[\s\.]+", "", low)
        nospace_alnum_set.add(stripped)
    return exact_lower_set, nospace_alnum_set


def _is_noise_token(text: str, naming_rules: dict, platform_name: str) -> bool:
    """Check whether a token matches the platform's ui_noise_blacklist."""
    t = text.strip()
    if not t or len(t) <= 1:
        return True
    low = t.lower()
    if low in UI_NAV_NOISE:
        return True
    exact_set, nospace_set = _get_platform_noise_set(naming_rules, platform_name)
    return low in exact_set or re.sub(r"[\s\.]+", "", low) in nospace_set


@st.cache_data(ttl=60, show_spinner=False)
def _interface_ref_examples_cached(intf_code: str):
    from core.shared_backup_state import SharedBackupState

    _, defaults = _interface_ref(intf_code)
    rx = INTERFACE_REF_PATTERNS.get(intf_code, INTERFACE_REF_PATTERNS["Uplink"])

    objects = []
    for endpoint in ("dcim/interfaces", "dcim_interfaces", "dcim/interface-templates"):
        try:
            objs = SharedBackupState.get_objects_by_type(endpoint)
        except Exception:
            objs = []
        if objs:
            objects = objs
            break

    matches = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        desc = str(obj.get("description") or "").strip()
        if not desc:
            continue
        if rx.search(desc):
            device = obj.get("device") or obj.get("device_name") or obj.get("virtual_machine") or ""
            if isinstance(device, dict):
                device = device.get("name") or device.get("display") or ""
            if_name = obj.get("name") or obj.get("interface") or ""
            matches.append(f'{device} | {if_name}: "{desc}"')

    matches = matches[:15]
    if matches:
        header = f"🟢 NetBox Interface Descriptions ({len(matches)}):"
        return header, "\n".join(matches)

    return f"🟡 Default Examples — No ingested interface descriptions found matching this type.", defaults


def build_naming_system_prompt(prompt: str) -> str:
    """Build the grounded naming/inventory system prompt for the AI Assistant."""
    from core.ai_helper import build_comprehensive_naming_context

    comprehensive_context = build_comprehensive_naming_context(prompt)

    return f"""You are an expert in infrastructure naming conventions, inventory management, and NetBox administration.
You have DIRECT ACCESS to the complete inventory database. Analyze the user's request and respond accurately using the ACTUAL DATABASE DATA provided below.

=== COMPLETE DATABASE CONTEXT ===
{comprehensive_context}

=== IMPORTANT GUIDELINES ===
1. **Answer ALL questions using the ACTUAL DATA above** - You have complete database access
2. When asked to list devices, VMs, or inventory:
   - Reference the inventory sections above
   - Provide actual device names, roles, manufacturers, sites
   - Show real data, not generic examples
3. When asked about specific sites:
   - Use the "Inventory for Site" section if available
   - List actual devices/VMs at that site with their details
4. When asked about VLANs at a site:
   - Explain that VLAN data is in the IPAM tab, but you can see the devices/VMs at the site
5. For naming convention questions:
   - Analyze the actual hostnames in the database
   - Identify patterns and standards being used
   - Suggest improvements based on real examples
6. When counting items (e.g., "how many devices in AGE"):
   - Provide exact counts from the data above
   - List the actual device names
7. Format responses clearly:
   - Use bullet points for lists
   - Include device names, roles, manufacturers, sites
   - Show actual counts and statistics
8. If no data exists for a query, clearly state "No records found in database for [query]"
9. Be specific and data-driven - always reference actual inventory items
10. For naming suggestions, consider:
    - Site codes, device types, sequence numbers
    - Consistency with existing naming patterns in the database
11. If a "NETBOX MASTER BACKUP" section is present it is the full NetBox export:
    - Treat it as authoritative for any object type (sites, racks, devices, interfaces, IPs, VMs, clusters, tenants, circuits, VRFs)
    - Use its "total in backup" figures when stating counts
    - Quote the exact records listed rather than generalizing"""

def apply_case(text: str, mode: str) -> str:
    return text.upper() if mode == "UPPERCASE" else text.lower()

def _render_casing_selector():
    """Return the active casing mode ('UPPERCASE'/'lowercase') selected globally."""
    if "naming_case_radio" not in st.session_state:
        st.session_state["naming_case_radio"] = "UPPERCASE"
    case_mode = st.radio(
        "Casing",
        ["UPPERCASE", "lowercase"],
        index=0 if st.session_state.get("naming_case_radio") == "UPPERCASE" else 1,
        horizontal=True,
        key="naming_case_radio",
        help="Render output in UPPERCASE or lowercase.",
    )
    SSM.set_naming_case_mode(case_mode)
    return case_mode



def display_reference_box(category_key: str, default_lines: str, label: str, site_filter: str = "", name_filter: str = ""):
    real_items = _cached_get_reference_records(category_key, site_filter=site_filter, name_filter=name_filter)

    filter_hint = f" matching '{site_filter.upper()}'" if site_filter else ""
    if name_filter:
        filter_hint += f" (prefix: {name_filter})"

    with st.expander(f"💡 Click to view reference {label} examples ({len(real_items) if real_items else 'Default'} records{filter_hint})", expanded=False):
        if real_items:
            st.markdown(f"##### 🟢 NetBox Ingested Data ({len(real_items)} records{filter_hint}):")
            formatted = []
            for r in real_items[:15]:
                meta_parts = []
                if r.get('manufacturer') and r.get('model_or_role'):
                    meta_parts.append(f"{r['manufacturer']} - {r['model_or_role']}")
                elif r.get('model_or_role'):
                    meta_parts.append(r['model_or_role'])
                if r.get('site'):
                    meta_parts.append(f"Site: {r['site']}")
                if r.get('description'):
                    meta_parts.append(r['description'])

                meta_str = f"  ({', '.join(meta_parts)})" if meta_parts else ""
                formatted.append(f"{r['name']}{meta_str}")
            st.code("\n".join(formatted), language="text")
        else:
            st.markdown("##### 🟡 Default Examples:")
            st.code(default_lines, language="text")

def render_compact_toolbar(active_model):
    # Early restore: Check if backup exists in DB but not in session state and restore it
    from core.shared_backup_state import SharedBackupState
    from core.backup_manager import get_backup_metadata, restore_backup_to_session_state
    
    if not SharedBackupState.has_backup():
        meta = get_backup_metadata()
        if meta.get("loaded", False):
            try:
                restore_backup_to_session_state()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to restore backup on tab load: {e}")
    
    total_recs, device_count, vm_count = _cached_fetch_toolbar_stats(st.session_state.get("backup_stats_version", 0))
    
    status_tag = f"🟢 ({device_count} Devices, {vm_count} VMs in DB)" if total_recs > 0 else "⚪ (Default Examples)"
    tick_devices = " ✅" if device_count > 0 else ""
    tick_vms = " ✅" if vm_count > 0 else ""
    
    with st.expander(f"📥 Ingest NetBox Data (Backup / CSV) {status_tag}", expanded=False):
        # Schema Registry Status Check
        from core.universal_schema_registry import UniversalSchemaRegistry
        registry = UniversalSchemaRegistry.load_from_session_state()
        
        if registry and registry.model_signatures:
            model_count = len(registry.model_signatures)
            st.caption(f"Schema registry active - {model_count} models loaded.")
        else:
            st.warning(
                "⚠️ **Schema Registry Not Initialized:** CSV files will be stored in 'unclassified' tables. "
                "Upload a NetBox backup JSON below to enable automatic classification.",
                icon="⚠️"
            )
        
        if total_recs > 0:
            # Get metadata for devices and VMs
            meta_devices = get_sync_metadata("netbox_devices")
            meta_vms = get_sync_metadata("netbox_virtual_machines")
            
            # Use the most recent source that's not "None"
            sources = [m['source'] for m in [meta_devices, meta_vms] if m['source'] != "None"]
            display_source = sources[0] if sources else "Manual CSV Upload"
            
            st.markdown(f"**DB Status:** `Source: {display_source}`")

        render_backup_uploader("naming")
    
    # AI Assistant
    render_ai_chat(
        history_key="naming_chat_history",
        caption="Ask for naming suggestions and verification (e.g., 'List all devices in AGE' or 'What devices are in Bristol?')",
        placeholder="Ask about devices and naming...",
        active_model=active_model,
        build_system_prompt=build_naming_system_prompt,
    )


    auto_correct = st.checkbox(
        "⚡ Apply Syntax Auto-Correction (Port Shortening & VMware Conventions)",
        value=True, key="esxi_auto_corr",
        help="When checked, enables interface port regex shortening (<Local_Port>/<Remote_Port>) and VMware casing normalization across all asset classes, driven by the Auto-Correction Rules in the Standards Tab.",
    )
    st.session_state["auto_correct"] = bool(auto_correct)

# Preset choices (Device Type / Interface Type) are loaded dynamically from the structured
# `device_presets` / `interface_presets` collections in data/naming_rules.yaml,
# so any Add/Modify/Delete in the Standards Tab is reflected immediately here.
# Reference (label, filter/examples) metadata keyed by device preset code.
DEVICE_REF_INFO = {
    "SW": ("Switch", "SW", "SWUSNYC01-0       (Switch Stack, Member 0)\nSWIUSLON01        (London Switch 01)"),
    "VS": ("Virtual Chassis", "VS", "VSUSNYC01-0       (Virtual Chassis, Member 0)\nVSUSLON01-1"),
    "FW": ("Firewall", "FW", "FWUSNYC01         (NYC Firewall 01)\nFWUSNYCPA01       (NYC Palo Alto FW 01)"),
    "ION": ("SD-WAN ION", "ION", "IONUSNYC01        (NYC SD-WAN 01)\nIONUSLON01"),
    "WAP": ("Wireless AP", "WAP", "WAPUSNYC01        (Access Point NYC 01)\nWAPUSLON01        (Access Point London 01)"),
    "RTR": ("Router", "RTR", "RTRUSNYC01        (NYC Router 01)\nRTRUSLON01"),
    "VA": ("Virtual Appliance", "VA", "VAUSNYC01         (NYC Virtual Appliance 01)\nVAUSLON01"),
}

# Reference (label, default examples) metadata keyed by interface preset code.
INTERFACE_REF_INFO = {
    "Uplink": ("Switch Uplink Interface", "Uplink_to_SWUSNYC02-0_Gi1/0/48\nUplink_to_FWUSNYC01_Te1/0/1\nUplink_to_Huawei-Core_XGE0/0/31"),
    "LAG": ("LAG Member Port", "LACP_to_SWUSNYC02-0_Gi1/0/1\nLACP_to_FWUSNYC01_Po1\nLACP_to_SWUSLONCORE01_Te1/0/1"),
    "Po": ("Port-Channel Interface", "LAG1_to_SWUSNYC02-0\nLAG2_to_FWUSNYC01\nLAG5_to_CV-CPD"),
    "Access": ("Access Port Interface", "Data - PC-001_eth0\nVoice - IP-Phone-101_PoE\nGuest - Printer-Lab_NIC1"),
    "FW Zone": ("Firewall Interface", "TRUST_10\nUNTRUST_100\nDMZ_50"),
}

INTERFACE_REF_PATTERNS = {
    "Uplink": re.compile(r'(?i)uplink|to_'),
    "LAG": re.compile(r'(?i)lacp|lag_member'),
    "Po": re.compile(r'(?i)\bpo\d+|port.channel|lag\d+'),
    "Access": re.compile(r'(?i)\bvlan|access|_eth|_nic|_poe'),
    "FW Zone": re.compile(r'(?i)\b(trust|untrust|dmz|zone|inside|outside|if)\b|\.\d+'),
}


def _device_presets(rules, naming_patterns):
    """Device preset (code, label, pattern_key) tuples loaded from YAML."""
    presets = get_device_presets(rules)
    return [(p["code"], p["label"], p["pattern_key"])
            for p in presets if p["pattern_key"] in naming_patterns]


def _interface_presets(rules, naming_patterns):
    """Interface preset (code, label, pattern_key) tuples loaded from YAML."""
    presets = get_interface_presets(rules)
    return [(p["code"], p["label"], p["pattern_key"])
            for p in presets if p["pattern_key"] in naming_patterns]


def _dev_pattern_key(dev_code, presets):
    for code, _label, key in presets:
        if code == dev_code:
            return key
    return presets[0][2] if presets else "branch_switch"


def _interface_ref(intf_code):
    return INTERFACE_REF_INFO.get(intf_code, ("Interface", ""))
def _ref_info(dev_code):
    return DEVICE_REF_INFO.get(dev_code, ("Device", "", "SWUSNYC01-0       (Switch Stack)\nWAPUSNYC01        (Access Point 01)\nFWUSNYCPA01       (Firewall 01)"))


def _edit_toggle(pattern_key):
    flag_key = f"edit_mode_{pattern_key}"
    ver = st.session_state.get(f"edit_toggle_ver_{pattern_key}", 0)
    widget_key = f"edit_toggle_widget_{pattern_key}_{ver}"
    # Initial state follows flag_key (fresh widget key on each version bump).
    default_val = st.session_state.get(flag_key, False)
    toggled = st.toggle(
        "Edit Mode",
        value=default_val,
        key=widget_key,
    )
    st.session_state[flag_key] = bool(toggled)
    return bool(toggled)


def _interface_key(intf_code, presets):
    for code, _label, key in presets:
        if code == intf_code:
            return key
    return presets[0][2] if presets else "switch_uplink_desc"


def _interface_ref_examples(intf_code):
    """Return ``(header, records_text)`` for the active interface type.

    **header** — status title rendered with ``st.markdown`` (bold).
    **records_text** — lines joined by newlines, passed to ``st.code(... language=None)``
    so every entry occupies its own monospace row matching Column 1's reference box.
    """
    from core.shared_backup_state import SharedBackupState
    return _interface_ref_examples_cached(intf_code)






def render_naming_tab(active_model):
    st.subheader("🏷️ Standardized Infrastructure Naming Generator", help="Generate and validate standardized hostnames and interface descriptions. All preset-driven patterns are configured in the Standards Tab.")
    st.caption("Generate and validate standardized hostnames for network devices, servers, VMs, and ESXi configurations using AI-powered naming conventions aligned with your NetBox inventory data.")

    naming_rules = SSM.get_naming_rules()
    if not naming_rules:
        naming_rules = load_naming_rules()
        SSM.set_naming_rules(naming_rules)
    naming_patterns = get_naming_patterns(naming_rules)
    variables = get_pattern_variables(naming_rules)
    render_compact_toolbar(active_model)

    ac_row_c1, ac_row_c2 = st.columns([3, 1], vertical_alignment="bottom")
    with ac_row_c1:
        naming_cat = st.radio(
            "Select Asset Class",
            [
                "🔧 Network & Security Devices (Switches, APs, Firewalls, Routers)",
                "🖥️ Hosts & Virtual Machines (ESXi & VMs)",
                "☁️ Hypervisor Network Descriptions (ESXi, Proxmox, etc.)",
            ],
            horizontal=True,
            help="Select the asset class to generate standardized infrastructure names. Each class loads its preset-driven form. Configured in the Standards Tab > Device Type Presets / Interface Type Presets / ESXi Network Description Presets.",
        )
    with ac_row_c2:
        case_mode = _render_casing_selector()

    global_site = ""
    if "Network & Security" in naming_cat or "Hosts & Virtual Machines" in naming_cat:
        global_site = _site_code_assistant_compact(naming_rules, "global")
    st.divider()

    if "Network & Security" in naming_cat:
        _asset_class_1(case_mode, active_model, naming_rules, naming_patterns, variables, global_site)
    elif "Hosts & Virtual Machines" in naming_cat:
        _asset_class_2(case_mode, active_model, naming_patterns, variables, global_site)
    else:
        _asset_class_3(naming_rules, case_mode, active_model)

def _site_code_assistant_compact(naming_rules, prefix: str) -> str:
    with st.expander("📍 Site Code Assistant", expanded=False):
        loc_c, btn_c, badge_c, clear_c = st.columns([3, 1.2, 2.5, 1], vertical_alignment="center")
        with loc_c:
            loc = st.text_input(
                "City / Location", value="", placeholder="Enter city e.g. Bristol, Sydney...",
                key=f"loc_compact_{prefix}", label_visibility="collapsed",
                help="Enter a city/location to compute its site code. City-to-code mappings follow the Original Pattern → Replacement format. Configured in Standards Tab > Site Code Mapping Rules.",
            )
        with btn_c:
            if st.button("Suggest & Fill", key=f"site_suggest_{prefix}", width='stretch'):
                code = compute_suggested_site_code(loc, naming_rules)
                st.session_state[f"_suggested_site_{prefix}"] = code
                st.rerun()
        with badge_c:
            if st.session_state.get(f"_suggested_site_{prefix}"):
                code = st.session_state[f"_suggested_site_{prefix}"]
                st.success(f"Site: **{code}**")
        with clear_c:
            if st.session_state.get(f"_suggested_site_{prefix}"):
                if st.button("Clear", key=f"site_clear_{prefix}", width='stretch'):
                    st.session_state.pop(f"_suggested_site_{prefix}", None)
                    st.rerun()
    return st.session_state.get(f"_suggested_site_{prefix}", "")

def _asset_class_1(case_mode, active_model, naming_rules, naming_patterns, variables, global_site=""):
    col_a, col_b = st.columns([1, 1])
    dev_presets = _device_presets(naming_rules, naming_patterns)
    intf_presets = _interface_presets(naming_rules, naming_patterns)
    dev_codes = [code for code, _label, _key in dev_presets]
    intf_codes = [code for code, _label, _key in intf_presets]
    if not intf_codes:
        intf_defaults = [p["code"] for p in get_interface_presets(naming_rules)]
        intf_codes = intf_defaults or ["Uplink"]
    if not dev_codes:
        dev_codes = [p["code"] for p in get_device_presets(naming_rules)] or ["SW"]

    dev_label_map = {code: label for code, label, _key in dev_presets}
    if not dev_label_map:
        dev_label_map = {p["code"]: p["label"] for p in get_device_presets(naming_rules)}
    intf_label_map = {code: label for code, label, _key in intf_presets}
    with col_a:
        st.subheader("Universal Device Hostname Generator", help="Generate standardized device hostnames using preset-driven patterns. Device type and interface presets are configured in the Standards Tab > Device Type Presets / Interface Type Presets.")
        auto_code = global_site
        dev_type = st.radio("Device Type", dev_codes, horizontal=True, key="dev_prefix_sel", label_visibility="collapsed", help="Select the device class to generate a standardized hostname. Choices are configured in the Standards Tab (Device Type Presets).", format_func=lambda c: c)
        if dev_type:
            st.caption(f"ℹ️ **{dev_type}**: {dev_label_map.get(dev_type, '')}")
        pk = _dev_pattern_key(dev_type, dev_presets)
        edit_on = _edit_toggle(pk)
        pat = naming_patterns.get(pk, "")
        if edit_on:
            render_edit_mode_ui(pk, pat, variables)
            st.stop()
            return

        defaults = {}
        if auto_code:
            defaults["site"] = auto_code
        order = (naming_rules.get("token_order") or {}).get(pk)
        values = render_token_widgets(pat, variables, f"dev_{pk}", defaults, custom_order=order)
        interpolated = render_dynamic_pattern(pat, values, variables)
        final = apply_case(interpolated, case_mode)
        st.caption("Generated Device Hostname:")
        st.code(final, language="text")

        if st.button("AI Verify / Suggest Device Hostname", key="ai_chk_dev"):
            with st.spinner("Auditing..."):
                dev_label = dev_label_map.get(dev_type, dev_type)
                st.info(verify_and_suggest_with_ai(final, active_model, asset_type=f"Network/Security Device ({dev_label})", category_key="device", site_filter=values.get("site", "")))

        ref_lbl, ref_flt, ref_ex = _ref_info(dev_type)
        display_reference_box("device", ref_ex, ref_lbl, values.get("site", ""), ref_flt)

    with col_b:
        st.subheader("Switch & Firewall Interface Formatter", help="Select the interface class to format. Choices are configured in the Standards Tab > Interface Type Presets.")
        intf_type = st.radio(
            "Interface Type",
            intf_codes,
            horizontal=True,
            key="p_cat_sel",
            label_visibility="collapsed",
            help="Select the interface class to format. Choices are configured in the Standards Tab (Interface Type Presets).",
            format_func=lambda c: c,
        )
        if intf_type:
            st.caption(f"ℹ️ **{intf_type}**: {intf_label_map.get(intf_type, '')}")
        ipk = _interface_key(intf_type, intf_presets)
        edit_on = _edit_toggle(ipk)
        pat = naming_patterns.get(ipk, "")
        if edit_on:
            render_edit_mode_ui(ipk, pat, variables)
            st.stop()
            return

        if ipk == "switch_access_desc":
            tokens = extract_tokens(pat)
            vlan_id = ""
            vlan_name = ""
            dev_end = ""
            port_end = ""
            if "vlan_id" in tokens:
                vlan_id = st.text_input("Access VLAN ID (Optional)", value="", placeholder="e.g. 10, 100", key="ac_vlan").strip()
            if "vlan_name" in tokens:
                vlan_name = st.text_input("VLAN Name (Optional)", value="", placeholder="e.g. Data, Voice", key="ac_vlan_name").strip()
            if "device" in tokens:
                dev_end = st.text_input("Connected Device/Host", value="", placeholder="e.g. PC-001", key="ac_device").strip()
            if "port" in tokens:
                port_end = st.text_input("Endpoint Port (Optional)", value="", placeholder="e.g. eth0", key="ac_port").strip()
            vlan_disp = vlan_name or (f"VLAN{vlan_id}" if vlan_id else "")
            vals = {"vlan_id": vlan_id or "<vlan_id>", "vlan_name": vlan_disp, "device": dev_end or "<device>", "port": port_end or "<port>"}
            gen = render_dynamic_pattern(pat, vals, variables)
        else:
            order = (naming_rules.get("token_order") or {}).get(ipk)
            vals = render_token_widgets(pat, variables, f"intf_{ipk}", custom_order=order)
            if st.session_state.get("esxi_auto_corr", True):
                for port_token in ("local_port", "remote_port"):
                    if port_token in vals and vals.get(port_token):
                        vals[port_token] = normalize_port_shortname(vals[port_token])
            for port_token in ("local_port", "remote_port"):
                if port_token in vals and vals.get(port_token):
                    vals[port_token] = vals[port_token].strip()
            gen = render_dynamic_pattern(pat, vals, variables)

        st.caption("Generated Interface Description:")
        st.code(gen, language="text")
        ref_lbl, ref_ex = _interface_ref(intf_type)
        if st.button("AI Verify Interface Description", key="ai_chk_intf"):
            with st.spinner("Auditing..."):
                st.info(verify_and_suggest_with_ai(gen, active_model, asset_type=ref_lbl, category_key="device", site_filter=""))
        with st.expander(f"💡 Click to view reference {ref_lbl} examples", expanded=False):
            ih, it = _interface_ref_examples(intf_type)
            st.markdown(f"**{ih}**")
            st.code(it, language=None)


def _host_vm_presets(rules, naming_patterns):
    """Host/VM preset (code, label, pattern_key) tuples loaded from YAML."""
    presets = get_host_vm_presets(rules)
    return [(p["code"], p["label"], p["pattern_key"])
            for p in presets if p["pattern_key"] in naming_patterns]



def _asset_class_2(case_mode, active_model, naming_patterns, variables, global_site=""):
    naming_rules = SSM.get_naming_rules()
    if not naming_rules:
        naming_rules = load_naming_rules()
        SSM.set_naming_rules(naming_rules)
    presets = _host_vm_presets(naming_rules, naming_patterns)

    host_keys = [code for code, _label, key in presets if key != "vm_host"]
    host_label_map = {code: label for code, label, _key in presets}
    if not host_keys:
        st.info("No host type presets. Add one in Standards.")
    
    vm_pk = "vm_host"
    vm_keys = [code for code, _label, key in presets if key == "vm_host"]
    vm_label_map = {code: label for code, label, _key in presets}

    hst_defaults = {"site_prefix": global_site, "site": global_site} if global_site else {}
    vm_defaults = {"site": global_site} if global_site else {}

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Host Type", help="Select the host type to generate a hostname. Choices are configured in the Standards Tab > Hosts Type Presets.")
        if host_keys:
            host_type = st.radio(
                "Host Type",
                host_keys,
                horizontal=True,
                key="host_type_sel",
                label_visibility="collapsed",
                help="Select the host type to generate a hostname. Choices are configured in the Standards Tab (Hosts & Virtual Machines Presets).",
                format_func=lambda c: c,
            )
            if host_type:
                st.caption(f"ℹ️ **{host_type}**: {host_label_map.get(host_type, '')}")
            host_pk = next((p[2] for p in presets if p[0] == host_type), None)
            pat = naming_patterns.get(host_pk, "")
            if not host_pk or not pat:
                st.info(f"No naming pattern for {host_type}. Add one in Standards.")
                return
            order = (naming_rules.get("token_order") or {}).get(host_pk)
            values = render_token_widgets(pat, variables, f"hst_{host_pk}", defaults=hst_defaults, custom_order=order)
            gen_raw = render_dynamic_pattern(pat, values, variables)
            gen_raw = re.sub(r"\s*\([^)]*\)", "", gen_raw).strip()
            gen_raw = re.sub(r"<[^>]+>", "", gen_raw)
            gen_raw = re.sub(r"\.+", ".", gen_raw).strip(".")
            if "." in gen_raw:
                parts = gen_raw.split(".", 1)
                gen = f"{apply_case(parts[0], case_mode)}.{parts[1].lower()}"
            else:
                gen = apply_case(gen_raw, case_mode)
            host_label = host_label_map.get(host_type, host_type)
            st.caption(f"Generated {host_label} Hostname:")
            st.code(gen, language="text")
            if st.button(f"AI Verify {host_label} Host", key="ai_chk_hst"):
                with st.spinner("Auditing..."):
                    st.info(verify_and_suggest_with_ai(gen, active_model, asset_type=f"{host_label} Hypervisor Hostname", category_key="hypervisor", site_filter=values.get("site_prefix", "")))
            display_reference_box("hypervisor", "NYCESX001.corp.internal\nLONESX001.corp.internal\nSYDESX01.corp.local", "Hypervisor", site_filter=values.get("site_prefix", ""))

    with col_b:
        st.subheader("Virtual Machine (VM) Hostname", help="Select the VM role/type to generate a hostname. Choices are configured in the Standards Tab > Virtual Machine Presets.")
        vm_type = st.radio(
            "VM Role / Type",
            vm_keys,
            horizontal=True,
            key="host_vm_vm_role",
            label_visibility="collapsed",
            help="Select the VM role/type. Choices are configured in the Standards Tab (Hosts & Virtual Machines Presets).",
            format_func=lambda c: c,
        )
        if vm_type:
            st.caption(f"ℹ️ **{vm_type}**: {vm_label_map.get(vm_type, '')}")
        vm_pat = naming_patterns.get(vm_pk, "")
        if _edit_toggle(vm_pk):
            render_edit_mode_ui(vm_pk, vm_pat, variables)
            st.stop()
            return
        order = (naming_rules.get("token_order") or {}).get(vm_pk)
        values = render_token_widgets(vm_pat, variables, "vm", defaults=vm_defaults, custom_order=order)
        gen_raw = render_dynamic_pattern(vm_pat, values, variables)
        gen_raw = re.sub(r"\s*\([^)]*\)", "", gen_raw).strip()
        gen_raw = re.sub(r"\s+or\s+.*", "", gen_raw).strip()
        gen_raw = re.sub(r"<[^>]+>", "", gen_raw)
        gen = apply_case(gen_raw, case_mode)
        st.caption(f"Generated {vm_label_map.get(vm_type, 'VM')} Hostname:")
        st.code(gen, language="text")
        if st.button("AI Verify VM Hostname", key="ai_chk_vm"):
            with st.spinner("Auditing..."):
                st.info(verify_and_suggest_with_ai(gen, active_model, asset_type="Virtual Machine (VM) Hostname", category_key="vm", site_filter=values.get("site", "")))
        display_reference_box("vm", "USNYCAPP01     (NYC Application Server 01)\nUKLONDB01\nAUSYDFS01", "Virtual Machine", site_filter=values.get("site", ""))


def _asset_class_3(naming_rules: dict, casing: str, active_model: str = ""):
    st.subheader("☁️ Hypervisor Network Description Formatter", help="Format standardized hypervisor network descriptions (VMware ESXi, Proxmox VE, Linux Bridges, etc.) matching infrastructure guidelines.")

    mode = st.radio(
        "Operation Mode",
        ["🛠️ Interactive Single Item Mode", "📸 Screenshot Batch Mode (Topology Parsing)"],
        index=0,
        horizontal=True,
        label_visibility="collapsed"
    )

    if mode == "🛠️ Interactive Single Item Mode":
        _render_interactive_hypervisor_mode(naming_rules, casing, active_model)
    else:
        _render_screenshot_batch_mode(naming_rules, casing, active_model)


def _build_dynamic_teaming_clause(uplinks: list) -> str:
    """Dynamically synthesize teaming description string without hardcoded assumptions.

    Reads empty_role_token and no_uplink_display from Standards (topology_parsing_presets +
    pattern_variables.uplink_role).  If uplinks is empty (or all are "No physical network
    adapters" state), returns the standards-driven no_uplink_text instead of an empty string.
    Speed-only or junk tokens are silently dropped so they never enter the clause.
    Uses pure Standards-driven dictionary mapping — zero Python regex stripping.
    """
    if not uplinks:
        _rules = SSM.get_naming_rules() or {}
        _tpp = _rules.get("topology_parsing_presets") or {}
        _plat_keys = list(_tpp.keys()) if isinstance(_tpp, dict) else []
        _active = st.session_state.get("naming_target_platform")
        if _active not in _plat_keys:
            _active = _plat_keys[0] if _plat_keys else None
        _plat_cfg = _tpp.get(_active, {}) if isinstance(_tpp.get(_active), dict) else {}
        _teaming = _plat_cfg.get("teaming_rules") or {}
        if isinstance(_teaming, dict):
            _v = _teaming.get("no_uplink_display")
            if _v and str(_v).strip():
                return str(_v).strip()
        _nlu = _plat_cfg.get("no_uplink_label")
        if _nlu and str(_nlu).strip():
            return str(_nlu).strip()
        _ur_cfg = (_rules.get("pattern_variables") or {}).get("uplink_role") or {}
        return str(_ur_cfg.get("no_uplink_fallback") or "").strip()

    def _is_junk_or_speed(text: str) -> bool:
        t = text.strip()
        if not t:
            return True
        if _is_junk_token(t, None):
            return True
        return bool(re.search(r"\b\d+\s*(?:Gbit/s|Mbit/s|Kbit/s|Full|Half)\b", t, re.I))

    # Load teaming rules once at function scope
    _rules = SSM.get_naming_rules() or {}
    _tpp = _rules.get("topology_parsing_presets") or {}
    _plat_keys = list(_tpp.keys()) if isinstance(_tpp, dict) else []
    _active = st.session_state.get("naming_target_platform")
    if _active not in _plat_keys:
        _active = _plat_keys[0] if _plat_keys else None
    _plat_cfg = _tpp.get(_active, {}) if isinstance(_tpp.get(_active), dict) else {}
    _teaming = _plat_cfg.get("teaming_rules") or {}
    if not isinstance(_teaming, dict):
        _teaming = {}
    _mapping = _teaming.get("uplink_to_teaming_mapping") or {}
    _empty_role_token = str(_teaming.get("empty_role_token")
        or ((_rules.get("pattern_variables") or {}).get("uplink_role") or {}).get("empty_role_token")
        or "None").strip().lower()

    parts = []
    for u in uplinks:
        iface = str(u.get("interface", "")).strip()
        if not iface or _is_junk_or_speed(iface):
            continue
        role_raw = str(u.get("role") or u.get("uplink_role") or "").strip()
        # Pure Standards-driven dictionary mapping — no regex stripping
        teaming_status = _mapping.get(role_raw, role_raw)
        if role_raw.lower() == _empty_role_token or not teaming_status:
            parts.append(iface)
        else:
            parts.append(f"{iface} {teaming_status}")

    return " / ".join(parts)


def _clean_rendered_clause(rendered: str) -> str:
    """Removes dangling slashes, empty parens, and orphan active/standby markers."""
    rendered = re.sub(r"\(\s*/\s*\)", "", rendered)
    rendered = re.sub(r"/\s*\)", ")", rendered)
    rendered = re.sub(r"\(\s*/", "(", rendered)
    rendered = re.sub(r"\(\s*Active\s*/\s*Standby\s*\)", "", rendered)
    rendered = re.sub(r"\(\s*Active\s*/\s*\)", "(Active)", rendered)
    rendered = re.sub(r"\(\s*/\s*Standby\s*\)", "(Standby)", rendered)
    rendered = re.sub(r"\(\s*\)", "", rendered)
    rendered = re.sub(r"\s{2,}", " ", rendered)
    return rendered.strip()


def render_hypervisor_edit_mode_ui(pattern_key: str, current_preset: dict, platform: str, naming_rules: dict):
    """Edit mode UI for hypervisor presets, syncing pattern back to hypervisor_presets and persisting."""
    from config.naming_rules import save_naming_rules as _save_naming_rules

    edit_key = f"edit_mode_{pattern_key}"
    pending_key = f"_edit_pending_{pattern_key}"
    initial = st.session_state.pop(pending_key, current_preset.get("pattern", "") or "")

    edited = st.text_area(
        "Pattern Template",
        value=initial,
        height=120,
        key=f"edit_text_{pattern_key}",
        help="Edit the pattern using <Token> placeholders. Manage variables in the Standards tab > Pattern Variables Reference.",
    )

    current_tokens = extract_tokens(edited)

    rules = SSM.get_naming_rules({})
    saved_token_order = (rules.get("token_order") or {}).get(pattern_key, [])
    order_key = f"field_order_{pattern_key}"
    custom_order = st.session_state.get(order_key)
    if custom_order is None:
        custom_order = [t for t in saved_token_order if t in current_tokens]
        for t in current_tokens:
            if t not in custom_order:
                custom_order.append(t)
        st.session_state[order_key] = list(custom_order)
    else:
        custom_order = [t for t in custom_order if t in current_tokens]
        for t in current_tokens:
            if t not in custom_order:
                custom_order.append(t)
        st.session_state[order_key] = list(custom_order)

    if custom_order:
        with st.expander("🎛️ Field Order Configuration", expanded=False):
            st.caption("Reorder the input fields. The saved order is used when rendering the generator.")
            for i in range(len(custom_order)):
                col_lbl, col_up, col_dn = st.columns([4, 1, 1])
                with col_lbl:
                    st.markdown(f"`{i + 1}.` {token_label({}, custom_order[i])}")
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

    if st.button("💾 Save to Standards", key=f"hyp_save_{pattern_key}", type="primary"):
        rules = SSM.get_naming_rules({})
        hyp_presets = rules.get("hypervisor_presets") or {}
        plat_presets = list(hyp_presets.get(platform, []))

        for item in plat_presets:
            if item.get("code") == current_preset.get("code"):
                item["pattern"] = edited
                break

        hyp_presets[platform] = plat_presets
        rules["hypervisor_presets"] = hyp_presets

        token_order_map = rules.get("token_order", {})
        if not isinstance(token_order_map, dict):
            token_order_map = {}
        token_order_map[pattern_key] = list(custom_order)
        rules["token_order"] = token_order_map
        rules["naming_patterns"] = rules.get("naming_patterns") or {}
        rules["naming_patterns"][pattern_key] = edited

        SSM.set_naming_rules(rules)
        st.session_state.pop(order_key, None)
        _save_naming_rules(rules, source=f"Edit Mode: {pattern_key}")

        st.session_state[edit_key] = False
        st.session_state[f"edit_toggle_ver_{pattern_key}"] = (
            st.session_state.get(f"edit_toggle_ver_{pattern_key}", 0) + 1
        )
        st.session_state.pop(pending_key, None)
        st.rerun()


def _render_interactive_hypervisor_mode(naming_rules: dict, casing: str, active_model: str):
    """Token-driven dynamic generator displaying default as yellow hint next to label."""
    with st.container(border=True):
        st.markdown("##### 🛠️ Universal Hypervisor Description Generator")
        st.caption("Dynamically generated token-based forms driven entirely by platform templates.")

        presets_map = naming_rules.get("hypervisor_presets") or {}
        if not presets_map:
            from config.naming_rules import DEFAULT_HYPERVISOR_PRESETS
            presets_map = DEFAULT_HYPERVISOR_PRESETS

        _tpp = naming_rules.get("topology_parsing_presets") or {}
        if isinstance(_tpp, list):
            _tpp = {
                item.get("platform") or item.get("name"): item
                for item in _tpp
                if isinstance(item, dict) and (item.get("platform") or item.get("name"))
            }
        _tpp_keys = list(_tpp.keys()) if isinstance(_tpp, dict) else []
        _saved_order = naming_rules.get("hypervisor_platform_order") or []
        _platforms = [p for p in _saved_order if p in _tpp]
        for p in _tpp_keys:
            if p not in _platforms:
                _platforms.append(p)
        platforms = _platforms if _platforms else _tpp_keys[:1]
        if not platforms:
            st.info("No platforms configured. Add one in Standards.")
        else:
            col_p1, col_p2 = st.columns([1, 2])
            with col_p1:
                sel_platform = st.selectbox("Platform", platforms, key="hyp_gen_platform_sel")
            with col_p2:
                presets = presets_map.get(sel_platform, [])
                interactive_presets = [p for p in presets if not p.get("hidden", False)] or presets
                codes = [p.get('code') for p in interactive_presets]
                sel_code = st.radio("Template", codes, horizontal=True, key="hyp_gen_preset_sel", format_func=lambda c: c)
                current_preset = next((p for p in interactive_presets if p.get('code') == sel_code), interactive_presets[0] if interactive_presets else {})
                st.caption(f"ℹ️ **{current_preset.get('label', sel_code)}**")

        if not presets:
            st.warning("No templates defined for this platform.")
            return

        edit_key = f"hyp_{sel_platform}_{sel_code}"
        edit_on = _edit_toggle(edit_key)
        if edit_on:
            render_hypervisor_edit_mode_ui(edit_key, current_preset, sel_platform, naming_rules)
            st.stop()
            return

        pattern = current_preset.get("pattern", "")
        tokens = list(dict.fromkeys(re.findall(r"<([a-zA-Z0-9_]+)>", pattern)))

        # Load registered pattern variables metadata
        all_vars = get_pattern_variables(naming_rules)

        st.markdown(f"**Pattern Template:** `{pattern}`")
        input_values = {}
        cols = st.columns(min(len(tokens), 3) if tokens else 1)
        for idx, token in enumerate(tokens):
            var_meta = all_vars.get(token.lower(), {})
            lbl = var_meta.get("label") or token.replace("_", " ").title()
            ph = var_meta.get("placeholder") or f"<{token}>"
            hint = var_meta.get("default") or ""

            with cols[idx % len(cols)]:
                # Render label with yellow hint if default metadata exists
                if hint:
                    st.markdown(
                        f"<div style='font-size: 0.88rem; font-weight: 500; margin-bottom: 4px;'>"
                        f"{lbl} <span style='color: #eab308; font-weight: 400; font-size: 0.8rem;'>({hint})</span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
                    input_values[token] = st.text_input(
                        lbl,
                        value="",
                        key=f"dyn_tok_val_{sel_platform}_{token}",
                        placeholder=ph,
                        label_visibility="collapsed"
                    ).strip()
                else:
                    input_values[token] = st.text_input(
                        lbl,
                        value="",
                        key=f"dyn_tok_val_{sel_platform}_{token}",
                        placeholder=ph
                    ).strip()

        rendered = pattern
        for token, val in input_values.items():
            if val:
                rendered = rendered.replace(f"<{token}>", val)
            else:
                rendered = rendered.replace(f"<{token}>", "")

        cleaned_output = _clean_rendered_clause(rendered)

        # Apply Auto-Correction if enabled globally
        if st.session_state.get("auto_correct", True):
            from utils.formatters import apply_auto_corrections
            cleaned_output = apply_auto_corrections(cleaned_output, "ocr_cleaning")
            cleaned_output = apply_auto_corrections(cleaned_output, "vmware")

        st.caption("Generated NetBox Description:")
        st.code(cleaned_output, language="text")


# ---------------------------------------------------------------------------
# Unified topology analysis helpers (Session-callback friendly)
# ---------------------------------------------------------------------------
def _publish_topology_records(
    records: List[Dict[str, Any]],
    mode_label: str = "",
) -> None:
    """Publish reduced topology records into session state and increment editor_ver."""
    st.session_state["topology_records"] = records
    st.session_state["discovered_switches"] = sorted(
        list({r["switch"] for r in records if r.get("switch")}),
        key=_natsort_key,
    )
    st.session_state["discovered_uplink_candidates"] = sorted(
        list({r["interface"] for r in records if r.get("type") == "Uplink" and r.get("interface") and r.get("interface") != "No Uplink"}),
        key=_natsort_key,
    )
    st.session_state["discovered_service_candidates"] = sorted(
        list({r["purpose"] for r in records if r.get("type") != "Uplink" and r.get("purpose")}),
        key=_natsort_key,
    )
    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
    if mode_label:
        st.session_state["paste_mode"] = mode_label


def _handle_clear_topology_all() -> None:
    """Clear ALL topology-related session state (called by both Clear All buttons)."""
    _topo_keys = [
        "staged_topology_imgs",
        "topology_records",
        "discovered_switches",
        "discovered_uplink_candidates",
        "discovered_service_candidates",
        "paste_gaps",
        "paste_parse_warnings",
        "paste_merge_report",
        "paste_mode",
        "latest_ocr_raw_text",
        "topology_ocr_cache",
        "esxi_analysis",
    ]
    for _k in _topo_keys:
        st.session_state.pop(_k, None)
    st.session_state["vsphere_paste_text"] = ""
    st.session_state["topo_uploader_key_ver"] = st.session_state.get("topo_uploader_key_ver", 0) + 1
    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
    st.session_state["paste_text_ver"] = st.session_state.get("paste_text_ver", 0) + 1
    st.toast("Cleared")


def _handle_topology_analyze() -> None:
    """Callback for 'Analyze & Auto-Populate' button — unified pipeline entry point."""
    from core.ocr_engine import run_local_ocr_pipeline

    naming_rules = SSM.get_naming_rules() or load_naming_rules()
    raw_text = st.session_state.get(f"vsphere_paste_text_{st.session_state.get('paste_text_ver', 0)}", "").strip()
    images = st.session_state.get("staged_topology_imgs", [])
    ocr_cache = st.session_state.setdefault("topology_ocr_cache", {})
    st.session_state["_pending_ocr_raw_lines"] = []
    _tpp_local = naming_rules.get("topology_parsing_presets") or {}
    _plat_keys = list(_tpp_local.keys()) if isinstance(_tpp_local, dict) else []
    target_platform = st.session_state.get("naming_target_platform")
    if target_platform not in _plat_keys:
        target_platform = _plat_keys[0] if _plat_keys else None
    _unified_presets = _tpp_local
    plat_cfg = _unified_presets.get(target_platform, {}) if isinstance(_unified_presets.get(target_platform), dict) else {}
    platform_tokens = (naming_rules.get("platform_tokens") or {}).get(target_platform, {})

    configured_modules = plat_cfg.get("modules")
    if not isinstance(configured_modules, list):
        configured_modules = []

    _img_mod_ids = [
        mid for mid in configured_modules
        if mid in PROCESSING_MODULES and PROCESSING_MODULES[mid]["input_kind"] == "image"
    ]
    _text_mod_ids = [
        mid for mid in configured_modules
        if mid in PROCESSING_MODULES and PROCESSING_MODULES[mid]["input_kind"] == "text"
    ]

    def _ocr_single_image(img):
        """Run OCR on a single image and return extracted records."""
        if hasattr(img, "seek"):
            img.seek(0)
        raw_bytes = img.getvalue() if hasattr(img, "getvalue") else img.read()
        if hasattr(img, "seek"):
            img.seek(0)
        ocr_res = run_local_ocr_pipeline([raw_bytes])
        if ocr_res.get("errors"):
            for err in ocr_res["errors"]:
                st.toast(f"OCR error: {err}", icon="⚠️")
        raw_boxes = ocr_res.get("raw_tokens", [])
        img_w = ocr_res.get("image_width", 1920)
        img_h = ocr_res.get("image_height", 1080)
        merged_records = []
        if _img_mod_ids:
            mid = _img_mod_ids[0]
            mod = PROCESSING_MODULES[mid]
            handler = mod["handler"]
            if hasattr(handler, "extract"):
                recs = handler.extract(raw_boxes, image_width=img_w, image_height=img_h, plat_cfg=plat_cfg)
            else:
                recs = handler(raw_boxes, plat_cfg=plat_cfg) if callable(handler) else []
            if isinstance(recs, list):
                for _r in recs:
                    _r.setdefault("source", "ocr")
                merged_records.extend(recs)
        if not merged_records:
            if not _img_mod_ids:
                st.toast("No image module configured for this platform.", icon="⚠️")
            else:
                st.toast("OCR found no topology in the screenshot.", icon="⚠️")
            return []
        # Store raw tokens for inspector
        _insp_lines = st.session_state.get("_pending_ocr_raw_lines", [])
        _fname = getattr(img, "name", "image")
        _insp_lines.append(f"--- [Image {_fname}] ---")
        for _t in raw_boxes:
            _txt = str(_t.get("text", "")).strip()
            _box = _t.get("box") or [0, 0, 0, 0]
            _conf = float(_t.get("conf", 0.0))
            if _txt:
                _insp_lines.append(f"[{_box[0]:.0f}, {_box[1]:.0f}, {_box[2]:.0f}, {_box[3]:.0f}] (conf: {_conf:.2f}) -> {_txt}")
        _insp_lines.append("")
        st.session_state["_pending_ocr_raw_lines"] = _insp_lines
        return merged_records

    def _ocr_records():
        if not images:
            return []
        all_recs = []
        import hashlib
        for img in images:
            _b = img.getvalue() if hasattr(img, "getvalue") else b""
            _sha = hashlib.sha1(_b).hexdigest() if _b else ""
            _cache_key = f"{target_platform}:{_sha}" if _sha else ""
            if _cache_key and _cache_key in ocr_cache:
                recs = [dict(r) for r in ocr_cache[_cache_key]]
            else:
                recs = _ocr_single_image(img)
                if _cache_key:
                    ocr_cache[_cache_key] = [dict(r) for r in recs]
            all_recs.extend(recs)
        return all_recs

    result = run_topology_pipeline(plat_cfg, raw_text, _ocr_records if images else None)
    source = result["source"]
    records_raw = result["records"]
    warnings = result.get("warnings", [])
    gaps = result.get("gaps", [])
    report = result.get("report")

    # Store diagnostics in session state
    st.session_state["esxi_analysis"] = {
        "source": source,
        "warnings": warnings,
        "gaps": gaps,
        "report": report,
    }
    st.session_state["paste_gaps"] = gaps
    st.session_state["paste_parse_warnings"] = warnings
    st.session_state["paste_merge_report"] = report
    st.session_state["paste_mode"] = source

    if source == "none":
        st.toast(warnings[0] if warnings else "Nothing to analyse.", icon="⚠️")
        return

    if source == "ocr":
        reduced = reduce_topology_pipeline(records_raw, plat_cfg, platform_tokens)
        _publish_topology_records(reduced, "ocr")
        _insp_lines = st.session_state.pop("_pending_ocr_raw_lines", [])
        if _insp_lines:
            st.session_state["latest_ocr_raw_text"] = "\n".join(_insp_lines)
        st.toast(f"Source: OCR only ({len(reduced)} records)", icon="📷")
    elif source in ("text", "text+ocr"):
        # Text is authoritative; strip normalizations and noise filters so real names survive
        text_cfg = {**plat_cfg, "token_normalizations": [], "noise_filters": []}
        reduced = reduce_topology_pipeline(records_raw, text_cfg, {})
        _publish_topology_records(reduced, source)
        if report:
            filled = len(report.get("filled", []))
            unresolved = len(report.get("unresolved", []))
            if source == "text+ocr":
                st.toast(f"Source: text + OCR ({filled} filled, {unresolved} missing)")
            else:
                st.toast(f"Source: text ({len(reduced)} records)")
        else:
            st.toast(f"Source: text ({len(reduced)} records)")
        # Keep raw OCR inspector data if OCR ran
        if source == "text+ocr":
            _insp_lines = st.session_state.get("_pending_ocr_raw_lines", [])
            if _insp_lines:
                st.session_state["latest_ocr_raw_text"] = "\n".join(_insp_lines)
    else:
        st.toast(f"Unhandled source: {source}", icon="⚠️")
        return

    if result.get("needs_screenshot"):
        gap_msgs = [g.get("message", "") for g in gaps if g.get("ocr_can_help")]
        if gap_msgs:
            st.toast(f"Gap(s) detected in pasted text — upload a screenshot of that switch, then click Analyze again", icon="ℹ️")


_GRID_COLS = ["switch", "interface", "type", "ip", "purpose", "vlan", "role", "speed", "source", "is_uncertain", "note"]
_GRID_TEXT_COLS = [_c for _c in _GRID_COLS if _c != "is_uncertain"]


def _is_blank(v) -> bool:
    """True for None / NaN / pd.NA. Strings (even empty) are never 'blank' here."""
    if v is None:
        return True
    if isinstance(v, str):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _as_bool(v) -> bool:
    """Tolerant bool: True/False, numpy bool, 1/0, 'true'/'yes'/'⚠️', None/NaN -> False."""
    if _is_blank(v):
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "\u26a0\ufe0f", "\u26a0")
    return bool(v)


def _records_to_grid_df(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """topology_records -> DataFrame for st.data_editor.

    Text columns are always object/str, `is_uncertain` is always a real bool (also when there are
    zero rows, where pandas .map() would otherwise keep the wrong dtype), `_check` is derived.
    """
    df = pd.DataFrame(list(records) if records else [], columns=_GRID_COLS)
    for col in _GRID_TEXT_COLS:
        df[col] = pd.Series(["" if _is_blank(v) else str(v) for v in df[col]], index=df.index, dtype="object")
    df["is_uncertain"] = pd.Series([_as_bool(v) for v in df["is_uncertain"]], index=df.index, dtype="bool")
    df["_check"] = pd.Series(["OCR \u2013 verify" if u else "" for u in df["is_uncertain"]], index=df.index, dtype="object")
    return df


def _grid_df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Edited grid DataFrame -> topology_records (plain str cells, real bool flag, no derived `_check`)."""
    out: List[Dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        rec = {c: ("" if _is_blank(row.get(c)) else str(row.get(c))) for c in _GRID_TEXT_COLS}
        rec["is_uncertain"] = _as_bool(row.get("is_uncertain"))
        out.append(rec)
    return out


def _render_screenshot_batch_mode(naming_rules: dict, casing: str, active_model: str):
    """Render the full screenshot OCR/VLM pipeline openly (no expanders hiding the workflow)."""

    if "hypervisor_editor_version" not in st.session_state:
        st.session_state["hypervisor_editor_version"] = 0
    if "hypervisor_cascade_mappings" not in st.session_state:
        st.session_state["hypervisor_cascade_mappings"] = []

    # Always reload fresh rules from disk/SSM on entry so that any Standards-tab
    # edits that triggered a rerun while this tab was inactive are picked up.
    from config.naming_rules import force_reload_naming_rules
    naming_rules = SSM.get_naming_rules(force_reload_naming_rules())

    st.session_state["_hyp_last_naming_rules_ts"] = id(naming_rules)

    st.markdown("#### 1️⃣ Platform & Data Ingestion")

    with st.container(border=True):
        # Platform selector at the top
        current_rules = SSM.get_naming_rules()
        hyp_presets_naming = current_rules.get("hypervisor_presets") or {}
        parsing_presets_naming = current_rules.get("topology_parsing_presets") or {}

        hyp_keys = list(hyp_presets_naming.keys()) if isinstance(hyp_presets_naming, dict) else []

        if isinstance(parsing_presets_naming, dict):
            parsing_keys = list(parsing_presets_naming.keys())
        elif isinstance(parsing_presets_naming, list):
            parsing_keys = [
                item.get("platform") or item.get("name")
                for item in parsing_presets_naming
                if isinstance(item, dict) and (item.get("platform") or item.get("name"))
            ]
        else:
            parsing_keys = []

        existing_set = set(hyp_keys + parsing_keys)

        saved_order = current_rules.get("hypervisor_platform_order") or []
        ordered_plats = [p for p in saved_order if p in parsing_keys]
        for p in parsing_keys:
            if p not in ordered_plats:
                ordered_plats.append(p)

        naming_platform_options = ordered_plats if ordered_plats else parsing_keys[:1]
        if not naming_platform_options:
            st.info("No platforms configured. Add one in Standards.")
        else:
            default_idx = 0
            cur_selected_plat = st.session_state.get("ocr_target_platform", naming_platform_options[0])
            if cur_selected_plat in naming_platform_options:
                default_idx = naming_platform_options.index(cur_selected_plat)

            if st.session_state.get("naming_target_platform") not in naming_platform_options:
                st.session_state.pop("naming_target_platform", None)

            col_plat, col_eng = st.columns([5, 5], vertical_alignment="center")
            with col_plat:
                target_platform = st.selectbox(
                    "Target Platform",
                    options=naming_platform_options,
                    index=default_idx,
                    key="naming_target_platform",
                    label_visibility="collapsed"
                )
                st.session_state["ocr_target_platform"] = target_platform

        # Collect text and image modules separately for input rendering
        _selected_preset = parsing_presets_naming.get(target_platform, {}) if isinstance(parsing_presets_naming.get(target_platform), dict) else {}
        _platform_modules = _selected_preset.get("modules")
        if not isinstance(_platform_modules, list):
            _platform_modules = []
        _text_mod_ids = [mid for mid in _platform_modules if mid in PROCESSING_MODULES and PROCESSING_MODULES[mid]["input_kind"] == "text"]
        _img_mod_ids = [mid for mid in _platform_modules if mid in PROCESSING_MODULES and PROCESSING_MODULES[mid]["input_kind"] == "image"]
        _unknown_mod_ids = [mid for mid in _platform_modules if mid not in PROCESSING_MODULES]

        with col_eng:
            if _img_mod_ids:
                st.markdown("**⚡ Instant Local OCR (Deterministic Python Engine)**")

        _kind_owners: Dict[str, str] = {}
        for _mid in _platform_modules:
            if _mid not in PROCESSING_MODULES:
                continue
            _kind = PROCESSING_MODULES[_mid]["input_kind"]
            if _kind in _kind_owners:
                st.caption(f"⚠️ Duplicate input_kind '{_kind}': both '{_kind_owners[_kind]}' and '{_mid}' are configured.")
            else:
                _kind_owners[_kind] = _mid

        for _unk_mid in _unknown_mod_ids:
            st.caption(f"⚠️ Unknown module '{_unk_mid}' — skipping.")

        _screenshot_title = "Screenshot Guidelines"
        _screenshot_md = ""

        if _text_mod_ids:
            _text_parser_id = _text_mod_ids[0]
            _tp_meta = PROCESSING_MODULES[_text_parser_id].get("metadata") or {}
            _paste_label = _tp_meta.get("paste_label", "Pasted Text")
            _paste_help = _tp_meta.get("paste_help", "")
            _export_js = _tp_meta.get("export_instructions", "")
            with st.expander(f"📋 Paste {_paste_label} (optional, recommended — no OCR)", expanded=True):
                if _paste_help:
                    st.caption(_paste_help)
                if _export_js:
                    st.code(_export_js, language="javascript")

                paste_text_key = f"vsphere_paste_text_{st.session_state.get('paste_text_ver', 0)}"
                _lines = len((st.session_state.get(paste_text_key) or '').strip().splitlines())
                with st.expander(f"Raw text ({_lines} lines)", expanded=False):
                    pasted_vsphere_text = st.text_area(
                        _paste_label,
                        height=180,
                        key=paste_text_key,
                        placeholder=f"Paste the copied {_paste_label.lower()} here...",
                        label_visibility="collapsed"
                    )

                _pv = preview_topology_text(_text_parser_id, pasted_vsphere_text)
                if _pv is not None:
                    _sw_list = _pv.get("switches", [])
                    _n_sw = len(_sw_list)
                    _n_up = sum(s["uplinks"] for s in _sw_list)
                    _n_svc = sum(s["services"] for s in _sw_list)
                    _n_empty = sum(1 for s in _sw_list if s["status"] == "empty")
                    st.caption(f"{_n_sw} switches · {_n_up} uplinks · {_n_svc} services · {_n_empty} empty")
                    for _s in _sw_list:
                        _icon = {"ok": "✅", "partial": "🟡", "empty": "⛔"}[_s["status"]]
                        st.caption(f"{_icon} {_s['name']}")
                    _seen_per_sw: Dict[str, str] = {}
                    for _w in _pv.get("warnings", []):
                        _sw_match = None
                        for _s in _sw_list:
                            if _s["name"] in _w:
                                _sw_match = _s["name"]
                                break
                        if _sw_match is not None:
                            if _sw_match not in _seen_per_sw:
                                _seen_per_sw[_sw_match] = _w
                                st.caption(f"⚠️ {_w}")
                        else:
                            if _w not in _seen_per_sw.values():
                                _seen_per_sw[_w] = _w
                                st.caption(f"⚠️ {_w}")
                    for _g in _pv.get("gaps", []):
                        _gs = _g.get("switch")
                        _key = _gs or _g.get("message", "")
                        if _key and _key not in _seen_per_sw:
                            _seen_per_sw[_key] = _g.get("message", "")
                            if not _g.get("ocr_can_help"):
                                st.caption(f"⛔ {_g.get('message', '')}")
                            elif not st.session_state.get("staged_topology_imgs") and _g.get("kind") in ("vmk_missing", "nic_missing"):
                                st.caption(f"⛔ {_g.get('message', '')} — upload a screenshot of that switch, then click again")
                    _merge_report = st.session_state.get("paste_merge_report", None)
                    _paste_mode = st.session_state.get("paste_mode", "")
                    if _merge_report:
                        _filled = _merge_report.get("filled", [])
                        if _filled:
                            for _f in _filled:
                                st.caption(f"🟡 {_f}")
                        _conflicts = _merge_report.get("conflicts", [])
                        if _conflicts:
                            for _c in _conflicts:
                                st.caption(f"⚠️ {_c}")
                        _suggestions = _merge_report.get("suggestions", [])
                        if _suggestions:
                            for _s in _suggestions:
                                st.caption(f"💡 {_s}")
                    if _paste_mode:
                        _mode_labels = {"text": "Source: text only", "text+ocr": "Source: text + OCR", "ocr": "Source: OCR only", "none": ""}
                        _mlabel = _mode_labels.get(_paste_mode, _paste_mode)
                        if _mlabel:
                            st.caption(_mlabel)
        elif not _text_mod_ids and not _img_mod_ids:
            st.caption("This platform has no processing modules configured. Add one in Standards.")

        for _mid in (_img_mod_ids + _text_mod_ids):
            _m = PROCESSING_MODULES[_mid].get("metadata") or {}
            if _m.get("screenshot_guidance"):
                _screenshot_title = _m.get("screenshot_guidance_title", _screenshot_title)
                _screenshot_md = _m.get("screenshot_guidance", "")
                break

        if _img_mod_ids:
            # Screenshot guidelines (moved from text slot per 4-3.1)
            if _screenshot_md:
                with st.expander(f"📸 {_screenshot_title}", expanded=False):
                    st.markdown(_screenshot_md)
            
            col_up1, col_up2 = st.columns([1, 1])

            with col_up1:
                if "topo_uploader_key_ver" not in st.session_state:
                    st.session_state["topo_uploader_key_ver"] = 0
                uploader_key = f"batch_mode_screenshot_uploader_{st.session_state['topo_uploader_key_ver']}"
                raw_uploaded = st.file_uploader(
                    "Upload Screenshots (Drag & Drop)",
                    type=["png", "jpg", "jpeg"],
                    accept_multiple_files=True,
                    key=uploader_key,
                    help="Upload one or multiple screenshots...",
                )
                if "staged_topology_imgs" not in st.session_state:
                    st.session_state["staged_topology_imgs"] = []

                if raw_uploaded:
                    import io as _io
                    existing_names = {(getattr(f, "name", ""), getattr(f, "size", 0)) for f in st.session_state["staged_topology_imgs"]}
                    new_files_added = False
                    for uf in raw_uploaded:
                        if (uf.name, uf.size) not in existing_names:
                            file_bytes = uf.read()
                            bio = _io.BytesIO(file_bytes)
                            bio.name = uf.name
                            bio.type = uf.type
                            bio.size = len(file_bytes)
                            st.session_state["staged_topology_imgs"].append(bio)
                            new_files_added = True
                    if new_files_added:
                        st.session_state["topo_uploader_key_ver"] = st.session_state.get("topo_uploader_key_ver", 0) + 1
                        st.rerun()

            with col_up2:
                st.markdown("**📋 Or Paste Screenshot from Clipboard (Ctrl+V)**")
                if "paste_input_ver" not in st.session_state:
                    st.session_state["paste_input_ver"] = 0
                paste_box_key = f"esxi_paste_input_{st.session_state.get('paste_input_ver', 0)}"
                def _paste_input_cb():
                    _val = st.session_state.get(paste_box_key, "")
                    if not _val:
                        return
                    if _val.startswith("data:image"):
                        import base64 as _b64, io as _io
                        try:
                            _, encoded = _val.split(",", 1)
                            img_bytes = _b64.b64decode(encoded)
                            pasted_file = _io.BytesIO(img_bytes)
                            idx = len(st.session_state["staged_topology_imgs"]) + 1
                            pasted_file.name = f"clipboard_screenshot_{idx}.png"
                            pasted_file.type = "image/png"
                            pasted_file.size = len(img_bytes)
                            st.session_state["staged_topology_imgs"].append(pasted_file)
                            st.session_state["paste_input_ver"] = st.session_state.get("paste_input_ver", 0) + 1
                        except Exception as e:
                            st.warning(f"Failed to process pasted image: {e}")
                pasted_data = st.text_input(
                    "Paste screenshot (Ctrl+V)",
                    placeholder="Click here and press Ctrl+V",
                    key=paste_box_key,
                    label_visibility="visible",
                    help="Focus this box and press Ctrl+V.",
                    on_change=_paste_input_cb,
                )
                st.html(
                    """
                    <script>
                    const parentDoc = window.parent.document;
                    if (!window.parent._esxiPasteDelegated) {
                        window.parent._esxiPasteDelegated = true;
                        parentDoc.addEventListener('paste', function(e) {
                            const target = e.target;
                            if (!target || target.getAttribute('aria-label') !== 'Paste screenshot (Ctrl+V)') return;
                            const items = (e.clipboardData || window.clipboardData).items;
                            for (let i = 0; i < items.length; i++) {
                                if (items[i].type.indexOf('image') !== -1) {
                                    const blob = items[i].getAsFile();
                                    const reader = new FileReader();
                                    reader.onload = function(event) {
                                        const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                                        nativeSetter.call(target, event.target.result);
                                        target.dispatchEvent(new Event('input', { bubbles: true }));
                                        target.dispatchEvent(new KeyboardEvent('keydown', {
                                            bubbles: true,
                                            cancelable: true,
                                            key: 'Enter',
                                            code: 'Enter',
                                            keyCode: 13,
                                            which: 13
                                        }));
                                        target.dispatchEvent(new Event('change', { bubbles: true }));
                                    };
                                    reader.readAsDataURL(blob);
                                    e.preventDefault();
                                    break;
                                }
                            }
                        });
                    }
                    </script>
                    """,
                    unsafe_allow_javascript=True,
                )

        # Ingest clipboard paste inside Step 1 (kept for backward compat; new logic uses on_change)

        uploaded_imgs = st.session_state.get("staged_topology_imgs", [])

        if uploaded_imgs:
            with st.expander(f"🔍 Preview Staged Screenshots ({len(uploaded_imgs)} file(s))", expanded=True):
                preview_cols = st.columns(min(len(uploaded_imgs), 4))
                del_idx = None
                for img_idx, img_item in enumerate(uploaded_imgs):
                    with preview_cols[img_idx % len(preview_cols)]:
                        img_title = getattr(img_item, "name", f"Screenshot #{img_idx + 1}")
                        st.caption(f"#{img_idx + 1}: {img_title}")
                        st.image(img_item, width="stretch")
                        if st.button("✖ Remove", key=f"unified_remove_btn_{img_idx}"):
                            del_idx = img_idx
                if del_idx is not None and 0 <= del_idx < len(st.session_state["staged_topology_imgs"]):
                    st.session_state["staged_topology_imgs"].pop(del_idx)
                    st.session_state["topo_uploader_key_ver"] = st.session_state.get("topo_uploader_key_ver", 0) + 1
                    if not st.session_state["staged_topology_imgs"]:
                        st.session_state.pop("hypervisor_parsed_descriptions", None)
                        st.session_state.pop("latest_ocr_raw_text", None)
                        st.session_state.pop("latest_vault_session", None)
                        st.session_state.pop("hypervisor_cascade_mappings", None)
                    st.rerun()

        # Action Bar (Analyze & Clear)
        c_an, c_clr = st.columns([3, 1], vertical_alignment="center")
        with c_an:
            st.button("🚀 Analyze & Auto-Populate", type="primary", width="stretch", on_click=_handle_topology_analyze, disabled=(not _text_mod_ids and not _img_mod_ids))
        with c_clr:
            st.button("🗑️ Clear All", key="btn_clear_topology_all", width="stretch", on_click=_handle_clear_topology_all)

    # Raw Text / Warnings Inspector (collapsible)
    staged_imgs = st.session_state.get("staged_topology_imgs", [])
    raw_ocr_text = st.session_state.get("latest_ocr_raw_text", "")
    esxi_analysis = st.session_state.get("esxi_analysis", {})
    _ana_source = esxi_analysis.get("source", "")
    _ana_warns = esxi_analysis.get("warnings", [])
    _ana_gaps = esxi_analysis.get("gaps", [])
    _ana_report = esxi_analysis.get("report")
    with st.expander("📄 Raw Text / Warnings Inspector (Click to expand)", expanded=False):
        char_count = len(raw_ocr_text) if raw_ocr_text else 0
        img_count = len(staged_imgs)
        st.caption(f"ℹ️ Extracted {char_count:,} characters across {img_count} screenshot(s)")
        if _ana_source:
            st.caption(f"**Source:** {_ana_source}")
        if _ana_warns:
            for _w in _ana_warns:
                st.caption(f"⚠️ {_w}")
        if _ana_gaps:
            for _g in _ana_gaps:
                if not _g.get("ocr_can_help"):
                    st.caption(f"⛔ {_g.get('message', '')} (expand it in vSphere and copy again)")
                else:
                    st.caption(f"🟡 {_g.get('message', '')}")
        if _ana_report:
            _filled = _ana_report.get("filled", [])
            if _filled:
                for _f in _filled:
                    st.caption(f"🟡 {_f}")
            _conflicts = _ana_report.get("conflicts", [])
            if _conflicts:
                for _c in _conflicts:
                    st.caption(f"⚠️ {_c}")
            _unresolved = _ana_report.get("unresolved", [])
            if _unresolved:
                for _u in _unresolved:
                    st.caption(f"⛔ {_u}")
            _suggestions = _ana_report.get("suggestions", [])
            if _suggestions:
                for _s in _suggestions:
                    st.caption(f"💡 {_s}")
            _unmatched = _ana_report.get("unmatched_switches", [])
            if _unmatched:
                for _u in _unmatched:
                    st.caption(f"⚠️ Unmatched OCR switch: {_u}")
        if raw_ocr_text:
            st.text_area("Extracted OCR Tokens", value=raw_ocr_text, height=420, disabled=True)
        else:
            st.info("No OCR data available. Upload screenshots and click Analyze & Auto-Populate.")

    # Load standards rules for dynamic role options (available to both cards and editor)
    _std_rules = SSM.get_naming_rules() or naming_rules

    if "editor_ver" not in st.session_state:
        st.session_state["editor_ver"] = 0

    records = st.session_state.get("topology_records", [])
    sw_options = st.session_state.get("discovered_switches", [])

    # Dynamic role options from active platform preset
    _tpp_nr = naming_rules.get("topology_parsing_presets") or {}
    _plat_nr_keys = list(_tpp_nr.keys()) if isinstance(_tpp_nr, dict) else []
    target_platform = st.session_state.get("naming_target_platform")
    if target_platform not in _plat_nr_keys:
        target_platform = _plat_nr_keys[0] if _plat_nr_keys else None
    tpp = _tpp_nr
    plat_cfg = tpp.get(target_platform, {}) if isinstance(tpp.get(target_platform), dict) else {}
    dynamic_blacklist: Set[str] = {s.strip().lower() for s in (plat_cfg.get("ui_noise_blacklist") or []) if s and str(s).strip()}
    active_junk_filter: Set[str] = UI_NAV_NOISE.union(dynamic_blacklist)
    _plat_tok = ((naming_rules.get("platform_tokens") or {}).get(target_platform, {})) if isinstance(naming_rules.get("platform_tokens"), dict) else {}
    _dynamic_uplink_pfx = str(_plat_tok.get("uplink_prefix", "vmnic")).strip()
    _dynamic_vmk_pfx = str(_plat_tok.get("vmk_prefix", "vmk")).strip()
    _dynamic_switch_pfx = str(_plat_tok.get("switch_prefix", "vSwitch")).strip()
    role_options = plat_cfg.get("role_options") or []
    default_connected_role = plat_cfg.get("default_connected_role") or (role_options[0] if role_options else "")
    default_disconnected_role = plat_cfg.get("default_disconnected_role") or "None"
    sentinel_no_adapter = plat_cfg.get("sentinel_no_adapter") or ""

    # Clean Empty State
    if not sw_options and not records:
        if not _text_mod_ids and not _img_mod_ids:
            st.info("Add a processing module in Standards to enable input for this platform.")
        else:
            st.info("ℹ️ No virtual switches discovered yet. Upload and analyze screenshots above to auto-populate topology.")
    else:
        # ── Interactive Cards Overview ──
        st.markdown("#### 2️⃣ Topology Review & Visual Editing")
        st.caption("Review extracted switch containers, assign uplinks/services, and verify parameters in the grid.")
        st.markdown("##### 🔹 Topology Visual Overview & Card Controls")
        cols = st.columns(min(len(sw_options), 4) if sw_options else 1)

        for idx, sw in enumerate(sw_options):
            with cols[idx % len(cols)]:
                with st.container(border=True):
                    c_name, c_del = st.columns([4, 1])
                    with c_name:
                        new_sw_val = st.text_input(
                            "Switch Name",
                            value=sw,
                            key=f"card_rename_input_{idx}_{sw}",
                            label_visibility="collapsed"
                        )
                        if new_sw_val.strip() and new_sw_val.strip() != sw:
                            for r in records:
                                if r.get("switch") == sw:
                                    r["switch"] = new_sw_val.strip()
                            sw_options[idx] = new_sw_val.strip()
                            st.session_state["topology_records"] = records
                            st.session_state["discovered_switches"] = sw_options
                            st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                            st.rerun()
                    with c_del:
                        if st.button("✖", key=f"btn_del_sw_{idx}_{sw}", help=f"Delete switch {sw}"):
                            records = [r for r in records if r.get("switch") != sw]
                            sw_options = [s for s in sw_options if s != sw]
                            st.session_state["topology_records"] = records
                            st.session_state["discovered_switches"] = sw_options
                            st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                            st.rerun()

                    assigned_uplink_indices = [i for i, r in enumerate(records) if r.get("switch") == sw and str(r.get("type", "")).strip().lower() == "uplink" and r.get("interface")]
                    assigned_service_indices = [i for i, r in enumerate(records) if r.get("switch") == sw and str(r.get("type", "")).strip().lower() != "uplink" and (r.get("purpose") or r.get("interface"))]

                    st.caption(f"Uplinks ({len(assigned_uplink_indices)}):")
                    if assigned_uplink_indices:
                        for u_idx in assigned_uplink_indices:
                            u_row = records[u_idx]
                            u_iface = u_row.get("interface", "").strip()
                            if not u_iface or _is_junk_token(u_iface, active_junk_filter) or re.search(r"\b\d+\s*(?:Gbit/s|Mbit/s|Kbit/s|Full|Half)\b", u_iface, re.I):
                                continue
                            u_role = u_row.get("role") or default_connected_role
                            _uncertain_prefix = "🟡 " if str(u_row.get("is_uncertain", False)).lower() in ("true", "1", "yes") else ""

                            uc_label, uc_role, uc_del = st.columns([3, 3, 1])
                            with uc_label:
                                 new_u_iface = st.text_input(
                                     "Uplink Name",
                                     value=f"{_uncertain_prefix}{u_iface}",
                                     key=f"card_up_edit_{idx}_{u_idx}_{u_iface}",
                                     label_visibility="collapsed"
                                 ).strip()
                                 # Strip the prefix before saving
                                 display_val = new_u_iface
                                 if display_val.startswith("🟡 "):
                                     display_val = display_val[2:]
                                 if display_val and display_val != u_iface:
                                     records[u_idx]["interface"] = display_val
                                     st.session_state["topology_records"] = records
                                     st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                     st.rerun()
                            with uc_role:
                                current_role = u_role
                                sel_idx = role_options.index(current_role) if current_role in role_options else 0
                                sel_role = st.selectbox(
                                    "Role",
                                    options=role_options,
                                    index=sel_idx,
                                    key=f"card_up_role_{u_idx}_{u_iface}",
                                    label_visibility="collapsed"
                                )
                                if sel_role != u_role:
                                    records[u_idx]["role"] = sel_role
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()
                            with uc_del:
                                if st.button("✖", key=f"btn_del_up_{u_idx}", help="Remove uplink"):
                                    records.pop(u_idx)
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()
                    else:
                        st.caption("No uplinks")

                    st.caption(f"Services ({len(assigned_service_indices)}):")
                    if assigned_service_indices:
                        for s_idx in assigned_service_indices:
                            s_row = records[s_idx]
                            s_iface = str(s_row.get("interface", "")).strip()
                            s_purpose = str(s_row.get("purpose", "")).strip()
                            _s_ip = str(s_row.get("ip") or "").strip()
                            if not s_purpose and not s_iface and not _s_ip:
                                continue
                            # VMkernel: display "purpose (vmk)" pairing with IP
                            is_vmkernel = s_row.get("type") == "VMkernel" and s_iface
                            edit_target_val = s_purpose if s_purpose else s_iface
                            _s_uncertain = str(s_row.get("is_uncertain", False)).lower() in ("true", "1", "yes")
                            _uncertain_prefix = "🟡 " if _s_uncertain else ""
                            sc_label, sc_del = st.columns([5, 1])
                            with sc_label:
                                _s_vlan = str(s_row.get("vlan", "")).strip()
                                if is_vmkernel:
                                    parts = [f"◆ {_uncertain_prefix}{s_iface}"]
                                    if _s_ip:
                                        parts.append(_s_ip)
                                    if _s_vlan:
                                        parts.append(f"VLAN {_s_vlan}")
                                    st.caption(" • ".join(parts))
                                else:
                                    if _s_vlan:
                                        st.caption(f"◆ {_uncertain_prefix}VLAN {_s_vlan}")
                                    elif _s_ip:
                                        st.caption(f"◆ {_uncertain_prefix}IP: {_s_ip}")
                                display_svc = f"{_uncertain_prefix}{edit_target_val}"
                                new_s_val = st.text_input(
                                    "Service Name",
                                    value=display_svc,
                                    key=f"card_svc_edit_{idx}_{s_idx}_{edit_target_val}",
                                    label_visibility="collapsed"
                                ).strip()
                                # Strip prefix before comparing/saving
                                clean_new = new_s_val.lstrip("🟡 ").lstrip()
                                if clean_new and clean_new != edit_target_val:
                                    if s_purpose:
                                        records[s_idx]["purpose"] = clean_new
                                    else:
                                        records[s_idx]["interface"] = clean_new
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()
                            with sc_del:
                                if st.button("✖", key=f"btn_del_svc_{s_idx}", help="Remove service"):
                                    records.pop(s_idx)
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()
                    else:
                        st.caption("No services")

                    st.divider()

                    btn_col1, btn_col2 = st.columns(2)
                    uplink_candidates = st.session_state.get("discovered_uplink_candidates", [])
                    service_candidates = st.session_state.get("discovered_service_candidates", [])

                    with btn_col1:
                        with st.popover("➕ Uplink", width="stretch"):
                            st.markdown("**Add Physical Uplink**")
                            _tpp_upk = naming_rules.get("topology_parsing_presets") or {}
                            _upk_keys = list(_tpp_upk.keys()) if isinstance(_tpp_upk, dict) else []
                            _upk_sel = st.session_state.get("naming_target_platform")
                            if _upk_sel not in _upk_keys:
                                _upk_sel = _upk_keys[0] if _upk_keys else None
                            _plat_teaming_cfg = _tpp_upk.get(_upk_sel, {}) if isinstance(_tpp_upk, dict) else {}
                            _no_uplink_display = str(plat_cfg.get("no_uplink_label") or ((_plat_teaming_cfg.get("teaming_rules") or {}).get("no_uplink_display") or "")).strip()
                            up_choice_options = ["➕ Custom / Manual Entry", _no_uplink_display] + [c for c in uplink_candidates if not any(r.get("interface") == c and r.get("switch") == sw for r in records)]
                            selected_up_choice = st.selectbox(
                                "Select Detected Adapter or Enter New",
                                options=up_choice_options,
                                key=f"pop_up_sel_{idx}_{sw}"
                            )
                            _up_choice_slug = re.sub(r"[^a-zA-Z0-9_]", "", selected_up_choice)
                            if selected_up_choice == "➕ Custom / Manual Entry":
                                _init_up_name = ""
                            else:
                                _init_up_name = selected_up_choice.strip()
                            final_up_name = st.text_input(
                                "✏️ Adapter Name (Editable)",
                                value=_init_up_name,
                                placeholder=f"e.g. {_dynamic_uplink_pfx}0, {_dynamic_uplink_pfx}4",
                                key=f"pop_up_name_{idx}_{sw}_{_up_choice_slug}"
                            ).strip()

                            new_up_role = st.selectbox("Initial Role", options=role_options, index=0, key=f"pop_up_role_{idx}_{sw}")
                            new_up_speed = st.selectbox("Speed (optional)", options=plat_cfg.get("speed_options") or [], index=0, key=f"pop_up_spd_{idx}_{sw}")

                            if st.button("Add to Switch", key=f"pop_up_confirm_{idx}_{sw}", width="stretch"):
                                _is_no_uplink = (selected_up_choice == _no_uplink_display) or (not final_up_name and new_up_role.lower() == "none")
                                if _is_no_uplink:
                                    records.append({
                                        "switch": sw,
                                        "interface": _no_uplink_display,
                                        "type": "Uplink",
                                        "ip": "",
                                        "purpose": "",
                                        "role": "None",
                                        "speed": "",
                                        "vlan": ""
                                    })
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()
                                elif not final_up_name or not final_up_name.strip():
                                    st.warning("Please enter an adapter name. If switch has no adapters, no uplink is needed.")
                                else:
                                    records.append({
                                        "switch": sw,
                                        "interface": final_up_name,
                                        "type": "Uplink",
                                        "ip": "",
                                        "purpose": "",
                                        "role": new_up_role,
                                        "speed": new_up_speed,
                                        "vlan": ""
                                    })
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()

                    with btn_col2:
                        with st.popover("➕ Service", width="stretch"):
                            st.markdown("**Add Port Group / VMkernel**")
                            _disc_svc_map = {r["purpose"] or r["interface"]: r for r in st.session_state.get("topology_records", []) if r.get("purpose") or r.get("interface")}
                            if not isinstance(_disc_svc_map, dict):
                                _disc_svc_map = {}
                            _ip_candidates = [r["ip"] for r in st.session_state.get("topology_records", []) if r.get("ip")]

                            # Format dropdown labels so user sees e.g. "Management Network (vmk0 - 10.27.177.246)" or "VM Network"
                            svc_choice_options = ["➕ Custom / Manual Entry"] + [
                                c for c in service_candidates
                                if not any((r.get("purpose") == c or r.get("interface") == c) and r.get("switch") == sw for r in records)
                            ]
                            selected_svc_choice = st.selectbox(
                                "Select Detected Service / Port Group",
                                options=svc_choice_options,
                                key=f"pop_svc_sel_{idx}_{sw}"
                            )
                            _svc_choice_slug = re.sub(r"[^a-zA-Z0-9_]", "", selected_svc_choice)

                            if selected_svc_choice == "➕ Custom / Manual Entry":
                                _autofill = {}
                                _init_name = ""
                                _init_ip = ""
                                _is_vm = False
                            else:
                                _autofill = _disc_svc_map.get(selected_svc_choice) or {}
                                if not isinstance(_autofill, dict):
                                    _autofill = {}
                                _init_name = str(_autofill.get("name") or selected_svc_choice).strip()
                                _init_ip = str(_autofill.get("ip") or "").strip()
                                _is_vm = (
                                    str(_autofill.get("type") or "").lower() == "vmkernel"
                                    or selected_svc_choice.lower().startswith(_dynamic_vmk_pfx.lower())
                                    or bool(_init_ip)
                                )

                            final_svc_name = st.text_input(
                                "✏️ Service / Port Group Name (Editable)",
                                value=_init_name,
                                placeholder=f"e.g. Management Network, VM Network, iSCSI",
                                key=f"pop_svc_name_{idx}_{sw}_{_svc_choice_slug}"
                            ).strip()

                            new_svc_type = st.selectbox(
                                "Type",
                                options=["Port Group", "VMkernel"],
                                index=1 if _is_vm else 0,
                                key=f"pop_svc_type_{idx}_{sw}_{_svc_choice_slug}"
                            )

                            # Selectable IP Address Dropdown (with OCR-discovered IPs + Custom option)
                            _ip_options = ["None (No IP)"] + _ip_candidates + ["➕ Custom / Manual IP"]
                            _default_ip_idx = _ip_options.index(_init_ip) if (_init_ip and _init_ip in _ip_options) else 0
                            selected_ip_choice = st.selectbox(
                                "IP Address (Select Detected IP or Custom)",
                                options=_ip_options,
                                index=_default_ip_idx,
                                key=f"pop_svc_ip_sel_{idx}_{sw}_{_svc_choice_slug}"
                            )
                            if selected_ip_choice == "➕ Custom / Manual IP":
                                new_svc_ip = st.text_input(
                                    "Enter Custom IP Address",
                                    value="",
                                    placeholder="e.g. 10.27.177.246",
                                    key=f"pop_svc_ip_custom_{idx}_{sw}_{_svc_choice_slug}"
                                ).strip()
                            elif selected_ip_choice == "None (No IP)":
                                new_svc_ip = ""
                            else:
                                new_svc_ip = selected_ip_choice.strip()

                            if st.button("Add to Switch", key=f"pop_svc_confirm_{idx}_{sw}", width="stretch"):
                                 effective_name = final_svc_name or (selected_svc_choice if selected_svc_choice != "➕ Custom / Manual Entry" else "")
                                 if not effective_name:
                                     st.warning("Please select or enter a Service / Port Group name.")
                                 else:
                                     _matched_iface = str(_autofill.get("interface") or "").strip()
                                     if new_svc_type == "VMkernel" and not _matched_iface and effective_name.lower().startswith(_dynamic_vmk_pfx.lower()):
                                         _matched_iface = effective_name
                                     records.append({
                                         "switch": sw,
                                         "interface": _matched_iface if new_svc_type == "VMkernel" else "",
                                         "type": new_svc_type,
                                         "ip": new_svc_ip,
                                         "purpose": effective_name,
                                         "vlan": str(_autofill.get("vlan", "")).strip(),
                                         "role": default_connected_role if new_svc_type == "VMkernel" and not _matched_iface else "None",
                                         "speed": ""
                                     })
                                     # Sync corrected name back to session state so it replaces the old spelling
                                     if selected_svc_choice != "➕ Custom / Manual Entry" and final_svc_name != selected_svc_choice:
                                         _existing_svcs = list(st.session_state.get("discovered_service_candidates") or [])
                                         if selected_svc_choice in _existing_svcs:
                                             _existing_svcs.remove(selected_svc_choice)
                                         if final_svc_name not in _existing_svcs:
                                             _existing_svcs.append(final_svc_name)
                                         st.session_state["discovered_service_candidates"] = sorted(_existing_svcs, key=_natsort_key)
                                     st.session_state["topology_records"] = records
                                     st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                     st.rerun()

    st.markdown("##### \U0001F39B\uFE0F Topology Data Grid")
    st.caption("Edit switch assignments, interface roles, and IPs directly. Changes update NetBox descriptions instantly.")
    df_for_editor = _records_to_grid_df(records)
    column_config = {
        "switch": st.column_config.SelectboxColumn("Switch Container", options=sw_options if sw_options else [_dynamic_switch_pfx + "0"], required=False),
        "interface": st.column_config.TextColumn("Interface / Port", required=False),
        "type": st.column_config.SelectboxColumn("Type", options=["Uplink", "VMkernel", "Port Group"], required=False),
        "ip": st.column_config.TextColumn("IPv4 Address", required=False),
        "purpose": st.column_config.TextColumn("Service / Purpose", required=False),
        "vlan": st.column_config.TextColumn("VLAN", required=False),
        "role": st.column_config.SelectboxColumn("Role", options=role_options, required=False),
        "speed": st.column_config.TextColumn("Link Speed", required=False),
        "source": st.column_config.TextColumn("Source", required=False),
        "is_uncertain": st.column_config.CheckboxColumn(
            "Uncertain",
            help="Ticked = value came from OCR or a filled gap and is not verified yet. Untick it after you have checked it.",
            default=False,
        ),
        "note": st.column_config.TextColumn("Note", required=False),
        "_check": st.column_config.TextColumn("Check", disabled=True),
    }
    editor_key = f"topology_data_editor_{st.session_state.get('editor_ver', 0)}"
    edited_df = st.data_editor(
        df_for_editor,
        column_config=column_config,
        num_rows="dynamic",
        width="stretch",
        key=editor_key,
    )
    topology_records = _grid_df_to_records(edited_df)
    st.session_state["topology_records"] = topology_records

    # ── Zone 3: Live NetBox Output ────────────────────────────────────────────
    st.divider()
    st.markdown("#### 3️⃣ Live NetBox Output & Copy")

    _output_bulk_text = "# No parsed topology records. Upload screenshots and click Analyze to generate descriptions.\n"

    topology_records = st.session_state.get("topology_records", [])
    _discovered_switches_list = st.session_state.get("discovered_switches", [])
    if topology_records or _discovered_switches_list:
        _groups: Dict[str, List[dict]] = {}
        for _sw_name in _discovered_switches_list:
            if _sw_name:
                _groups[_sw_name] = []
        for _tr in topology_records:
            _gparent = str(_tr.get("switch") or "").strip() or "General"
            _groups.setdefault(_gparent, []).append(_tr)

        # Load standards for pattern/template rendering
        _std_rules = SSM.get_naming_rules() or naming_rules
        _naming_pats = get_naming_patterns(_std_rules)
        _pattern_vars = get_pattern_variables(_std_rules)
        _hyp_presets_map = _std_rules.get("hypervisor_presets") or {}
        _tpp_std = _std_rules.get("topology_parsing_presets") or {}
        _plat_std_keys = list(_tpp_std.keys()) if isinstance(_tpp_std, dict) else []
        _plat_key = st.session_state.get("naming_target_platform")
        if _plat_key not in _plat_std_keys:
            _plat_key = _plat_std_keys[0] if _plat_std_keys else None
        _active_hyp_presets = _hyp_presets_map.get(_plat_key, [])

        if not _active_hyp_presets:
            st.error(
                f"❌ Missing template definitions for platform '{_plat_key}' in Standards Tab "
                f"(Hypervisor > Description Templates). Please configure templates there before generating output."
            )
            st.stop()

        _required_codes = {"Switch_Teaming", "Uplink", "PortGroup", "VMkernel"}
        _available_codes = {
            p.get("code", "").strip()
            for p in _active_hyp_presets
            if not p.get("hidden", False)
        }
        _missing_codes = _required_codes - _available_codes
        if _missing_codes:
            st.error(
                f"❌ Missing required template codes for platform '{_plat_key}': "
                f"{', '.join(sorted(_missing_codes))}. "
                f"Configure all four templates (Switch_Teaming, Uplink, PortGroup, VMkernel) "
                f"in the Standards Tab under Hypervisor > Description Templates, then retry."
            )
            st.stop()

        _out_lines = [f"# NOTE:\n# Generated from NetBox Hub v{APP_VERSION} (Deterministic Engine).\n"]
        for _gparent in sorted(_groups.keys(), key=_natsort_key):
            _out_lines.append(f"=== {_gparent} ===")
            _sw_uplinks = [r for r in _groups[_gparent] if r.get("type") == "Uplink" and r.get("interface")]
            _sw_services = [r for r in _groups[_gparent] if r.get("type") != "Uplink" and (r.get("interface") or r.get("purpose"))]
            _sw_vmks = [r for r in _groups[_gparent] if r.get("type") == "VMkernel"]

            # Dynamic teaming clause
            teaming_str = _build_dynamic_teaming_clause(_sw_uplinks)

            # ── Switch Teaming Header (rendered ONCE per switch) ──────────────
            _switch_teaming_tpl = None
            for _ptpl in _active_hyp_presets:
                if _ptpl.get("code") == "Switch_Teaming" and not _ptpl.get("hidden", False):
                    _switch_teaming_tpl = _ptpl.get("pattern", "")
                    break
            _sw_vals = {"v_switch": _gparent, "teaming": teaming_str}
            _rendered_header = render_dynamic_pattern(_switch_teaming_tpl, _sw_vals, _pattern_vars)
            _rendered_header = re.sub(r"\s{2,}", " ", _rendered_header).strip()
            if _rendered_header:
                _out_lines.append(_rendered_header)

            # ── Physical Uplinks (rendered per physical adapter) ───────────────
            _uplink_tpl = None
            for _upt in _active_hyp_presets:
                if _upt.get("code") == "Uplink" and not _upt.get("hidden", False):
                    _uplink_tpl = _upt.get("pattern", "")
                    break

            _valid_uplinks = [u for u in _sw_uplinks if str(u.get("interface", "")).strip() not in ("", "No Uplink")]
            for _ug in _valid_uplinks:
                _ui = str(_ug.get("interface", "")).strip()
                if not _ui:
                    continue
                _upurpose = str(_ug.get("purpose", "")).strip()
                _urole = str(_ug.get("role", "")).strip()
                _uplink_row = {
                    "interface": _ui,
                    "vmnic": _ui,
                    "v_switch": _gparent,
                    "purpose": _upurpose,
                    "uplink_role": _urole,
                }
                _upline = render_dynamic_pattern(_uplink_tpl, _uplink_row, _pattern_vars)
                _upline = re.sub(r"\s{2,}", " ", _upline).strip()
                _unresolved = re.findall(r"<([a-zA-Z0-9_]+)>", _upline)
                if _unresolved:
                    st.error(
                        f"❌ Unassigned variables in Uplink template for '{_gparent}': "
                        f"{', '.join(_unresolved)}. Template: `{_uplink_tpl}`. "
                        f"Provide values for these tokens in the topology DataGrid."
                    )
                    st.stop()
                if _upline:
                    _out_lines.append(_upline)

            # ── Port Groups & VMkernel (rendered per service, no teaming dup) ─
            _seen_svc_keys: Set[tuple] = set()
            for _svc in _sw_services:
                _svc_name = str(_svc.get("purpose", "")).strip() or str(_svc.get("interface", "")).strip()
                if not _svc_name:
                    continue
                _svc_type = str(_svc.get("type", "Port Group")).strip()
                _svc_iface = str(_svc.get("interface", "")).strip()
                _dedup_key = (_svc_type, _svc_iface, _svc_name)
                if _dedup_key in _seen_svc_keys:
                    continue
                _seen_svc_keys.add(_dedup_key)

                if _svc_type == "VMkernel":
                    _svc_tpl = None
                    for _stpl in _active_hyp_presets:
                        if _stpl.get("code") == "VMkernel" and not _stpl.get("hidden", False):
                            _svc_tpl = _stpl.get("pattern", "")
                            break
                else:
                    _svc_tpl = None
                    for _ptpl in _active_hyp_presets:
                        if _ptpl.get("code") == "PortGroup" and not _ptpl.get("hidden", False):
                            _svc_tpl = _ptpl.get("pattern", "")
                            break

                _svc_vals = {
                    "purpose": _svc_name,
                    "port_group": _svc_name,
                    "v_switch": _gparent,
                    "interface": _svc_iface,
                    "vlan": str(_svc.get("vlan", "")).strip(),
                    "ip": str(_svc.get("ip") or "").strip(),
                }
                _rendered_svc = render_dynamic_pattern(_svc_tpl, _svc_vals, _pattern_vars)
                _rendered_svc = re.sub(r"\s{2,}", " ", _rendered_svc).strip()
                _unresolved_svc = re.findall(r"<([a-zA-Z0-9_]+)>", _rendered_svc)
                if _unresolved_svc:
                    st.error(
                        f"❌ Unassigned variables in {_svc_type} template for '{_gparent}/{_svc_name}': "
                        f"{', '.join(_unresolved_svc)}. Template: `{_svc_tpl}`. "
                        f"Provide values for these tokens in the topology DataGrid."
                    )
                    st.stop()
                if _rendered_svc:
                    _out_lines.append(_rendered_svc)

        _output_bulk_text = "\n".join(_out_lines).strip()

    st.markdown("**NetBox Description Output**")
    st.code(_output_bulk_text, language="text")

    st.download_button(
        "📥 Download (.txt)",
        _output_bulk_text.encode("utf-8"),
        file_name="netbox-descriptions.txt",
        mime="text/plain",
        key="dl_esxi_descriptions_pipe",
        width="stretch",
    )

