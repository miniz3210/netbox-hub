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
    get_total_ipam_count,
    get_total_sites_count,
    lookup_scope_id,
    lookup_site_supernet_from_db,
    get_existing_prefix_strings,
    get_sync_metadata
)

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
                    raise ValueError(f"Unrecognized CSV format. Expected `netbox_sites.csv` or `netbox_VLANs.csv`.")
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
            * **VLANs & In-Use Prefixes:** Go to `IPAM` ➔ `VLANs` ➔ `Export` ➔ `All Data` (`netbox_VLANs.csv`){tick_vlans}
            """
        )
        c_up, c_rst = st.columns([3, 1])
        with c_up:
            st.file_uploader(
                "Upload NetBox CSVs (netbox_sites.csv, netbox_VLANs.csv) or Excel", 
                type=["xlsx", "csv"], 
                accept_multiple_files=True,
                key="ipam_multi_uploader",
                on_change=handle_ipam_file_upload,
                label_visibility="collapsed"
            )

        with c_rst:
            if total_db_count > 0:
                st.button("🗑️ Clear DB", on_click=handle_ipam_db_reset, width="stretch", key="rst_ipam_csv_btn")
            else:
                st.caption("No custom data loaded.")

    # 2. Site Inputs
    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        site_name = st.text_input(
            "Branch / Site Name",
            value="",
            key="ipam_site_in",
            placeholder="e.g. Bristol, AGE, Adelaide, UK, Site-01",
        ).strip()

    auto_scope_id = lookup_scope_id(site_name) if site_name else None
    auto_supernet = lookup_site_supernet_from_db(site_name) if site_name else None

    if site_name:
        last_synced_site = st.session_state.get("_last_synced_site", "")
        if last_synced_site != site_name:
            st.session_state["_last_synced_site"] = site_name
            if auto_scope_id is not None:
                st.session_state["ipam_scope_in"] = str(auto_scope_id)
            if auto_supernet is not None:
                st.session_state["ipam_super_in"] = str(auto_supernet)

    display_site_name = format_branch_display(site_name)

    with top2:
        scope_id = st.text_input(
            "Scope ID (NetBox Site ID)", 
            value=st.session_state.get("ipam_scope_in", ""),
            key="ipam_scope_in",
            placeholder="e.g. 42",
            help="Auto-discovered from uploaded data/agent sync, or editable manually."
        ).strip()
        if auto_scope_id:
            st.caption(f"🟢 Matched Scope ID: **`{auto_scope_id}`**")
        else:
            st.caption("⚪ Manual Scope ID mode")

    with top3:
        supernet_in = st.text_input(
            "Site Supernet (CIDR)", 
            value=st.session_state.get("ipam_super_in", auto_supernet or ""), 
            key="ipam_super_in",
            placeholder="e.g. 10.1.0.0/16",
            help="Top-level container subnet for this branch site."
        ).strip()
        if supernet_in and "/" in supernet_in:
            try:
                sup_net = ipaddress.ip_network(supernet_in, strict=False)
                sup_range = calculate_ip_range_str(sup_net)
                st.caption(f"📍 Supernet Usable Range: **`{sup_range}`**")
            except ValueError:
                st.caption("⚠️ Invalid CIDR format")

    existing_prefixes = get_existing_prefix_strings()

    # 3. Preset Selection & Dynamic Allocation Editor
    st.markdown("---")
    c_title, c_preset = st.columns([2.5, 1.5])
    with c_title:
        st.markdown("##### 📊 Subnet Allocation Editor (✏️ Click any cell to edit)")
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

    # Apply Delta Changes from previous interaction before computing next suggestions
    raw_rows = [dict(r) for r in st.session_state["ipam_persisted_rows"]]
    editor_state = st.session_state.get("ipam_data_editor_live", {})
    
    # 1. Apply row deletions
    deleted_indices = set(editor_state.get("deleted_rows", []))
    if deleted_indices:
        raw_rows = [r for i, r in enumerate(raw_rows) if i not in deleted_indices]

    # 2. Apply cell edits
    edited_cells = editor_state.get("edited_rows", {})
    for row_idx_str, changes in edited_cells.items():
        row_idx = int(row_idx_str)
        if row_idx < len(raw_rows):
            # If user typed or changed Role, auto-update VLAN Name & auto-lookup description
            if "Role" in changes and "VLAN Name" not in changes:
                changes["VLAN Name"] = changes["Role"]
                if "VLAN Description" not in changes:
                    changes["VLAN Description"] = lookup_role_description(changes["Role"])
            raw_rows[row_idx].update(changes)

    # 3. Apply added rows
    for new_r in editor_state.get("added_rows", []):
        r_name = new_r.get("Role", "")
        raw_rows.append({
            "VLAN ID": new_r.get("VLAN ID", None),
            "Role": r_name,
            "VLAN Name": new_r.get("VLAN Name", r_name),
            "VLAN Description": new_r.get("VLAN Description", lookup_role_description(r_name)),
            "Subnet (CIDR)": new_r.get("Subnet (CIDR)", "")
        })

    # Accurately compute next network suggestions on the updated rows
    computed_rows = compute_chained_rows(supernet_in, raw_rows)
    st.session_state["ipam_persisted_rows"] = computed_rows

    TABLE_COLS = ["VLAN ID", "Role", "VLAN Name", "VLAN Description", "Suggest Subnet", "Subnet (CIDR)"]
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
            "Role": st.column_config.TextColumn("Role", help="VLAN Role (e.g. Corporate WiFi, Workstations). Auto-sets VLAN Name & Description."),
            "VLAN Name": st.column_config.TextColumn("VLAN Name", help="VLAN Name in NetBox. Defaults to Role, or editable."),
            "VLAN Description": st.column_config.TextColumn("VLAN Description", help="VLAN Description. Auto-looked up from Role, or editable."),
            "Suggest Subnet": st.column_config.TextColumn("Suggest Subnet", help="Calculated next available network IP ID (without subnet mask).", disabled=True),
            "Subnet (CIDR)": st.column_config.TextColumn("Subnet (CIDR)", help="Type subnet CIDR (e.g. 10.113.66.0/24, 10.113.66.0/23) and hit Enter.")
        }
    )

    # 4. Live Usable Ranges & Status Evaluation
    final_records = []
    allocated_subnets = []
    
    for r in computed_rows:
        sub_str = str(r.get("Subnet (CIDR)", "") or "").strip()
        allocated_subnets.append(sub_str)
        eval_res = evaluate_subnet_row(
            sub_str, 
            r.get("VLAN ID"), 
            r.get("Role", ""), 
            site_name, 
            supernet_in, 
            existing_prefixes
        )
        row_eval = dict(r)
        row_eval["Usable Range"] = eval_res["usable_range"]
        row_eval["Status"] = eval_res["status"]
        row_eval["Prefix Description"] = eval_res["desc"]
        final_records.append(row_eval)

    c_prev, c_cap = st.columns([3, 1.2])
    with c_prev:
        st.markdown("##### 🔍 Live Usable IP Ranges & Collision Status")
        PREV_COLS = ["VLAN ID", "Role", "VLAN Name", "Subnet (CIDR)", "Usable Range", "Status", "Prefix Description"]
        if final_records:
            df_prev = pd.DataFrame(final_records)[PREV_COLS]
        else:
            df_prev = pd.DataFrame(columns=PREV_COLS)
            
        st.dataframe(
            df_prev, 
            width="stretch", 
            hide_index=True
        )

    with c_cap:
        st.markdown("##### 📈 Remaining Capacity")
        cap_matrix = calculate_remaining_subnets(supernet_in, allocated_subnets)
        cap_rows = [{"Subnet Size": k, "Available": f"{v} subnets"} for k, v in cap_matrix.items()]
        st.dataframe(pd.DataFrame(cap_rows), width="stretch", hide_index=True)

    # 5. NetBox Bulk-Import CSV Copy Cards
    st.markdown("---")
    st.markdown("### 📋 NetBox Bulk-Import CSV Generators")

    display_site = display_site_name or "Site"
    csv_site = generate_netbox_site_csv(display_site)
    csv_group = generate_netbox_vlan_group_csv(display_site, scope_id or "0")
    csv_vlans = generate_netbox_vlans_csv(display_site, final_records)
    csv_prefixes = generate_netbox_prefixes_csv(display_site, scope_id or "0", supernet_in, final_records)

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