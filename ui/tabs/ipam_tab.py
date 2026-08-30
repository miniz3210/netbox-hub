import io
import re
import ipaddress
import streamlit as st
import pandas as pd
import openpyxl
from core.ipam_engine import (
    VLAN_PRESETS,
    compute_chained_rows,
    slugify,
    evaluate_subnet_row,
    calculate_remaining_subnets,
    get_subnet_availability_analysis,
    calculate_ip_range_str,
    format_branch_display,
    lookup_role_description,
    generate_netbox_site_csv,
    generate_netbox_vlan_group_csv,
    generate_netbox_vlans_csv,
    generate_netbox_prefixes_csv
)
from core.db_manager import (
    save_ipam_records_batch,
    save_sites_batch,
    clear_ipam_records,
    clear_sites_records,
    clear_prefixes_records,
    get_total_ipam_count,
    get_total_sites_count,
    lookup_scope_id,
    lookup_site_supernet_from_db,
    get_existing_prefix_strings,
    get_sync_metadata
)
from utils.formatters import to_title_case_preserve_acronyms

POWERSHELL_AGENT_CODE = """<#
.SYNOPSIS
    NetBox Hub Sync Agent (Optimized Core Sync)
#>
[CmdletBinding()]
param (
    [Parameter(Mandatory = $true)]
    [string]$NetBoxUrl,

    [Parameter(Mandatory = $true)]
    [Alias("NetBoxToken", "Token")]
    [string]$ApiToken,

    [Parameter(Mandatory = $true)]
    [Alias("Destination", "Hub")]
    [string]$HubUrl,

    [string]$HubSyncKey = "netbox-hub-secret-sync-key",
    [int]$PageSize = 2000
)

$NetBoxUrl = $NetBoxUrl.TrimEnd('/')
$HubEndpoint = "$($HubUrl.TrimEnd('/'))/api/v1/sync/push"

$BackupData = [ordered]@{ sync_key = $HubSyncKey }

function Get-PaginatedData {
    param([string]$Endpoint)
    $Results = @()
    $Url = "$NetBoxUrl/api/$Endpoint/?limit=$PageSize"
    do {
        Write-Host "Fetching: $Url"
        try {
            $rawJson = & curl.exe -k -s -L -H "Authorization: Token $ApiToken" -H "Accept: application/json" $Url
            if (-not $rawJson) { break }
            $Response = $rawJson | ConvertFrom-Json
            if ($Response.results) {
                $Results += $Response.results
                Write-Host ("Retrieved {0} records" -f $Results.Count)
            } else { break }
            $Url = $Response.next
        } catch { break }
    } while ($Url)
    return $Results
}

$Endpoints = @(
    "dcim/sites",
    "ipam/vlans",
    "ipam/prefixes",
    "dcim/devices",
    "virtualization/virtual-machines"
)

Write-Host "`n=========================================" -ForegroundColor Cyan
Write-Host "NETBOX CORE FAST SYNC & CLOUD HUB SYNC" -ForegroundColor Cyan
Write-Host "=========================================`n" -ForegroundColor Cyan

foreach ($Endpoint in $Endpoints) {
    Write-Host "Exporting $Endpoint..." -ForegroundColor Yellow
    $Key = $Endpoint.Replace("/", "_")
    $BackupData[$Key] = Get-PaginatedData -Endpoint $Endpoint
    Write-Host "Completed $Endpoint`n" -ForegroundColor Green
}

$PayloadJson = $BackupData | ConvertTo-Json -Depth 100 -Compress
Write-Host "Payload size: $([Math]::Round(($PayloadJson.Length / 1MB), 2)) MB"
Write-Host "Syncing with Cloud Hub: $HubEndpoint..." -ForegroundColor Yellow

try {
    $tempFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tempFile, $PayloadJson, [System.Text.Encoding]::UTF8)

    $rawPush = & curl.exe -k -s -L -X POST `
        -H "Content-Type: application/json" `
        -H "X-Hub-Key: $HubSyncKey" `
        --data-binary "@$tempFile" `
        $HubEndpoint

    Remove-Item -Path $tempFile -Force -ErrorAction SilentlyContinue
    $PushResponse = $rawPush | ConvertFrom-Json

    if ($PushResponse.success) {
        Write-Host "`n=========================================" -ForegroundColor Green
        Write-Host "CLOUD SYNC SUCCESSFUL!" -ForegroundColor Green
        Write-Host "=========================================" -ForegroundColor Green
        Write-Host "   • Sites Imported:    $($PushResponse.imported.sites)" -ForegroundColor White
        Write-Host "   • Prefixes Imported: $($PushResponse.imported.prefixes)" -ForegroundColor White
        Write-Host "   • Devices Imported:  $($PushResponse.imported.devices)" -ForegroundColor White
        Write-Host "   • VMs Imported:      $($PushResponse.imported.vms)" -ForegroundColor White
    } else {
        Write-Host "❌ Error: $($PushResponse.error)" -ForegroundColor Red
    }
} catch {
    Write-Host "❌ Push failed: $($_.Exception.Message)" -ForegroundColor Red
}
"""

def handle_site_change():
    """Triggered on site name input change: automatically looks up and fills Scope ID & Supernet."""
    entered_site = st.session_state.get("ipam_site_in", "").strip()
    if entered_site:
        matched_scope = lookup_scope_id(entered_site)
        matched_super = lookup_site_supernet_from_db(entered_site)
        if matched_scope is not None:
            st.session_state["ipam_scope_in"] = str(matched_scope)
        if matched_super is not None:
            st.session_state["ipam_super_in"] = str(matched_super)

def handle_ipam_file_upload():
    uploaded_files = st.session_state.get("ipam_multi_uploader")
    if not uploaded_files:
        return

    if not isinstance(uploaded_files, list):
        uploaded_files = [uploaded_files]

    total_scopes = 0
    total_prefixes = 0
    errors = []

    for file_obj in uploaded_files:
        filename = file_obj.name.lower()
        content = file_obj.getvalue()
        
        if filename.endswith(".xlsx"):
            try:
                wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
                scope_records = []
                ipam_records = []

                if "Scope" in wb.sheetnames:
                    ws_scope = wb["Scope"]
                    for r in range(2, ws_scope.max_row + 1):
                        s_id = ws_scope.cell(row=r, column=1).value
                        s_name = ws_scope.cell(row=r, column=5).value
                        s_slug = ws_scope.cell(row=r, column=6).value
                        if s_name:
                            scope_records.append({"id": s_id, "name": s_name, "slug": s_slug})
                    if scope_records:
                        cnt = save_sites_batch(scope_records, clear_first=False, source="Manual CSV Upload")
                        total_scopes += cnt

                if "Prefixes" in wb.sheetnames:
                    ws_pfx = wb["Prefixes"]
                    for r in range(2, ws_pfx.max_row + 1):
                        pfx_str = ws_pfx.cell(row=r, column=6).value
                        scope_id_val = ws_pfx.cell(row=r, column=9).value
                        vlan_val = ws_pfx.cell(row=r, column=12).value
                        role_val = ws_pfx.cell(row=r, column=14).value
                        desc_val = ws_pfx.cell(row=r, column=17).value
                        if pfx_str and str(pfx_str).strip():
                            ipam_records.append({
                                "prefix_or_subnet": str(pfx_str).strip(),
                                "scope_id": scope_id_val,
                                "vlan_name": str(vlan_val or ""),
                                "role": str(role_val or ""),
                                "description": str(desc_val or "")
                            })
                    if ipam_records:
                        cnt = save_ipam_records_batch(ipam_records, clear_first=False, source="Manual CSV Upload")
                        total_prefixes += cnt
            except Exception as e:
                errors.append(f"• **{file_obj.name}**: {str(e)}")
        else:
            try:
                df = pd.read_csv(io.BytesIO(content))
                cols = {str(c).lower().strip(): c for c in df.columns}

                if "slug" in cols and ("name" in cols or "site" in cols) and "id" in cols:
                    name_col = cols.get("name", cols.get("site"))
                    id_col = cols.get("id")
                    slug_col = cols.get("slug")
                    scope_records = []
                    for _, row in df.iterrows():
                        s_name = str(row.get(name_col, "")).strip()
                        s_id = row.get(id_col)
                        s_slug = str(row.get(slug_col, "")).strip()
                        if s_name and s_name.lower() != "nan":
                            scope_records.append({"id": s_id, "name": s_name, "slug": s_slug})
                    if scope_records:
                        cnt = save_sites_batch(scope_records, clear_first=False, source="Manual CSV Upload")
                        total_scopes += cnt
                elif "prefixes" in cols or "prefix" in cols or "subnet" in cols or "vid" in cols:
                    pfx_col = cols.get("prefixes", cols.get("prefix", cols.get("subnet")))
                    vid_col = cols.get("vid", cols.get("vlan_id", cols.get("vlan", "")))
                    vname_col = cols.get("name", cols.get("vlan_name", ""))

                    ipam_records = []
                    for _, row in df.iterrows():
                        raw_prefixes = str(row.get(pfx_col, "")).strip() if pfx_col else ""
                        if not raw_prefixes or raw_prefixes.lower() == "nan":
                            continue

                        found_cidrs = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b', raw_prefixes)
                        vid = str(row.get(vid_col, "")).strip() if vid_col else ""
                        vname = str(row.get(vname_col, "")).strip() if vname_col else ""
                        role_str = str(row.get("role", "")).strip()
                        site_str = str(row.get("site", "")).strip()
                        desc_str = str(row.get("description", "")).strip()

                        for cidr in found_cidrs:
                            ipam_records.append({
                                "prefix_or_subnet": cidr,
                                "vlan_id": vid if vid.isdigit() else None,
                                "vlan_name": vname if vname.lower() != "nan" else "",
                                "role": role_str if role_str.lower() != "nan" else "",
                                "site": site_str if site_str.lower() != "nan" else "",
                                "description": desc_str if desc_str.lower() != "nan" else ""
                            })

                    if ipam_records:
                        cnt = save_ipam_records_batch(ipam_records, clear_first=False, source="Manual CSV Upload")
                        total_prefixes += cnt
                else:
                    raise ValueError(f"Unrecognized CSV format. Expected `netbox_sites.csv`, `netbox_VLANs.csv`, or `netbox_prefixes.csv`.")
            except Exception as e:
                errors.append(f"• **{file_obj.name}**: {str(e)}")

    if errors:
        for err in errors:
            st.error(err)

    if total_scopes > 0 or total_prefixes > 0:
        st.toast(f"✅ Ingested: {total_scopes} Sites, {total_prefixes} Prefixes!", icon="🚀")

def handle_ipam_db_reset():
    clear_ipam_records()
    if "ipam_persisted_rows" in st.session_state:
        del st.session_state["ipam_persisted_rows"]
    if "ipam_scope_in" in st.session_state:
        del st.session_state["ipam_scope_in"]
    if "ipam_site_in" in st.session_state:
        del st.session_state["ipam_site_in"]
    if "ipam_super_in" in st.session_state:
        del st.session_state["ipam_super_in"]
    st.toast("🗑️ Database Cleared. Restored default templates.", icon="🧹")

def on_preset_change():
    selected = st.session_state.get("ipam_preset_selector")
    template_list = VLAN_PRESETS.get(selected, [])
    
    if "ipam_data_editor_live" in st.session_state:
        del st.session_state["ipam_data_editor_live"]

    if not template_list:
        st.session_state["ipam_persisted_rows"] = []
    else:
        new_rows = []
        for t in template_list:
            role_name = t["role"]
            new_rows.append({
                "VLAN ID": t["vid"],
                "Role": role_name,
                "VLAN Name": t.get("vlan_name", role_name),
                "VLAN Description": t.get("desc", lookup_role_description(role_name)),
                "Subnet (CIDR)": ""
            })
        st.session_state["ipam_persisted_rows"] = new_rows

def render_ipam_tab(active_model: str):
    st.subheader("🌐 IPAM & Site Subnet Provisioning Engine")
    st.caption("Plan site supernets, allocate non-overlapping VLAN subnets, and export ready-to-import NetBox CSV blocks.")

    # 1. Ingestion Toolbar
    total_ipam_recs = get_total_ipam_count()
    total_sites_recs = get_total_sites_count()
    total_db_count = total_ipam_recs + total_sites_recs
    meta = get_sync_metadata("ipam")

    status_tag = f"🟢 ({total_sites_recs} sites, {total_ipam_recs} prefixes in DB)" if total_db_count > 0 else "⚪ (Default Examples)"
    tick_sites = " ✅" if total_sites_recs > 0 else ""
    tick_vlans = " ✅" if total_ipam_recs > 0 else ""
    tick_prefixes = " ✅" if total_ipam_recs > 0 else ""

    with st.expander(f"📥 Ingest NetBox Sites & VLANs / Prefixes CSV {status_tag}", expanded=False):
        if total_db_count > 0:
            st.markdown(
                f"**DB Status:** `Source: {meta['source']}` | `Last Updated: {meta['updated_at']}`"
            )

        st.markdown("**Option A: Automated Push via PowerShell Agent (Recommended):**")
        st.code('.\\Sync-NetBoxHub.ps1 -NetBoxUrl "https://xxxx" -ApiToken "xxxx" -HubUrl "xxxx"', language="powershell")

        st.caption("💡 *Press **Refresh** after the upload is completed in PowerShell to reload the local data.*")

        c_dl, c_ref = st.columns([2, 1])
        with c_dl:
            st.download_button(
                "⬇️ Download Sync-NetBoxHub.ps1 Agent",
                POWERSHELL_AGENT_CODE,
                file_name="Sync-NetBoxHub.ps1",
                mime="text/plain",
                key="dl_ps1_ipam"
            )
        with c_ref:
            if st.button("🔄 Refresh", key="ref_ipam_btn", width="stretch"):
                st.rerun()

        st.markdown(
            f"""
            **Option B: Manual CSV Export & Upload:**
            * **Scope IDs & Site Names:** Go to `Organization` ➔ `Sites` ➔ `Export` ➔ `All Data` (`netbox_sites.csv`){tick_sites}
            * **VLANs:** Go to `IPAM` ➔ `VLANs` ➔ `Export` ➔ `All Data` (`netbox_VLANs.csv`){tick_vlans}
            * **IP Prefixes:** Go to `IPAM` ➔ `Prefixes` ➔ `Export` ➔ `All Data` (`netbox_prefixes.csv`){tick_prefixes}
            """
        )
        c_up, c_rst = st.columns([2.5, 1.5])
        with c_up:
            st.file_uploader(
                "Upload NetBox CSVs (netbox_sites.csv, netbox_VLANs.csv, netbox_prefixes.csv) or Excel", 
                type=["xlsx", "csv"], 
                accept_multiple_files=True,
                key="ipam_multi_uploader",
                on_change=handle_ipam_file_upload,
                label_visibility="collapsed"
            )

        with c_rst:
            if total_db_count > 0:
                c1, c2 = st.columns(2)
                with c1:
                    with st.popover("🗑️ Clear 1 File", use_container_width=True):
                        st.markdown("**Clear Specific Dataset:**")
                        if st.button("Clear Sites (`netbox_sites.csv`)", key="btn_clr_sites", use_container_width=True):
                            clear_sites_records()
                            st.toast("🗑️ Cleared Sites table data.", icon="🧹")
                            st.rerun()
                        if st.button("Clear Prefixes & VLANs (`netbox_prefixes.csv` / `netbox_VLANs.csv`)", key="btn_clr_pfx", use_container_width=True):
                            clear_prefixes_records()
                            st.toast("🗑️ Cleared Prefixes & VLANs table data.", icon="🧹")
                            st.rerun()
                with c2:
                    st.button("🗑️ Clear All DB", on_click=handle_ipam_db_reset, use_container_width=True, key="rst_ipam_csv_btn")
            else:
                st.caption("No custom data loaded.")

    # 2. Site Inputs and Dynamic Lookups
    if "ipam_site_in" not in st.session_state:
        st.session_state["ipam_site_in"] = ""
    if "ipam_scope_in" not in st.session_state:
        st.session_state["ipam_scope_in"] = ""
    if "ipam_super_in" not in st.session_state:
        st.session_state["ipam_super_in"] = ""

    top1, top2, top3 = st.columns([2, 1, 2.2])
    with top1:
        st.text_input(
            "Branch / Site Name",
            key="ipam_site_in",
            placeholder="e.g. Bristol, AGE, Adelaide, UK, Site-01",
            on_change=handle_site_change
        )
        site_name = st.session_state["ipam_site_in"].strip()

    auto_scope_id = lookup_scope_id(site_name) if site_name else None

    with top2:
        st.text_input(
            "Scope ID (NetBox Site ID)", 
            key="ipam_scope_in",
            placeholder="e.g. 42",
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
            placeholder="e.g. 10.113.240.0/21",
            help="Top-level container subnet for this branch site."
        )
        supernet_in = st.session_state["ipam_super_in"].strip()
        cap_placeholder = st.empty()

    display_site_name = format_branch_display(site_name)
    existing_prefixes = get_existing_prefix_strings()

    # 2.5. AI IPAM & Subnet Assistant
    with st.expander("🤖 AI IPAM & Subnet Assistant", expanded=False):
        st.caption("Ask for subnet suggestions using natural language (e.g., 'I have a new office in UK with 50 devices, please suggest a subnet' or 'Suggest the next available /24 in 10.113.0.0/16')")
        
        # Initialize chat history
        if "ipam_chat_history" not in st.session_state:
            st.session_state["ipam_chat_history"] = []
        
        # Display chat messages
        for message in st.session_state["ipam_chat_history"]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        
        # Chat input
        if prompt := st.chat_input("Ask for subnet suggestions..."):
            # Add user message to chat history
            st.session_state["ipam_chat_history"].append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            
            # Generate AI response
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        from core.ai_client import call_ai
                        
                        # Prepare context for AI: combine DB + active UI table prefixes
                        ui_prefixes = []
                        for r in st.session_state.get("ipam_persisted_rows", []):
                            sub = str(r.get("Subnet (CIDR)", "") or "").strip()
                            if sub and "/" in sub:
                                ui_prefixes.append(sub)

                        combined_prefixes = list(dict.fromkeys(existing_prefixes + ui_prefixes))

                        target_networks = []
                        cidr_matches = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b", prompt)
                        if supernet_in:
                            cidr_matches.append(supernet_in)

                        for match in cidr_matches:
                            try:
                                net = ipaddress.ip_network(match, strict=False)
                                target_networks.append(net)
                            except Exception:
                                pass

                        relevant_prefixes = []
                        if target_networks:
                            for pref_str in combined_prefixes:
                                try:
                                    p_net = ipaddress.ip_network(pref_str, strict=False)
                                    if any(p_net.overlaps(tn) for tn in target_networks):
                                        relevant_prefixes.append(pref_str)
                                except Exception:
                                    continue

                        if not relevant_prefixes:
                            relevant_prefixes = combined_prefixes[:30]

                        if relevant_prefixes:
                            prefixes_context = f"Known in-use/registered prefixes: {', '.join(relevant_prefixes)}"
                            if len(combined_prefixes) > len(relevant_prefixes):
                                prefixes_context += f" (showing {len(relevant_prefixes)} relevant out of {len(combined_prefixes)} total known)"
                        else:
                            prefixes_context = "Known in-use/registered prefixes: None recorded in current database or active editor table"

                        # Extract requested mask (e.g., /24) from user prompt if specified
                        mask_match = re.search(r"/(\d{1,2})", prompt)
                        req_mask = int(mask_match.group(1)) if mask_match else 24

                        # Determine target supernet for pre-calculation
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

                        site_context = f"Current site: {site_name or 'Not specified'}"
                        supernet_context = f"Site supernet: {supernet_in or 'Not specified'}"
                        stats_context = f"Database contains: {get_total_sites_count()} sites, {get_total_ipam_count()} prefixes"
                        
                        system_prompt = f"""You are an expert network architect specializing in IP address management and subnet planning.
Your task is to analyze the user's request and suggest appropriate subnet allocations.

Context:
- {site_context}
- {supernet_context}
- {stats_context}
- {prefixes_context}

{calc_analysis}

Guidelines:
1. Strictly follow the PRE-CALCULATED SUBNET AVAILABILITY ANALYSIS when present above. Never suggest a subnet marked OCCUPIED / OVERLAPS.
2. Suggest non-overlapping CIDR subnets within the site supernet when specified.
3. Note that subnets overlapping with larger or smaller blocks (e.g., 10.113.240.0/24 inside 10.113.240.0/23) are OCCUPIED and unavailable.
4. Provide clear reasoning for your suggestions.
5. Format CIDR notation properly (e.g., 10.113.242.0/24).
6. If insufficient information is provided, ask clarifying questions.
7. Be concise but thorough in your analysis."""
                        
                        # Use the active model from sidebar
                        ai_response = call_ai(prompt, active_model, custom_system_msg=system_prompt)
                        
                        st.markdown(ai_response)
                        # Add assistant response to chat history
                        st.session_state["ipam_chat_history"].append({"role": "assistant", "content": ai_response})
                        
                    except Exception as e:
                        error_msg = f"❌ AI Assistant temporarily unavailable: {str(e)}"
                        st.error(error_msg)
                        st.session_state["ipam_chat_history"].append({"role": "assistant", "content": error_msg})

    # 3. Preset Selection & Allocation Editor
    st.markdown("---")
    c_title, c_preset = st.columns([2.5, 1.5])
    with c_title:
        st.markdown("##### 📊 Subnet Allocation & Live Status (✏️ Click any cell to edit)")
    with c_preset:
        st.selectbox(
            "Load Standard Preset",
            options=list(VLAN_PRESETS.keys()),
            index=0,
            key="ipam_preset_selector",
            on_change=on_preset_change,
            help="Quickly load pre-defined standard VLAN structures or start blank."
        )

    if "ipam_persisted_rows" not in st.session_state:
        st.session_state["ipam_persisted_rows"] = []

    # Sync editor deltas
    raw_rows = [dict(r) for r in st.session_state["ipam_persisted_rows"]]
    editor_state = st.session_state.get("ipam_data_editor_live", {})
    
    deleted_indices = set(editor_state.get("deleted_rows", []))
    if deleted_indices:
        raw_rows = [r for i, r in enumerate(raw_rows) if i not in deleted_indices]

    edited_cells = editor_state.get("edited_rows", {})
    for row_idx_str, changes in edited_cells.items():
        row_idx = int(row_idx_str)
        if row_idx < len(raw_rows):
            if "Role" in changes and "VLAN Name" not in changes:
                changes["VLAN Name"] = changes["Role"]
                if "VLAN Description" not in changes:
                    changes["VLAN Description"] = lookup_role_description(changes["Role"])
            # Apply Title Case formatting to Role and Description
            if "Role" in changes:
                changes["Role"] = to_title_case_preserve_acronyms(changes["Role"])
            if "VLAN Description" in changes:
                changes["VLAN Description"] = to_title_case_preserve_acronyms(changes["VLAN Description"])
            raw_rows[row_idx].update(changes)

    for new_r in editor_state.get("added_rows", []):
        r_name = new_r.get("Role", "")
        raw_rows.append({
            "VLAN ID": new_r.get("VLAN ID", None),
            "Role": to_title_case_preserve_acronyms(r_name),
            "VLAN Name": new_r.get("VLAN Name", r_name),
            "VLAN Description": to_title_case_preserve_acronyms(new_r.get("VLAN Description", lookup_role_description(r_name))),
            "Subnet (CIDR)": new_r.get("Subnet (CIDR)", "")
        })

    computed_rows = compute_chained_rows(supernet_in, raw_rows)
    st.session_state["ipam_persisted_rows"] = computed_rows

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
            existing_prefixes
        )
        r["Usable Range"] = eval_res["usable_range"]
        r["Status"] = eval_res["status"]
        r["Prefix Description"] = eval_res["desc"]

    # Real-time Available Subnets and Capacity
    with cap_placeholder.container():
        if supernet_in and "/" in supernet_in:
            try:
                sup_net = ipaddress.ip_network(supernet_in, strict=False)
                sup_range = calculate_ip_range_str(sup_net)
                cap_matrix = calculate_remaining_subnets(supernet_in, allocated_subnets)
                cap_str = f"**Available:** `{cap_matrix['/24']}x /24` | `{cap_matrix['/25']}x /25` | `{cap_matrix['/26']}x /26` | `{cap_matrix['/27']}x /27`"
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
            "VLAN Name": st.column_config.TextColumn("VLAN Name", help="VLAN Name in NetBox. Defaults to Role, or editable."),
            "VLAN Description": st.column_config.TextColumn("VLAN Description", help="VLAN Description. Auto-looked up from DB or editable."),
            "Suggest Subnet": st.column_config.TextColumn("Suggest Subnet", help="Calculated next available network IP ID.", disabled=True),
            "Subnet (CIDR)": st.column_config.TextColumn("Subnet (CIDR)", help="Type subnet CIDR (e.g. 10.113.252.0/23) and hit Enter."),
            "Usable Range": st.column_config.TextColumn("Usable Range", help="Calculated usable host IP range.", disabled=True),
            "Status": st.column_config.TextColumn("Status", help="Collision & Database usage status.", disabled=True),
            "Prefix Description": st.column_config.TextColumn("Prefix Description", help="Calculated NetBox prefix description.", disabled=True)
        }
    )

    # 4. NetBox Bulk-Import CSV Copy Cards & Scope ID Notification
    st.markdown("---")
    st.markdown("### 📋 NetBox Bulk-Import CSV Generators")

    if not scope_id:
        st.warning(
            "⚠️ **Notice: Scope ID (NetBox Site ID) is empty.** NetBox requires a valid Site `scope_id` to import VLAN Groups and Prefixes. "
            "If this is a newly created site, please first import `site.csv` into NetBox, export your latest `netbox_sites.csv` from NetBox and upload it above, or manually type the created Site ID into the **Scope ID** box.",
            icon="⚠️"
        )

    display_site = display_site_name or "Site"
    csv_site = generate_netbox_site_csv(display_site)
    csv_group = generate_netbox_vlan_group_csv(display_site, scope_id)
    csv_vlans = generate_netbox_vlans_csv(display_site, computed_rows)
    csv_prefixes = generate_netbox_prefixes_csv(display_site, scope_id, supernet_in, computed_rows)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**1. Import Site (`dcim.site`)**")
        st.code(csv_site, language="csv")

        st.markdown("**3. Import VLANs (`ipam.vlan`)** *(Assigned IP subnets only)*")
        st.code(csv_vlans, language="csv")

    with c2:
        st.markdown("**2. Import VLAN Group (`ipam.vlangroup`)**")
        st.code(csv_group, language="csv")

        st.markdown("**4. Import Prefixes (`ipam.prefix`)**")
        st.code(csv_prefixes, language="csv")