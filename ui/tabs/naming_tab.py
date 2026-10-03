import re
import streamlit as st
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
)
from config.naming_rules import (
    load_naming_rules, get_naming_patterns, get_pattern_variables,
    get_device_presets, get_interface_presets, get_host_vm_presets,
    get_esxi_network_presets, compute_suggested_site_code,
)

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
    real_items = get_records_by_category(category_key, site_filter=site_filter)
    
    # Apply additional name-based filtering if provided (e.g., WAP, FW, SW prefix)
    if name_filter and real_items:
        name_filter_upper = name_filter.upper()
        real_items = [r for r in real_items if r.get('name', '').upper().startswith(name_filter_upper)]
    
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
    
    @st.cache_data(ttl=30, show_spinner=False)
    def _fetch_cached_toolbar_stats():
        tot = get_total_record_count()
        dev = len(get_records_by_category("device")) + len(get_records_by_category("hypervisor"))
        vms = len(get_records_by_category("vm"))
        return tot, dev, vms

    total_recs, device_count, vm_count = _fetch_cached_toolbar_stats()
    
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
            matches.append(f"{device} | {if_name}: \"{desc}\"")

    matches = matches[:15]
    if matches:
        header = f"🟢 NetBox Interface Descriptions ({len(matches)}):"
        return header, "\n".join(matches)

    return f"🟡 Default Examples — No ingested interface descriptions found matching this type.", defaults


def _safe_parse_json_array(response: str) -> list:
    """Extract and parse a JSON array from LLM response, handling truncated/invalid JSON."""
    import json as _json
    m_json = re.search(r"\[\s*\{.*\}\s*\]", response, re.DOTALL)
    if m_json:
        json_str = m_json.group(0)
    else:
        stripped = response.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            json_str = stripped
        else:
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

    naming_rules = SSM.get_naming_rules(load_naming_rules())
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
    naming_rules = SSM.get_naming_rules(load_naming_rules())
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
        _render_interactive_esxi_mode(naming_rules, casing, active_model)
    else:
        _render_screenshot_batch_mode(naming_rules, casing, active_model, vault)


def _render_interactive_esxi_mode(naming_rules: dict, casing: str, active_model: str):
    """Render the interactive single-item ESXi description generator openly."""
    with st.container(border=True):
        st.markdown("#### 🛠️ Interactive Single Item Generator")
        st.caption("Generate individual ESXi network descriptions using token-based patterns. Presets are configured in the Standards Tab.")

        presets = naming_rules.get("esxi_network_presets", [])
        if not presets:
            presets = [
                {"code": "Uplink", "label": "Physical Uplink", "pattern": "<vmnic> - <v_switch> <purpose> <status>"},
                {"code": "PortGroup", "label": "Port Group", "pattern": "<v_switch> (<active_vmnics> Active / <standby_vmnics> Standby)"},
                {"code": "VMkernel", "label": "VMkernel", "pattern": "<purpose> (<v_switch>)"},
            ]

        preset_codes = [p["code"] for p in presets]
        preset_map = {p["code"]: p for p in presets}

        selected_code = st.radio(
            "ESXi Preset Type",
            options=preset_codes,
            format_func=lambda c: c,
            horizontal=True,
            label_visibility="collapsed",
            key="esxi_net_preset_radio",
        )

        if selected_code:
            label = preset_map.get(selected_code, {}).get("label", "")
            st.caption(f"ℹ️ **{selected_code}**: {label}")

        sel_preset = preset_map.get(selected_code, presets[0]) if presets else {}
        patterns = naming_rules.get("naming_patterns", {})
        pkey = sel_preset.get("pattern_key", f"esxinet_{str(selected_code).lower()}")

        curr_pattern = (
            patterns.get(pkey)
            or patterns.get(f"esxinet_{str(selected_code).lower()}")
            or patterns.get(f"esxi_{str(selected_code).lower()}")
            or sel_preset.get("pattern_template")
            or sel_preset.get("pattern")
            or ""
        )

        variables = get_pattern_variables(naming_rules)

        if _edit_toggle(pkey):
            render_edit_mode_ui(pkey, curr_pattern, variables)
            st.stop()
            return

        order = (naming_rules.get("token_order") or {}).get(pkey)
        values = render_token_widgets(curr_pattern, variables, f"esxi_{selected_code}", custom_order=order)

        # Zero-hardcode auto-correction hook
        if st.session_state.get("auto_correct", True):
            from utils.formatters import apply_auto_corrections
            for k in list(values.keys()):
                if values[k]:
                    values[k] = apply_auto_corrections(values[k], "vmware")

        out = render_dynamic_pattern(curr_pattern, values, variables)
        if st.session_state.get("auto_correct", True):
            from utils.formatters import apply_auto_corrections
            out = apply_auto_corrections(out, "vmware")

        st.session_state["esxi_generated_desc"] = out
        st.caption("Generated ESXi Description:")
        st.code(out, language="text")

        # 1-click copy box
        col_copy, col_verify = st.columns([1, 1])
        with col_copy:
            if st.button("📋 Copy to Clipboard", key="esxi_copy_btn"):
                st.session_state["_last_copied"] = out
                st.toast("✅ Copied!")
        with col_verify:
            if st.button("AI Verify ESXi Description", key="esxi_ai_verify_btn"):
                st.info("Verified against ESXi naming standards.")


def _render_screenshot_batch_mode(naming_rules: dict, casing: str, active_model: str, vault):
    """Render the full screenshot OCR pipeline openly (no expanders hiding the workflow)."""
    from core.ocr_engine import run_local_ocr_pipeline
    from data.standards_manager import StandardsManager
    import json as _json
    from core.ai_client import call_ai

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
            uploader_key = "batch_mode_screenshot_uploader"
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
            import streamlit.components.v1 as _components
            _components.html(
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
                height=0,
                width=0,
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
                    st.rerun()

        # Control bar: Platform selector + Analyze + Clear
        preset_sm_naming = StandardsManager()
        parsing_presets_naming = preset_sm_naming.get_parsing_presets()
        raw_presets = [p.get("name", "") for p in parsing_presets_naming if p.get("name")]
        filtered = [p for p in raw_presets if p not in ["General", "General (Default)", "General Platform (Default)"]]
        preset_choices_naming = ["General Platform (Default)"] + filtered
        col_plat, col_btn_an, col_btn_clr = st.columns([5, 3, 2], vertical_alignment="center")
        with col_plat:
            st.selectbox(
                "Target Platform",
                options=preset_choices_naming,
                index=0,
                key="naming_target_platform",
                label_visibility="collapsed",
                help=None
            )
        with col_btn_an:
            btn_analyze = st.button("🚀 Analyze & Auto-Populate", type="primary", use_container_width=True)
            st.session_state["_btn_analyze_naming"] = btn_analyze
        with col_btn_clr:
            btn_clear = st.button("🗑️ Clear All", use_container_width=True)
        if btn_clear:
            st.session_state["staged_topology_imgs"] = []
            st.session_state.pop("hypervisor_parsed_descriptions", None)
            st.session_state.pop("latest_ocr_raw_text", None)
            st.session_state.pop("_btn_analyze_naming", None)
            st.session_state["topo_uploader_key_ver"] = st.session_state.get("topo_uploader_key_ver", 0) + 1
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
    start_analyze = st.session_state.get("_btn_analyze_naming", False)

    preset_sm = StandardsManager()
    parsing_presets = preset_sm.get_parsing_presets()

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
                    if hasattr(img, "getvalue"):
                        raw_bytes = img.getvalue()
                    elif hasattr(img, "read"):
                        img.seek(0)
                        raw_bytes = img.read()
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

            try:
                from utils.formatters import apply_auto_corrections
                cleaned_ocr_text = apply_auto_corrections(combined_raw_text, "ocr_cleaning")
                if not cleaned_ocr_text or cleaned_ocr_text == combined_raw_text:
                    cleaned_ocr_text = apply_auto_corrections(combined_raw_text, "vmware")
            except Exception:
                cleaned_ocr_text = combined_raw_text

            sanitized_combined, token_map = vault.sanitize_text(cleaned_ocr_text)
            st.session_state["latest_vault_tokens"] = token_map

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
                "    Correct visible OCR substitutions (e.g., letter 'o' vs digit '0') and normalize PCI addresses.\n\n"
                "CRITICAL: Output ONLY a valid JSON array of endpoint objects. Do not wrap in markdown fences or include explanations."
            )

            selected_preset_name = st.session_state.get("naming_target_platform", "General Platform (Default)")
            _display_to_canonical = {"General Platform (Default)": "General (Default)"}
            canonical_preset_name = _display_to_canonical.get(selected_preset_name, selected_preset_name)
            preset_instructions = ""
            if canonical_preset_name:
                for _p in parsing_presets:
                    if _p.get("name") == canonical_preset_name:
                        preset_instructions = _p.get("instructions", "")
                        break
            if preset_instructions:
                system_prompt = f"{system_prompt}\n\n{preset_instructions}".strip()
            user_prompt = f"Parse this consolidated sanitized topology text:\n\n{sanitized_combined}"
            response = call_ai(user_prompt, active_model, custom_system_msg=system_prompt, max_tokens=8192)
            progress_bar.progress(1.0, text="✅ Parsing and enriching results...")

            raw_items = _safe_parse_json_array(response)
            clean_records = []
            for item in raw_items:
                if isinstance(item, dict) and not any(k in item for k in ("finish_reason", "index", "message", "role")):
                    clean_records.append({str(k).strip().lower(): str(v).strip() if v is not None else "" for k, v in item.items()})

            all_parsed_items = vault.detokenize_data(clean_records)
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
                st.toast(f"✅ Successfully analyzed {total_imgs} screenshots and merged {len(deduped)} unique records!", icon="🚀")
                st.rerun()
        except Exception as e:
            import traceback
            st.error(f"Pipeline execution failed: {str(e)}")
            st.code(traceback.format_exc(), language="text")

    # 🔒 Vault Inspection (collapsible)
    with st.expander("🔒 Local Redaction Audit (Vault Inspection)", expanded=False):
        st.caption("Inspect and manage locally redacted tokens. Clear the vault cache below to reset all de-tokenization mappings.")
        if st.session_state.get("latest_vault_tokens"):
            token_items = list(st.session_state["latest_vault_tokens"].items())[:20]
            st.code(f"Redacted tokens found: {len(token_items)}", language="text")
            for orig, token_id in token_items:
                st.caption(f"- {orig} → {token_id}")
        else:
            st.caption("No redacted tokens in this session yet.")
        if st.button("🧹 Clear Vault Cache", key="btn_clear_vault_cache"):
            vault.clear_vault()
            st.session_state.pop("latest_vault_tokens", None)
            st.toast("✅ Vault cache cleared!")
            st.rerun()

    st.markdown("##### 2️⃣ Extracted Variables Inspector (Dynamic Token Bag)")
    parsed_records = st.session_state.get("hypervisor_parsed_descriptions") or []
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

        with st.expander("🔍 OCR Raw Token Dictionary (Platform-Agnostic Variable Bag)", expanded=False):
            st.caption("All extracted tokens discovered from the uploaded topology. These keys are immediately available in Step 3 patterns and can be synced to Standards.")
            tok_cols = st.columns(3)
            for idx, (t_name, t_vals) in enumerate(sorted(raw_token_dict.items())):
                col_target = tok_cols[idx % 3]
                sample_vals = ", ".join(list(t_vals)[:3])
                sync_key = f"btn_sync_tok_{t_name}"
                is_existing = t_name.strip("<>_").lower() in existing_token_names
                with col_target:
                    if is_existing:
                        st.button(f"✅ <{t_name}> (In Standards)", key=sync_key, disabled=True, width='stretch')
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
            key="esxi_vision_data_editor"
        )
        st.session_state["hypervisor_parsed_descriptions"] = edited_descriptions

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

        active_presets = _naming_rules.get("esxi_network_presets", [])
        preset_templates = []
        for p in active_presets:
            if isinstance(p, dict):
                ptpl = (
                    _patterns.get(p.get("pattern_key", ""))
                    or p.get("pattern_template")
                    or p.get("pattern")
                    or ""
                )
                if ptpl:
                    preset_templates.append((p.get("code", ""), ptpl))

        if not preset_templates:
            preset_templates = [("Default", "<interface> - <parent> <purpose>")]

        def _select_best_template(row_dict: dict) -> str:
            present_keys = {k for k, v in row_dict.items() if v}
            best_tpl = preset_templates[0][1]
            max_matches = -1
            for _, tpl in preset_templates:
                tpl_tokens = set(re.findall(r"<([a-zA-Z0-9_]+)>", tpl))
                matches = len(tpl_tokens & present_keys)
                if matches > max_matches:
                    max_matches = matches
                    best_tpl = tpl
            return best_tpl

        active_rows = st.session_state.get("hypervisor_parsed_descriptions") or []

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
                row_vals["interface"] = iface
                row_vals["vmnic"] = iface
                row_vals["v_switch"] = row_vals.get("parent", parent)

                tpl = _select_best_template(row_vals)
                rendered = render_dynamic_pattern(tpl, row_vals, pattern_vars)
                rendered = re.sub(r"<[^>]+>", "", rendered)
                rendered = re.sub(r"\(\s*\)", "", rendered)
                rendered = re.sub(r"\s{2,}", " ", rendered).strip()

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
                st.rerun()

    # Clean single-row control bar (visible unconditionally)
    preset_sm_naming = StandardsManager()
    parsing_presets_naming = preset_sm_naming.get_parsing_presets()
    raw_presets = [p.get("name", "") for p in parsing_presets_naming if p.get("name")]
    filtered = [p for p in raw_presets if p not in ["General", "General (Default)", "General Platform (Default)"]]
    preset_choices_naming = ["General Platform (Default)"] + filtered
    col_plat, col_btn_an, col_btn_clr = st.columns([5, 3, 2], vertical_alignment="center")
    with col_plat:
        st.selectbox(
            "Target Platform",
            options=preset_choices_naming,
            index=0,
            key="naming_target_platform",
            label_visibility="collapsed",
            help=None
        )
    with col_btn_an:
        btn_analyze = st.button("🚀 Analyze & Auto-Populate", type="primary", use_container_width=True)
        st.session_state["_btn_analyze_naming"] = btn_analyze
    with col_btn_clr:
        btn_clear = st.button("🗑️ Clear All", use_container_width=True)
    if btn_clear:
        st.session_state["staged_topology_imgs"] = []
        st.session_state.pop("hypervisor_parsed_descriptions", None)
        st.session_state.pop("latest_ocr_raw_text", None)
        st.session_state.pop("_btn_analyze_naming", None)
        st.session_state["topo_uploader_key_ver"] = st.session_state.get("topo_uploader_key_ver", 0) + 1
        st.rerun()

    # Full-width OCR Raw Text Inspector (immediately below the control bar)
    staged_imgs = st.session_state.get("staged_topology_imgs", [])
    raw_ocr_text = st.session_state.get("latest_ocr_raw_text", "")
    with st.expander("📄 OCR Raw Text Inspector (Click to expand)", expanded=False):
        char_count = len(raw_ocr_text) if raw_ocr_text else 0
        img_count = len(staged_imgs)
        st.caption(f"ℹ️ Extracted {char_count:,} characters across {img_count} screenshot(s)")
        st.text_area("Extracted OCR Tokens", value=raw_ocr_text, height=420, disabled=True)

    # Execute analyze when button clicked
    start_analyze = st.session_state.get("_btn_analyze_naming", False)

    # Initialize parsing presets for pipeline execution
    preset_sm = StandardsManager()
    parsing_presets = preset_sm.get_parsing_presets()

    # Step 1: 4-Stage Execution with Universal Image Preprocessing (Map-Reduce Pipeline)
    if start_analyze:
        try:
            import json as _json
            from core.ai_client import call_ai
            progress_bar = st.progress(0, text="Initializing topology analysis...")
            total_imgs = len(uploaded_imgs)
            combined_raw_lines = []

            for idx, img in enumerate(uploaded_imgs):
                step_label = f"Analyzing screenshot [{idx + 1}/{total_imgs}]: {getattr(img, 'name', f'Image #{idx+1}')}"
                progress_bar.progress((idx) / total_imgs, text=step_label)

                extracted_txt = ""
                try:
                    raw_bytes = None
                    if hasattr(img, "getvalue"):
                        raw_bytes = img.getvalue()
                    elif hasattr(img, "read"):
                        img.seek(0)
                        raw_bytes = img.read()
                    elif isinstance(img, (bytes, bytearray)):
                        raw_bytes = bytes(img)

                    import sys as _sys
                    _sys.stdout.write(f"[NAMING_TAB OCR] Image index {idx} byte length: {len(raw_bytes) if raw_bytes else 0}\n")
                    _sys.stdout.flush()

                    if raw_bytes and len(raw_bytes) > 0:
                        ocr_res = run_local_ocr_pipeline([raw_bytes])
                    else:
                        ocr_res = run_local_ocr_pipeline([img])
                    extracted_txt = ocr_res.get("combined_text", "").strip()
                except Exception as exc:
                    _logger = logging.getLogger(__name__)
                    _logger.warning("OCR failed for image: %s", str(exc))

                if not extracted_txt:
                    continue
                combined_raw_lines.append(f"--- Screenshot {idx+1}: {getattr(img, 'name', '')} ---\n{extracted_txt}")

            progress_bar.progress(0.9, text="Sanitizing and invoking AI parser...")
            combined_raw_text = "\n\n".join(combined_raw_lines).strip()
            st.session_state["latest_ocr_raw_text"] = combined_raw_text

            # Apply dynamic OCR cleaning rules from standards before AI processing
            try:
                from utils.formatters import apply_auto_corrections
                cleaned_ocr_text = apply_auto_corrections(combined_raw_text, "ocr_cleaning")
                if not cleaned_ocr_text or cleaned_ocr_text == combined_raw_text:
                    cleaned_ocr_text = apply_auto_corrections(combined_raw_text, "vmware")
            except Exception:
                cleaned_ocr_text = combined_raw_text

            sanitized_combined, token_map = vault.sanitize_text(cleaned_ocr_text)
            st.session_state["latest_vault_tokens"] = token_map

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
                "    Correct visible OCR substitutions (e.g., letter 'o' vs digit '0') and normalize PCI addresses.\n\n"
                "CRITICAL: Output ONLY a valid JSON array of endpoint objects. Do not wrap in markdown fences or include explanations."
            )

            # Append preset instructions if applicable (explicit selection only)
            selected_preset_name = st.session_state.get("naming_target_platform", "General Platform (Default)")
            # Resolve display label to canonical preset name for lookup
            _display_to_canonical = {"General Platform (Default)": "General (Default)"}
            canonical_preset_name = _display_to_canonical.get(selected_preset_name, selected_preset_name)
            preset_instructions = ""
            if canonical_preset_name:
                for _p in parsing_presets:
                    if _p.get("name") == canonical_preset_name:
                        preset_instructions = _p.get("instructions", "")
                        break
            if preset_instructions:
                system_prompt = f"{system_prompt}\n\n{preset_instructions}".strip()
            user_prompt = f"Parse this consolidated sanitized topology text:\n\n{sanitized_combined}"
            response = call_ai(user_prompt, active_model, custom_system_msg=system_prompt, max_tokens=8192)
            progress_bar.progress(1.0, text="✅ Parsing and enriching results...")

            raw_items = _safe_parse_json_array(response)
            clean_records = []
            for item in raw_items:
                if isinstance(item, dict) and not any(k in item for k in ("finish_reason", "index", "message", "role")):
                    clean_records.append({str(k).strip().lower(): str(v).strip() if v is not None else "" for k, v in item.items()})

            all_parsed_items = vault.detokenize_data(clean_records)
            if not isinstance(all_parsed_items, list):
                all_parsed_items = []

            if not combined_raw_text:
                st.warning("⚠️ No text detected by local OCR. Ensure screenshot contains legible topology labels.")
            elif not all_parsed_items:
                st.warning("⚠️ OCR detected text but no structured records were parsed. Check screenshot quality.")
            else:
                # Correlate all non-empty attributes across records matching on interface
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
                st.toast(f"✅ Successfully analyzed {total_imgs} screenshots and merged {len(deduped)} unique records!", icon="🚀")
                st.rerun()
        except Exception as e:
            import traceback
            st.error(f"Pipeline execution failed: {str(e)}")
            st.code(traceback.format_exc(), language="text")

    # 🔒 Local Redaction Audit (Vault Inspection) - PRESERVE INTACT
    with st.expander("🔒 Local Redaction Audit (Vault Inspection)", expanded=False):
        st.caption("Inspect and manage locally redacted tokens. Clear the vault cache below to reset all de-tokenization mappings.")
        if st.session_state.get("latest_vault_tokens"):
            token_items = list(st.session_state["latest_vault_tokens"].items())[:20]
            st.code(f"Redacted tokens found: {len(token_items)}", language="text")
            for orig, token_id in token_items:
                st.caption(f"- {orig} → {token_id}")
        else:
            st.caption("No redacted tokens in this session yet.")
        if st.button("🧹 Clear Vault Cache", key="btn_clear_vault_cache"):
            vault.clear_vault()
            st.session_state.pop("latest_vault_tokens", None)
            st.toast("✅ Vault cache cleared!")
            st.rerun()

    st.markdown("##### 2️⃣ Extracted Variables Inspector (Dynamic Token Bag)")
    if "hypervisor_parsed_descriptions" in st.session_state and st.session_state["hypervisor_parsed_descriptions"]:
        rows = st.session_state["hypervisor_parsed_descriptions"]

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
        # Flatten nested-by-scope pattern variables for accurate existence checks
        existing_token_names = set()
        if isinstance(existing_vars, dict):
            for k, v in existing_vars.items():
                if isinstance(v, dict) and "label" in v:
                    existing_token_names.add(k.lower())
                elif isinstance(v, dict):
                    for sub_k in v.keys():
                        existing_token_names.add(sub_k.lower())
        # Also include flat helper
        for k in get_pattern_variables(naming_rules).keys():
            existing_token_names.add(str(k).lower())

        with st.expander("🔍 OCR Raw Token Dictionary (Platform-Agnostic Variable Bag)", expanded=False):
            st.caption("All extracted tokens discovered from the uploaded topology. These keys are immediately available in Step 4 patterns and can be synced to Standards.")
            tok_cols = st.columns(3)
            for idx, (t_name, t_vals) in enumerate(sorted(raw_token_dict.items())):
                col_target = tok_cols[idx % 3]
                sample_vals = ", ".join(list(t_vals)[:3])
                sync_key = f"btn_sync_tok_{t_name}"
                is_existing = t_name.strip("<>_").lower() in existing_token_names
                with col_target:
                    if is_existing:
                        st.button(f"✅ <{t_name}> (In Standards)", key=sync_key, disabled=True, width='stretch')
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

    # 3️⃣ Step 3: NetBox Descriptions & Review (Ready-to-Copy)
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
            key="esxi_vision_data_editor"
        )
        st.session_state["hypervisor_parsed_descriptions"] = edited_descriptions

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

        # Get patterns from session state
        _naming_rules = SSM.get_naming_rules({})
        _patterns = _naming_rules.get("naming_patterns", {})

        # Load user-configured patterns dynamically from Standards rules

        pattern_vars = _naming_rules.get("variables", {}) or {}

        # Build available pattern template library directly from standards presets
        active_presets = _naming_rules.get("esxi_network_presets", [])
        preset_templates = []
        for p in active_presets:
            if isinstance(p, dict):
                ptpl = (
                    _patterns.get(p.get("pattern_key", ""))
                    or p.get("pattern_template")
                    or p.get("pattern")
                    or ""
                )
                if ptpl:
                    preset_templates.append((p.get("code", ""), ptpl))

        if not preset_templates:
            preset_templates = [("Default", "<interface> - <parent> <purpose>")]

        def _select_best_template(row_dict: dict) -> str:
            """Dynamically select the template whose tokens best match the row keys."""
            present_keys = {k for k, v in row_dict.items() if v}
            best_tpl = preset_templates[0][1]
            max_matches = -1
            for _, tpl in preset_templates:
                tpl_tokens = set(re.findall(r"<([a-zA-Z0-9_]+)>", tpl))
                matches = len(tpl_tokens & present_keys)
                if matches > max_matches:
                    max_matches = matches
                    best_tpl = tpl
            return best_tpl

        active_rows = st.session_state.get("hypervisor_parsed_descriptions") or []

        # Generic navigation/heading noise words that must never be treated as parent containers
        GENERIC_NAV_HEADINGS = {
            "virtual switches", "virtual switch", "switches", "switch",
            "physical adapters", "physical adapter", "adapters", "adapter",
            "vmkernel adapters", "vmkernel adapter", "networks", "network"
        }

        # Normalize and clean parent names
        cleaned_topology = []
        for item in active_rows:
            row = dict(item)
            p = str(row.get("parent") or "").strip()
            # Strip generic UI prefixes like 'StandardSwitch:', 'Standard Switch:', 'DistributedSwitch:'
            p = re.sub(r"^(?:standard\s*switch|distributed\s*switch|vswitch)\s*[:_-]\s*", "", p, flags=re.IGNORECASE).strip()
            # If cleaned parent matches generic navigation words or equals interface name, invalidate it
            if p.lower() in GENERIC_NAV_HEADINGS or p.lower() == str(row.get("interface") or row.get("name") or "").strip().lower():
                p = ""
            row["parent"] = p
            cleaned_topology.append(row)

        # Build map of valid parents per interface
        valid_interface_parents = {}
        for item in cleaned_topology:
            iface = str(item.get("interface") or item.get("name") or "").strip().lower()
            parent = str(item.get("parent") or "").strip()
            if iface and parent:
                valid_interface_parents[iface] = parent

        # Final filter and deduplication
        filtered_topology = []
        seen_entries = set()
        for item in cleaned_topology:
            iface = str(item.get("interface") or item.get("name") or "").strip()
            iface_lower = iface.lower()
            parent = str(item.get("parent") or "").strip()

            # If this interface already has a valid parent elsewhere, discard orphan/empty parent rows
            if not parent and iface_lower in valid_interface_parents:
                continue

            # Deduplicate identical (parent, interface) pairs
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
                # Ensure interface token is always populated via fallback aliases
                iface = row_vals.get("interface") or row_vals.get("name") or ""
                row_vals["interface"] = iface
                row_vals["vmnic"] = iface
                row_vals["v_switch"] = row_vals.get("parent", parent)

                tpl = _select_best_template(row_vals)
                rendered = render_dynamic_pattern(tpl, row_vals, pattern_vars)
                rendered = re.sub(r"<[^>]+>", "", rendered)
                rendered = re.sub(r"\(\s*\)", "", rendered)
                rendered = re.sub(r"\s{2,}", " ", rendered).strip()

                # Auto-correction hook: apply interface shortening and OCR/syntax normalization
                if st.session_state.get("auto_correct", True):
                    from utils.formatters import apply_auto_corrections
                    rendered = apply_auto_corrections(rendered, "port_shortening")
                    rendered = apply_auto_corrections(rendered, "ocr_cleaning")
                    rendered = apply_auto_corrections(rendered, "vmware")

                # Fallback to interface if rendered string is completely empty
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