import io
import re
import ipaddress
import streamlit as st
import pandas as pd
import openpyxl
from core.ipam_engine import (
    STANDARD_VLAN_TEMPLATES,
    compute_chained_rows,
    slugify,
    evaluate_subnet_row,
    calculate_remaining_subnets,
    calculate_ip_range_str,
    format_branch_display,
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
    get_existing_prefix_strings
)

def process_uploaded_files(uploaded_files):
    if not uploaded_files:
        return 0, 0, []

    total_scopes = 0
    total_prefixes = 0
    errors = []

    for file_obj in uploaded_files:
        filename = file_obj.name.lower()
        content = file_obj.getvalue()
        
        # 1. Multi-Sheet Excel (.xlsx)
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
                        cnt = save_sites_batch(scope_records, clear_first=False)
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
                        cnt = save_ipam_records_batch(ipam_records, clear_first=False)
                        total_prefixes += cnt
            except Exception as e:
                errors.append(f"• **{file_obj.name}**: {str(e)}")

        # 2. CSV Files with Strict Validation
        else:
            try:
                df = pd.read_csv(io.BytesIO(content))
                cols = {str(c).lower().strip(): c for c in df.columns}

                # Validation Guard A: Reject IP Addresses export
                if "address" in cols and ("interface" in cols or "device" in cols or "assigned_object" in cols or "dns_name" in cols or "assigned" in cols or "nat" in cols):
                    raise ValueError(f"This is a NetBox IP Addresses export (`netbox_IP addresses.csv`). Please upload `netbox_sites.csv` or `netbox_VLANs.csv`.")
                if "address" in cols and not ("prefixes" in cols or "prefix" in cols or "subnet" in cols or "slug" in cols):
                    raise ValueError(f"This is an individual host IP addresses export (`netbox_IP addresses.csv`). Please upload `netbox_sites.csv` or `netbox_VLANs.csv`.")

                # Validation Guard B: Reject Device / VM export
                if "device type" in cols or "serial" in cols or "asset tag" in cols or "vcpus" in cols:
                    raise ValueError(f"This is a Device/VM export (`netbox_devices.csv`). Please upload it in the 'Naming' tab instead.")

                # Case A: netbox_sites.csv (Name, Status, Facility, Region, Group, Tenant, Description, ID, Slug...)
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
                        cnt = save_sites_batch(scope_records, clear_first=False)
                        total_scopes += cnt

                # Case B: netbox_VLANs.csv or prefix export (VID, Name, Site, Group, Prefixes...)
                elif "prefixes" in cols or "prefix" in cols or "subnet" in cols or "vid" in cols or "q-in-q role" in cols:
                    pfx_col = cols.get("prefixes", cols.get("prefix", cols.get("subnet")))
                    vid_col = cols.get("vid", cols.get("vlan_id", cols.get("vlan", "")))
                    vname_col = cols.get("name", cols.get("vlan_name", ""))
                    role_col = cols.get("role", cols.get("vlan_role", ""))
                    site_col = cols.get("site", cols.get("scope", ""))
                    desc_col = cols.get("description", cols.get("comments", ""))

                    ipam_records = []
                    for _, row in df.iterrows():
                        raw_prefixes = str(row.get(pfx_col, "")).strip() if pfx_col else ""
                        if not raw_prefixes or raw_prefixes.lower() == "nan":
                            continue

                        found_cidrs = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b', raw_prefixes)
                        vid = str(row.get(vid_col, "")).strip() if vid_col else ""
                        vname = str(row.get(vname_col, "")).strip() if vname_col else ""
                        role = str(row.get(role_col, "")).strip() if role_col else ""
                        site_val = str(row.get(site_col, "")).strip() if site_col else ""
                        desc = str(row.get(desc_col, "")).strip() if desc_col else ""

                        for cidr in found_cidrs:
                            ipam_records.append({
                                "prefix_or_subnet": cidr,
                                "vlan_id": vid if vid.isdigit() else None,
                                "vlan_name": vname if vname.lower() != "nan" else "",
                                "role": role if role.lower() != "nan" else "",
                                "site": site_val if site_val.lower() != "nan" else "",
                                "description": desc if desc.lower() != "nan" else ""
                            })

                    if ipam_records:
                        cnt = save_ipam_records_batch(ipam_records, clear_first=False)
                        total_prefixes += cnt
                else:
                    raise ValueError(f"Unrecognized CSV format. Expected `netbox_sites.csv` or `netbox_VLANs.csv`.")

            except Exception as e:
                errors.append(f"• **{file_obj.name}**: {str(e)}")

    return total_scopes, total_prefixes, errors

def handle_ipam_db_reset():
    clear_ipam_records()
    if "ipam_persisted_rows" in st.session_state:
        del st.session_state["ipam_persisted_rows"]
    if "ipam_scope_in" in st.session_state:
        del st.session_state["ipam_scope_in"]
    st.toast("🗑️ Database Cleared. Restored default templates.", icon="🧹")

def render_ipam_tab(active_model: str):
    st.subheader("🌐 IPAM & Site Subnet Provisioning Engine")
    st.caption("Plan site supernets, allocate non-overlapping VLAN subnets, and export ready-to-import NetBox CSV blocks.")

    # 1. Ingestion Toolbar with Strict Validation and Checkmarks
    total_ipam_recs = get_total_ipam_count()
    total_sites_recs = get_total_sites_count()
    total_db_count = total_ipam_recs + total_sites_recs
    status_tag = f"🟢 ({total_sites_recs} sites, {total_ipam_recs} prefixes in DB)" if total_db_count > 0 else "⚪ (Default Examples)"

    tick_sites = " ✅" if total_sites_recs > 0 else ""
    tick_vlans = " ✅" if total_ipam_recs > 0 else ""

    with st.expander(f"📥 Ingest NetBox Sites & VLANs / Prefixes CSV {status_tag}", expanded=False):
        st.markdown(
            f"""
            **Export Instructions from NetBox:**
            * **Scope IDs & Site Names:** Go to `Organization` ➔ `Sites` ➔ `Export` ➔ `All Data` (`netbox_sites.csv`){tick_sites}
            * **VLANs & In-Use Prefixes:** Go to `IPAM` ➔ `VLANs` ➔ `Export` ➔ `All Data` (`netbox_VLANs.csv`){tick_vlans}
            * *Tip: You can select and upload both CSV files together.*
            """
        )
        c_up, c_rst = st.columns([3, 1])
        with c_up:
            uploaded_files = st.file_uploader(
                "Upload NetBox CSVs (netbox_sites.csv, netbox_VLANs.csv) or Excel", 
                type=["xlsx", "csv"], 
                accept_multiple_files=True,
                key="ipam_multi_uploader",
                label_visibility="collapsed"
            )
            if uploaded_files:
                sc_cnt, pfx_cnt, errs = process_uploaded_files(uploaded_files)
                if errs:
                    for err in errs:
                        st.error(err)
                if sc_cnt > 0 or pfx_cnt > 0:
                    st.toast(f"✅ Ingested: {sc_cnt} Sites, {pfx_cnt} Prefixes!", icon="🚀")

        with c_rst:
            if total_db_count > 0:
                st.button("🗑️ Clear DB", on_click=handle_ipam_db_reset, use_container_width=True, key="rst_ipam_csv_btn")
            else:
                st.caption("No custom data loaded.")

    # 2. Site Inputs
    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        site_name = st.text_input(
            "Branch / Site Name",
            value="",
            key="ipam_site_in",
            placeholder="e.g. Bristol, AGE, Adelaide, Site-01",
        ).strip()

    auto_scope_id = lookup_scope_id(site_name) if site_name else None
    auto_supernet = lookup_site_supernet_from_db(site_name) if site_name else None

    if auto_scope_id is not None:
        last_site = st.session_state.get("_last_synced_site", "")
        if last_site != site_name:
            st.session_state["ipam_scope_in"] = str(auto_scope_id)
            st.session_state["_last_synced_site"] = site_name

    display_site_name = format_branch_display(site_name)

    with top2:
        scope_id = st.text_input(
            "Scope ID (NetBox Site ID)", 
            value=st.session_state.get("ipam_scope_in", ""),
            key="ipam_scope_in",
            placeholder="e.g. 42",
            help="Auto-discovered from uploaded netbox_sites.csv, or editable manually."
        ).strip()
        if auto_scope_id:
            st.caption(f"🟢 Matched Scope ID: **`{auto_scope_id}`**")
        else:
            st.caption("⚪ Manual Scope ID mode")

    with top3:
        supernet_default = auto_supernet or ""
        supernet_in = st.text_input(
            "Site Supernet (CIDR)", 
            value=supernet_default, 
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

    # 3. Base Row Template Structure with Dynamic Row Support
    base_default = []
    for v in STANDARD_VLAN_TEMPLATES:
        base_default.append({
            "VLAN ID": v["vid"],
            "Role": v["role"],
            "VLAN Name": v["role"],
            "VLAN Description": "",
            "Subnet (CIDR)": "",
            "fallback_subnet": v.get("fallback_subnet", "")
        })

    if "ipam_persisted_rows" in st.session_state:
        working_rows = [dict(r) for r in st.session_state["ipam_persisted_rows"]]
    else:
        working_rows = [dict(t) for t in base_default]

    widget_state = st.session_state.get("ipam_data_editor", {})

    deleted_indices = set(widget_state.get("deleted_rows", []))
    if deleted_indices:
        working_rows = [r for i, r in enumerate(working_rows) if i not in deleted_indices]

    edited_changes = widget_state.get("edited_rows", {})
    for row_idx_str, changes in edited_changes.items():
        row_idx = int(row_idx_str)
        if row_idx < len(working_rows):
            working_rows[row_idx].update(changes)

    added_changes = widget_state.get("added_rows", [])
    for new_row in added_changes:
        working_rows.append({
            "VLAN ID": new_row.get("VLAN ID", None),
            "Role": new_row.get("Role", ""),
            "VLAN Name": new_row.get("VLAN Name", ""),
            "VLAN Description": new_row.get("VLAN Description", ""),
            "Subnet (CIDR)": new_row.get("Subnet (CIDR)", ""),
            "fallback_subnet": ""
        })

    for r in working_rows:
        for k in ["Role", "VLAN Name", "VLAN Description", "Subnet (CIDR)"]:
            if k not in r or r[k] is None or str(r[k]).lower() == "none":
                r[k] = ""
        if r.get("VLAN ID") is not None and str(r.get("VLAN ID")).lower() == "none":
            r["VLAN ID"] = None

    computed_rows = compute_chained_rows(supernet_in, working_rows)
    st.session_state["ipam_persisted_rows"] = computed_rows

    df_init = pd.DataFrame(computed_rows)[[
        "VLAN ID", "Role", "VLAN Name", "VLAN Description", "Suggest Subnet", "Subnet (CIDR)"
    ]]

    st.markdown("##### 📊 Subnet Allocation Editor (✏️ Click any cell to edit)")

    edited_df = st.data_editor(
        df_init,
        use_container_width=True,
        num_rows="dynamic",
        key="ipam_data_editor",
        column_config={
            "VLAN ID": st.column_config.NumberColumn("VLAN ID", step=1, required=True),
            "Role": st.column_config.TextColumn("Role", help="Type role (e.g. Guest, Corporate WiFi, Audio Visual). Auto-corrects on Enter."),
            "VLAN Name": st.column_config.TextColumn("VLAN Name", help="VLAN Name. Auto-filled from Role or editable."),
            "VLAN Description": st.column_config.TextColumn("VLAN Description", help="VLAN Description. Auto-looked up from Role or editable."),
            "Suggest Subnet": st.column_config.TextColumn("Suggest Subnet", help="Calculated dynamically based on previous Subnet (CIDR) input.", disabled=True),
            "Subnet (CIDR)": st.column_config.TextColumn("Subnet (CIDR)", help="Type subnet CIDR (e.g. 10.1.1.0/24) and hit Enter.")
        }
    )

    # 4. Live Usable Ranges and Status Evaluation
    final_records = computed_rows
    allocated_subnets = []
    
    for r in final_records:
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
        r["Usable Range"] = eval_res["usable_range"]
        r["Status"] = eval_res["status"]
        r["Prefix Description"] = eval_res["desc"]

    # Preview summary table and Remaining Capacity Matrix
    c_prev, c_cap = st.columns([3, 1.2])
    with c_prev:
        st.markdown("##### 🔍 Live Usable IP Ranges & Collision Status")
        st.dataframe(
            pd.DataFrame(final_records)[["VLAN ID", "Role", "VLAN Name", "Subnet (CIDR)", "Usable Range", "Status", "Prefix Description"]], 
            use_container_width=True, 
            hide_index=True
        )

    with c_cap:
        st.markdown("##### 📈 Remaining Capacity")
        cap_matrix = calculate_remaining_subnets(supernet_in, allocated_subnets)
        cap_rows = [{"Subnet Size": k, "Available": f"{v} subnets"} for k, v in cap_matrix.items()]
        st.dataframe(pd.DataFrame(cap_rows), use_container_width=True, hide_index=True)

    # 5. NetBox Bulk-Import CSV Generators
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
        st.download_button("⬇️ Download Site CSV", csv_site, f"site_{slugify(display_site)}.csv", "text/csv", key="dl_site_csv")

        st.markdown("**3. Import VLANs (`ipam.vlan`)**")
        st.code(csv_vlans, language="csv")
        st.download_button("⬇️ Download VLANs CSV", csv_vlans, f"vlans_{slugify(display_site)}.csv", "text/csv", key="dl_vlans_csv")

    with c2:
        st.markdown("**2. Import VLAN Group (`ipam.vlangroup`)**")
        st.code(csv_group, language="csv")
        st.download_button("⬇️ Download VLAN Group CSV", csv_group, f"vlangroup_{slugify(display_site)}.csv", "text/csv", key="dl_group_csv")

        st.markdown("**4. Import Prefixes (`ipam.prefix`)**")
        st.code(csv_prefixes, language="csv")
        st.download_button("⬇️ Download Prefixes CSV", csv_prefixes, f"prefixes_{slugify(display_site)}.csv", "text/csv", key="dl_prefixes_csv")