import io
import re
import ipaddress
from datetime import datetime
from typing import Dict, List
import streamlit as st
import pandas as pd
import openpyxl
from config.naming_rules import load_naming_rules, get_vlan_presets, get_vlan_description_mappings, get_ipam_role_mappings
from core.ipam_engine import (
    compute_chained_rows,
    slugify,
    evaluate_subnet_row,
    calculate_remaining_subnets,
    get_subnet_availability_analysis,
    calculate_ip_range_str,
    format_branch_display,
    lookup_role_description,
    resolve_pattern_template,
    resolve_vlan_description,
    resolve_vlan_name,
    normalize_role_name,
    generate_netbox_site_csv,
    generate_netbox_vlan_group_csv,
    generate_netbox_vlans_csv,
    generate_netbox_prefixes_csv
)
from core.db_manager import (get_all_site_names,
    save_ipam_records_batch,
    save_sites_batch,
    clear_ipam_records,
    clear_sites_records,
    clear_vlans_records,
    clear_prefixes_records,
    get_total_ipam_count,
    get_total_sites_count,
    get_total_vlans_count,
    get_total_prefixes_count,
    get_max_scope_id,
    lookup_scope_id,
    lookup_site_supernet_from_db,
    get_existing_prefix_strings,
    get_sync_metadata,
    get_site_summary,
    get_ipam_records_by_site,
    get_full_site_inventory_summary
)
from core.shared_backup_state import SharedBackupState
from utils.formatters import to_title_case_preserve_acronyms
from ui.components import render_ai_chat

_last_site_lookup = {}  # tracks (site_name, scope_id, supernet) to avoid redundant DB queries


def handle_site_change():
    """Triggered on site name input change: looks up Scope ID & Supernet once."""
    entered_site = st.session_state.get("ipam_site_in", "").strip()
    cached = _last_site_lookup.get("site", "")
    if entered_site == cached:
        return  # no change since last lookup, skip
    _last_site_lookup["site"] = entered_site

    if entered_site:
        matched_scope = lookup_scope_id(entered_site)
        matched_super = lookup_site_supernet_from_db(entered_site)
        if matched_scope is not None:
            st.session_state["ipam_scope_in"] = str(matched_scope)
        if matched_super is not None:
            st.session_state["ipam_super_in"] = str(matched_super)
        _last_site_lookup["scope"] = matched_scope
        _last_site_lookup["supernet"] = matched_super


def handle_ipam_file_upload():
    """Universal file upload handler using dynamic schema classification."""
    uploader_key_val = st.session_state.get("uploader_key", 0)
    uploaded_files = st.session_state.get(f"ipam_multi_uploader_{uploader_key_val}")
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
            model_breakdown = ", ".join([
                f"{count} {model.split('.')[-1]}"
                for model, count in sorted(results['by_model'].items())
            ])
            
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
            
            uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            source_files = ", ".join([f.name for f in uploaded_files])
            for model_key, count in results['by_model'].items():
                endpoint = model_key.replace('.', '/')
                SharedBackupState.add_csv_override(endpoint, count, source_files, uploaded_at)
            
            st.session_state["uploader_key"] = st.session_state.get("uploader_key", 0) + 1
        elif not results['errors']:
            st.session_state["uploader_key"] = st.session_state.get("uploader_key", 0) + 1
            
    except Exception as e:
        st.error(f"❌ Upload failed: {str(e)}")
        if "schema registry" in str(e).lower() or "classify" in str(e).lower():
            st.info(
                "💡 **Tip:** Upload a NetBox backup JSON first to initialize the schema registry, "
                "then CSV files will be automatically classified and routed."
            )


def handle_ipam_db_reset():
    clear_ipam_records()
    
    from core.shared_backup_state import SharedBackupState
    SharedBackupState.clear_csv_only()
    
    if "ipam_persisted_rows" in st.session_state:
        del st.session_state["ipam_persisted_rows"]
    if "ipam_scope_in" in st.session_state:
        del st.session_state["ipam_scope_in"]
    if "ipam_site_in" in st.session_state:
        del st.session_state["ipam_site_in"]
    if "ipam_super_in" in st.session_state:
        del st.session_state["ipam_super_in"]
    st.toast("🗑️ Database Cleared. Restored default templates.", icon="🧹")


def build_vlan_description(site_name: str, role: str, vlan_id, vlan_name: str = "", vlan_presets=None, vlan_desc_mappings=None, role_mappings=None) -> str:
    clean_role = str(role or "").strip()
    if vlan_presets is None:
        vlan_presets = get_vlan_presets(load_naming_rules())
    return resolve_vlan_description(vlan_id, clean_role, vlan_presets, vlan_desc_mappings, role_mappings)


def load_ipam_records_from_db(site_name: str) -> bool:
    """Load IPAM allocation rows for a site straight from the local database.

    Returns True when records were found and loaded into the editor.
    """
    site_name = (site_name or "").strip()
    records = get_ipam_records_by_site(site_name) if site_name else []

    if "ipam_data_editor_live" in st.session_state:
        del st.session_state["ipam_data_editor_live"]

    st.session_state["ipam_loaded_site"] = site_name

    if not records:
        st.session_state["ipam_persisted_rows"] = []
        st.session_state["ipam_loaded_from_db"] = False
        st.session_state["ipam_site_found_in_db"] = False
        return False

    st.session_state["ipam_site_found_in_db"] = True

    rules = load_naming_rules()
    dynamic_presets = get_vlan_presets(rules)
    db_vids = {r.get('vlan_id') for r in records if r.get('vlan_id') is not None}

    best_group = None
    best_score = -1
    for grp_name, grp_items in dynamic_presets.items():
        preset_vids = {p['vid'] for p in grp_items.get("items", []) if 'vid' in p}
        score = len(db_vids.intersection(preset_vids))
        if score > best_score:
            best_score = score
            best_group = grp_items

    target_preset = best_group or {"items": [], "vlan_name_pattern": "<role>", "prefix_pattern": ""}
    order_map = {p['vid']: i for i, p in enumerate(target_preset.get("items", []))}

    def sort_key(r):
        vlan_id = r.get('vlan_id')
        if vlan_id is None:
            return (999, 9999)
        return (order_map.get(vlan_id, 999), vlan_id)

    records.sort(key=sort_key)

    def _clean(value) -> str:
        return str(value or "").strip()

    merged: Dict[int, Dict[str, str]] = {}
    order: List[int] = []
    for r in records:
        vid = r.get("vlan_id")
        if vid is None:
            continue

        if vid not in merged:
            merged[vid] = {
                "role": "",
                "vlan_name": "",
                "vlan_desc": "",
                "prefix_desc": "",
                "subnet": "",
            }
            order.append(vid)

        entry = merged[vid]
        rec_type = _clean(r.get("record_type")).lower()
        desc = _clean(r.get("description"))

        if not entry["role"]:
            entry["role"] = _clean(r.get("role"))
        if not entry["vlan_name"]:
            entry["vlan_name"] = _clean(r.get("vlan_name"))
        if not entry["subnet"]:
            entry["subnet"] = _clean(r.get("prefix_or_subnet"))

        if rec_type == "vlan":
            if desc and not entry["vlan_desc"]:
                entry["vlan_desc"] = desc
        elif rec_type == "prefix":
            if desc and not entry["prefix_desc"]:
                entry["prefix_desc"] = desc
        else:
            if desc and not entry["vlan_desc"]:
                entry["vlan_desc"] = desc
            if desc and not entry["prefix_desc"]:
                entry["prefix_desc"] = desc

    new_rows = []
    g_vname_pat = target_preset.get("vlan_name_pattern", "<role>")
    g_prefix_pat = target_preset.get("prefix_pattern", "")
    for vid in order:
        entry = merged[vid]
        new_rows.append({
            "VLAN ID": vid,
            "Role": entry["role"],
            "VLAN Name": entry["vlan_name"],
            "VLAN Description": entry["vlan_desc"] or entry["prefix_desc"],
            "Subnet (CIDR)": entry["subnet"],
            "_vlan_desc_manual": True,
            "_db_prefix_desc": entry["prefix_desc"] or entry["vlan_desc"],
            "_db_vlan_desc": entry["vlan_desc"] or entry["prefix_desc"],
            "_name_pattern": g_vname_pat,
            "_group_vlan_name_pattern": g_vname_pat,
            "_group_prefix_pattern": g_prefix_pat,
        })

    st.session_state["ipam_persisted_rows"] = new_rows
    st.session_state["ipam_loaded_from_db"] = True
    return True


def on_preset_change():
    selected = st.session_state.get("ipam_preset_selector")
    site_name = st.session_state.get("ipam_site_in", "").strip()

    st.session_state["ipam_loaded_from_db"] = False

    if selected == "🗄️ Load From DB (Existing Site)":
        load_ipam_records_from_db(site_name)
        return

    st.session_state["ipam_loaded_site"] = None
    rules = load_naming_rules()
    dynamic_presets = get_vlan_presets(rules)
    vlan_desc_mappings = get_vlan_description_mappings(rules)
    role_rules = get_ipam_role_mappings(rules)
    template_list = dynamic_presets.get(selected, {})

    if "ipam_data_editor_live" in st.session_state:
        del st.session_state["ipam_data_editor_live"]

    if not template_list or not template_list.get("items"):
        st.session_state["ipam_persisted_rows"] = []
    else:
        site_display = format_branch_display(site_name)
        group_vname_pattern = template_list.get("vlan_name_pattern", "<role>")
        group_prefix_pattern = template_list.get("prefix_pattern", "")
        new_rows = []
        for t in template_list.get("items", []):
            role_name = t["role"]
            vid = t["vid"]
            vlan_name = resolve_vlan_name(site_display, vid, role_name, group_vname_pattern)
            desc = resolve_vlan_description(vid, role_name, dynamic_presets, vlan_desc_mappings, role_rules)
            prefix_desc = resolve_pattern_template(group_prefix_pattern, site_display, vid, role_name, vlan_name) if group_prefix_pattern else ""
            new_rows.append({
                "VLAN ID": vid,
                "Role": role_name,
                "VLAN Name": vlan_name,
                "VLAN Description": desc,
                "_group_vlan_name_pattern": group_vname_pattern,
                "_group_prefix_pattern": group_prefix_pattern,
                "_pattern_template": group_prefix_pattern,
                "_name_pattern": group_vname_pattern,
                "Subnet (CIDR)": ""
            })
        st.session_state["ipam_persisted_rows"] = new_rows


def build_ipam_system_prompt(prompt, site_name, supernet_in, existing_prefixes, max_scope_id):
    """Build the grounded IPAM system prompt for the AI Assistant."""
    from core.ai_helper import build_comprehensive_ipam_context

    comprehensive_context = build_comprehensive_ipam_context(prompt, site_filter=site_name)

    ui_prefixes = []
    for r in st.session_state.get("ipam_persisted_rows", []):
        sub = str(r.get("Subnet (CIDR)", "") or "").strip()
        if sub and "/" in sub:
            ui_prefixes.append(sub)

    combined_prefixes = list(dict.fromkeys(existing_prefixes + ui_prefixes))

    mask_match = re.search(r"/(\d{1,2})", prompt)
    req_mask = int(mask_match.group(1)) if mask_match else 24

    target_networks = []
    cidr_matches = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b", prompt)
    if supernet_in:
        cidr_matches.append(supernet_in)

    for match in cidr_matches:
        try:
            target_networks.append(ipaddress.ip_network(match, strict=False))
        except Exception:
            pass

    calc_supernet = supernet_in
    if not calc_supernet and target_networks:
        calc_supernet = str(target_networks[0])

    calc_analysis = ""
    if calc_supernet and "/" in calc_supernet:
        calc_analysis = get_subnet_availability_analysis(
            calc_supernet,
            combined_prefixes,
            requested_prefix_len=req_mask
        )

    next_scope_id_val = (max_scope_id + 1) if max_scope_id is not None else 1

    return f"""You are an expert network architect specializing in IP address management, NetBox provisioning, and subnet planning.
You have DIRECT ACCESS to the complete IPAM database. Analyze the user's request and respond accurately using the ACTUAL DATABASE DATA provided below.

=== COMPLETE DATABASE CONTEXT ===
{comprehensive_context}

=== CURRENT UI STATE ===
- Current Site Input: {site_name or 'Not specified'}
- Current Supernet Input: {supernet_in or 'Not specified'}
- Next Available Scope ID: {next_scope_id_val}
- Max Scope ID in DB: {max_scope_id or 'None'}

{calc_analysis}

=== IMPORTANT GUIDELINES ===
1. **Answer ALL questions using the ACTUAL DATA above** - You have complete database access
2. When asked about VLANs, prefixes, or subnets for a site:
   - Reference the "VLANs and Prefixes for Site" section above
   - List the REAL VLAN IDs, names, subnets, roles, and descriptions
   - Be specific with actual data, not generic examples
3. When asked about devices or inventory:
   - Reference the "Inventory for Site" section if available
   - Provide actual device names, counts, and details
4. For subnet suggestions:
   - Follow the PRE-CALCULATED SUBNET AVAILABILITY ANALYSIS
   - Never suggest subnets marked as OCCUPIED or OVERLAPS
   - Explain why suggestions are available
5. For Scope ID questions:
   - Use the "Next Available Scope ID" value
6. Format responses clearly:
   - Use bullet points for lists
   - Include VLAN IDs, names, and subnets
   - Show actual counts and statistics
7. If no data exists for a query, clearly state "No records found in database"
8. Be concise but comprehensive - show all relevant data
9. When counting items (e.g., "how many VLANs"), provide the exact number from the data above
10. If a "NETBOX MASTER BACKUP" section is present it is the full NetBox export:
    - Treat it as authoritative for any object type (sites, racks, devices, interfaces, IPs, VMs, clusters, tenants, circuits, VRFs)
    - Use its "total in backup" figures when stating counts
    - Quote the exact records listed rather than generalizing"""


# ---------------------------------------------------------------------------
# Reconciliation helpers
# ---------------------------------------------------------------------------

def _recon_cache_key(filename: str, size: int) -> str:
    return f"recon_{filename}_{size}"


def _reconcile_uploaded(file_bytes: io.BytesIO, filename: str):
    from core.ipam_excel_diff import parse_file_bytes, classify_records, load_naming_rules
    rules = load_naming_rules()
    records = parse_file_bytes(file_bytes, filename=filename, rules=rules)
    matched, pending, conflicts, metrics = classify_records(records)
    return {
        "matched": matched,
        "pending_add": pending,
        "conflicts": conflicts,
        "metrics": metrics,
        "filename": filename,
        "parsed_at": datetime.utcnow().isoformat(timespec="seconds"),
        "_records_raw": records,
    }


def _render_reconciliation_tab():
    """Progressive open-layout Master IPAM Reconciliation dashboard."""
    from core.ipam_excel_diff import (
        export_pending_csvs,
        build_reconciliation_result,
        build_import_csvs,
        build_import_json,
        build_import_zip,
    )
    from core.db_manager import get_all_site_names, get_sync_metadata
    from core.shared_backup_state import SharedBackupState
    import ipaddress as _ipaddress

    st.markdown(
        "**Master IPAM Reconciliation** — Upload an Excel / CSV / JSON inventory and compare it "
        "against the local NetBox database. The engine classifies every row into matched, pending-add, "
        "or conflicted buckets and produces ready-to-import CSV bundles."
    )

    # ------------------------------------------------------------------
    # Session state init
    # ------------------------------------------------------------------
    if "recon_result" not in st.session_state:
        st.session_state["recon_result"] = None
    if "recon_uploaded_file" not in st.session_state:
        st.session_state["recon_uploaded_file"] = None
    if "recon_bundle" not in st.session_state:
        st.session_state["recon_bundle"] = None
    if "recon_selected_sites" not in st.session_state:
        st.session_state["recon_selected_sites"] = None
    if "recon_selected_vlans" not in st.session_state:
        st.session_state["recon_selected_vlans"] = None
    if "recon_selected_prefixes" not in st.session_state:
        st.session_state["recon_selected_prefixes"] = None
    if "recon_selected_ips" not in st.session_state:
        st.session_state["recon_selected_ips"] = None

    # ------------------------------------------------------------------
    # Section 1: Upload & Ingest
    # ------------------------------------------------------------------
    st.markdown("### 📤 Upload & Ingest")
    file_types = [".xlsx", ".xls", ".csv", ".json"]
    uploaded_file = st.file_uploader(
        "Upload IPAM Excel / CSV / JSON",
        type=file_types,
        key="ipam_recon_uploaded_file",
        help="Multi-sheet Excel, flat CSV, or NetBox backup JSON accepted.",
    )

    col_clr, col_ref = st.columns(2)
    with col_clr:
        if st.button("🗑️ Clear Excel Data", key="btn_clear_ipam_recon"):
            st.session_state.pop("recon_result", None)
            st.session_state.pop("recon_uploaded_file", None)
            st.session_state.pop("recon_bundle", None)
            st.session_state.pop("recon_selected_sites", None)
            st.session_state.pop("recon_selected_vlans", None)
            st.session_state.pop("recon_selected_prefixes", None)
            st.session_state.pop("recon_selected_ips", None)
            for k in list(st.session_state.keys()):
                if k.startswith("recon_") or k.startswith("ipam_recon_"):
                    st.session_state.pop(k, None)
            st.toast("🧹 Cleared.", icon="🧹")
            st.rerun()
    with col_ref:
        if st.button("🔄 Refresh", key="btn_refresh_ipam_recon"):
            st.rerun()

    if uploaded_file is not None:
        cache_key = _recon_cache_key(uploaded_file.name, uploaded_file.size)
        if st.session_state.get(cache_key) is None:
            with st.spinner("Parsing and classifying…"):
                try:
                    result = _reconcile_uploaded(
                        io.BytesIO(uploaded_file.read()), uploaded_file.name
                    )
                    st.session_state[cache_key] = result
                    st.session_state["recon_result"] = result
                    st.session_state["recon_uploaded_file"] = uploaded_file.name
                    st.toast(f"✅ Parsed {result['metrics']['total_parsed']} records.", icon="📄")
                except Exception as exc:
                    st.error(f"❌ Parse failed: {exc}")
        else:
            result = st.session_state[cache_key]
            st.session_state["recon_result"] = result

    # ------------------------------------------------------------------
    # Section 2: Global Preview & Metrics
    # ------------------------------------------------------------------
    result = st.session_state.get("recon_result")
    if result is not None:
        st.markdown("---")
        st.markdown("### 📊 Global Preview & Metrics")

        metrics = result["metrics"]

        # Authoritative source indicator
        inspector = SharedBackupState.get_inspector()
        backup_status = "🟢 NetBox Backup Loaded" if inspector else "⚪ SQLite Fallback"

        # Compute "Missing in Excel" — records present in NetBox backup but absent from parsed Excel
        missing_in_excel = 0
        try:
            from core.ipam_excel_diff import _build_backup_index
            nb_prefixes, _, _, _ = _build_backup_index()
            if nb_prefixes:
                parsed_subnets = {
                    str(r.get("subnet", "")).strip()
                    for r in result.get("_records_raw", [])
                } - {""}
                missing_in_excel = len(nb_prefixes - parsed_subnets)
        except Exception:
            pass

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Total Analyzed", metrics["total_parsed"])
        with c2:
            st.metric("❌ Need to Add to NetBox", metrics["pending_add"])
        with c3:
            st.metric("📥 Missing in Excel", missing_in_excel)
        with c4:
            st.metric("✅ In Sync", metrics["matched"])

        st.caption(backup_status)

        # Full diff dataframe with status badges
        all_rows = []
        for _, row in result["matched"].iterrows():
            all_rows.append({**row, "_bucket": "✅ In Sync"})
        for _, row in result["pending_add"].iterrows():
            all_rows.append({**row, "_bucket": "❌ Need to Add"})
        for _, row in result["conflicts"].iterrows():
            all_rows.append({**row, "_bucket": "⚠️ Conflict"})

        if all_rows:
            df_all = pd.DataFrame(all_rows)
            display_cols = [c for c in df_all.columns if c != "_bucket"] + ["_bucket"]
            st.dataframe(df_all[display_cols], use_container_width=True, hide_index=True)
        else:
            st.info("No records to display.")

    # ------------------------------------------------------------------
    # Section 3: Interactive Checkbox Selection — NetBox Objects Required
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 🏢 NetBox Objects Required")

    analyze_btn = st.button("📋 Analyze Requirements", key="btn_analyze_ipam_recon", type="primary")

    if analyze_btn and result is not None:
        # Build the full reconciliation bundle (dependency-ordered)
        enriched = build_reconciliation_result(result)
        st.session_state["recon_bundle"] = enriched
        bundle = enriched["bundle"]

        # ---- Table A: Missing Sites ----
        st.markdown("**Table A — Missing Sites to Create**")
        sites_data = bundle.get("missing_sites", [])
        if sites_data:
            sites_df = pd.DataFrame(sites_data)
            # Ensure 'Selected' column exists
            if "Selected" not in sites_df.columns:
                sites_df.insert(0, "Selected", True)
            # Reorder columns
            col_order = ["Selected", "name", "slug", "scope_id", "status", "raw_input"]
            avail = [c for c in col_order if c in sites_df.columns]
            sites_df = sites_df[avail]
            # Init session state for selections if missing
            if st.session_state.get("recon_selected_sites") is None:
                st.session_state["recon_selected_sites"] = sites_df.copy()
            edited_sites = st.data_editor(
                st.session_state["recon_selected_sites"],
                num_rows="dynamic",
                key="recon_sites_editor",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Selected": st.column_config.CheckboxColumn(
                        "Import?",
                        help="Check to include in import bundle.",
                        default=True,
                    ),
                    "name": st.column_config.TextColumn("Site Name"),
                    "slug": st.column_config.TextColumn("Slug"),
                    "scope_id": st.column_config.NumberColumn("Scope ID", help="NetBox site ID (auto-filled if matched)."),
                    "status": st.column_config.TextColumn("Status"),
                    "raw_input": st.column_config.TextColumn("Raw Input", help="Original site value from sheet."),
                },
            )
            st.session_state["recon_selected_sites"] = edited_sites
            sa, sma = st.columns(2)
            with sa:
                if st.button("Select All", key="sites_select_all"):
                    st.session_state["recon_selected_sites"]["Selected"] = True
                    st.rerun()
            with sma:
                if st.button("Deselect All", key="sites_deselect_all"):
                    st.session_state["recon_selected_sites"]["Selected"] = False
                    st.rerun()
        else:
            st.caption("All sites already exist in NetBox.")

        # ---- Table B: Missing VLAN Groups & VLANs ----
        st.markdown("**Table B — Missing VLAN Groups & VLANs to Create**")
        vlan_groups_data = bundle.get("missing_vlan_groups", [])
        vlans_data = bundle.get("missing_vlans", [])
        if vlan_groups_data or vlans_data:
            # Combine VLAN groups and VLANs into one table for simplicity
            rows_b = []
            for vg in vlan_groups_data:
                rows_b.append({
                    "Selected": True,
                    "Type": "VLAN Group",
                    "ID/VID": "",
                    "Name": vg["name"],
                    "Site": vg.get("site", ""),
                    "Description": f"VLAN group for {vg.get('site', '')}",
                    "Role": "",
                })
            for v in vlans_data:
                rows_b.append({
                    "Selected": True,
                    "Type": "VLAN",
                    "ID/VID": v.get("vid", ""),
                    "Name": v.get("name", ""),
                    "Site": v.get("site", ""),
                    "Description": v.get("description", ""),
                    "Role": v.get("role", ""),
                })
            df_b = pd.DataFrame(rows_b)
            if st.session_state.get("recon_selected_vlans") is None:
                st.session_state["recon_selected_vlans"] = df_b.copy()
            edited_b = st.data_editor(
                st.session_state["recon_selected_vlans"],
                num_rows="dynamic",
                key="recon_vlans_editor",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Selected": st.column_config.CheckboxColumn("Import?", default=True),
                    "Type": st.column_config.TextColumn("Type"),
                    "ID/VID": st.column_config.TextColumn("ID / VID"),
                    "Name": st.column_config.TextColumn("Name"),
                    "Site": st.column_config.TextColumn("Site"),
                    "Description": st.column_config.TextColumn("Description"),
                    "Role": st.column_config.TextColumn("Role"),
                },
            )
            st.session_state["recon_selected_vlans"] = edited_b
            sb, smb = st.columns(2)
            with sb:
                if st.button("Select All", key="vlans_select_all"):
                    st.session_state["recon_selected_vlans"]["Selected"] = True
                    st.rerun()
            with smb:
                if st.button("Deselect All", key="vlans_deselect_all"):
                    st.session_state["recon_selected_vlans"]["Selected"] = False
                    st.rerun()
        else:
            st.caption("All VLAN groups and VLANs already exist in NetBox.")

        # ---- Table C: Missing Prefixes ----
        st.markdown("**Table C — Missing Prefixes to Allocate**")
        prefixes_data = bundle.get("missing_prefixes", [])
        if prefixes_data:
            df_c = pd.DataFrame(prefixes_data)
            if "Selected" not in df_c.columns:
                df_c.insert(0, "Selected", True)
            col_order_c = ["Selected", "prefix", "site", "vlan_id", "description", "status"]
            avail_c = [c for c in col_order_c if c in df_c.columns]
            df_c = df_c[avail_c]
            if st.session_state.get("recon_selected_prefixes") is None:
                st.session_state["recon_selected_prefixes"] = df_c.copy()
            edited_c = st.data_editor(
                st.session_state["recon_selected_prefixes"],
                num_rows="dynamic",
                key="recon_prefixes_editor",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Selected": st.column_config.CheckboxColumn("Import?", default=True),
                    "prefix": st.column_config.TextColumn("Prefix (CIDR)"),
                    "site": st.column_config.TextColumn("Site"),
                    "vlan_id": st.column_config.TextColumn("VLAN ID"),
                    "description": st.column_config.TextColumn("Description"),
                    "status": st.column_config.TextColumn("Status"),
                },
            )
            st.session_state["recon_selected_prefixes"] = edited_c
            sc, smc = st.columns(2)
            with sc:
                if st.button("Select All", key="prefixes_select_all"):
                    st.session_state["recon_selected_prefixes"]["Selected"] = True
                    st.rerun()
            with smc:
                if st.button("Deselect All", key="prefixes_deselect_all"):
                    st.session_state["recon_selected_prefixes"]["Selected"] = False
                    st.rerun()
        else:
            st.caption("All prefixes already exist in NetBox.")

        # ---- Table D: IP Addresses & Gateways ----
        st.markdown("**Table D — IP Addresses & Gateways**")
        ips_data = bundle.get("ip_addresses", [])
        if ips_data:
            df_d = pd.DataFrame(ips_data)
            if "Selected" not in df_d.columns:
                df_d.insert(0, "Selected", True)
            col_order_d = ["Selected", "address", "role", "site", "description", "subnet"]
            avail_d = [c for c in col_order_d if c in df_d.columns]
            df_d = df_d[avail_d]
            if st.session_state.get("recon_selected_ips") is None:
                st.session_state["recon_selected_ips"] = df_d.copy()
            edited_d = st.data_editor(
                st.session_state["recon_selected_ips"],
                num_rows="dynamic",
                key="recon_ips_editor",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Selected": st.column_config.CheckboxColumn("Import?", default=True),
                    "address": st.column_config.TextColumn("IP Address"),
                    "role": st.column_config.TextColumn("Role"),
                    "site": st.column_config.TextColumn("Site"),
                    "description": st.column_config.TextColumn("Description"),
                    "subnet": st.column_config.TextColumn("Subnet"),
                },
            )
            st.session_state["recon_selected_ips"] = edited_d
            sd, smd = st.columns(2)
            with sd:
                if st.button("Select All", key="ips_select_all"):
                    st.session_state["recon_selected_ips"]["Selected"] = True
                    st.rerun()
            with smd:
                if st.button("Deselect All", key="ips_deselect_all"):
                    st.session_state["recon_selected_ips"]["Selected"] = False
                    st.rerun()
        else:
            st.caption("No additional IP addresses to import.")

        # Quick-action: site loader
        all_sites = get_all_site_names()
        parsed_records = result.get("_records_raw", [])
        parsed_sites = sorted({
            str(r.get("site", "")).strip()
            for r in parsed_records
        } - {""}) if parsed_records else []
        parsed_site_options = parsed_sites or all_sites
        if parsed_site_options:
            site_sel = st.selectbox(
                "🔍 Quick Load Site into Subnet Allocation Editor",
                options=[""] + parsed_site_options + [s for s in all_sites if s not in parsed_site_options],
                key="recon_site_selector",
                help="Auto-fills ipam_site_in for immediate editing.",
            )
            if site_sel:
                st.session_state["ipam_site_in"] = site_sel
                st.toast(f"Loaded site **{site_sel}** into Subnet Allocation Editor.", icon="📌")

    elif result is None:
        st.caption("Upload a file above, then click **Analyze Requirements**.")

    # ------------------------------------------------------------------
    # Section 4: Export & Sync Bundles — Dependency-Ordered Import Generators
    # ------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### 📦 Export & Sync Bundles")

    bundle = st.session_state.get("recon_bundle")
    if bundle is not None:
        csv_buffers = build_import_csvs(bundle)

        # Filter to only user-selected rows per table
        def _filter_selected(df, key):
            if df is None or df.empty:
                return None
            selected = df[df.get("Selected", True) is True if "Selected" in df.columns else df.index.astype(bool)]
            if selected.empty:
                return None
            return selected.drop(columns=["Selected"], errors="ignore")

        sel_sites = _filter_selected(
            st.session_state.get("recon_selected_sites"), "sites"
        )
        sel_vlans = _filter_selected(
            st.session_state.get("recon_selected_vlans"), "vlans"
        )
        sel_prefixes = _filter_selected(
            st.session_state.get("recon_selected_prefixes"), "prefixes"
        )
        sel_ips = _filter_selected(
            st.session_state.get("recon_selected_ips"), "ips"
        )

        # Build selection-aware bundle
        selection_bundle = {
            "missing_sites": sel_sites.to_dict("records") if sel_sites is not None and not sel_sites.empty else [],
            "missing_vlan_groups": [],
            "missing_vlans": [],
            "missing_prefixes": sel_prefixes.to_dict("records") if sel_prefixes is not None and not sel_prefixes.empty else [],
            "ip_addresses": sel_ips.to_dict("records") if sel_ips is not None and not sel_ips.empty else [],
            "aggregates": bundle.get("aggregates", []),
            "metrics": bundle.get("metrics", {}),
        }
        # Re-derive vlan_groups from sites if needed
        if sel_sites is not None and not sel_sites.empty:
            for _, row in sel_sites.iterrows():
                site_name = row.get("name", row.get("raw_input", ""))
                if site_name:
                    selection_bundle["missing_vlan_groups"].append({
                        "name": f"{site_name} VLAN Group",
                        "slug": slugify(f"{site_name} VLAN Group"),
                        "scope_type": "dcim.site",
                        "scope_id": row.get("scope_id"),
                        "site": site_name,
                    })
        # Pull VLANs from the original bundle filtered by site
        if sel_vlans is not None and not sel_vlans.empty:
            for _, row in sel_vlans.iterrows():
                if row.get("Type") == "VLAN":
                    selection_bundle["missing_vlans"].append({
                        "vid": str(row.get("ID/VID", "")).strip(),
                        "name": str(row.get("Name", "")).strip(),
                        "site": str(row.get("Site", "")).strip(),
                        "description": str(row.get("Description", "")).strip(),
                        "role": str(row.get("Role", "")).strip(),
                    })
        elif sel_sites is not None and not sel_sites.empty:
            # Include all VLANs for selected sites
            selected_site_names = set()
            for _, row in sel_sites.iterrows():
                sn = row.get("name", row.get("raw_input", ""))
                if sn:
                    selected_site_names.add(sn.lower())
            for v in bundle.get("missing_vlans", []):
                if v.get("site", "").lower() in selected_site_names:
                    selection_bundle["missing_vlans"].append(v)

        st.markdown("**Dependency-Ordered Import Pipeline** — Import in order: Sites → VLAN Groups → VLANs → Prefixes → IP Addresses.")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("**1. Sites (`dcim.site`)**")
            if "01_sites.csv" in csv_buffers:
                buf = csv_buffers["01_sites.csv"]
                if buf.tell() > 0:
                    buf.seek(0)
                    preview = buf.read().decode("utf-8")
                    st.code(preview[:400] + ("..." if len(preview) > 400 else ""), language="csv")
                st.download_button(
                    label="📥 Download 01_sites.csv",
                    data=csv_buffers["01_sites.csv"].getvalue(),
                    file_name="01_sites.csv",
                    mime="text/csv",
                    key="dl_01_sites",
                )
            else:
                st.caption("No sites to import.")

        with col2:
            st.markdown("**2. VLAN Groups (`ipam.vlangroup`)**")
            if "02_vlan_groups.csv" in csv_buffers:
                buf = csv_buffers["02_vlan_groups.csv"]
                if buf.tell() > 0:
                    buf.seek(0)
                    preview = buf.read().decode("utf-8")
                    st.code(preview[:400] + ("..." if len(preview) > 400 else ""), language="csv")
                st.download_button(
                    label="📥 Download 02_vlan_groups.csv",
                    data=csv_buffers["02_vlan_groups.csv"].getvalue(),
                    file_name="02_vlan_groups.csv",
                    mime="text/csv",
                    key="dl_02_vg",
                )
            else:
                st.caption("No VLAN groups to import.")

        with col3:
            st.markdown("**3. VLANs (`ipam.vlan`)**")
            if "03_vlans.csv" in csv_buffers:
                buf = csv_buffers["03_vlans.csv"]
                if buf.tell() > 0:
                    buf.seek(0)
                    preview = buf.read().decode("utf-8")
                    st.code(preview[:400] + ("..." if len(preview) > 400 else ""), language="csv")
                st.download_button(
                    label="📥 Download 03_vlans.csv",
                    data=csv_buffers["03_vlans.csv"].getvalue(),
                    file_name="03_vlans.csv",
                    mime="text/csv",
                    key="dl_03_vlans",
                )
            else:
                st.caption("No VLANs to import.")

        col4, col5 = st.columns(2)
        with col4:
            st.markdown("**4. Prefixes (`ipam.prefix`)**")
            if "04_prefixes.csv" in csv_buffers:
                buf = csv_buffers["04_prefixes.csv"]
                if buf.tell() > 0:
                    buf.seek(0)
                    preview = buf.read().decode("utf-8")
                    st.code(preview[:400] + ("..." if len(preview) > 400 else ""), language="csv")
                st.download_button(
                    label="📥 Download 04_prefixes.csv",
                    data=csv_buffers["04_prefixes.csv"].getvalue(),
                    file_name="04_prefixes.csv",
                    mime="text/csv",
                    key="dl_04_prefixes",
                )
            else:
                st.caption("No prefixes to import.")

        with col5:
            st.markdown("**5. IP Addresses (`ipam.ipaddress`)**")
            if "05_ip_addresses.csv" in csv_buffers:
                buf = csv_buffers["05_ip_addresses.csv"]
                if buf.tell() > 0:
                    buf.seek(0)
                    preview = buf.read().decode("utf-8")
                    st.code(preview[:400] + ("..." if len(preview) > 400 else ""), language="csv")
                st.download_button(
                    label="📥 Download 05_ip_addresses.csv",
                    data=csv_buffers["05_ip_addresses.csv"].getvalue(),
                    file_name="05_ip_addresses.csv",
                    mime="text/csv",
                    key="dl_05_ips",
                )
            else:
                st.caption("No IP addresses to import.")

        st.divider()
        col_zip, col_json = st.columns(2)
        with col_zip:
            st.download_button(
                label="📥 Download NetBox Import Bundle (.zip)",
                data=build_import_zip(bundle).getvalue(),
                file_name="netbox-ipam-import-bundle.zip",
                mime="application/zip",
                key="dl_ipam_bundle_zip",
                type="primary",
            )
            st.caption("Includes all 5 dependency-ordered CSV files.")
        with col_json:
            st.download_button(
                label="🚀 Generate & Download NetBox Import JSON",
                data=build_import_json(bundle),
                file_name="netbox-ipam-import.json",
                mime="application/json",
                key="dl_ipam_bundle_json",
                type="primary",
            )
            st.caption("Single JSON payload structured by endpoint for REST API bulk-creation.")

        st.caption("💡 Import order matters: Sites → VLAN Groups → VLANs → Prefixes → IP Addresses.")
    elif result is not None:
        st.caption("Click **Analyze Requirements** above first to build the import bundle.")
    else:
        st.caption("Upload and parse a file to enable export bundles.")


def render_ipam_tab(active_model: str):
    st.subheader("🌐 IPAM & Site Subnet Provisioning Engine")
    st.caption("Plan site supernets, allocate non-overlapping VLAN subnets, and export ready-to-import NetBox CSV blocks.")

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

    # Two top-level sub-tabs
    tab_subnet, tab_reconcile = st.tabs(["🏢 Site Subnet Allocation", "⚖️ Master IPAM Reconciliation"])

    # ==================================================================
    # TAB 1: Site Subnet Allocation
    # ==================================================================
    with tab_subnet:
        # 1. AI IPAM & Subnet Assistant
        render_ai_chat(
            history_key="ipam_chat_history",
            caption="Ask for subnet suggestions using natural language (e.g., 'Show me VLANs in Weybridge' or 'Suggest the next available /24 in 10.113.0.0/16')",
            placeholder="Ask for subnet suggestions...",
            active_model=active_model,
            build_system_prompt=lambda p: build_ipam_system_prompt(
                p,
                st.session_state.get("ipam_site_in", "").strip(),
                st.session_state.get("ipam_super_in", "").strip(),
                get_existing_prefix_strings(),
                get_max_scope_id(),
            ),
        )

        # 2. Site Inputs and Dynamic Lookups
        if "ipam_site_in" not in st.session_state:
            st.session_state["ipam_site_in"] = ""
        if "ipam_scope_in" not in st.session_state:
            st.session_state["ipam_scope_in"] = ""
        if "ipam_super_in" not in st.session_state:
            st.session_state["ipam_super_in"] = ""

        max_scope_id = get_max_scope_id()
        scope_eg = f"e.g. {max_scope_id + 1}" if max_scope_id is not None else "e.g. 42"
        site_name = st.session_state.get("ipam_site_in", "").strip()
        supernet_in = st.session_state.get("ipam_super_in", "").strip()
        existing_prefixes = get_existing_prefix_strings()

        # Site input fields
        top1, top2, top3 = st.columns([2, 1, 2.2])
        with top1:
            st.text_input(
                "Branch / Site Name",
                key="ipam_site_in",
                placeholder="e.g. Bristol, AGE, Adelaide, UK, Site-01",
                on_change=handle_site_change
            )

        cached_site = _last_site_lookup.get("site", "")
        if site_name and site_name != cached_site:
            handle_site_change()

        auto_scope_id = _last_site_lookup.get("scope")
        auto_supernet = _last_site_lookup.get("supernet")

        if site_name and auto_supernet and not st.session_state.get("ipam_super_in"):
            st.session_state["ipam_super_in"] = str(auto_supernet)

        site_found = bool(st.session_state.get("ipam_site_found_in_db", False))
        if site_name and st.session_state.get("ipam_loaded_site") != site_name:
            site_found = bool(get_ipam_records_by_site(site_name))
            st.session_state["ipam_site_found_in_db"] = site_found

        with top2:
            st.text_input(
                "Scope ID (NetBox Site ID)", 
                key="ipam_scope_in",
                placeholder=scope_eg,
                help="Auto-discovered from uploaded data/agent sync, or editable manually."
            )
            scope_id = st.session_state["ipam_scope_in"].strip()
            if auto_scope_id:
                st.caption(f"🟢 Matched Scope ID: **`{auto_scope_id}`**")
            else:
                st.caption("⚪ Manual Scope ID mode")

        with top3:
            st.text_input(
                "Site Supernet (CIDR)", 
                key="ipam_super_in",
                placeholder="e.g. 10.x.x.0/24",
                help="Top-level container subnet for this branch site."
            )
            cap_placeholder = st.empty()

        display_site_name = format_branch_display(site_name)

        # 3. Preset Selection & Allocation Editor
        st.divider()
        c_title, c_preset_container = st.columns([2.3, 1.5])
        with c_title:
            st.markdown("##### 📊 Subnet Allocation & Live Status (✏️ Click any cell to edit)")
        with c_preset_container:
            c_preset_inner, c_refresh, c_status = st.columns([1.0, 0.3, 0.3])
            rules = load_naming_rules()
            dynamic_presets = get_vlan_presets(rules)
            preset_options = ["🗄️ Load From DB (Existing Site)", "Custom / Empty Preset"] + [k for k in dynamic_presets.keys() if k != "Custom / Empty Preset"]
            default_idx = preset_options.index("Custom / Empty Preset") if "Custom / Empty Preset" in preset_options else 0
            with c_preset_inner:
                st.selectbox(
                    "Load Standard Preset",
                    options=preset_options,
                    index=default_idx,
                    key="ipam_preset_selector",
                    on_change=on_preset_change,
                    help="Quickly load pre-defined standard VLAN structures or start blank."
                )
            selected_preset = st.session_state.get("ipam_preset_selector", "")
            db_mode = selected_preset == "🗄️ Load From DB (Existing Site)"

            with c_refresh:
                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                if db_mode:
                    if st.button("🔄", key="btn_refresh_ipam", help=f"Reload '{site_name or 'site'}' data from database"):
                        if load_ipam_records_from_db(site_name):
                            st.toast(f"🔄 Reloaded {site_name} from database.", icon="✅")
                        else:
                            st.toast(f"⚠️ No records found for '{site_name}'.", icon="⚠️")
                        st.rerun()
            with c_status:
                st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
                if site_found:
                    st.markdown("✅", help="Site found in DB - Select 'Load From DB' to load data")
                else:
                    st.caption("⚪")

        if db_mode and site_name and st.session_state.get("ipam_loaded_site") != site_name:
            load_ipam_records_from_db(site_name)
            st.rerun()

        if "ipam_persisted_rows" not in st.session_state:
            st.session_state["ipam_persisted_rows"] = []

        raw_rows = [dict(r) for r in st.session_state["ipam_persisted_rows"]]
        editor_state = st.session_state.get("ipam_data_editor_live", {})

        active_vlan_presets = get_vlan_presets(rules)
        vlan_desc_mappings = get_vlan_description_mappings(rules)
        role_rules = get_ipam_role_mappings(rules)
        
        deleted_indices = set(editor_state.get("deleted_rows", []))
        if deleted_indices:
            raw_rows = [r for i, r in enumerate(raw_rows) if i not in deleted_indices]

        edited_cells = editor_state.get("edited_rows", {})
        for row_idx_str, changes in edited_cells.items():
            row_idx = int(row_idx_str)
            if row_idx < len(raw_rows):
                if "VLAN Description" in changes:
                    raw_rows[row_idx]["_vlan_desc_manual"] = True

                manual_desc = raw_rows[row_idx].get("_vlan_desc_manual", False)

                if "Role" in changes:
                    changes["Role"] = normalize_role_name(changes["Role"], role_rules)
                    name_pattern = raw_rows[row_idx].get("_name_pattern", "")
                    if name_pattern and "VLAN Name" not in changes:
                        site_display = format_branch_display(site_name)
                        changes["VLAN Name"] = resolve_vlan_name(
                            site_display,
                            changes.get("VLAN ID", raw_rows[row_idx].get("VLAN ID")),
                            changes["Role"],
                            name_pattern
                        )
                pattern_template = raw_rows[row_idx].get("_pattern_template", "")
                if (
                    not manual_desc
                    and ("Role" in changes or "VLAN ID" in changes)
                    and "VLAN Description" not in changes
                ):
                    next_role = changes.get("Role", raw_rows[row_idx].get("Role", ""))
                    next_vlan_id = changes.get("VLAN ID", raw_rows[row_idx].get("VLAN ID"))
                    next_vlan_name = changes.get("VLAN Name", raw_rows[row_idx].get("VLAN Name", next_role))
                    changes["VLAN Description"] = build_vlan_description(
                        site_name, next_role, next_vlan_id, next_vlan_name, active_vlan_presets, vlan_desc_mappings, role_rules
                    )
                if pattern_template and ("Role" in changes or "VLAN ID" in changes):
                    if "Prefix Description" not in changes:
                        site_display = format_branch_display(site_name)
                        role_for_prefix = changes.get("Role", raw_rows[row_idx].get("Role", ""))
                        vid_for_prefix = changes.get("VLAN ID", raw_rows[row_idx].get("VLAN ID"))
                        name_for_prefix = changes.get("VLAN Name", raw_rows[row_idx].get("VLAN Name", role_for_prefix))
                        changes["Prefix Description"] = resolve_pattern_template(
                            pattern_template, site_display, vid_for_prefix, role_for_prefix, name_for_prefix
                        )
                raw_rows[row_idx].update(changes)

        for new_r in editor_state.get("added_rows", []):
            r_name = new_r.get("Role", "")
            new_vid = new_r.get("VLAN ID", None)
            typed_desc = str(new_r.get("VLAN Description", "") or "").strip()
            typed_vlan_name = str(new_r.get("VLAN Name", "") or "").strip()
            site_display = format_branch_display(site_name)
            selected_preset_name = st.session_state.get("ipam_preset_selector", "")
            if selected_preset_name and selected_preset_name != "🗄️ Load From DB (Existing Site)" and selected_preset_name in dynamic_presets:
                group_vname_pat = dynamic_presets[selected_preset_name].get("vlan_name_pattern", "")
                group_prefix_pat = dynamic_presets[selected_preset_name].get("prefix_pattern", "")
            else:
                group_vname_pat = ""
                group_prefix_pat = ""
            name_pattern = new_r.get("_name_pattern", group_vname_pat) or group_vname_pat
            prefix_template = new_r.get("_pattern_template", group_prefix_pat) or group_prefix_pat
            if typed_vlan_name and not name_pattern:
                vlan_name = typed_vlan_name
            else:
                vlan_name = resolve_vlan_name(site_display, new_vid, r_name, name_pattern)
            raw_rows.append({
                "VLAN ID": new_vid,
                "Role": normalize_role_name(r_name, role_rules) or to_title_case_preserve_acronyms(r_name),
                "VLAN Name": vlan_name,
                "VLAN Description": typed_desc if typed_desc else resolve_vlan_description(new_vid, r_name, active_vlan_presets, vlan_desc_mappings, role_rules),
                "_vlan_desc_manual": bool(typed_desc),
                "_pattern_template": prefix_template,
                "_name_pattern": name_pattern,
                "Subnet (CIDR)": new_r.get("Subnet (CIDR)", "")
            })

        computed_rows = compute_chained_rows(supernet_in, raw_rows)
        st.session_state["ipam_persisted_rows"] = computed_rows

        loaded_from_db = st.session_state.get("ipam_loaded_from_db", False)
        allocated_subnets = []
        for r in computed_rows:
            sub_str = str(r.get("Subnet (CIDR)", "") or "").strip()
            if sub_str:
                allocated_subnets.append(sub_str)
            
            eval_res = evaluate_subnet_row(
                sub_str, 
                r.get("VLAN ID"), 
                r.get("Role", ""), 
                site_name, 
                supernet_in, 
                existing_prefixes,
                vlan_name=r.get("VLAN Name", ""),
                pattern_template=r.get("_pattern_template", ""),
            )
            r["Usable Range"] = eval_res["usable_range"]
            r["Status"] = eval_res["status"]
            
            if loaded_from_db and "_db_prefix_desc" in r:
                r["Prefix Description"] = r.get("_db_prefix_desc") or ""
            else:
                r["Prefix Description"] = eval_res["desc"]

        with cap_placeholder.container():
            if supernet_in and "/" in supernet_in:
                try:
                    sup_net = ipaddress.ip_network(supernet_in, strict=False)
                    sup_range = calculate_ip_range_str(sup_net)
                    cap_matrix = calculate_remaining_subnets(supernet_in, allocated_subnets)
                    sup_prefix_len = sup_net.prefixlen
                    step_masks = [f"/{m}" for m in range(sup_prefix_len + 1, min(sup_prefix_len + 5, 31))]
                    cap_items = [f"`{cap_matrix.get(m, 0)}x {m}`" for m in step_masks]
                    cap_str = f"**Available:** {' | '.join(cap_items)}" if cap_items else "No subnets"
                    st.markdown(f"📍 **Site Subnet:** `{sup_range}`")
                    st.caption(cap_str)
                except ValueError:
                    st.caption("⚠️ Invalid CIDR format")

        TABLE_COLS = [
            "VLAN ID", "Role", "VLAN Name", "VLAN Description", 
            "Suggest Subnet", "Subnet (CIDR)", "Usable Range", "Status", "Prefix Description"
        ]
        if computed_rows:
            df_init = pd.DataFrame(computed_rows)[TABLE_COLS]
        else:
            df_init = pd.DataFrame(columns=TABLE_COLS)

        edited_df = st.data_editor(
            df_init,
            width="stretch",
            num_rows="dynamic",
            key="ipam_data_editor_live",
            column_config={
                "VLAN ID": st.column_config.NumberColumn("VLAN ID", step=1, required=True),
                "Role": st.column_config.TextColumn("Role", help="VLAN Role. Auto-sets VLAN Name & Description via DB lookup."),
                "VLAN Name": st.column_config.TextColumn("VLAN Name", help="VLAN Name in NetBox. Defaults to Role (or dynamic pattern), or editable."),
                "VLAN Description": st.column_config.TextColumn("VLAN Description", help="VLAN Description. Auto-looked up from DB or editable."),
                "Suggest Subnet": st.column_config.TextColumn("Suggest Subnet", help="Calculated next available network IP ID.", disabled=True),
                "Subnet (CIDR)": st.column_config.TextColumn("Subnet (CIDR)", help="Type subnet CIDR (e.g. 10.113.252.0/23) and hit Enter."),
                "Usable Range": st.column_config.TextColumn("Usable Range", help="Calculated usable host IP range.", disabled=True),
                "Status": st.column_config.TextColumn("Status", help="Collision & Database usage status.", disabled=True),
                "Prefix Description": st.column_config.TextColumn("Prefix Description", help="Calculated NetBox prefix description.", disabled=True)
            }
        )

        # 4. NetBox Bulk-Import CSV Copy Cards & Scope ID Notification
        st.divider()
        st.markdown("### 📋 NetBox Bulk-Import CSV Generators")

        if not scope_id:
            st.warning("⚠️ **Notice:** Scope ID is empty. Export NetBox Sites CSV to discover Scope ID, or enter it manually.", icon="⚠️")

        display_site = display_site_name or "Site"
        csv_site = generate_netbox_site_csv(display_site)
        csv_group = generate_netbox_vlan_group_csv(display_site, scope_id)
        csv_vlans = generate_netbox_vlans_csv(display_site, computed_rows)

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**1. Import Site (`dcim.site`)**", help="📍 NetBox Path: Organization → Sites → Import")
            st.code(csv_site, language="csv")

            st.markdown("**3. Import VLANs (`ipam.vlan`)**", help="📍 NetBox Path: IPAM → VLANs → Import\n\nExports VLANs with allocated IP subnets. Unassigned presets are filtered out.")
            st.code(csv_vlans, language="csv")

        with c2:
            st.markdown("**2. Import VLAN Group (`ipam.vlangroup`)**", help="📍 NetBox Path: IPAM → VLAN Groups → Import")
            st.code(csv_group, language="csv")

            prefix_title, prefix_option = st.columns([2.2, 1.8])
            with prefix_title:
                st.markdown("**4. Import Prefixes (`ipam.prefix`)**", help="📍 NetBox Path: IPAM → Prefixes → Import")
            with prefix_option:
                st.checkbox(
                    "Include site subnet in Prefixes CSV",
                    value=True,
                    help="Add the top-level site supernet.",
                    key="ipam_include_site_subnet",
                )
            include_site_subnet = st.session_state.get("ipam_include_site_subnet", True)
            csv_prefixes = generate_netbox_prefixes_csv(
                display_site,
                scope_id,
                supernet_in,
                computed_rows,
                include_site_subnet=include_site_subnet,
            )
            st.code(csv_prefixes, language="csv")

    # ==================================================================
    # TAB 2: Master IPAM Reconciliation
    # ==================================================================
    with tab_reconcile:
        _render_reconciliation_tab()
