import logging
import re
from typing import Dict, List, Optional, Tuple, Any, Set
import streamlit as st
import pandas as pd

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

# ---------------------------------------------------------------------------
# Cache helpers for toolbar stats and reference records
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Switch name normalization
# ---------------------------------------------------------------------------
def _normalize_switch_name(name: str) -> str:
    """Normalize a switch/container name for consistent deck-key usage.

    Strips whitespace and removes any 'manual:' prefix that may have been left
    from prior manual adds.  Normalizes OCR substitution of letter 'o'/'O' for
    digit '0' (e.g. vSwitcho -> vSwitch0, vSwitcho1 -> vSwitch01) while
    preserving leading zeros — 'vSwitch01' and 'vSwitch02' remain distinct.
    """
    n = str(name).strip()
    if n.startswith("manual:"):
        n = n[len("manual:"):]
    # Normalize OCR substitution of letter 'o'/'O' for digit '0' (e.g. vSwitcho -> vSwitch0, vSwitcho1 -> vSwitch01)
    n = re.sub(r"(?i)(switch)o(\d*)", r"\g<1>0\2", n)
    return n


# ---------------------------------------------------------------------------
# Natural sort key — pure Python, no external deps
# ---------------------------------------------------------------------------
def _natsort_key(s: str):
    """Return a sort key that orders numeric substrings as integers,
    so vmnic1 < vmnic2 < vmnic10 instead of alphabetical ordering."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------------------
# Compiled regexes for switch header extraction (module scope)
# ---------------------------------------------------------------------------
SW_HEADER_RE = re.compile(
    r"(?:Standard\s*Switch|Distributed\s*Switch|(?<!\w)Switch|Bridge)\s*:?\s*([a-zA-Z0-9_\-\.\s]+)",
    re.IGNORECASE
)
ACTION_SPLIT_RE = re.compile(
    r"(?:ADD\s*NETWORKING|EDIT|MANAGE\s*PHYSICAL\s*ADAPTERS?|MANAGE\s*ADAPTERS?|REFRESH|ACTIONS?|\.\.\.|Ver\s*\.+\||Ref)",
    re.IGNORECASE
)

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
# Session-level cascade replacement helpers
# ---------------------------------------------------------------------------
_CASCADE_FIELDS = ("interface", "parent", "note")
_ANOMALY_PATTERNS = (
    re.compile(r"\d+[a-zA-Z]+\d+"),  # isolated letter between digits (e.g. 6Q5)
    re.compile(r"(?i)(?<=\d)o(?=[\d$])"),  # 'o' used as '0' next to a digit
)


def _has_anomalous_tokens(value: str) -> bool:
    """Return True if the value likely still contains OCR-error tokens."""
    if not value:
        return False
    for pat in _ANOMALY_PATTERNS:
        if pat.search(str(value)):
            return True
    return False


def _apply_cascade_to_rows(rows_before: list, edited_cells: dict) -> list:
    """Apply session-level cascade replacement for edited cells in the
    interface/parent/note fields across all rows. Returns a new list."""
    cascaded = [dict(r) for r in rows_before]
    for row_idx_str, changes in edited_cells.items():
        try:
            row_idx = int(row_idx_str)
        except (ValueError, TypeError):
            continue
        if row_idx < 0 or row_idx >= len(cascaded):
            continue
        prev_row = rows_before[row_idx]
        for col, new_val in changes.items():
            old_val = prev_row.get(col)
            new_str = "" if new_val is None else str(new_val).strip()
            old_str = "" if old_val is None else str(old_val).strip()
            if not old_str or not new_str or old_str == new_str:
                continue
            if col not in _CASCADE_FIELDS:
                continue
            for r in cascaded:
                for fc in _CASCADE_FIELDS:
                    val = r.get(fc)
                    val_str = "" if val is None else str(val)
                    if old_str in val_str:
                        r[fc] = val_str.replace(old_str, new_str)
                if fc == "interface" and new_str and not _has_anomalous_tokens(new_str):
                    r["is_uncertain"] = False
                    r["note"] = None
    return cascaded


@st.cache_data(ttl=60, show_spinner=False)
def _interface_ref_examples_cached(intf_code: str, _ts: str = ""):
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
            help="Select the host type to generate a hostname. Choices are configured in the Standards Tab (Hosts & Virtual Machines Presets).",
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
        _active = st.session_state.get("naming_target_platform", "VMware ESXi")
        _plat_cfg = _tpp.get(_active, {}) if isinstance(_tpp.get(_active), dict) else {}
        _teaming = _plat_cfg.get("teaming_rules") or {}
        if isinstance(_teaming, dict):
            _v = _teaming.get("no_uplink_display")
            if _v and str(_v).strip():
                return str(_v).strip()
        _ur_cfg = (_rules.get("pattern_variables") or {}).get("uplink_role") or {}
        return str(_ur_cfg.get("no_uplink_fallback") or "No Uplink").strip()

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
    _active = st.session_state.get("naming_target_platform", "VMware ESXi")
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
    if "hypervisor_cascade_mappings" not in st.session_state:
        st.session_state["hypervisor_cascade_mappings"] = []

    # Always reload fresh rules from disk/SSM on entry so that any Standards-tab
    # edits that triggered a rerun while this tab was inactive are picked up.
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
3. **Expand**: Open target switches.
4. **Capture**: Ensure all three sections are visible:
   - **Left**: Port Groups & VMkernel ports
   - **Middle**: Virtual Switch diagram
   - **Right**: Physical Adapters (with link speeds)

> 💡 **Pro-Tip (Complete Data Discovery)**: In addition to **Virtual switches** topology, also upload/paste screenshots of **Networking ➜ Physical adapters** (click `>>` to expand adapters). This provides exact **MAC addresses**, **PCI slot mappings (PCIeX/PortX)**, and **CDP/LLDP Switch Ports (Cable Connections)** with zero manual guesswork!

> ⚠️ **Manual Verification Required:** ESXi topology views do not explicitly display Active vs. Standby status. Please verify in ESXi: click **EDIT** beside the switch ➜ go to **Teaming and failover** ➜ check **Failover order** (Active vs Standby adapters) before committing to NetBox.
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
                        st.session_state.pop("hypervisor_cascade_mappings", None)
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
            st.session_state.pop("hypervisor_cascade_mappings", None)
            st.session_state.pop("chips_unassigned", None)
            st.session_state.pop("switch_deck", None)
            st.session_state.pop("chips_session_corrections", None)
            st.session_state.pop("_pool_initialized_v4", None)
            st.session_state["topology_records"] = []
            st.session_state["discovered_switches"] = []
            st.session_state["discovered_uplink_candidates"] = []
            st.session_state["discovered_service_candidates"] = []
            st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
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

    # Resolve active junk filter at function scope so Section 3 can access it
    # on every render pass (not just when start_analyze is True).
    active_plat_display = st.session_state.get("naming_target_platform", "VMware ESXi")
    plat_cfg = (parsing_presets.get(active_plat_display) or {}) if isinstance(parsing_presets, dict) else {}
    dynamic_blacklist: Set[str] = set(
        s.strip().lower() for s in (plat_cfg.get("ui_noise_blacklist") or []) if s and str(s).strip()
    )
    active_junk_filter: Set[str] = UI_NAV_NOISE.union(dynamic_blacklist)

    # Dynamic platform token prefixes (zero hardcoding) — available in both analyze and render paths
    _plat_tok = ((naming_rules.get("platform_tokens") or {})
                 .get(active_plat_display, {})) if isinstance(naming_rules.get("platform_tokens"), dict) else {}
    _dynamic_uplink_pfx = str(_plat_tok.get("uplink_prefix", "vmnic")).strip()
    _dynamic_vmk_pfx = str(_plat_tok.get("vmk_prefix", "vmk")).strip()
    _dynamic_switch_pfx = str(_plat_tok.get("switch_prefix", "vSwitch")).strip()

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
                "    1. ONLY extract endpoint interfaces, physical adapters, logical ports, or virtual interfaces.\n"
                "    2. NEVER create an interface record for the hosting switch, bridge, or container fabric itself (e.g., vSwitch, vmbr, bridge are purely container attributes, not standalone interface items).\n\n"
                "MANDATORY TOPOLOGICAL KEYS (EVERY OBJECT MUST HAVE THESE):\n"
                "    - interface: The exact identifier of the adapter, port, port group, or endpoint. NEVER name this key 'name'; it MUST be 'interface'.\n"
                "    - parent: The clean identifier of the specific switch, bridge, or fabric. STRICT RULES FOR PARENT:\n"
                "        * Extract ONLY the specific instance name. Strip off all generic category labels, prefixes, and colons.\n"
                "        * NEVER treat page headings, navigation tabs, or section labels as a parent.\n"
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
                st.session_state.pop("hypervisor_cascade_mappings", None)

                # ── Harvest discovered uplink / service candidates from OCR ─────
                plat_tokens_cfg = ((naming_rules.get("platform_tokens") or {})
                                   .get(active_plat_display, {})) if isinstance(naming_rules.get("platform_tokens"), dict) else {}
                uplink_pfx = str(plat_tokens_cfg.get("uplink_prefix", _dynamic_uplink_pfx)).strip()
                vmk_pfx = str(plat_tokens_cfg.get("vmk_prefix", _dynamic_vmk_pfx)).strip()

                discovered_uplinks = set()
                discovered_services = set()
                for item in deduped:
                    iface = str(item.get("interface", "")).strip()
                    purpose = str(item.get("purpose", "")).strip()
                    if iface:
                        if iface.lower().startswith(uplink_pfx.lower()):
                            discovered_uplinks.add(iface)
                        elif iface.lower().startswith(vmk_pfx.lower()):
                            discovered_services.add(iface)
                        elif len(iface) > 2 and iface.lower() not in UI_CHROME_WORDS:
                            discovered_services.add(iface)
                    if purpose and len(purpose) > 2 and purpose.lower() not in UI_CHROME_WORDS:
                        discovered_services.add(purpose)

                ocr_uplink_matches = re.findall(rf"\b{re.escape(uplink_pfx)}\d+\b", combined_raw_text, re.IGNORECASE)
                for u in ocr_uplink_matches:
                    discovered_uplinks.add(u.strip())

                ocr_vmk_matches = re.findall(rf"\b{re.escape(vmk_pfx)}\d+\b", combined_raw_text, re.IGNORECASE)
                for v in ocr_vmk_matches:
                    discovered_services.add(v.strip())

                st.session_state["discovered_uplink_candidates"] = sorted(discovered_uplinks, key=_natsort_key)
                st.session_state["discovered_service_candidates"] = sorted(discovered_services, key=_natsort_key)

                # ── Hybrid Architecture: Topology Records Ingestion ───────────
                discovered_switches = set()

                # 1. Capture from AI deduped entities
                for r in deduped:
                    p = r.get("parent")
                    if p and str(p).strip().lower() not in ("", "none", "null", "general"):
                        discovered_switches.add(_normalize_switch_name(str(p).strip()))

                # 2. Capture from OCR raw text with pipe splitting and leading arrow stripping
                combined_raw_text_for_sw = st.session_state.get("latest_ocr_raw_text", "")
                for _line in combined_raw_text_for_sw.splitlines():
                    if not _line.strip():
                        continue
                    _segments = _line.split("|") if "|" in _line else [_line]
                    for _seg in _segments:
                        _s_clean = _seg.strip()
                        if not _s_clean:
                            continue
                        _s_clean = re.sub(r"^[\s>›»▶v⌄]+\s*", "", _s_clean)
                        _m = SW_HEADER_RE.search(_s_clean)
                        if _m:
                            _raw_sw = _m.group(1).strip()
                            _clean_sw = ACTION_SPLIT_RE.split(_raw_sw)[0].strip()
                            _norm_sw = _normalize_switch_name(_clean_sw)
                            if _norm_sw and len(_norm_sw) > 1:
                                discovered_switches.add(_norm_sw)

                sorted_switches = sorted(discovered_switches, key=_natsort_key) if discovered_switches else [_dynamic_switch_pfx + "0"]
                UI_CHROME_WORDS = {
                    "summary", "monitor", "configure", "permissions", "vms", "datastores",
                    "networks", "updates", "storage", "actions", "add networking", "refresh",
                    "edit", "manage physical adapters", "protocol endpoints", "system", "hardware"
                }

                clean_switches = []
                for _s in sorted_switches:
                    _s_str = str(_s).strip()
                    if len(_s_str) > 2 and _s_str.lower() != "es" and _s_str.lower() not in UI_CHROME_WORDS:
                        clean_switches.append(_s_str)

                st.session_state["discovered_switches"] = clean_switches if clean_switches else [_dynamic_switch_pfx + "0"]

                # 3. Build initial topology records list
                records = []
                seen_entity = set()

                for r in deduped:
                    iface = str(r.get("interface", "")).strip()
                    if not iface or len(iface) <= 1:
                        continue
                    if any(_cw in iface.lower() for _cw in UI_CHROME_WORDS):
                        continue
                    parent_sw = _normalize_switch_name(str(r.get("parent", "")).strip())
                    if parent_sw not in sorted_switches:
                        parent_sw = sorted_switches[0]

                    ip_val = str(r.get("ip") or r.get("ip_address") or "").strip()
                    purpose_val = str(r.get("purpose", "")).strip()
                    speed_val = str(r.get("speed", "")).strip()

                    if iface.lower().startswith(uplink_pfx):
                        row_type = "Uplink"
                        _init_role_cfg = ((_std_rules.get("pattern_variables") or {})
                                          .get("uplink_role") or
                                          (_std_rules.get("pattern_variables") or {}).get("role") or {})
                        _init_role_opts = _init_role_cfg.get("options") if isinstance(_init_role_cfg.get("options"), list) else [
                            "Active Uplink", "Standby Uplink", "Active", "Standby", "Primary", "Backup", "None"
                        ]
                        role_val = _init_role_cfg.get("default", _init_role_opts[0] if _init_role_opts else "Active Uplink")
                    elif iface.lower().startswith(vmk_pfx):
                        row_type = "VMkernel"
                        role_val = "Management" if "mgmt" in purpose_val.lower() or "management" in purpose_val.lower() else "None"
                    else:
                        row_type = "Port Group"
                        role_val = "VM Traffic"

                    entity_key = (parent_sw, iface.lower())
                    if entity_key in seen_entity:
                        continue
                    seen_entity.add(entity_key)

                    records.append({
                        "switch": parent_sw,
                        "interface": iface,
                        "type": row_type,
                        "ip": ip_val,
                        "purpose": purpose_val,
                        "role": role_val,
                        "speed": speed_val
                    })

                # ── Pipe-Aware Service / Port Group & VMkernel IP Harvesting ──
                _plat_cfg_svc = (naming_rules.get("topology_parsing_presets") or {}).get(active_plat_display, {})
                if not isinstance(_plat_cfg_svc, dict):
                    _plat_cfg_svc = {}
                _service_delimiters = _plat_cfg_svc.get("service_delimiters")
                if not isinstance(_service_delimiters, list):
                    _service_delimiters = ["VLAN ID:", "VMkernel Ports", "Virtual Machines"]
                _teaming_rules = _plat_cfg_svc.get("teaming_rules") or {}
                if not isinstance(_teaming_rules, dict):
                    _teaming_rules = {}
                _no_uplink_label = str(_teaming_rules.get("no_uplink_display") or "No Uplink").strip()

                _service_map = {}
                _noise_clean_re = re.compile(r"^[\s>›»▶v⌄•·▪▫○●✔✖!–—\-\d/O]+?\s*Filters\b|^[\s>›»▶v⌄•·▪▫○●✔✖!–—-]+")
                _current_switch = clean_switches[0] if clean_switches else (_dynamic_switch_pfx + "0")
                _current_svc_label = None

                # Extra UI sidebar noise words to exclude when splitting by pipe
                _SIDEBAR_NOISE = UI_CHROME_WORDS | active_junk_filter | {
                    "storage", "storageadapters", "storagedevices", "hostcache configuration",
                    "protocol endpoints", "1/o filters", "i/o filters", "storageproviders",
                    "networking", "virtualswitches", "vmkerneladapters", "physical adapters",
                    "physicaladapters", "tcp/ip configuration", "virtual machines",
                    "vmstartup/shutdown", "agentvmsettings", "defaultvmcompatibility",
                    "swap file location", "system", "licensing", "host profile",
                    "time configuration", "authenticationservices", "certificate",
                    "power management", "advanced systemsettin...", "systemresourcereser...",
                    "firewall", "services", "securityprofile", "system swap", "packages",
                    "hardware", "actions", "add networking...", "refres", "refresh"
                }

                _ocr_lines = combined_raw_text.splitlines()
                for i, raw_line in enumerate(_ocr_lines):
                    if not raw_line.strip():
                        continue
                    segments = [s.strip() for s in raw_line.split("|") if s.strip()]

                    # 1. Check if any segment on this line is a Switch Header (only expanded switches, ignore collapsed '>')
                    for seg in segments:
                        if seg.startswith(">") or seg.startswith("›"):
                            continue
                        s_clean = re.sub(r"^[\s⌄v▼]+\s*", "", seg)
                        sw_m = SW_HEADER_RE.search(s_clean)
                        if sw_m:
                            raw_sw = ACTION_SPLIT_RE.split(sw_m.group(1).strip())[0].strip()
                            norm_sw = _normalize_switch_name(raw_sw)
                            if norm_sw and len(norm_sw) > 1 and norm_sw.lower() not in _SIDEBAR_NOISE:
                                _current_switch = norm_sw
                                if norm_sw not in clean_switches:
                                    clean_switches.append(norm_sw)
                            break

                    # 2. Check if the NEXT line contains any service delimiter (even after a pipe '|')
                    next_line_has_delim = False
                    if i + 1 < len(_ocr_lines):
                        next_segs = [ns.strip() for ns in _ocr_lines[i + 1].split("|") if ns.strip()]
                        for ns in next_segs:
                            ns_clean = re.sub(r"^[\s>›»▶v⌄]+\s*", "", ns)
                            if any(ns_clean.lower().startswith(d.lower()) for d in _service_delimiters):
                                next_line_has_delim = True
                                break

                    if next_line_has_delim:
                        # Find the valid service title segment on the current line (ignoring sidebar noise & PhysicalAdapters)
                        for seg in segments:
                            seg_clean = re.sub(r"^[\s>›»▶v⌄•·▪▫○●✔✖!–—-]+", "", seg).strip()
                            seg_clean = re.sub(r"\.{2,}$", "", seg_clean).strip()
                            low_seg = seg_clean.lower()
                            if (
                                not seg_clean
                                or len(seg_clean) <= 1
                                or low_seg in _SIDEBAR_NOISE
                                or any(low_seg.startswith(d.lower()) for d in _service_delimiters)
                                or SW_HEADER_RE.search(seg_clean)
                                or re.search(rf"\b{re.escape(uplink_pfx)}\d+\b", seg_clean, re.I)
                                or "physicaladapter" in low_seg.replace(" ", "")
                                or "no physical" in low_seg
                                or "no associated" in low_seg
                            ):
                                continue
                            # Fix common OCR casing typo 'iscSI' -> 'iSCSI'
                            if low_seg == "iscsi":
                                seg_clean = "iSCSI"
                            elif low_seg == "vmnetwork":
                                seg_clean = "VM Network"
                            _current_svc_label = seg_clean
                            _service_map.setdefault(seg_clean, {
                                "name": seg_clean,
                                "interface": "",
                                "ip": "",
                                "type": "Port Group",
                                "switch": _current_switch
                            })
                            discovered_services.add(seg_clean)

                    # 3. Check for VMkernel interface & IP on the current line: "vmk0 : 10.27.177.246"
                    vmk_m = re.search(rf"\b({re.escape(vmk_pfx)}\d+)\s*:\s*((?:\d{{1,3}}\.){{3}}\d{{1,3}})\b", raw_line, re.IGNORECASE)
                    if vmk_m:
                        vmk_iface = vmk_m.group(1).strip()
                        vmk_ip = vmk_m.group(2).strip()
                        if _current_svc_label and _current_svc_label in _service_map:
                            _service_map[_current_svc_label]["interface"] = vmk_iface
                            _service_map[_current_svc_label]["ip"] = vmk_ip
                            _service_map[_current_svc_label]["type"] = "VMkernel"
                            _service_map[_current_svc_label]["switch"] = _current_switch
                        _service_map[vmk_iface] = {
                            "name": _current_svc_label or vmk_iface,
                            "interface": vmk_iface,
                            "ip": vmk_ip,
                            "type": "VMkernel",
                            "switch": _current_switch
                        }
                        discovered_services.add(vmk_iface)

                st.session_state["discovered_switches"] = clean_switches
                st.session_state["discovered_service_map"] = _service_map
                st.session_state["discovered_service_candidates"] = sorted(discovered_services, key=_natsort_key)

                # Filter out empty/invalid service records before auto-populating
                records = [
                    r for r in records
                    if (r.get("type") == "Uplink" and r.get("interface"))
                    or (r.get("type") != "Uplink" and (r.get("purpose") or r.get("interface")))
                ]

                # Auto-populate discovered services into topology_records (avoiding duplicates)
                for s_label, s_entry in _service_map.items():
                    if s_label.lower().startswith(vmk_pfx.lower()) and s_entry.get("name") != s_label:
                        # Skip duplicate vmk key when already represented by its parent service label
                        continue
                    sw_target = s_entry.get("switch") or _current_switch
                    s_iface = s_entry.get("interface", "")
                    s_type = s_entry.get("type", "Port Group")
                    s_ip = s_entry.get("ip", "")
                    already_exists = False
                    for existing_r in records:
                        if existing_r.get("switch") == sw_target and existing_r.get("type") != "Uplink":
                            if (s_label and existing_r.get("purpose", "").lower() == s_label.lower()) or (s_iface and existing_r.get("interface", "").lower() == s_iface.lower()):
                                # Enrich existing record if missing purpose, interface, or IP
                                if not existing_r.get("purpose") and s_label:
                                    existing_r["purpose"] = s_label
                                if not existing_r.get("interface") and s_iface:
                                    existing_r["interface"] = s_iface
                                if not existing_r.get("ip") and s_ip:
                                    existing_r["ip"] = s_ip
                                if s_type == "VMkernel":
                                    existing_r["type"] = "VMkernel"
                                already_exists = True
                                break
                    if not already_exists:
                        records.append({
                            "switch": sw_target,
                            "interface": s_iface,
                            "type": s_type,
                            "ip": s_ip,
                            "purpose": s_label,
                            "role": "Management" if "mgmt" in s_label.lower() or "management" in s_label.lower() else "None",
                            "speed": ""
                        })

                st.session_state["topology_records"] = records
                st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                st.session_state["hypervisor_editor_version"] = st.session_state.get("hypervisor_editor_version", 0) + 1
                st.toast(f"Successfully analyzed {total_imgs} screenshots and merged {len(deduped)} unique records!", icon="🚀")
        except Exception as e:
            import traceback
            logger.error("Pipeline execution failed: %s", str(e))
            st.error(f"Pipeline execution failed: {str(e)}")
            st.code(traceback.format_exc(), language="text")

    # ── Synchronous State Commitment (v4.4.21) ──────────────────────────────
    # Populate switch_deck and chips_unassigned BEFORE the post-analyze
    # st.rerun() so that Section 2 renders with real data on the next pass.
    if start_analyze:
        _sc_rules = SSM.get_naming_rules(force_reload_naming_rules())
        _sc_plat = st.session_state.get("naming_target_platform", "VMware ESXi")
        _sc_combined = st.session_state.get("latest_ocr_raw_text", "")
        _sc_deduped = st.session_state.get("hypervisor_parsed_descriptions") or deduped if 'deduped' in dir() else []

        # Inline container-header discovery (avoid forward-reference to nested defs below)
        _builtin_container_headers = ["Switch:", "Bridge:", "Distributed Switch:"]
        _sv_presets = _sc_rules.get("topology_parsing_presets") or {}
        _sv_plat_data = _sv_presets.get(_sc_plat, {})
        if not isinstance(_sv_plat_data, dict):
            for _k, _v in _sv_presets.items():
                if isinstance(_k, str) and isinstance(_v, dict) and _k.strip().lower() == _sc_plat.strip().lower():
                    _sv_plat_data = _v
                    break
        _sv_headers = _builtin_container_headers
        if isinstance(_sv_plat_data, dict):
            _sp_cfg = _sv_plat_data.get("spatial_anchors_and_redaction", {})
            if isinstance(_sp_cfg, dict):
                _anchors = _sp_cfg.get("anchors", [])
                if isinstance(_anchors, list):
                    _h: List[str] = []
                    for _a in _anchors:
                        if isinstance(_a, dict) and _a.get("role") == "container_header":
                            _pats = _a.get("patterns", [])
                            if isinstance(_pats, list):
                                _h.extend(p for p in _pats if p)
                    if _h:
                        _sv_headers = _h

        _detected_switches = set()
        for _r in _sc_deduped:
            _p = _r.get("parent")
            if _p and str(_p).strip().lower() not in ("", "none", "null", "general"):
                _detected_switches.add(_normalize_switch_name(str(_p).strip()))

        # Scan OCR text for container headers (with pipe segment splitting)
        _seen_cn: set = set()
        for _line in _sc_combined.splitlines():
            if not _line.strip():
                continue
            _segments = _line.split("|") if "|" in _line else [_line]
            for _seg in _segments:
                _s_clean = _seg.strip()
                if not _s_clean:
                    continue
                _s_clean = re.sub(r"^[\s>›»▶v⌄]+\s*", "", _s_clean)
                _low = _s_clean.lower()
                if not any(_h.lower() in _low for _h in _sv_headers):
                    continue
                _m = SW_HEADER_RE.search(
                    _s_clean,
                )
                if _m:
                    _raw_name = _m.group(1).strip()
                    _raw_name = ACTION_SPLIT_RE.split(_raw_name)[0].strip()
                    _cn = _normalize_switch_name(_raw_name)
                    if _cn and _cn not in _seen_cn:
                        _seen_cn.add(_cn)
                        _detected_switches.add(_cn)

        _new_deck = {}
        for _s in sorted(_detected_switches, key=_natsort_key):
            _new_deck[_s] = {"name": _s, "port_groups": [], "uplinks": []}
        st.session_state["switch_deck"] = _new_deck

        _UI_CHROME = {
            "summary", "monitor", "configure", "permissions", "vms", "datastores",
            "networks", "updates", "storage", "actions", "add networking", "refresh",
            "edit", "manage physical adapters", "protocol endpoints", "system", "hardware",
            "no associated port groups", "no physical network adapters", "virtual switches",
        }

        def _is_valid_entity_token(_text: str) -> bool:
            _t = str(_text).strip()
            if not _t or len(_t) <= 1:
                return False
            _low = _t.lower()
            if "|" in _t or _low.startswith("--- screenshot"):
                return False
            if _low in ("false", "true", "null", "none", "is_uncertain"):
                return False
            if any(_chrome in _low for _chrome in _UI_CHROME):
                return False
            if re.match(r"^virtual\s*machines\s*\(\d+\)", _low):
                return False
            if re.match(r"^vmkernel\s*ports\s*\(\d+\)", _low):
                return False
            if re.match(r"^vlan\s*id\s*:\s*(?:--|\d+)", _low):
                return False
            return True

        _raw_chips = []
        _seen_texts = set()
        for _r2 in _sc_deduped:
            if not isinstance(_r2, dict):
                continue
            _iface = str(_r2.get("interface", "")).strip()
            if _iface and _is_valid_entity_token(_iface) and _iface.lower() not in _seen_texts:
                _seen_texts.add(_iface.lower())
                _raw_chips.append({"id": _iface, "text": _iface, "category": "interface", "speed": None})

            _ip_val = str(_r2.get("ip") or _r2.get("ip_address") or "").strip()
            if _ip_val and re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", _ip_val) and _ip_val.lower() not in _seen_texts:
                _seen_texts.add(_ip_val.lower())
                _raw_chips.append({"id": _ip_val, "text": _ip_val, "category": "interface", "speed": None})

            _purpose = str(_r2.get("purpose", "")).strip()
            if _purpose and _is_valid_entity_token(_purpose) and _purpose.lower() not in _seen_texts:
                _seen_texts.add(_purpose.lower())
                _raw_chips.append({"id": _purpose, "text": _purpose, "category": "purpose", "speed": None})

            _speed = str(_r2.get("speed", "")).strip()
            if _speed and re.search(r"\b\d+\s*(?:Gbit/s|Mbit/s|Full)\b", _speed, re.I) and _speed.lower() not in _seen_texts:
                _seen_texts.add(_speed.lower())
                _raw_chips.append({"id": _speed, "text": _speed, "category": "speed", "speed": None})

        st.session_state["chips_unassigned"] = _raw_chips
        st.session_state["_pool_initialized_v4"] = True

    # st.rerun() is placed OUTSIDE the try/except so that Streamlit's internal
    # RerunException (raised by st.rerun) cannot be caught by the bare
    # except Exception and silently swallowed, which would leave the page
    # rendering Section 3 before the session state has been persisted.
    if start_analyze and "hypervisor_parsed_descriptions" in st.session_state:
        st.rerun()

    # Load standards rules for dynamic role options (available to both cards and editor)
    _std_rules = SSM.get_naming_rules() or naming_rules

    if "editor_ver" not in st.session_state:
        st.session_state["editor_ver"] = 0

    records = st.session_state.get("topology_records", [])
    sw_options = st.session_state.get("discovered_switches", [])

    # Dynamic role options from Standards
    role_cfg = (_std_rules.get("pattern_variables") or {}).get("uplink_role") or (_std_rules.get("pattern_variables") or {}).get("role") or {}
    role_options = role_cfg.get("options") if isinstance(role_cfg.get("options"), list) else [
        "Active Uplink", "Standby Uplink", "Active", "Standby", "Primary", "Backup", "None"
    ]
    default_role = role_cfg.get("default", role_options[0] if role_options else "Active Uplink")

    # Clean Empty State
    if not sw_options and not records:
        st.info("ℹ️ No virtual switches discovered yet. Upload and analyze screenshots above, or add a switch manually below.")
        col_add_swn, col_add_swb = st.columns([4, 1])
        with col_add_swn:
            manual_sw_name = st.text_input("New Switch Name", placeholder=f"e.g. {_dynamic_switch_pfx}0, vmbr0", key="manual_add_sw_input", label_visibility="collapsed")
        with col_add_swb:
            if st.button("➕ Add Switch", key="manual_add_sw_btn", use_container_width=True):
                if manual_sw_name.strip():
                    st.session_state["discovered_switches"] = [manual_sw_name.strip()]
                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                    st.rerun()
    else:
        # ── Interactive Cards Overview ──
        st.markdown("#### 🔹 Topology Visual Overview & Card Controls")
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

                    assigned_uplink_indices = [i for i, r in enumerate(records) if r.get("switch") == sw and r.get("type") == "Uplink" and r.get("interface")]
                    assigned_service_indices = [i for i, r in enumerate(records) if r.get("switch") == sw and r.get("type") != "Uplink" and (r.get("purpose") or r.get("interface"))]

                    st.caption(f"Uplinks ({len(assigned_uplink_indices)}):")
                    if assigned_uplink_indices:
                        for u_idx in assigned_uplink_indices:
                            u_row = records[u_idx]
                            u_iface = u_row.get("interface", "").strip()
                            if not u_iface or _is_junk_token(u_iface, active_junk_filter) or re.search(r"\b\d+\s*(?:Gbit/s|Mbit/s|Kbit/s|Full|Half)\b", u_iface, re.I):
                                continue
                            u_role = u_row.get("role") or default_role

                            uc_label, uc_role, uc_del = st.columns([3, 3, 1])
                            with uc_label:
                                st.markdown(f"🟢 **{u_iface}**")
                            with uc_role:
                                cur_r_idx = role_options.index(u_role) if u_role in role_options else 0
                                sel_role = st.selectbox(
                                    "Role",
                                    options=role_options,
                                    index=cur_r_idx,
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
                            if not s_purpose and not s_iface:
                                continue
                            # VMkernel: display "purpose (vmk)" pairing
                            if s_row.get("type") == "VMkernel" and s_purpose and s_iface:
                                s_name = f"{s_purpose} ({s_iface})"
                            else:
                                s_name = s_purpose or s_iface

                            sc_label, sc_del = st.columns([5, 1])
                            with sc_label:
                                st.markdown(f"🟡 **{s_name}**")
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
                        with st.popover("➕ Uplink", use_container_width=True):
                            st.markdown("**Add Physical Uplink**")
                            _plat_teaming_cfg = ((naming_rules.get("topology_parsing_presets") or {})
                                                 .get(st.session_state.get("naming_target_platform", "VMware ESXi"), {}))
                            _no_uplink_display = str((_plat_teaming_cfg.get("teaming_rules") or {}).get("no_uplink_display") or "No Uplink").strip()
                            up_choice_options = ["➕ Custom / Manual Entry", _no_uplink_display] + [c for c in uplink_candidates if not any(r.get("interface") == c and r.get("switch") == sw for r in records)]
                            selected_up_choice = st.selectbox(
                                "Select Detected Adapter or Enter New",
                                options=up_choice_options,
                                key=f"pop_up_sel_{idx}_{sw}"
                            )
                            if selected_up_choice == "➕ Custom / Manual Entry":
                                final_up_name = st.text_input(
                                    "Adapter Name",
                                    placeholder=f"e.g. {_dynamic_uplink_pfx}0, {_dynamic_uplink_pfx}4",
                                    key=f"pop_up_custom_{idx}_{sw}"
                                ).strip()
                            else:
                                final_up_name = selected_up_choice.strip()
                                st.caption(f"Selected: `{final_up_name}`")

                            new_up_role = st.selectbox("Initial Role", options=role_options, index=0, key=f"pop_up_role_{idx}_{sw}")
                            new_up_speed = st.selectbox("Speed (optional)", options=["", "10000 Full", "1000 Full", "25000 Full"], index=0, key=f"pop_up_spd_{idx}_{sw}")

                            if st.button("Add to Switch", key=f"pop_up_confirm_{idx}_{sw}", use_container_width=True):
                                _is_no_uplink = (selected_up_choice == _no_uplink_display) or (not final_up_name and new_up_role.lower() == "none")
                                if _is_no_uplink:
                                    records.append({
                                        "switch": sw,
                                        "interface": _no_uplink_display,
                                        "type": "Uplink",
                                        "ip": "",
                                        "purpose": "",
                                        "role": "None",
                                        "speed": ""
                                    })
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()
                                elif not final_up_name or not final_up_name.strip():
                                    st.warning("Please enter an adapter name. If switch has no adapters, no uplink is needed.")
                                else:
                                    records.append({
                                        "switch": sw,
                                        "interface": final_up_name.strip(),
                                        "type": "Uplink",
                                        "ip": "",
                                        "purpose": "",
                                        "role": new_up_role,
                                        "speed": new_up_speed
                                    })
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()

                    with btn_col2:
                        with st.popover("➕ Service", use_container_width=True):
                            st.markdown("**Add Port Group / VMkernel**")
                            _disc_svc_map = st.session_state.get("discovered_service_map") or {}
                            if not isinstance(_disc_svc_map, dict):
                                _disc_svc_map = {}
                            svc_choice_options = ["➕ Custom / Manual Entry"] + [c for c in service_candidates if not any((r.get("purpose") == c or r.get("interface") == c) and r.get("switch") == sw for r in records)]
                            selected_svc_choice = st.selectbox(
                                "Select Detected Service or Enter New",
                                options=svc_choice_options,
                                key=f"pop_svc_sel_{idx}_{sw}"
                            )
                            _svc_choice_slug = re.sub(r"[^a-zA-Z0-9_]", "", selected_svc_choice)

                            _autofill = _disc_svc_map.get(selected_svc_choice) or {} if selected_svc_choice != "➕ Custom / Manual Entry" else {}
                            if not isinstance(_autofill, dict):
                                _autofill = {}
                            _init_name = str(_autofill.get("name") or selected_svc_choice).strip()
                            _init_ip = str(_autofill.get("ip") or "").strip()
                            _is_vm = str(_autofill.get("type") or "").lower() == "vmkernel" or _init_name.lower().startswith(_dynamic_vmk_pfx.lower())

                            final_svc_name = st.text_input(
                                "Service / Port Group Name",
                                value=_init_name,
                                placeholder=f"e.g. Management Network, {_dynamic_vmk_pfx}0",
                                key=f"pop_svc_name_{idx}_{sw}_{_svc_choice_slug}"
                            ).strip()

                            new_svc_type = st.selectbox(
                                "Type",
                                options=["Port Group", "VMkernel"],
                                index=1 if _is_vm else 0,
                                key=f"pop_svc_type_{idx}_{sw}_{_svc_choice_slug}"
                            )

                            new_svc_ip = st.text_input(
                                "IP Address (optional)",
                                value=_init_ip,
                                placeholder="e.g. 10.0.0.1/24",
                                key=f"pop_svc_ip_{idx}_{sw}_{_svc_choice_slug}"
                            ).strip()

                            if st.button("Add to Switch", key=f"pop_svc_confirm_{idx}_{sw}", use_container_width=True):
                                effective_name = final_svc_name or (selected_svc_choice if selected_svc_choice != "➕ Custom / Manual Entry" else "")
                                if not effective_name:
                                    st.warning("Please enter or select a Service / Port Group name.")
                                else:
                                    _matched_vmk = str(_autofill.get("interface") or (effective_name if new_svc_type == "VMkernel" else "")).strip()
                                    records.append({
                                        "switch": sw,
                                        "interface": _matched_vmk,
                                        "type": new_svc_type,
                                        "ip": new_svc_ip,
                                        "purpose": effective_name,
                                        "role": "Management" if new_svc_type == "VMkernel" else "None",
                                        "speed": ""
                                    })
                                    st.session_state["topology_records"] = records
                                    st.session_state["editor_ver"] = st.session_state.get("editor_ver", 0) + 1
                                    st.rerun()

    st.markdown("#### 🎛️ Topology Data Grid")
    st.caption("Edit switch assignments, interface roles, and IPs directly. Changes update NetBox descriptions instantly.")

    df = pd.DataFrame(records if records else [], columns=["switch", "interface", "type", "ip", "purpose", "role", "speed"])

    column_config = {
        "switch": st.column_config.SelectboxColumn("Switch Container", options=sw_options if sw_options else [_dynamic_switch_pfx + "0"], required=False),
        "interface": st.column_config.TextColumn("Interface / Port", required=False),
        "type": st.column_config.SelectboxColumn("Type", options=["Uplink", "VMkernel", "Port Group"], required=False),
        "ip": st.column_config.TextColumn("IPv4 Address", required=False),
        "purpose": st.column_config.TextColumn("Service / Purpose", required=False),
        "role": st.column_config.SelectboxColumn("Role", options=role_options, required=False),
        "speed": st.column_config.TextColumn("Link Speed", required=False),
    }

    editor_key = f"topology_data_editor_{st.session_state.get('editor_ver', 0)}"
    edited_df = st.data_editor(
        df,
        column_config=column_config,
        num_rows="dynamic",
        use_container_width=True,
        key=editor_key
    )
    st.session_state["topology_records"] = edited_df.to_dict(orient="records")

    # ── Zone 3: Live NetBox Output ────────────────────────────────────────────
    st.divider()
    st.markdown("##### 3️⃣ Live NetBox Output & Copy")

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
        _plat_key = st.session_state.get("naming_target_platform", "VMware ESXi")
        _active_hyp_presets = _hyp_presets_map.get(_plat_key, [])

        _out_lines = ["# NOTE:\n# Generated from Hybrid Topology Architecture v4.4.37.\n"]
        for _gparent in sorted(_groups.keys(), key=_natsort_key):
            _out_lines.append(f"=== {_gparent} ===")
            _sw_uplinks = [r for r in _groups[_gparent] if r.get("type") == "Uplink" and r.get("interface")]
            _sw_services = [r for r in _groups[_gparent] if r.get("type") != "Uplink" and (r.get("interface") or r.get("purpose"))]
            _sw_vmks = [r for r in _groups[_gparent] if r.get("type") == "VMkernel"]

            # Dynamic teaming clause
            teaming_str = _build_dynamic_teaming_clause(_sw_uplinks)

            # Legacy active/standby lists for backward-compatible template tokens
            _active_nics = [u.get("interface") for u in _sw_uplinks if "active" in str(u.get("role", "")).lower()]
            _standby_nics = [u.get("interface") for u in _sw_uplinks if "standby" in str(u.get("role", "")).lower()]

            # ── Render Port Group descriptions via dynamic templates ──────────
            _pg_templates = [p for p in _active_hyp_presets if p.get("code") in ("PortGroup", "PortGroup_VLAN", "PG_Header") and not p.get("hidden", False)]
            if not _pg_templates:
                # Fallback to generic patterns from naming_patterns
                _pg_templates = []
                for _pk in ("esxi_portgroup", "esxinet_portgroup", "esxi_vswitch"):
                    _pat = _naming_pats.get(_pk)
                    if _pat:
                        _pg_templates.append({"pattern": _pat})

            if _pg_templates and _sw_services:
                for _svc in _sw_services:
                    _svc_name = str(_svc.get("purpose", "")).strip() or str(_svc.get("interface", "")).strip()
                    if not _svc_name:
                        continue
                    row_vals = {
                        "v_switch": _gparent,
                        "active_vmnics": ", ".join(_active_nics) if _active_nics else "",
                        "standby_vmnics": ", ".join(_standby_nics) if _standby_nics else "",
                        "teaming": teaming_str,
                        "uplink_teaming": teaming_str,
                        "pg_network": _svc_name,
                        "port_group": _svc_name,
                    }
                    best_tpl = _select_best_template(row_vals, _pg_templates)
                    rendered = render_dynamic_pattern(best_tpl, row_vals, _pattern_vars)
                    rendered = re.sub(r"\s*/\s*Standby", "", rendered)
                    rendered = re.sub(r"\s*/\s*Active", "", rendered)
                    rendered = re.sub(r"Active\s*/\s*\)", "Active)", rendered)
                    rendered = re.sub(r"\(\s*/\s*\)", "", rendered)
                    rendered = re.sub(r"\(\s*\)", "", rendered)
                    rendered = re.sub(r"\s{2,}", " ", rendered).strip()
                    if rendered:
                        _out_lines.append(rendered)
            elif _sw_services:
                # Plain fallback without templates
                for _svc in _sw_services:
                    _svc_name = str(_svc.get("purpose", "")).strip() or str(_svc.get("interface", "")).strip()
                    if not _svc_name:
                        continue
                    _line = _gparent
                    if teaming_str:
                        _line += f" ({teaming_str})"
                    _line += f" - {_svc_name}"
                    _out_lines.append(_line)

            # Uplink descriptions
            if _sw_uplinks:
                _uplink_tpls = [p for p in _active_hyp_presets if p.get("code") == "Uplink" and not p.get("hidden", False)]
                if not _uplink_tpls:
                    for _pk in ("esxi_uplink", "esxinet_uplink"):
                        _pat = _naming_pats.get(_pk)
                        if _pat:
                            _uplink_tpls.append({"pattern": _pat})
                            break
                for _ug in _sw_uplinks:
                    _ui = str(_ug.get("interface", "")).strip()
                    if not _ui:
                        continue
                    _upurpose = str(_ug.get("purpose", "")).strip()
                    _urole = str(_ug.get("role", "")).strip()
                    _std_rules_zone3 = SSM.get_naming_rules() or naming_rules
                    _tpp_z3 = (_std_rules_zone3.get("topology_parsing_presets") or {}).get(st.session_state.get("naming_target_platform", "VMware ESXi"), {})
                    _teaming_z3 = (_tpp_z3 or {}).get("teaming_rules") or {}
                    if not isinstance(_teaming_z3, dict):
                        _teaming_z3 = {}
                    _empty_role_tok_z3 = str(_teaming_z3.get("empty_role_token")
                        or ((_std_rules_zone3.get("pattern_variables") or {}).get("uplink_role") or {}).get("empty_role_token")
                        or "None").strip().lower()
                    if _uplink_tpls:
                        _row = {
                            "vmnic": _ui,
                            "v_switch": _gparent,
                            "purpose": _upurpose,
                            "uplink_role": _urole,
                        }
                        _tpl = _select_best_template(_row, _uplink_tpls)
                        _line = render_dynamic_pattern(_tpl, _row, _pattern_vars)
                        _line = re.sub(r"\s+/Uplink\b", "", _line, flags=re.IGNORECASE)
                        _line = re.sub(rf"(?i)\s+{re.escape(_empty_role_tok_z3)}\b", "", _line)
                        _line = re.sub(r"\s{2,}", " ", _line).strip()
                        if _line:
                            _out_lines.append(_line)
                    else:
                        _line = f"{_ui} - {_gparent}"
                        if _upurpose:
                            _line += f" {_upurpose}"
                        if _urole:
                            _tpp_z3 = (_std_rules_zone3.get("topology_parsing_presets") or {}).get(st.session_state.get("naming_target_platform", "VMware ESXi"), {})
                            _teaming_z3 = (_tpp_z3 or {}).get("teaming_rules") or {}
                            if not isinstance(_teaming_z3, dict):
                                _teaming_z3 = {}
                            _mapping_z3 = _teaming_z3.get("uplink_to_teaming_mapping") or {}
                            _display_role = _mapping_z3.get(_urole, _urole)
                            if _display_role and _display_role.lower() not in (_empty_role_tok_z3, ""):
                                _line += f" {_display_role}"
                        _out_lines.append(_line)

            # VMkernel descriptions
            if _sw_vmks:
                for _vk in _sw_vmks:
                    _vk_iface = str(_vk.get("interface", "")).strip()
                    _vk_purpose = str(_vk.get("purpose", "")).strip()
                    _label = _vk_purpose or _vk_iface
                    if _label:
                        _out_lines.append(f"{_label} ({_gparent})")

            # If switch has no services and no physical uplinks, emit the switch header with teaming clause
            if not _sw_services and not _sw_uplinks:
                if teaming_str:
                    _out_lines.append(f"{_gparent} ({teaming_str})")

        _output_bulk_text = "\n".join(_out_lines).strip()

    st.markdown("**NetBox Description Output**")
    st.code(_output_bulk_text, language="text")

    col_copy, col_dl = st.columns([2, 1])
    with col_copy:
        if st.button("📋 Copy All Descriptions", key="copy_all_descriptions", width="stretch"):
            st.session_state["_last_copied"] = _output_bulk_text
            st.toast("Copied to clipboard!", icon="📋")
    with col_dl:
        st.download_button(
            "📥 Download (.txt)",
            _output_bulk_text.encode("utf-8"),
            file_name="netbox-descriptions.txt",
            mime="text/plain",
            key="dl_esxi_descriptions_pipe",
        )


def _render_chip_card(
    chip: dict,
    is_assigned: bool = False,
    switch_id: str = "",
    slot_type: str = "",
):
    """Render a single clickable chip with inline correction and assignment dropdown."""
    import streamlit as st
    text = chip.get("text", "")
    chip_id = chip.get("id", "")
    category = chip.get("category", "")

    cat_colors = {
        "parent": "#3b82f6",
        "interface": "#10b981",
        "purpose": "#f59e0b",
        "speed": "#8b5cf6",
    }
    cat_color = cat_colors.get(category, "#64748b")

    st.markdown(
        f'<div style="display:inline-block;padding:4px 10px;border-radius:12px;'
        f'background:{cat_color}22;border:1px solid {cat_color};'
        f'color:{cat_color};font-size:0.82rem;font-weight:500;'
        f'margin:2px 2px 6px 2px;cursor:text;" '
        f'title="Edit to correct OCR errors">{_esc_html(text)}</div>',
        unsafe_allow_html=True,
    )

    if is_assigned:
        if st.button("✖ Return to Pool", key=f"chip_rm_{chip_id}_{switch_id}_{slot_type}", help="Return this chip to the unassigned pool", type="secondary", width="stretch"):
            _return_chip_to_pool(chip_id, switch_id, slot_type)
    else:
        raw_val = st.text_input(
            label="Chip",
            value=text,
            key=f"chip_edit_{chip_id}",
            label_visibility="collapsed",
            help=f"Category: {category}",
        )
        edited = (raw_val or "").strip()
        if edited and edited != text:
            st.session_state.setdefault("chips_session_corrections", {})
            st.session_state["chips_session_corrections"][text] = edited
            _deck = st.session_state.get("switch_deck", {})
            _norm_text = _normalize_switch_name(text)
            _norm_edited = _normalize_switch_name(edited)
            _found_in_deck = text in _deck or _norm_text in _deck
            if category == "parent" and _norm_edited and _found_in_deck:
                _rename_switch_container(text, edited)
            else:
                st.session_state["chips_unassigned"] = [
                    c if c["id"] != chip_id else {**c, "text": edited}
                    for c in st.session_state.get("chips_unassigned", [])
                ]
                if switch_id and slot_type:
                    deck = st.session_state.get("switch_deck", {})
                    if switch_id in deck:
                        slot_key = "port_groups" if slot_type == "service" else "uplinks"
                        deck[switch_id][slot_key] = [
                            c if c["id"] != chip_id else {**c, "text": edited}
                            for c in deck[switch_id][slot_key]
                        ]
                        st.session_state["switch_deck"] = deck
                st.rerun()

        deck = st.session_state.get("switch_deck", {})
        sw_keys = sorted(deck.keys(), key=_natsort_key) if deck else []

        if not is_assigned:
            if category in ("interface", "purpose", "speed"):
                assign_options = ["-- Assign to Switch --"] + sw_keys
                target_sw = st.selectbox(
                    "Assign to Switch",
                    options=assign_options,
                    key=f"sel_assign_{chip_id}",
                    label_visibility="collapsed",
                    help="Assign this chip to a switch container below"
                )
                if target_sw != "-- Assign to Switch --":
                    _ch = dict(chip)
                    _ch["text"] = edited if edited else text
                    _assign_chip(_ch, target_sw, category)
                    st.rerun()

            if category != "parent":
                if st.button("Promote to Container", key=f"chip_prom_{chip_id}", help="Convert token into a new Switch Container", use_container_width=True):
                    _promote_chip_to_container(chip)


def _esc_html(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _deck_switch_name(deck: dict, sid: str) -> str:
    sd = deck.get(sid, {})
    return str(sd.get("name", sid))


def _return_chip_to_pool(chip_id: str, switch_id: str, slot_type: str):
    import streamlit as st
    deck = st.session_state.get("switch_deck", {})
    if switch_id not in deck:
        return
    slot_key = "port_groups" if slot_type == "service" else "uplinks"
    slot_list = deck[switch_id].get(slot_key, [])
    for i, c in enumerate(slot_list):
        if c.get("id") == chip_id:
            chip = dict(c)
            chip["category"] = slot_type  # service or interface
            chip["text"] = _apply_corrections_from_state(chip.get("text", ""))
            pool = st.session_state.get("chips_unassigned", [])
            existing_ids = {pc["id"] for pc in pool}
            if chip["id"] not in existing_ids:
                pool.append(chip)
            st.session_state["chips_unassigned"] = pool
            slot_list.pop(i)
            st.session_state["switch_deck"] = deck
            st.toast(f"Returned to pool: {chip.get('text', '')}", icon="↩️")
            st.rerun()
            return


def _apply_corrections_from_state(text: str) -> str:
    corrections = st.session_state.get("chips_session_corrections", {})
    return corrections.get(text, text)


def _promote_chip_to_container(chip: dict):
    """Turn an unassigned brick into a new Baseplate (Container) in the deck."""
    import streamlit as st
    text = chip.get("text", "")
    if not text:
        return
    deck = st.session_state.get("switch_deck", {})
    sid = _normalize_switch_name(text)
    if sid not in deck:
        deck[sid] = {"name": sid, "port_groups": [], "uplinks": []}
        st.session_state["switch_deck"] = deck
    # Remove from pool
    pool = st.session_state.get("chips_unassigned", [])
    st.session_state["chips_unassigned"] = [c for c in pool if c["id"] != chip["id"]]
    st.toast(f"Promoted '{text}' to container.", icon="⬆️")
    st.rerun()


def _rename_switch_container(old_name: str, new_name: str) -> None:
    """Globally rename a switch container key, merge duplicates, and sync chips.

    Normalizes both names (strip whitespace, 'o'→'0', drop 'manual:' prefix).
    - If ``new_name`` already exists as a deck key, merge all port_groups/uplinks
      from ``old_name`` into it and remove the old key.
    - Otherwise just rename the deck key (preserving contents) and update the
      stored ``name`` field so the header reflects the new label.
    - Unassigned parent-category chips whose text matched ``old_name`` are
      re-keyed to ``new_name`` (first occurrence wins via dedup).
    """
    import streamlit as st
    deck = st.session_state.get("switch_deck", {})
    pool = st.session_state.get("chips_unassigned", [])
    corrections = st.session_state.setdefault("chips_session_corrections", {})

    old_norm = _normalize_switch_name(old_name)
    new_norm = _normalize_switch_name(new_name)

    # If the old normalized name isn't directly in the deck, try to find it by value match.
    if old_norm not in deck:
        for k in list(deck.keys()):
            if _normalize_switch_name(k) == old_norm:
                old_norm = k
                break

    if old_norm not in deck:
        return

    old_data = deck[old_norm]
    has_existing_new = new_norm in deck and new_norm != old_norm

    if has_existing_new:
        # Merge: absorb old into existing new
        existing_new = deck[new_norm]
        for slot_key in ("port_groups", "uplinks"):
            existing_list = existing_new.get(slot_key, [])
            old_list = old_data.get(slot_key, [])
            existing_ids = {c["id"] for c in existing_list}
            for c in old_list:
                if c["id"] not in existing_ids:
                    existing_list.append(c)
            existing_new[slot_key] = existing_list
        deck.pop(old_norm)
    else:
        # Pure rename — move the key
        deck[new_norm] = old_data
        if old_norm in deck:
            del deck[old_norm]
        # Update the stored name so the header reflects the new label
        deck[new_norm]["name"] = new_norm

    st.session_state["switch_deck"] = deck

    # Sync corrections map for chips whose text was old_name
    if old_name in corrections:
        corrections[new_name] = corrections.pop(old_name)
    if old_norm in corrections:
        corrections[new_norm] = corrections.pop(old_norm)

    # Re-key pool chips whose id/text matched old_name
    new_pool = []
    for c in pool:
        c_id = c.get("id", "")
        if c.get("category") in ("parent", "container") and (c_id == old_norm or _normalize_switch_name(c_id) == old_norm):
            c = {**c, "id": new_norm, "text": new_norm}
        new_pool.append(c)
    st.session_state["chips_unassigned"] = new_pool

    # Deduplicate pool by text
    seen = set()
    deduped = []
    for c in new_pool:
        if c["text"] in seen:
            continue
        seen.add(c["text"])
        deduped.append(c)
    st.session_state["chips_unassigned"] = deduped

    st.toast(f"Renamed container: {old_norm} → {new_norm}", icon="✏️")
    st.rerun()


def _assign_chip(chip: dict, switch_id: str, category: str):
    import streamlit as st
    deck = st.session_state.get("switch_deck", {})
    if switch_id not in deck:
        return
    slot_key = "port_groups" if category == "purpose" else "uplinks"
    chip_resolved = dict(chip)
    chip_resolved["text"] = _apply_corrections_from_state(chip_resolved.get("text", ""))
    # Remove from pool
    pool = st.session_state.get("chips_unassigned", [])
    st.session_state["chips_unassigned"] = [
        c for c in pool if c["id"] != chip["id"]
    ]
    # Add to deck slot
    deck[switch_id][slot_key].append(chip_resolved)
    st.session_state["switch_deck"] = deck
    st.toast(f"Assigned '{chip_resolved['text']}' to {_deck_switch_name(deck, switch_id)}", icon="✅")
    st.rerun()


def _render_switch_card(
    switch_id: str,
    switch_data: dict,
    pool: list,
    iface_chips: list,
    service_chips: list,
    speed_chips: list,
):
    import streamlit as st
    name = switch_data.get("name", switch_id)
    port_groups = switch_data.get("port_groups", [])
    uplinks = switch_data.get("uplinks", [])

    st.markdown(f"### 🔹 {name}")
    st.caption(f"Container ID: `{switch_id}`")

    col_pg, col_up = st.columns(2)

    with col_pg:
        st.markdown("**Services / Port Groups**")
        if port_groups:
            for idx, pg in enumerate(port_groups):
                _render_assigned_chip_in_card(pg, switch_id, "service", idx)
        else:
            st.caption("No services assigned. Click a service chip from the pool above.")

    with col_up:
        st.markdown("**Uplinks / Adapters**")
        if uplinks:
            for idx, up in enumerate(uplinks):
                _render_assigned_chip_in_card(up, switch_id, "interface", idx)
        else:
            st.caption("No uplinks assigned. Click an interface chip from the pool above.")

    # Switch-level actions
    col_rename, col_remove = st.columns(2)
    with col_rename:
        new_name = st.text_input(
            "Rename",
            value=name,
            key=f"sw_rename_{switch_id}",
            label_visibility="collapsed",
            help="Rename this container",
        )
        if new_name.strip() and new_name.strip() != name:
            _rename_switch_container(switch_id, new_name.strip())
    with col_remove:
        if st.button("Remove Container", key=f"sw_remove_{switch_id}", type="secondary", help="Remove this container and return chips to pool"):
            _remove_switch(switch_id)


def _render_assigned_chip_in_card(
    chip: dict,
    switch_id: str,
    slot_type: str,
    index: int,
):
    import streamlit as st
    text = chip.get("text", "")
    chip_id = chip.get("id", "")
    role = chip.get("role", "")

    c1, c2, c3 = st.columns([4, 1, 1])
    with c1:
        st.markdown(
            f'<div style="display:inline-block;padding:3px 8px;border-radius:10px;'
            f'background:#f1f5f9;border:1px solid #94a3b8;'
            f'color:#334155;font-size:0.78rem;margin:1px;">'
            f'{_esc_html(text)}'
            f'</div>',
            unsafe_allow_html=True,
        )
    with c2:
        if slot_type == "interface":
            _chip_std = SSM.get_naming_rules() or {}
            _chip_role_cfg = ((_chip_std.get("pattern_variables") or {})
                              .get("uplink_role") or
                              (_chip_std.get("pattern_variables") or {}).get("role") or {})
            _chip_role_opts = _chip_role_cfg.get("options") if isinstance(_chip_role_cfg.get("options"), list) else [
                "Active Uplink", "Standby Uplink", "Active", "Standby", "Primary", "Backup", "None"
            ]
            _chip_default = _chip_role_cfg.get("default", _chip_role_opts[0] if _chip_role_opts else "Active Uplink")
            _current_role = role or _chip_default
            _role_index = 0
            if _current_role in _chip_role_opts:
                _role_index = _chip_role_opts.index(_current_role)
            role_opt = st.selectbox(
                "Role",
                options=_chip_role_opts,
                index=_role_index,
                key=f"sw_{switch_id}_{slot_type}_{index}_role",
                label_visibility="collapsed",
            )
            deck = st.session_state.get("switch_deck", {})
            if switch_id in deck:
                slot_key = "uplinks"
                if deck[switch_id][slot_key][index].get("role") != role_opt:
                    deck[switch_id][slot_key][index]["role"] = role_opt
                    st.session_state["switch_deck"] = deck
    with c3:
        if st.button("✖", key=f"sw_{switch_id}_{slot_type}_{index}_rm", help="Return to pool", type="secondary"):
            _return_chip_to_pool(chip_id, switch_id, slot_type)


def _remove_switch(switch_id: str):
    import streamlit as st
    deck = st.session_state.get("switch_deck", {})
    if switch_id not in deck:
        return
    sw = deck.pop(switch_id)
    st.session_state["switch_deck"] = deck
    # Return all chips to pool
    pool = list(st.session_state.get("chips_unassigned", []))
    existing_ids = {c["id"] for c in pool}
    for slot_key in ("port_groups", "uplinks"):
        for c in sw.get(slot_key, []):
            if c["id"] not in existing_ids:
                pool.append(c)
    st.session_state["chips_unassigned"] = pool
    st.toast(f"Removed container '{sw.get('name', switch_id)}'. Chips returned to pool.", icon="🗑️")
    st.rerun()
