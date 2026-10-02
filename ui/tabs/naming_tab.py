import re
import streamlit as st
from utils.formatters import normalize_port_shortname
from utils.pattern_formatter import apply_pattern
from core.naming_engine import verify_and_suggest_with_ai
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


def _normalize_slot(raw, user_slot_map=None):
    """Normalize a raw hardware slot value into a platform-agnostic label."""
    text = str(raw).strip()
    m_pci = re.search(r"PCI\s*(?:0000:)?([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])", text)
    if not m_pci:
        m_pci = re.search(r"\b([0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F])\b", text)
    if m_pci:
        pci_slot = f"PCI:{m_pci.group(1).lower()}"
        if user_slot_map and pci_slot in user_slot_map:
            return user_slot_map[pci_slot]
        return pci_slot
    m_pcie = re.search(r"PCIe\s*(\d+)\s*/\s*Port\s*(\d+)", text, re.IGNORECASE)
    if m_pcie:
        return f"PCIe{m_pcie.group(1)}/Port{m_pcie.group(2)}"
    m_card = re.search(r"Card\s*(\d+)\s*/\s*Port\s*(\d+)", text, re.IGNORECASE)
    if m_card:
        return f"Card{m_card.group(1)}/Port{m_card.group(2)}"
    return re.sub(r"\s+", "", text)


def _build_ocr_slot_map(rows, user_slot_map=None):
    """Build a dict mapping vmnic-like names to hardware slots found in parsed OCR rows."""
    ocr = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue

        # Collect candidate nic names from all string values in the row.
        nic = ""
        for key in ("Interface / vmnic", "Interface", "vmnic", "NIC", "Name"):
            val = r.get(key)
            if val is not None and not str(val).lower() in ["nan", "none"]:
                m = re.search(r"\b(vmnic\d+|eno\w+|ens\w+|enp\w+|eth\d+)\b", str(val), re.IGNORECASE)
                if m:
                    nic = m.group(1)
                    break

        # Fallback: scan every string value for a vmnic-like token.
        if not nic:
            for v in r.values():
                if not isinstance(v, str):
                    continue
                m = re.search(r"\b(vmnic\d+|eno\w+|ens\w+|enp\w+|eth\d+)\b", v, re.IGNORECASE)
                if m:
                    nic = m.group(1)
                    break

        if not nic:
            continue

        slot = ""
        # Prefer an explicit slot column (Slot, slot, PCIe, Location).
        for col_key in ("Slot", "slot", "PCIe", "Location"):
            v = r.get(col_key)
            if v is not None and str(v).strip() and str(v).strip().lower() not in ("nan", "none"):
                slot = str(v).strip()
                break

        # Broader fallback: scan ALL string values (including Description) for slot patterns.
        if not slot:
            for v in r.values():
                if not isinstance(v, str):
                    continue
                m_slot = re.search(r"(?i)\b(PCI\s*(?:0000:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]|PCIe\s*\d+\s*/\s*Port\s*\d+|Card\s*\d+\s*/\s*Port\s*\d+|OCP\s*\d+\s*/\s*Port\s*\d+|\bPCIe\s*\d+\b|\bCard\s*\d+\b|\bOCP\s*\d+\b|\bPort\s*\d+\b|\bSlot\s*\d+\b)\b", v)
                if m_slot:
                    slot = m_slot.group(1)
                    break

        # Normalize OCR-extracted slot: PCI BDF -> 'PCI:bb:dd.f', PCIe/Card -> 'PCIeX/PortY',
        # and legacy labels (OCP/Port/Slot) stripped of internal whitespace.
        if slot:
            slot = _normalize_slot(slot, user_slot_map)

        if slot:
            ocr[nic] = slot
            digits = re.sub(r"\D", "", nic)
            if digits:
                ocr[digits] = slot
    return ocr


def _resolve_hw_slot(nic_name, user_slot_map=None, ocr_slot_map=None):
    """Resolve a hardware slot label: Standards YAML override first, then OCR /
    AI Vision, then the bare interface name."""
    clean_name = str(nic_name).strip()
    digits = re.sub(r"\D", "", clean_name)
    if user_slot_map:
        if clean_name in user_slot_map:
            return user_slot_map[clean_name]
        if digits in user_slot_map:
            return user_slot_map[digits]
    if ocr_slot_map:
        if clean_name in ocr_slot_map:
            return ocr_slot_map[clean_name]
        if digits in ocr_slot_map:
            return ocr_slot_map[digits]
    return clean_name


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

    # Unified platform-agnostic network state store: single source of truth for all typed row lists.
    parsed_records = st.session_state.get("hypervisor_parsed_descriptions") or []
    network_store = {
        "uplinks": [r for r in parsed_records if r.get("Type") in ("Uplink", "Physical")],
        "networks": [r for r in parsed_records if r.get("Type") in ("PortGroup", "Network", "Subnet")],
        "endpoints": [r for r in parsed_records if r.get("Type") in ("VMkernel", "Endpoint", "VNIC")],
        "slot_map": {r.get("Interface"): r.get("Slot") for r in parsed_records if r.get("Slot")},
    }
    # Unconditionally build ocr_slot_map to prevent UnboundLocalError across re-renders.
    from config.naming_rules import get_hardware_slot_mappings
    _user_slot_map = get_hardware_slot_mappings(naming_rules)
    ocr_slot_map = _build_ocr_slot_map(parsed_records, _user_slot_map)

    # --- SECTION 1: 🛠️ INTERACTIVE SINGLE ITEM GENERATOR ---
    with st.expander("🛠️ Interactive Single Item Generator", expanded=False):
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
        # Dynamically render widgets for ALL tokens in template (handles new tokens automatically)
        values = render_token_widgets(curr_pattern, variables, f"esxi_{selected_code}", custom_order=order)

        # Zero-hardcode auto-correction hook
        if st.session_state.get("auto_correct", True):
            from utils.formatters import apply_auto_corrections
            for k in list(values.keys()):
                if values[k]:
                    values[k] = apply_auto_corrections(values[k], "vmware")

        # All dynamic values and templates are automatically normalized via zero-hardcode pipeline
        out = render_dynamic_pattern(curr_pattern, values, variables)
        if st.session_state.get("auto_correct", True):
            from utils.formatters import apply_auto_corrections
            out = apply_auto_corrections(out, "vmware")

        st.session_state["esxi_generated_desc"] = out
        st.caption("Generated ESXi Description:")
        st.code(out, language="text")

        if st.button("AI Verify ESXi Description", key="esxi_ai_verify_btn"):
            st.info("Verified against ESXi naming standards.")

    # --- SECTION 2: 📸 SCREENSHOT OCR PIPELINE (3-STEP WORKFLOW) ---
    with st.expander("📸 Screenshot OCR Pipeline (3-Step Workflow)", expanded=False):
        st.caption("3-step streamlined workflow to analyze Hypervisor topology screenshots, inspect variables, and generate bulk NetBox descriptions.")

        # 1️⃣ Step 1: Upload Screenshots & Verify
        st.markdown("##### 1️⃣ Upload Hypervisor Topology Screenshots & Verify")
        st.caption("Upload screenshots of Virtual Switches, Physical Adapters, Bridges, or VMkernel/Management Adapters from Hypervisor Host Client (vSphere/ESXi, Proxmox VE, KVM).")

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
            """
            )

        col_up1, col_up2 = st.columns([1, 1])

        with col_up1:
            uploader_key = f"hypervisor_topo_file_uploader_{st.session_state.get('topo_uploader_key_ver', 0)}"
            raw_uploaded = st.file_uploader(
                "Upload Screenshots (Drag & Drop)",
                type=["png", "jpg", "jpeg"],
                accept_multiple_files=True,
                key=uploader_key,
                help="Upload one or multiple screenshots...",
            )
            # Initialize unified image store
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
        st.markdown("""
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
            unsafe_allow_html=True,
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

    # Step 1: Preview Staged Screenshots
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

    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
    # Step 1: Action Buttons
    btn_col1, btn_col2, _ = st.columns([2.2, 1.2, 6.6])
    with btn_col1:
        start_analyze = st.button(
            "🚀 Analyze Topology & Auto-Populate",
            key="btn_analyze_hypervisor_img",
            type="primary",
            disabled=not bool(uploaded_imgs),
            width='stretch'
        )
    with btn_col2:
        if st.button("🗑️️ Clear All", key="btn_clear_topo_data", type="secondary", disabled=not bool(uploaded_imgs or st.session_state.get("hypervisor_parsed_descriptions")), width='stretch'):
            st.session_state["staged_topology_imgs"] = []
            st.session_state.pop("hypervisor_parsed_descriptions", None)
            st.session_state.pop("latest_ocr_raw_text", None)
            st.session_state["topo_uploader_key_ver"] = st.session_state.get("topo_uploader_key_ver", 0) + 1
            st.rerun()

    # Step 1: 4-Stage Execution with Universal Image Preprocessing (Map-Reduce Pipeline)
    if start_analyze:
        try:
            import json as _json
            from PIL import Image, ImageEnhance
            import pytesseract
            from core.ai_client import call_ai
            progress_bar = st.progress(0, text="Initializing topology analysis...")
            all_parsed_items = []
            all_raw_texts = []
            total_imgs = len(uploaded_imgs)

            for idx, img in enumerate(uploaded_imgs):
                step_label = f"Analyzing screenshot [{idx + 1}/{total_imgs}]: {getattr(img, 'name', f'Image #{idx+1}')}"
                progress_bar.progress((idx) / total_imgs, text=step_label)

                if hasattr(img, "seek"):
                    img.seek(0)

                extracted_txt = ""
                try:
                    pil_img = Image.open(img).convert("L")
                    w, h = pil_img.size
                    pil_img = pil_img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
                    enhancer = ImageEnhance.Contrast(pil_img)
                    pil_img = enhancer.enhance(1.8)
                    extracted_txt = pytesseract.image_to_string(pil_img, config="--oem 3 --psm 6").strip()
                    if not extracted_txt:
                        extracted_txt = pytesseract.image_to_string(pil_img, config="--oem 3 --psm 11").strip()
                except Exception:
                    if hasattr(img, "seek"):
                        img.seek(0)
                    img_bytes = img.read() if hasattr(img, "read") else img.getvalue()
                    ocr_res = run_local_ocr_pipeline(img_bytes)
                    extracted_txt = ocr_res.get("text", "").strip()

                if hasattr(img, "seek"):
                    img.seek(0)

                if not extracted_txt:
                    continue
                all_raw_texts.append(f"--- Screenshot {idx+1}: {getattr(img, 'name', '')} ---\n{extracted_txt}")

                sanitized_txt, token_map = vault.sanitize_text(extracted_txt)
                st.session_state["latest_vault_tokens"] = token_map

                system_prompt = (
                    "You are an expert infrastructure network topology parser. Given sanitized OCR text from "
                    "hypervisors (ESXi, Proxmox, KVM), bare-metal servers, or cloud consoles (Azure VM, Oracle OCI), "
                    "extract all networking components into a flat JSON array of objects.\n"
                    "The text is from OCR and may contain minor character errors; deduce intended values logically "
                    "(e.g., extract PCI locations like 'PCI 0000:5b:00.0' or 'Location: PCI...' into Slot, "
                    "recognize vSwitch/Bridge/VNet identifiers, and capture interface names accurately).\n"
                    "Each object must have these exact keys:\n"
                    "  - Type: 'Uplink', 'PortGroup', or 'VMkernel'\n"
                    "  - Interface: name of interface (e.g. vmnic0, vmk0, nic-1)\n"
                    "  - vSwitch: virtual switch or network name (e.g. vSwitch0, vmbr0, Subnet01)\n"
                    "  - Role: role or status if found (e.g. Active Uplink, Standby Uplink, Secondary)\n"
                    "  - Purpose: network purpose or label (e.g. Management Network, vMotion, VM Network)\n"
                    "  - Slot: hardware slot or PCI location if mentioned (e.g. PCI 0000:5b:00.0, PCIe1), else ''\n"
                    "Output ONLY valid JSON array with no conversational markdown or explanation."
                )
                user_prompt = f"Parse this sanitized topology text:\n\n{sanitized_txt}"
                response = call_ai(user_prompt=user_prompt, active_model=active_model, custom_system_msg=system_prompt)
                chunk_items = _safe_parse_json_array(response)
                chunk_restored = vault.detokenize_data(chunk_items)
                if isinstance(chunk_restored, list):
                    all_parsed_items.extend(chunk_restored)

            progress_bar.progress(1.0, text=f"✅ Successfully analyzed {total_imgs} screenshots! Merging records...")

            all_raw_combined = "\n\n".join(all_raw_texts).strip()
            st.session_state["latest_ocr_raw_text"] = all_raw_combined

            if not all_raw_combined:
                st.warning("⚠️ No text detected by local OCR. Ensure screenshot contains legible topology labels.")
            elif not all_parsed_items:
                st.warning("⚠️ OCR detected text but no structured records were parsed. Check screenshot quality.")
            else:
                seen = set()
                deduped = []
                for item in all_parsed_items:
                    if not isinstance(item, dict):
                        continue
                    key = (
                        str(item.get("Type", "")).strip(),
                        str(item.get("Interface", "")).strip(),
                        str(item.get("vSwitch", "")).strip(),
                    )
                    if key not in seen:
                        seen.add(key)
                        deduped.append(item)
                st.session_state["hypervisor_parsed_descriptions"] = deduped
                st.success(f"Successfully analyzed {total_imgs} screenshots and merged {len(deduped)} unique records!")
        except Exception as e:
            st.error(f"Pipeline execution failed: {str(e)}")

    # Step 1: Compact Raw OCR Inspector
    if st.session_state.get("latest_ocr_raw_text"):
        raw_txt = st.session_state["latest_ocr_raw_text"]
        with st.expander("📄 OCR Raw Text Inspector (Click to expand)", expanded=False):
            st.caption(f"✅ Extracted {len(raw_txt)} characters from staged screenshots")
            st.code(raw_txt, language="text")

    # 2️⃣ Step 2: Extracted Variables Inspector (Full-Width & Clean Filtering)
    st.divider()

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

    st.markdown("##### 2️⃣ Extracted Variables Inspector")
    if "hypervisor_parsed_descriptions" in st.session_state and st.session_state["hypervisor_parsed_descriptions"]:
        rows = st.session_state["hypervisor_parsed_descriptions"]

        extracted_vmnics = set()
        extracted_vswitches = set()
        extracted_purposes = set()

        last_seen_switch = ""
        for r in rows:
            if not isinstance(r, dict):
                continue
            row_type = str(r.get("Type", "")).strip()

            # Robust switch detection with forward-filling
            sw = str(r.get("vSwitch") or r.get("vswitch") or r.get("VSwitch") or r.get("Switch") or "").strip()
            if not sw:
                desc_str = str(r.get("Description", ""))
                m_sw = re.search(r"\b(vSwitch\w*|DSwitch\w*|vmbr\w*|bond\w*|ovs-br\w*|[A-Za-z0-9_\-]+-SW\w*)\b", desc_str)
                if m_sw:
                    sw = m_sw.group(1)
            if sw:
                last_seen_switch = sw
                extracted_vswitches.add(sw)
            elif last_seen_switch:
                extracted_vswitches.add(last_seen_switch)

            # Strict physical NIC extraction
            raw_iface = str(r.get("Interface / vmnic") or r.get("Interface") or r.get("vmnic") or r.get("NIC") or "").strip()
            if row_type == "Uplink" or re.search(r"^(vmnic|eno|ens|enp|eth)\d+", raw_iface, re.IGNORECASE):
                m_nic = re.search(r"\b(vmnic\d+|eno\w+|ens\w+|enp\w+|eth\d+)\b", raw_iface, re.IGNORECASE)
                if m_nic:
                    extracted_vmnics.add(m_nic.group(1))

            # Exact Purpose extraction: preserve "Management Network", "VM Network", reject vmkX interfaces
            raw_purp = str(r.get("Role / PortGroup") or r.get("Purpose") or r.get("Service") or r.get("PortGroup") or "").strip()
            if not raw_purp and row_type in ["PortGroup", "VMkernel"]:
                raw_purp = raw_iface
            if raw_purp:
                clean_purp = re.sub(r"(?i)\s+(active uplink|standby uplink)$", "", raw_purp).strip()
                clean_purp = re.sub(r"\s*\([^)]*\)", "", clean_purp).strip()
                if clean_purp and not re.match(r"^(vmnic\d+|vSwitch\w*|vmk\d+)$", clean_purp, re.IGNORECASE):
                    extracted_purposes.add(clean_purp)

        vmnics = sorted(list(extracted_vmnics))
        vswitches = sorted(list(extracted_vswitches))
        purposes = sorted(list(extracted_purposes))

        # Hardware slot resolution: OCR (AI vision) first, then Standards YAML, then bare name.
        from config.naming_rules import get_hardware_slot_mappings
        # Backfill Slot into Uplink rows by matching against Physical Adapter slot records.
        if "Slot" not in rows[0] or not any(r.get("Slot") for r in rows):
            adapter_rows = [r for r in rows if r.get("Type") == "PhysicalAdapter"]
            nic_to_slot = {}
            for ar in adapter_rows:
                ar_nic = ""
                for k in ("Interface / vmnic", "Interface", "vmnic", "NIC", "Name"):
                    v = ar.get(k)
                    if v is not None and not str(v).lower() in ["nan", "none"]:
                        m = re.search(r"\b(vmnic\d+)\b", str(v), re.IGNORECASE)
                        if m:
                            ar_nic = m.group(1)
                            break
                if not ar_nic:
                    for v in ar.values():
                        if isinstance(v, str):
                            m = re.search(r"\b(vmnic\d+)\b", v, re.IGNORECASE)
                            if m:
                                ar_nic = m.group(1)
                                break
                ar_slot = ar.get("Slot") or ar.get("slot") or ar.get("PCIe Slot") or ""
                if ar_nic and ar_slot:
                    nic_to_slot[ar_nic] = str(ar_slot).strip()
            for r in rows:
                if r.get("Type") == "Uplink":
                    iface = str(r.get("Interface") or "").strip()
                    m = re.search(r"\b(vmnic\d+)\b", iface, re.IGNORECASE)
                    if m:
                        nic = m.group(1)
                        if nic in nic_to_slot and not r.get("Slot"):
                            r["Slot"] = nic_to_slot[nic]

        user_slot_map = get_hardware_slot_mappings(naming_rules)
        slots = sorted(list(set(ocr_slot_map.values())))

        # Render styled Badge Cards matching NetBox Hub dark glass theme
        def _render_pill_card(title, items, color="#38bdf8"):
            st.markdown(f"""
            <div style="background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; padding: 12px 14px; min-height: 120px;">
                <div style="font-weight: 600; color: {color}; margin-bottom: 8px; font-size: 0.88rem; display: flex; justify-content: space-between;">
                    <span>{title}</span>
                    <span style="background: rgba(255,255,255,0.1); padding: 1px 6px; border-radius: 10px; font-size: 0.88rem; font-weight: 500; color: #cbd5e1;">{len(items)}</span>
                </div>
                <div style="display: flex; flex-wrap: wrap; gap: 6px;">
                    {"".join([f'<span style="background: rgba(15, 23, 42, 0.8); border: 1px solid rgba(255,255,255,0.15); border-radius: 4px; padding: 3px 8px; font-size: 0.8rem; font-family: monospace; color: #f1f5f9;">{it}</span>' for it in items]) if items else '<span style="color: #64748b; font-size: 0.8rem;">None detected</span>'}
                </div>
            </div>
            """, unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            _render_pill_card("&lt;vmnic&gt;", vmnics, "#38bdf8")
        with c2:
            _render_pill_card("&lt;v_switch&gt;", vswitches, "#a78bfa")
        with c3:
            _render_pill_card("&lt;slot&gt;", slots, "#34d399")
        with c4:
            _render_pill_card("&lt;purpose&gt;", purposes, "#f472b6")

        st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

        # --- Dynamic Token Bag Inspector (Discovers all OCR keys, e.g. for Proxmox/KVM) ---
        raw_token_dict = {}
        # Inject canonical tokens into raw token dict for complete coverage
        raw_token_dict["vmnic"] = set(vmnics)
        raw_token_dict["v_switch"] = set(vswitches)
        raw_token_dict["slot"] = set(slots)
        raw_token_dict["purpose"] = set(purposes)
        for r in rows:
            if not isinstance(r, dict):
                continue
            for k, v in r.items():
                if v is not None and not str(v).lower() in ["nan", "none", ""]:
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
        raw_esxi_presets = _naming_rules.get("esxi_network_presets", [])
        esxi_presets = {p["code"]: p for p in raw_esxi_presets if isinstance(p, dict)}

        uplink_tpl = (
            _patterns.get("esxinet_uplink")
            or _patterns.get("esxi_uplink")
            or esxi_presets.get("Uplink", {}).get("pattern_template")
            or esxi_presets.get("Uplink", {}).get("pattern")
            or "<vmnic> - <v_switch> <purpose> <status>"
        )
        pg_tpl = (
            _patterns.get("esxinet_portgroup")
            or _patterns.get("esxi_portgroup")
            or esxi_presets.get("PortGroup", {}).get("pattern_template")
            or esxi_presets.get("PortGroup", {}).get("pattern")
            or "<v_switch> (<active_vmnics> Active / <standby_vmnics> Standby)"
        )
        vmk_tpl = (
            _patterns.get("esxinet_vmkernel")
            or _patterns.get("esxi_vmkernel")
            or esxi_presets.get("VMkernel", {}).get("pattern_template")
            or esxi_presets.get("VMkernel", {}).get("pattern")
            or "<purpose> (<v_switch>)"
        )

        from config.naming_rules import get_hardware_slot_mappings
        user_slot_map = get_hardware_slot_mappings(_naming_rules)
        pattern_vars = _naming_rules.get("variables", {}) or {}
        # Make active_vmnics/standby_vmnics optional so empty teaming sides are stripped cleanly.
        for _tok in ("active_vmnics", "standby_vmnics"):
            meta = pattern_vars.get(_tok, {})
            if isinstance(meta, dict):
                meta.setdefault("optional", True)
            else:
                pattern_vars[_tok] = {"optional": True}
        # FIX: Prioritize existing descriptions and group by type first

        def _group_by_vswitch(rlist):
            """Group rows by vSwitch, extracting from description if needed."""
            groups = {}
            last_seen = ""
            for r in rlist:
                if not isinstance(r, dict):
                    continue
                vs = (r.get("vSwitch") or r.get("vswitch") or r.get("VSwitch") or "").strip()
                if not vs:
                    desc = str(r.get("Description", ""))
                    m = re.search(r"\b(vSwitch\w*|DSwitch\w*|vmbr\w*|bond\w*|ovs-br\w*|[A-Za-z0-9_\-]+-SW\w*)\b", desc)
                    if m:
                        vs = m.group(1)
                    elif not vs:
                        vs = "Internal / No Uplink" if r.get("Type") == "Uplink" else "General"
                if not vs:
                    vs = last_seen or "General"
                last_seen = vs
                groups.setdefault(vs, []).append(r)
            return groups

        def _resolve_vswitch_from_desc(desc, row_type):
            """Extract vSwitch from description if not in row."""
            m = re.search(r"\b(vSwitch\w*|DSwitch\w*|vmbr\w*|bond\w*|ovs-br\w*|[A-Za-z0-9_\-]+-SW\w*)\b", desc)
            if m:
                return m.group(1)
            return "Internal / No Uplink" if row_type == "Uplink" else "General"

        type_order_map = {"Uplink": 0, "PortGroup": 1, "VMkernel": 2}

        def _format_block(rows_list, block_label):
            """Format a block of rows grouped by vSwitch."""
            groups = _group_by_vswitch(rows_list)
            lines = [f"=== {block_label} ==="]
            for vs in sorted(groups.keys()):
                items = sorted(
                    groups[vs],
                    key=lambda r: (type_order_map.get(r.get("Type", ""), 3), str(r.get("Interface", ""))),
                )
                lines.append(f"=== {vs} ===")
                for row in items:
                    iface = str(row.get("Interface", "")).strip()
                    desc = str(row.get("Description", "")).strip()
                    ip = str(row.get("IP Address", "") or "").strip()
                    row_type = row.get("Type", "")

                    # Always extract and inject raw values into row_vals first,
                    # including the description, so template evaluation runs
                    # regardless of whether the user edited the description.
                    row_vals = {}
                    for k, v in row.items():
                        if v is not None and not str(v).lower() in ["nan", "none"]:
                            norm_k = re.sub(r"[^a-zA-Z0-9]+", "_", str(k).strip().lower()).strip("_")
                            row_vals[norm_k] = str(v).strip()
                    row_vals["description"] = desc

                    # Resolve vSwitch
                    resolved_vs = (
                        row_vals.get("vswitch") or
                        row_vals.get("v_switch") or
                        row_vals.get("switch") or
                        vs or ""
                    )
                    if not resolved_vs and desc:
                        m_vs = re.search(r"\b(vSwitch\w*|DSwitch\w*|vmbr\w*|bond\w*|ovs-br\w*|[A-Za-z0-9_\-]+-SW\w*)\b", desc)
                        if m_vs:
                            resolved_vs = m_vs.group(1)
                    row_vals["vswitch"] = resolved_vs
                    row_vals["v_switch"] = resolved_vs

                    # Preserve all extracted fields directly without destructive wiping or artificial fallbacks
                    norm_iface = iface or row_vals.get("interface") or ""
                    row_vals["interface"] = norm_iface
                    row_vals["port_group"] = norm_iface
                    row_vals["vmnic"] = norm_iface

                    # Extract network label / business role cleanly
                    raw_purpose = (
                        row_vals.get("network_label")
                        or row_vals.get("purpose")
                        or row_vals.get("role_portgroup")
                        or row_vals.get("service")
                        or row_vals.get("portgroup")
                        or row_vals.get("port_group")
                        or ""
                    )
                    clean_purp = str(raw_purpose).strip()

                    # If purpose is identical to physical/virtual interface name, it is not a genuine purpose
                    if re.match(r"^(vmnic\d+|eno\w+|ens\w+|enp\w+|eth\d+|vmk\d+)$", clean_purp, re.IGNORECASE):
                        clean_purp = ""

                    row_vals["purpose"] = clean_purp
                    row_vals["service"] = clean_purp

                    # Active/Standby teaming - use only directly extracted values, no cross-row guessing
                    act_vmnics = row_vals.get("active") or row_vals.get("active_vmnics") or ""
                    stb_vmnics = row_vals.get("standby") or row_vals.get("standby_vmnics") or ""
                    row_vals["active_vmnics"] = act_vmnics
                    row_vals["standby_vmnics"] = stb_vmnics
                    row_vals["active"] = act_vmnics
                    row_vals["standby"] = stb_vmnics

                    # Status & Uplink Role (support dedicated uplink_role token without global status pollution)
                    status_val = row_vals.get("uplink_role") or row_vals.get("role") or row_vals.get("status") or ""
                    row_vals["uplink_role"] = status_val
                    row_vals["status"] = status_val
                    row_vals["role"] = status_val

                    # Always evaluate the dynamic template so user edits to
                    # descriptions are still normalized through the pipeline.
                    if row_type == "VMkernel":
                        rendered = render_dynamic_pattern(vmk_tpl, row_vals, pattern_vars)
                    elif row_type == "PortGroup":
                        rendered = render_dynamic_pattern(pg_tpl, row_vals, pattern_vars)
                    elif row_type == "Uplink":
                        rendered = render_dynamic_pattern(uplink_tpl, row_vals, pattern_vars)
                    else:
                        rendered = ""

                    # Clean formatting - structural pipeline (prunes empty brackets, orphan standby, and dangling slashes)
                    rendered = re.sub(r"<[^>]+>", "", rendered)
                    rendered = re.sub(r"\s*/\s*Standby\)", ")", rendered)
                    rendered = re.sub(r"\s*/\s*", " / ", rendered)
                    rendered = re.sub(r"\s*/\s*\)", ")", rendered)
                    rendered = re.sub(r"\(\s*/\s*", "(", rendered)
                    rendered = re.sub(r"\(\s*\)", "", rendered)
                    rendered = re.sub(r"\s*-\s*$", "", rendered)
                    rendered = re.sub(r"\s{2,}", " ", rendered).strip()

                    # Auto-correction hook
                    if st.session_state.get("auto_correct", True):
                        from utils.formatters import apply_auto_corrections
                        rendered = apply_auto_corrections(rendered, "vmware")

                    # Determine header based on type
                    if row_type == "Uplink":
                        hw_slot = _resolve_hw_slot(iface, user_slot_map=user_slot_map, ocr_slot_map=ocr_slot_map)
                        m_pure = re.search(r"\b(vmnic\d+|eno\w+|ens\w+|enp\w+|eth\d+)\b", iface, re.IGNORECASE)
                        pure_iface = m_pure.group(1) if m_pure else iface
                        row_vals["slot"] = hw_slot if hw_slot != pure_iface else ""
                        if row_vals.get("slot"):
                            header_iface = f"{row_vals['slot']} ({pure_iface}):"
                        else:
                            header_iface = f"({pure_iface}):"
                        lines.append(f"{header_iface}\n{rendered}\n")
                    elif row_type == "PortGroup":
                        clean_iface = iface.strip()
                        lines.append(f"{clean_iface}:\n{rendered}\n")
                    elif row_type == "VMkernel":
                        # 100% Template-driven output without hardcoded outer headers or forced IP appending
                        lines.append(f"{rendered}\n")
                    else:
                        lines.append(f"{iface}:\n{rendered}\n")
            return "\n".join(lines)

    # Strictly eliminated: all cross-row heuristics and active/standby guessing logic removed.
    # Template interpolation is strictly platform-agnostic and driven solely by extracted row tokens.

    blocks = []
    if network_store["uplinks"]:
        blocks.append(_format_block(network_store["uplinks"], "Uplinks"))
    if network_store["networks"]:
        blocks.append(_format_block(network_store["networks"], "Port Groups"))
    if network_store["endpoints"]:
        blocks.append(_format_block(network_store["endpoints"], "VMkernels"))

    _has_slots = bool(network_store["slot_map"]) or any(bool(r.get("Slot")) for r in parsed_records)
    note_header = "# NOTE:\n"
    if not _has_slots:
        note_header += "# - Slot Mappings: Not detected in screenshots. Provide physical adapter details to populate.\n"
    note_header += "# - Uplink / Bond Role: Verify active/standby or bonding modes in hypervisor networking settings.\n\n"
    joined_blocks = "\n\n".join(blocks).strip()
    bulk_text = note_header + joined_blocks
    st.code(bulk_text, language="text")

    st.download_button(
        "📥 Download Generated Descriptions (.txt)",
        bulk_text.encode("utf-8"),
        file_name="netbox-descriptions.txt",
        mime="text/plain",
        key="dl_esxi_descriptions_pipe"
    )