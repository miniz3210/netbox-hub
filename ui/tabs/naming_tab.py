import logging
import re
from typing import Dict, List
import streamlit as st

logger = logging.getLogger(__name__)
from utils.formatters import normalize_port_shortname
from utils.pattern_formatter import apply_pattern
from core.naming_engine import verify_and_suggest_with_ai
from data.standards_manager import StandardsManager
from datetime import datetime
from core.db_manager import (
    save_universal_csv,
    get_records_by_category,
    clear_inventory_records,
    clear_device_records,
    clear_vm_records,
    get_total_record_count,
    get_sync_metadata,
    get_file_sync_metadata,
)
from core.session_manager import SessionStateManager as SSM
from core.shared_backup_state import SharedBackupState
from ui.components import render_ai_chat, render_backup_uploader
from core.naming_dynamic_helper import (
    render_dynamic_pattern,
    render_token_widgets,
    render_esxi_network_inputs,
    render_edit_mode_ui,
    render_multi_edit_mode_ui,
    extract_tokens,
    token_label,
)
from config.naming_rules import (
    load_naming_rules, get_naming_patterns, get_pattern_variables,
    get_device_presets, get_interface_presets, get_host_vm_presets,
    get_esxi_network_presets, compute_suggested_site_code,
)

@st.cache_data(ttl=60, show_spinner=False)
def _cached_fetch_toolbar_stats():
    tot = get_total_record_count()
    dev = len(get_records_by_category("device")) + len(get_records_by_category("hypervisor"))
    vms = len(get_records_by_category("vm"))
    return tot, dev, vms


def _cached_get_reference_records(category_key, site_filter="", name_filter=""):
    """Cache reference records to avoid full-table SQLite scans on every widget interaction."""
    items = get_records_by_category(category_key, site_filter=site_filter)
    if name_filter:
        nfu = name_filter.upper()
        items = [r for r in items if r.get('name', '').upper().startswith(nfu)]
    return items


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


def _copy_to_clipboard(text: str):
    st.session_state["_last_copied"] = text


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

def handle_csv_upload(uploader_key: str):
    """Universal file upload handler using dynamic schema classification."""
    uploaded_files = st.session_state.get(uploader_key)
    if not uploaded_files:
        return

    if not isinstance(uploaded_files, list):
        uploaded_files = [uploaded_files]

    # Use universal uploader for automatic classification and routing
    from core.universal_uploader import UniversalUploader
    
    try:
        uploader = UniversalUploader()
        results = uploader.process_uploaded_files(uploaded_files)
        
        if results['errors']:
            for err in results['errors']:
                st.error(err)
        
        if results['total_records'] > 0:
            # Generate detailed toast message with model breakdown
            model_breakdown = ", ".join([
                f"{count} {model.split('.')[-1]}"
                for model, count in sorted(results['by_model'].items())
            ])
            
            # Check for unclassified files
            has_unclassified = any('unclassified' in model for model in results['by_model'].keys())
            
            if has_unclassified:
                st.warning(
                    f"⚠️ Stored {results['total_records']} records in unclassified table: {model_breakdown}\n\n"
                    "💡 **Tip:** Upload a NetBox backup JSON first to enable automatic classification. "
                    "The data is safely stored and can be re-classified later.",
                    icon="⚠️"
                )
            else:
                st.toast(f"✅ Ingested {results['total_records']} records: {model_breakdown}", icon="🚀")
            
            # Update SharedBackupState for dynamic backup display
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Build source file list for display
            source_files = ", ".join([f.name for f in uploaded_files])
            for model_key, count in results['by_model'].items():
                # Map model keys to endpoint paths for SharedBackupState
                endpoint = model_key.replace('.', '/')
                SharedBackupState.add_csv_override(endpoint, count, source_files, now)
            
            # Clear uploader by incrementing key
            st.session_state["naming_csv_key"] = st.session_state.get("naming_csv_key", 0) + 1
        elif not results['errors']:
            # No data ingested and no errors - clear uploader anyway
            st.session_state["naming_csv_key"] = st.session_state.get("naming_csv_key", 0) + 1
            
    except Exception as e:
        st.error(f"❌ Upload failed: {str(e)}")
        # Show helpful message if schema registry not initialized
        if "schema registry" in str(e).lower() or "classify" in str(e).lower():
            st.info(
                "💡 **Tip:** Upload a NetBox backup JSON first to initialize the schema registry, "
                "then CSV files will be automatically classified and routed."
            )

def handle_csv_reset():
    clear_inventory_records()
    
    # Also clear CSV entries from SharedBackupState
    from core.shared_backup_state import SharedBackupState
    SharedBackupState.clear_csv_only()
    
    st.toast("🗑️ Database Cleared. Restored default examples.", icon="🧹")

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
    
    total_recs, device_count, vm_count = _cached_fetch_toolbar_stats()
    
    status_tag = f"🟢 ({device_count} Devices, {vm_count} VMs in DB)" if total_recs > 0 else "⚪ (Default Examples)"
    tick_devices = " ✅" if device_count > 0 else ""
    tick_vms = " ✅" if vm_count > 0 else ""
    
    with st.expander(f"📥 Ingest NetBox Data (Backup / CSV) {status_tag}", expanded=False):
        # Schema Registry Status Check
        from core.universal_schema_registry import UniversalSchemaRegistry
        registry = UniversalSchemaRegistry.load_from_session_state()
        
        if registry and registry.model_signatures:
            model_count = len(registry.model_signatures)
            st.success(f"✅ **Schema Registry Active:** {model_count} NetBox models loaded. CSV files will be automatically classified.", icon="🔍")
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


from config.naming_rules import get_naming_patterns, get_pattern_variables, load_naming_rules


# Preset choices (Device Type / Interface Type) are loaded dynamically from the structured
# `device_presets` / `interface_presets` collections in data/naming_rules.yaml,
# so any Add/Modify/Delete in the Standards Tab is reflected immediately here.
# The dicts below are reference metadata keyed by preset code used for fallback examples.
DEVICE_TYPE_LABELS = {
    "SW": "Switch (SW / SWI)",
    "VS": "Virtual Chassis / Stack (VS)",
    "FW": "Firewall / Security (FW)",
    "ION": "SD-WAN / Prisma (ION)",
    "WAP": "Wireless AP (WAP)",
    "RTR": "Router (RTR)",
    "VA": "Virtual Appliance (VA)",
}

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
    _ts = id(SharedBackupState.get_objects_by_type("dcim/interfaces") or [])
    return _interface_ref_examples_cached(intf_code, _ts)


def _safe_parse_json_array(response: str) -> list:
    """Extract and parse a JSON array from LLM response, handling truncated/invalid JSON and markdown fences."""
    import json as _json
    # Strip markdown code fences if present
    cleaned = response.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    m_json = re.search(r"\[\s*\{.*\}\s*\]", cleaned, re.DOTALL)
    if m_json:
        json_str = m_json.group(0)
    else:
        stripped = cleaned.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            json_str = stripped
        else:
            # Handle wrapped dictionaries like {"interfaces": [...]} or markdown blocks
            m_obj = re.search(r"\{\s*.*?\s*\}", cleaned, re.DOTALL)
            if m_obj:
                try:
                    obj = _json.loads(m_obj.group(0))
                    if isinstance(obj, dict):
                        for v in obj.values():
                            if isinstance(v, list) and v and isinstance(v[0], dict):
                                return v
                except _json.JSONDecodeError:
                    pass
            return []
    try:
        return _json.loads(json_str)
    except _json.JSONDecodeError:
        pass
    # Attempt recovery: close unclosed brackets/braces and fix unterminated strings
    for fix in (lambda s: s.rstrip().rstrip(",") + "\n]",
                lambda s: s.rstrip().rstrip(",") + "\n}",
                lambda s: s.replace("\n", " ").replace("  ", " ")):
        try:
            return _json.loads(fix(json_str))
        except _json.JSONDecodeError:
            continue
    return []


def _is_vision_model(model_name: str) -> bool:
    """Return True if model name indicates multimodal vision support."""
    if not model_name:
        return False
    m = model_name.lower()
    vision_keywords = ["vision", "-vl", "4o", "gpt-5", "claude", "gemini", "pixtral", "qwen2.5-vl"]
    return any(k in m for k in vision_keywords)


def _render_esxi_pattern(pat: str, vals: dict, variables: dict) -> str:
    """Interpolate an ESXi description pattern through the unified rendering pipeline,
    which strips empty optional clauses before substitution. All fixed words (Active,
    Standby, Network, etc.) come from the editable template - none are hardcoded
    here."""
    return render_dynamic_pattern(pat, vals, variables)


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


def _esxi_network_presets_fn(rules, naming_patterns):
    """ESXi network preset (code, label, pattern_key) tuples loaded from YAML."""
    presets = get_esxi_network_presets(rules)
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
    host_key_map = {code: key for code, _label, key in presets}
    if not host_keys:
        host_keys = ["ESXi"]
        host_label_map = {"ESXi": "ESXi Host"}
        host_key_map = {"ESXi": "esxi_host"}
    vm_pk = "vm_host"
    vm_keys = [code for code, _label, key in presets if key == "vm_host"]
    vm_label_map = {code: label for code, label, _key in presets}

    hst_defaults = {"site_prefix": global_site, "site": global_site} if global_site else {}
    vm_defaults = {"site": global_site} if global_site else {}

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Host Type", help="Select the host type to generate a hostname. Choices are configured in the Standards Tab > Hosts Type Presets.")
        host_type = st.radio(
            "Host Type",
            host_keys,
            horizontal=True,
            key="host_type_sel",
            label_visibility="collapsed",
            help="Select the host type to generate a hostname. Choices are configured in the Standards Tab (Hosts Type Presets).",
            format_func=lambda c: c,
        )
        if host_type:
            st.caption(f"ℹ️ **{host_type}**: {host_label_map.get(host_type, '')}")
        host_pk = host_key_map.get(host_type, "esxi_host")
        pat = naming_patterns.get(host_pk, naming_patterns.get("esxi_host", ""))
        if _edit_toggle(host_pk):
            render_edit_mode_ui(host_pk, pat, variables)
            st.stop()
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


def _asset_class_3(naming_rules: dict, casing: str, active_model: str = "", auto_correct: bool = True):
    from core.ocr_engine import run_local_ocr_pipeline
    from core.vault import SanitizerVault
    vault = SanitizerVault()
    st.subheader("☁️ Hypervisor Network Description Formatter", help="Format standardized hypervisor network descriptions (VMware ESXi, Proxmox VE, Linux Bridges, etc.) matching infrastructure guidelines.")

    mode = st.radio(
        "Operation Mode",
        ["🛠️ Interactive Single Item Mode", "📸 Screenshot Batch Mode (OCR)"],
        index=0,
        horizontal=True,
        label_visibility="collapsed"
    )

    if mode == "🛠️ Interactive Single Item Mode":
        _render_interactive_hypervisor_mode(naming_rules, casing, active_model)
    else:
        _render_screenshot_batch_mode(naming_rules, casing, active_model, vault)


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


def _select_best_template(row_dict: dict, preset_templates: list) -> str:
    """Select template based strictly on token availability and penalty for missing required tokens."""
    if not preset_templates:
        return "<interface> - <parent> <purpose>"

    present_keys = {k for k, v in row_dict.items() if str(v).strip()}
    best_tpl = preset_templates[0].get("pattern", "<interface> - <parent> <purpose>")
    max_score = -999.0

    for item in preset_templates:
        tpl = item.get("pattern", "")
        tpl_tokens = list(dict.fromkeys(re.findall(r"<([a-zA-Z0-9_]+)>", tpl)))
        if not tpl_tokens:
            continue

        matched = sum(1 for t in tpl_tokens if t in present_keys)
        missing = len(tpl_tokens) - matched

        score = (matched * 20.0) - (missing * 50.0)

        if score > max_score:
            max_score = score
            best_tpl = tpl

    return best_tpl


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
        st.markdown("#### 🛠️ Universal Hypervisor Description Generator")
        st.caption("Dynamically generated token-based forms driven entirely by platform templates.")

        presets_map = naming_rules.get("hypervisor_presets") or {}
        if not presets_map:
            from ui.tabs.standards_tab import DEFAULT_HYPERVISOR_PRESETS
            presets_map = DEFAULT_HYPERVISOR_PRESETS

        platforms = list(presets_map.keys())

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


def _render_screenshot_batch_mode(naming_rules: dict, casing: str, active_model: str, vault):
    """Render the full screenshot OCR pipeline openly (no expanders hiding the workflow)."""
    from core.ocr_engine import run_local_ocr_pipeline
    from data.standards_manager import StandardsManager
    import json as _json
    from core.ai_client import call_ai

    if "hypervisor_editor_version" not in st.session_state:
        st.session_state["hypervisor_editor_version"] = 0

    # Always reload fresh rules from disk/SSM on entry so that any Standards-tab
    # edits that triggered a rerun while this tab was inactive are picked up.
    # The caller's `naming_rules` param is a snapshot from the top of
    # render_naming_tab and may be stale after cross-tab saves.
    #
    # We use force_reload_naming_rules() (which clears _cached_naming_rules in
    # memory) instead of reading the file directly, to avoid triggering
    # Streamlit's "File change detected. Rerun?" dialog.
    from config.naming_rules import force_reload_naming_rules
    naming_rules = SSM.get_naming_rules(force_reload_naming_rules())
    st.session_state["_hyp_last_naming_rules_ts"] = id(naming_rules)

    st.markdown("##### 1️⃣ Upload & Analyze Screenshots")

    with st.container(border=True):
        with st.expander("📸 Screenshot Guidelines (Virtual Switches Topology)", expanded=False):
            st.markdown("""
**Recommended Capture Location:**

1. **Access**: vCenter or ESXi Host Client.
2. **Navigate**: `Host` ➔ `Configure` ➔ `Networking` ➔ `Virtual switches`.
3. **Expand**: Open target switches (e.g., `vSwitch0`, `vSwitch01`, `vSwitch1`).
4. **Capture**: Ensure all three sections are visible:
   - **Left**: Port Groups & VMkernel ports
   - **Middle**: Virtual Switch diagram
   - **Right**: Physical Adapters (with link speeds)

> 💡 **Pro-Tip (Complete Data Discovery)**: In addition to **Virtual switches** topology, also upload/paste screenshots of **Networking ➜ Physical adapters** (click `>>` to expand adapters like `vmnic1`~`vmnic6`). This provides exact **MAC addresses**, **PCI slot mappings (PCIeX/PortX)**, and **CDP/LLDP Switch Ports (Cable Connections)** with zero manual guesswork!

> ⚠️ **Manual Verification Required:** ESXi topology views do not explicitly display Active vs. Standby status. Please verify in ESXi: click **EDIT** beside the vSwitch ➜ go to **Teaming and failover** ➜ check **Failover order** (Active vs Standby adapters) before committing to NetBox.
            """)

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
                existing_names = {getattr(f, "name", "") for f in st.session_state["staged_topology_imgs"]}
                for uf in raw_uploaded:
                    if uf.name not in existing_names:
                        file_bytes = uf.read()
                        bio = _io.BytesIO(file_bytes)
                        bio.name = uf.name
                        bio.type = uf.type
                        bio.size = len(file_bytes)
                        st.session_state["staged_topology_imgs"].append(bio)

        with col_up2:
            st.markdown("**📋 Or Paste from Clipboard (Ctrl+V)**")
            if "paste_input_ver" not in st.session_state:
                st.session_state["paste_input_ver"] = 0
            paste_box_key = f"esxi_paste_input_{st.session_state.get('paste_input_ver', 0)}"
            pasted_data = st.text_input(
                "Paste Area",
                placeholder="Click here and press Ctrl+V",
                key=paste_box_key,
                label_visibility="collapsed",
                help="Focus this box and press Ctrl+V.",
            )
            st.html(
                """
                <script>
                const parentDoc = window.parent.document;
                if (!window.parent._esxiPasteDelegated) {
                    window.parent._esxiPasteDelegated = true;
                    parentDoc.addEventListener('paste', function(e) {
                        const target = e.target;
                        if (!target || target.getAttribute('aria-label') !== 'Paste Area') return;
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

        # Ingest clipboard paste inside Step 1
        if pasted_data and pasted_data.startswith("data:image"):
            import base64 as _b64, io as _io
            try:
                _, encoded = pasted_data.split(",", 1)
                img_bytes = _b64.b64decode(encoded)
                pasted_file = _io.BytesIO(img_bytes)
                idx = len(st.session_state["staged_topology_imgs"]) + 1
                pasted_file.name = f"clipboard_screenshot_{idx}.png"
                pasted_file.type = "image/png"
                pasted_file.size = len(img_bytes)
                st.session_state["staged_topology_imgs"].append(pasted_file)
                st.session_state["paste_input_ver"] = st.session_state.get("paste_input_ver", 0) + 1
                st.rerun()
            except Exception as e:
                st.warning(f"Failed to process pasted image: {e}")

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
                    st.rerun()

        # Control bar: Platform selector + Analyze + Clear
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
        ordered_plats = [p for p in saved_order if p in existing_set]
        for p in ["VMware ESXi", "Proxmox VE"]:
            if p in existing_set and p not in ordered_plats:
                ordered_plats.append(p)
        for p in sorted(list(existing_set)):
            if p not in ordered_plats:
                ordered_plats.append(p)

        naming_platform_options = ordered_plats if ordered_plats else ["VMware ESXi"]

        default_idx = 0
        cur_selected_plat = st.session_state.get("ocr_target_platform", naming_platform_options[0])
        if cur_selected_plat in naming_platform_options:
            default_idx = naming_platform_options.index(cur_selected_plat)

        col_plat, col_btn_an, col_btn_clr = st.columns([5, 3, 2], vertical_alignment="center")
        with col_plat:
            target_platform = st.selectbox(
                "Target Platform",
                options=naming_platform_options,
                index=default_idx,
                key="naming_target_platform",
                label_visibility="collapsed",
                help=None
            )
            st.session_state["ocr_target_platform"] = target_platform
        with col_btn_an:
            btn_analyze = st.button("🚀 Analyze & Auto-Populate", type="primary", width="stretch")
        with col_btn_clr:
            btn_clear = st.button("🗑️ Clear All", width="stretch")
        if btn_clear:
            st.session_state["staged_topology_imgs"] = []
            st.session_state["hypervisor_parsed_descriptions"] = []
            st.session_state["hypervisor_parsed_items"] = []
            st.session_state["latest_ocr_raw_text"] = ""
            st.session_state.pop("latest_vault_session", None)
            st.session_state["topo_uploader_key_ver"] = st.session_state.get("topo_uploader_key_ver", 0) + 1
            st.session_state["hypervisor_editor_version"] = st.session_state.get("hypervisor_editor_version", 0) + 1
            st.rerun()

    # OCR Raw Text Inspector (collapsible)
    staged_imgs = st.session_state.get("staged_topology_imgs", [])
    raw_ocr_text = st.session_state.get("latest_ocr_raw_text", "")
    with st.expander("📄 OCR Raw Text Inspector (Click to expand)", expanded=False):
        char_count = len(raw_ocr_text) if raw_ocr_text else 0
        img_count = len(staged_imgs)
        st.caption(f"ℹ️ Extracted {char_count:,} characters across {img_count} screenshot(s)")
        st.text_area("Extracted OCR Tokens", value=raw_ocr_text, height=420, disabled=True)

    # Execute analyze when button clicked
    start_analyze = btn_analyze

    preset_sm = StandardsManager()
    # Reload parsing presets fresh from the SAME freshly-loaded rules dict
    # (naming_rules) rather than calling load_naming_rules() again, to avoid
    # hitting a potentially still-stale cache.
    _unified_presets = naming_rules.get("topology_parsing_presets") or {}
    parsing_presets = _unified_presets if _unified_presets else preset_sm.get_parsing_presets()

    if start_analyze:
        try:
            progress_bar = st.progress(0, text="Initializing topology analysis...")
            total_imgs = len(uploaded_imgs)
            combined_raw_lines = []

            for idx, img in enumerate(uploaded_imgs):
                step_label = f"Analyzing screenshot [{idx + 1}/{total_imgs}]: {getattr(img, 'name', f'Image #{idx+1}')}"
                progress_bar.progress((idx) / total_imgs, text=step_label)

                extracted_txt = ""
                try:
                    raw_bytes = None
                    if hasattr(img, "seek"):
                        img.seek(0)
                    raw_bytes = None
                    if hasattr(img, "getvalue"):
                        raw_bytes = img.getvalue()
                    elif hasattr(img, "read"):
                        raw_bytes = img.read()
                        if hasattr(img, "seek"):
                            img.seek(0)
                    elif isinstance(img, (bytes, bytearray)):
                        raw_bytes = bytes(img)

                    if raw_bytes and len(raw_bytes) > 0:
                        ocr_res = run_local_ocr_pipeline([raw_bytes])
                    else:
                        ocr_res = run_local_ocr_pipeline([img])
                    extracted_txt = ocr_res.get("combined_text", "").strip()
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("OCR failed for image: %s", str(exc))

                if not extracted_txt:
                    continue
                combined_raw_lines.append(f"--- Screenshot {idx+1}: {getattr(img, 'name', '')} ---\n{extracted_txt}")

            progress_bar.progress(0.9, text="Sanitizing and invoking AI parser...")
            combined_raw_text = "\n\n".join(combined_raw_lines).strip()
            st.session_state["latest_ocr_raw_text"] = combined_raw_text

            # ── Collect raw OCR tokens with cumulative Y-offset across screenshots ──
            all_raw_tokens: List[Dict] = []
            cumulative_y = 0
            for idx, img in enumerate(uploaded_imgs):
                try:
                    raw_bytes = None
                    if hasattr(img, "seek"):
                        img.seek(0)
                    if hasattr(img, "getvalue"):
                        raw_bytes = img.getvalue()
                    elif hasattr(img, "read"):
                        raw_bytes = img.read()
                        if hasattr(img, "seek"):
                            img.seek(0)
                    elif isinstance(img, (bytes, bytearray)):
                        raw_bytes = bytes(img)
                    if raw_bytes and len(raw_bytes) > 0:
                        ocr_res = run_local_ocr_pipeline([raw_bytes])
                    else:
                        ocr_res = run_local_ocr_pipeline([img])
                    toks = ocr_res.get("raw_tokens", [])
                    img_heights = ocr_res.get("image_heights", [])
                    img_h = img_heights[0] if img_heights else 0
                    for t in toks:
                        t["_src"] = idx
                        box = t.get("box")
                        if box and len(box) >= 2 and cumulative_y > 0:
                            t["box"] = [box[0], box[1] + cumulative_y, box[2], box[3]]
                    all_raw_tokens.extend(toks)
                    cumulative_y += img_h
                except Exception:
                    pass

            # ── Spatial redaction hook ──────────────────────────────────────
            structured_text = None
            spatial_redaction_map: Dict[str, str] = {}
            try:
                from core.spatial_redactor import process_spatial_topology
                active_plat_name = st.session_state.get("naming_target_platform", "VMware ESXi")
                _sp_cfg = (
                    (naming_rules.get("topology_parsing_presets") or {})
                    .get(active_plat_name, {})
                    .get("spatial_anchors_and_redaction")
                    if isinstance(naming_rules.get("topology_parsing_presets"), dict)
                    else {}
                )
                if not isinstance(_sp_cfg, dict):
                    _sp_cfg = {}
                if _sp_cfg.get("enabled", False) and all_raw_tokens:
                    structured_text, spatial_redaction_map = process_spatial_topology(
                        all_raw_tokens, _sp_cfg
                    )
                    st.session_state["active_redaction_map"] = spatial_redaction_map
                    logger.info(
                        "Spatial grouping applied. Redacted %d sensitive tokens locally.",
                        len(spatial_redaction_map),
                    )
            except Exception as exc:
                import logging as _log
                _log.getLogger(__name__).warning(
                    "Spatial redaction hook failed, falling back to flat text: %s", exc
                )
                structured_text = None
                spatial_redaction_map = {}

            try:
                from utils.formatters import apply_auto_corrections
                if structured_text:
                    cleaned_ocr_text = structured_text
                else:
                    cleaned_ocr_text = apply_auto_corrections(combined_raw_text, "ocr_cleaning")
                    if not cleaned_ocr_text or cleaned_ocr_text == (structured_text or combined_raw_text):
                        cleaned_ocr_text = apply_auto_corrections(combined_raw_text, "vmware")
            except Exception:
                cleaned_ocr_text = combined_raw_text

            # Payload validation: prevent empty/sub-threshold payloads from reaching the LLM.
            if len(cleaned_ocr_text.strip()) < 50:
                logger.warning(
                    "Payload too short (%d chars). Falling back to raw OCR text.",
                    len(cleaned_ocr_text.strip()),
                )
                cleaned_ocr_text = combined_raw_text

            if not cleaned_ocr_text or not cleaned_ocr_text.strip():
                cleaned_ocr_text = combined_raw_text

            sanitized_combined, session_id = vault.sanitize(cleaned_ocr_text)
            st.session_state["latest_vault_session"] = session_id

            system_prompt = (
                "You are an expert infrastructure network architect and data modeling specialist.\n"
                "Analyze the provided sanitized OCR text extracted from infrastructure topologies or management dashboards.\n\n"
                "Extract all observed network entities into a flat JSON array of objects using strictly lowercase keys.\n\n"
                "STRICT TOPOLOGICAL ENTITY RULES:\n"
                "    1. ONLY extract endpoint interfaces, physical adapters (e.g., vmnic, eth), logical ports, or virtual interfaces (e.g., vmk, bond, port groups).\n"
                "    2. NEVER create an interface record for the hosting switch, bridge, or container fabric itself (e.g., vSwitch, vmbr, bridge are purely container attributes, not standalone interface items).\n\n"
                "MANDATORY TOPOLOGICAL KEYS (EVERY OBJECT MUST HAVE THESE):\n"
                "    - interface: The exact identifier of the adapter, port, port group, or endpoint (e.g., vmnic0, vmk0). NEVER name this key 'name'; it MUST be 'interface'.\n"
                "    - parent: The clean identifier of the specific switch, bridge, or fabric (e.g., vSwitch0, vmbr0). STRICT RULES FOR PARENT:\n"
                "        * Extract ONLY the specific instance name (e.g., 'vSwitch0', NOT 'StandardSwitch:vSwitch0' or 'Standard Switch: vSwitch0'). Strip off all generic category labels, prefixes, and colons.\n"
                "        * NEVER treat page headings, navigation tabs, or section labels (such as 'Virtual switches', 'Physical adapters', 'VMkernel adapters') as a parent.\n"
                "        * If an interface is displayed in a global inventory or unassigned list, leave parent as ''.\n\n"
                "DYNAMIC ATTRIBUTES & CROSS-SCREENSHOT CORRELATION:\n"
                "    Extract all observable attributes into clean lowercase keys:\n"
                "    - slot: Physical PCIe hardware slot location if visible.\n"
                "    - ip: Network address or CIDR prefix if present.\n"
                "    - speed: Connection throughput or duplex mode.\n"
                "    - vlan: Associated VLAN tag or identifier.\n"
                "    - purpose: Stated functional role, service, or network designation.\n"
                "    - remote_device: Discovered peer neighbor device identifier.\n"
                "    - remote_port: Discovered peer neighbor interface or port.\n"
                "    - Actively propagate attributes (slot, remote_device, remote_port) across records that share the same interface name.\n\n"
                "TYPO NORMALIZATION:\n"
                "    Correct visible OCR substitutions (e.g., letter 'o' vs digit '0') and normalize PCI addresses.\n"
                "STRICT 1:1 IP ASSIGNMENT:\n"
                "    Assign each unique IP address to exactly ONE interface record; never duplicate an IP across multiple records.\n\n"
                "CRITICAL: Output ONLY a valid JSON array of endpoint objects. Do not wrap in markdown fences or include explanations."
            )

            selected_preset_name = st.session_state.get("naming_target_platform", "VMware ESXi")
            # Read instructions directly from the unified topology_parsing_presets
            # (the single source of truth).  Fall back to DEFAULT_PARSING_PRESETS
            # only when the key is absent.
            preset_instructions = ""
            unified_presets = parsing_presets  # already loaded above
            if isinstance(unified_presets, dict):
                target_obj = unified_presets.get(selected_preset_name)
                if not target_obj:
                    for _k, _v in unified_presets.items():
                        if isinstance(_k, str) and _k.strip().lower() == selected_preset_name.strip().lower():
                            target_obj = _v
                            break
                if isinstance(target_obj, dict):
                    preset_instructions = target_obj.get("instructions", "")
                elif isinstance(target_obj, str):
                    preset_instructions = target_obj
            if not preset_instructions:
                from data.standards_manager import DEFAULT_PARSING_PRESETS
                if isinstance(DEFAULT_PARSING_PRESETS, dict):
                    _default_cfg = DEFAULT_PARSING_PRESETS.get(selected_preset_name, {})
                    if isinstance(_default_cfg, dict):
                        preset_instructions = _default_cfg.get("instructions", "")
                    elif isinstance(_default_cfg, str):
                        preset_instructions = _default_cfg
            if preset_instructions:
                system_prompt = f"{system_prompt}\n\n{preset_instructions}".strip()

            # Dynamically inject token expectations from hypervisor presets
            hypervisor_presets_map = naming_rules.get("hypervisor_presets") or {}
            active_hyp_platform = st.session_state.get("naming_target_platform", "VMware ESXi")
            active_presets_for_tokens = hypervisor_presets_map.get(active_hyp_platform, [])
            active_platform_tokens = set(re.findall(r"<([a-zA-Z0-9_]+)>", " ".join([p.get("pattern", "") for p in active_presets_for_tokens])))
            if active_platform_tokens:
                system_prompt += f"\n\nTARGET OUTPUT SCHEMA KEYS: Extract fields into matching lowercase keys: {', '.join(sorted(active_platform_tokens))}"

            user_prompt = f"Parse this consolidated sanitized topology text:\n\n{sanitized_combined}"
            response = call_ai(user_prompt, active_model, custom_system_msg=system_prompt, max_tokens=8192)
            progress_bar.progress(1.0, text="✅ Parsing and enriching results...")

            raw_items = _safe_parse_json_array(response)
            clean_records = []
            for item in raw_items:
                if isinstance(item, dict) and not any(k in item for k in ("finish_reason", "index", "message", "role")):
                    clean_records.append({str(k).strip().lower(): str(v).strip() if v is not None else "" for k, v in item.items()})

            all_parsed_items = clean_records
            if session_id:
                token_map = vault.get_session_token_map(session_id)
                if token_map:
                    try:
                        all_parsed_items = [
                            {k: vault.restore(str(v), session_id=session_id) for k, v in item.items()}
                            for item in all_parsed_items
                        ]
                    except Exception as e:
                        logger.warning("Vault de-tokenization failed: %s — keeping raw records.", e)
            # Post-LLM reverse de-tokenization for spatial redaction map
            if spatial_redaction_map:
                try:
                    all_parsed_items = [
                        {
                            k: spatial_redaction_map.get(str(v), str(v))
                            for k, v in item.items()
                        }
                        for item in all_parsed_items
                    ]
                except Exception as e:
                    logger.warning("Spatial redaction map apply failed: %s — keeping parsed items as-is.", e)
            if not isinstance(all_parsed_items, list):
                all_parsed_items = []

            if not combined_raw_text:
                st.warning("⚠️ No text detected by local OCR. Ensure screenshot contains legible topology labels.")
            elif not all_parsed_items:
                st.warning("⚠️ OCR detected text but no structured records were parsed. Check screenshot quality.")
            else:
                attr_bag = {}
                for item in all_parsed_items:
                    if not isinstance(item, dict):
                        continue
                    iface = str(item.get("interface") or "").strip().lower()
                    if not iface:
                        continue
                    if iface not in attr_bag:
                        attr_bag[iface] = {}
                    for k, v in item.items():
                        if k not in ("interface", "parent") and v:
                            attr_bag[iface][k] = v

                for item in all_parsed_items:
                    if not isinstance(item, dict):
                        continue
                    iface = str(item.get("interface") or "").strip().lower()
                    if iface in attr_bag:
                        for k, v in attr_bag[iface].items():
                            if not item.get(k):
                                item[k] = v

                seen = set()
                deduped = []
                for item in all_parsed_items:
                    if not isinstance(item, dict):
                        continue
                    key = (
                        str(item.get("interface") or "").strip().lower(),
                        str(item.get("parent") or "").strip().lower(),
                    )
                    if key not in seen:
                        seen.add(key)
                        deduped.append(item)

                st.session_state["hypervisor_parsed_descriptions"] = deduped
                # Increment editor version to FORCE Streamlit to mount a brand new data_editor widget
                st.session_state["hypervisor_editor_version"] = st.session_state.get("hypervisor_editor_version", 0) + 1
                st.toast(f"Successfully analyzed {total_imgs} screenshots and merged {len(deduped)} unique records!", icon="🚀")
        except Exception as e:
            import traceback
            logger.error("Pipeline execution failed: %s", str(e))
            st.error(f"Pipeline execution failed: {str(e)}")
            st.code(traceback.format_exc(), language="text")

    # st.rerun() is placed OUTSIDE the try/except so that Streamlit's internal
    # RerunException (raised by st.rerun) cannot be caught by the bare
    # except Exception and silently swallowed, which would leave the page
    # rendering Section 3 before the session state has been persisted.
    if start_analyze and "hypervisor_parsed_descriptions" in st.session_state:
        st.rerun()

    st.markdown("##### 2️⃣ Extracted Variables Inspector (Dynamic Token Bag)")
    parsed_records = st.session_state.get("hypervisor_parsed_descriptions") or []
    parsed_records = [
        r for r in parsed_records
        if isinstance(r, dict) and str(r.get("interface", "")).strip().lower() not in ("", "none", "null")
    ]
    if parsed_records:
        rows = parsed_records

        raw_token_dict = {}
        for r in rows:
            if not isinstance(r, dict):
                continue
            for k, v in r.items():
                if v is not None and str(v).strip() and str(v).lower() not in ("nan", "none", ""):
                    norm_k = re.sub(r"[^a-zA-Z0-9]+", "_", str(k).strip().lower()).strip("_")
                    if norm_k:
                        raw_token_dict.setdefault(norm_k, set()).add(str(v).strip())

        existing_vars = get_pattern_variables(naming_rules)
        existing_token_names = set()
        if isinstance(existing_vars, dict):
            for k, v in existing_vars.items():
                if isinstance(v, dict) and "label" in v:
                    existing_token_names.add(k.lower())
                elif isinstance(v, dict):
                    for sub_k in v.keys():
                        existing_token_names.add(sub_k.lower())
        for k in get_pattern_variables(naming_rules).keys():
            existing_token_names.add(str(k).lower())

        # Build alias lookup from the UNIFIED topology_parsing_presets
        active_plat_display = st.session_state.get("naming_target_platform", "VMware ESXi")
        active_aliases = {}
        if isinstance(parsing_presets, dict):
            plat_data = parsing_presets.get(active_plat_display)
            if not plat_data:
                for _k, _v in parsing_presets.items():
                    if isinstance(_k, str) and _k.strip().lower() == active_plat_display.strip().lower():
                        plat_data = _v
                        break
            if isinstance(plat_data, dict):
                active_aliases = plat_data.get("aliases", {}) or {}
        if not isinstance(active_aliases, dict):
            active_aliases = {}
        # Reverse map: alias -> canonical
        alias_to_canonical = {}
        for canon, alias_list in active_aliases.items():
            if isinstance(alias_list, list):
                for a in alias_list:
                    alias_to_canonical[a.lower()] = canon

        with st.expander("🔍 OCR Raw Token Dictionary (Platform-Agnostic Variable Bag)", expanded=False):
            st.caption("All extracted tokens discovered from the uploaded topology. These keys are immediately available in Step 3 patterns and can be synced to Standards.")
            tok_cols = st.columns(3)
            for idx, (t_name, t_vals) in enumerate(sorted(raw_token_dict.items())):
                col_target = tok_cols[idx % 3]
                sample_vals = ", ".join(list(t_vals)[:3])
                sync_key = f"btn_sync_tok_{t_name}"
                clean_t = t_name.strip("<>_").lower()
                is_existing = clean_t in existing_token_names
                matched_alias = None
                if not is_existing:
                    matched_alias = alias_to_canonical.get(clean_t)
                    if matched_alias and matched_alias in existing_token_names:
                        is_existing = True
                with col_target:
                    if is_existing:
                        alias_note = f" / Alias of {matched_alias}" if matched_alias else " (In Standards)"
                        st.button(f"✅ <{t_name}>{alias_note}", key=sync_key, disabled=True, width='stretch')
                    else:
                        if st.button(f"🔗 Sync <{t_name}>", key=sync_key, help=f"Sync <{t_name}> to Hypervisor Standards with default value: {sample_vals}", width='stretch'):
                            from config.naming_rules import save_naming_rules
                            norm_token = re.sub(r"[^a-z0-9_]", "", t_name)
                            if "pattern_variables" not in naming_rules or not isinstance(naming_rules.get("pattern_variables"), dict):
                                naming_rules.setdefault("pattern_variables", {})
                            sample_val_list = list(t_vals)
                            default_val = sample_val_list[0] if sample_val_list else ""
                            naming_rules["pattern_variables"][norm_token] = {
                                "label": norm_token.replace("_", " ").title(),
                                "placeholder": f"e.g. {default_val or norm_token}",
                                "default": default_val,
                                "optional": False,
                                "scope": "hypervisor",
                            }
                            save_naming_rules(naming_rules, source="Variable Inspector Sync")
                            SSM.set_naming_rules(naming_rules.copy())
                            st.toast(f"✅ <{norm_token}> synced! Manage in Standards Tab ➔ Pattern Variables.", icon="💾")
                            st.rerun()
                    border_color = "#22c55e" if is_existing else "rgba(255,255,255,0.08)"
                    title_color = "#22c55e" if is_existing else "#38bdf8"
                    with st.container(border=True):
                        st.markdown(f"""
                        <div style="font-family: monospace; font-weight: 600; color: {title_color}; font-size: 0.85rem;">&lt;{t_name}&gt;</div>
                        <div style="color: #94a3b8; font-size: 0.78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="{sample_vals}">e.g. {sample_vals}</div>
                        """, unsafe_allow_html=True)
    else:
        st.caption("Tokens will be listed here after analyzing topology screenshots.")

    # 3️⃣ NetBox Descriptions & Review (Ready-to-Copy)
    st.divider()
    st.markdown("##### 3️⃣ NetBox Descriptions & Review (Ready-to-Copy)")

    if parsed_records:
        st.markdown("###### 📋 Generated NetBox Interface Descriptions (Editable)")
        st.caption("Review and edit parsed topology directly below. Batch text updates reactively in real time.")

        edited_descriptions = st.data_editor(
            st.session_state["hypervisor_parsed_descriptions"],
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            key=f"hypervisor_data_editor_{st.session_state['hypervisor_editor_version']}"
        )
        # PROTECT STATE: Only update if the user actually edited non-empty content
        if edited_descriptions is not None:
            if hasattr(edited_descriptions, "to_dict"):
                ed_list = edited_descriptions.to_dict(orient="records")
            else:
                ed_list = list(edited_descriptions)
            # Only overwrite if ed_list is populated or if session was already empty
            if ed_list:
                st.session_state["hypervisor_parsed_descriptions"] = ed_list

        col_qc_title, col_qc_btn = st.columns([3.5, 1.0], vertical_alignment="center")
        with col_qc_title:
            st.markdown("###### 📋 Quick Copy for NetBox (Batch Text)")
        with col_qc_btn:
            if st.button("🔄 Refresh", key="btn_refresh_quick_copy", width='stretch', help="Re-render Quick Copy using the latest standards and patterns without re-running OCR"):
                from config.naming_rules import load_naming_rules
                fresh_rules = load_naming_rules()
                SSM.set_naming_rules(fresh_rules.copy())
                SSM.refresh_naming_rules()
                st.toast("✅ Refreshed with latest standards!")
                st.rerun()
        rows = edited_descriptions

        _naming_rules = SSM.get_naming_rules({})
        _patterns = _naming_rules.get("naming_patterns", {})
        pattern_vars = _naming_rules.get("variables", {}) or {}

        # Load templates from hypervisor_presets (multi-platform)
        presets_map = _naming_rules.get("hypervisor_presets") or {}
        active_platform = st.session_state.get("naming_target_platform", "VMware ESXi")
        preset_templates = presets_map.get(active_platform, presets_map.get("VMware ESXi", []))

        if not preset_templates:
            preset_templates = [{"pattern": "<interface> - <parent> <purpose>"}]

        tpl_keys = st.session_state.get("_hyp_last_tpl_keys")
        if tpl_keys != preset_templates:
            st.session_state["_hyp_last_tpl_keys"] = preset_templates

        active_rows = st.session_state.get("hypervisor_parsed_descriptions") or []

        # Load platform aliases from the UNIFIED topology_parsing_presets for bidirectional resolution in Step 3
        active_aliases_step3 = {}
        if isinstance(parsing_presets, dict):
            plat_data_s3 = parsing_presets.get(active_platform)
            if not plat_data_s3:
                for _k, _v in parsing_presets.items():
                    if isinstance(_k, str) and _k.strip().lower() == active_platform.strip().lower():
                        plat_data_s3 = _v
                        break
            if isinstance(plat_data_s3, dict):
                active_aliases_step3 = plat_data_s3.get("aliases", {}) or {}
        if not isinstance(active_aliases_step3, dict):
            active_aliases_step3 = {}

        GENERIC_NAV_HEADINGS = {
            "virtual switches", "virtual switch", "switches", "switch",
            "physical adapters", "physical adapter", "adapters", "adapter",
            "vmkernel adapters", "vmkernel adapter", "networks", "network"
        }

        cleaned_topology = []
        for item in active_rows:
            row = dict(item)
            p = str(row.get("parent") or "").strip()
            p = re.sub(r"^(?:standard\s*switch|distributed\s*switch|vswitch)\s*[:_-]\s*", "", p, flags=re.IGNORECASE).strip()
            if p.lower() in GENERIC_NAV_HEADINGS or p.lower() == str(row.get("interface") or row.get("name") or "").strip().lower():
                p = ""
            row["parent"] = p
            cleaned_topology.append(row)

        valid_interface_parents = {}
        for item in cleaned_topology:
            iface = str(item.get("interface") or item.get("name") or "").strip().lower()
            parent = str(item.get("parent") or "").strip()
            if iface and parent:
                valid_interface_parents[iface] = parent

        filtered_topology = []
        seen_entries = set()
        for item in cleaned_topology:
            iface = str(item.get("interface") or item.get("name") or "").strip()
            iface_lower = iface.lower()
            parent = str(item.get("parent") or "").strip()

            if not parent and iface_lower in valid_interface_parents:
                continue

            entry_key = (parent.lower(), iface_lower)
            if entry_key in seen_entries:
                continue
            seen_entries.add(entry_key)
            filtered_topology.append(item)

        groups: dict[str, list[dict]] = {}
        for item in filtered_topology:
            p = str(item.get("parent") or "").strip() or "General"
            groups.setdefault(p, []).append(item)

        output_lines = ["# NOTE:\n# Generated from active Standards templates.\n"]
        for parent in sorted(groups.keys()):
            output_lines.append(f"=== {parent} ===")
            for row in sorted(groups[parent], key=lambda x: str(x.get("interface") or x.get("name") or "")):
                row_vals = {str(k).strip().lower(): str(v).strip() for k, v in row.items() if v is not None}
                iface = row_vals.get("interface") or row_vals.get("name") or ""
                parent_switch = row_vals.get("parent", parent)

                # Dynamic aliases for universal template tokens
                row_vals["interface"] = iface
                row_vals["parent"] = parent_switch
                row_vals["v_switch"] = parent_switch

                for canonical, alias_list in active_aliases_step3.items():
                    if canonical in row_vals and row_vals[canonical]:
                        c_val = row_vals[canonical]
                        for a in alias_list:
                            if a not in row_vals or not row_vals[a]:
                                row_vals[a] = c_val
                    else:
                        for a in alias_list:
                            if a in row_vals and row_vals[a]:
                                a_val = row_vals[a]
                                row_vals[canonical] = a_val
                                for sibling in alias_list:
                                    if sibling not in row_vals or not row_vals[sibling]:
                                        row_vals[sibling] = a_val
                                break

                tpl = _select_best_template(row_vals, preset_templates)
                rendered = render_dynamic_pattern(tpl, row_vals, pattern_vars)
                rendered = _clean_rendered_clause(rendered)

                if st.session_state.get("auto_correct", True):
                    from utils.formatters import apply_auto_corrections
                    rendered = apply_auto_corrections(rendered, "port_shortening")
                    rendered = apply_auto_corrections(rendered, "ocr_cleaning")
                    rendered = apply_auto_corrections(rendered, "vmware")

                if not rendered:
                    rendered = iface

                slot = str(row_vals.get("slot", "")).strip()
                header = f"{slot} ({iface}):" if slot else f"{iface}:"
                if iface:
                    output_lines.append(f"{header}\n{rendered}\n")

        bulk_text = "\n".join(output_lines).strip()
        st.code(bulk_text, language="text")
    else:
        bulk_text = "# No parsed topology records. Upload screenshots and click Analyze to generate descriptions.\n"
        st.code(bulk_text, language="text")

    st.download_button(
        "📥 Download Generated Descriptions (.txt)",
        bulk_text.encode("utf-8"),
        file_name="netbox-descriptions.txt",
        mime="text/plain",
        key="dl_esxi_descriptions_pipe"
    )
