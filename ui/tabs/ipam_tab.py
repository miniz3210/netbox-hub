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
    lookup_scope_id,
    lookup_site_supernet_from_db,
    get_existing_prefix_strings
)

def handle_ipam_file_upload():
    uploaded = st.session_state.get("ipam_uploader_widget")
    if uploaded is not None:
        filename = uploaded.name.lower()
        try:
            if filename.endswith(".xlsx"):
                wb = openpyxl.load_workbook(uploaded, data_only=True)
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
                        save_sites_batch(scope_records, clear_first=False)

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
                        save_ipam_records_batch(ipam_records, clear_first=True)
                st.toast(f"✅ Ingested {len(scope_records)} Scopes and {len(ipam_records)} Prefixes from Excel!", icon="📊")
            else:
                df = pd.read_csv(uploaded)
                cols = {str(c).lower().strip(): c for c in df.columns}
                scope_records = []
                ipam_records = []

                if "slug" in cols and ("name" in cols or "site" in cols):
                    name_c = cols.get("name", cols.get("site"))
                    id_c = cols.get("id")
                    slug_c = cols.get("slug")
                    for _, row in df.iterrows():
                        s_name = str(row.get(name_c, "")).strip()
                        s_id = row.get(id_c) if id_c else None
                        s_slug = str(row.get(slug_c, "")).strip()
                        if s_name and s_name.lower() != "nan":
                            scope_records.append({"id": s_id, "name": s_name, "slug": s_slug})
                    if scope_records:
                        save_sites_batch(scope_records, clear_first=False)

                pfx_col = cols.get("prefix", cols.get("address", cols.get("subnet", cols.get("ip address", ""))))
                vid_col = cols.get("vlan_id", cols.get("vid", cols.get("vlan", "")))
                vname_col = cols.get("vlan_name", cols.get("vlan", cols.get("name", "")))
                role_col = cols.get("role", cols.get("vlan_role", ""))
                site_col = cols.get("site", cols.get("scope", cols.get("scope_id", "")))
                desc_col = cols.get("description", cols.get("comments", ""))

                for _, row in df.iterrows():
                    sub = str(row.get(pfx_col, "")).strip() if pfx_col else ""
                    if not sub or sub.lower() == "nan":
                        continue
                    vid = str(row.get(vid_col, "")).strip() if vid_col else ""
                    vname = str(row.get(vname_col, "")).strip() if vname_col else ""
                    role = str(row.get(role_col, "")).strip() if role_col else ""
                    site_val = str(row.get(site_col, "")).strip() if site_col else ""
                    desc = str(row.get(desc_col, "")).strip() if desc_col else ""

                    ipam_records.append({
                        "prefix_or_subnet": sub,
                        "vlan_id": vid if vid.isdigit() else None,
                        "vlan_name": vname if vname.lower() != "nan" else "",
                        "role": role if role.lower() != "nan" else "",
                        "site": site_val if site_val.lower() != "nan" else "",
                        "description": desc if desc.lower() != "nan" else ""
                    })

                if ipam_records:
                    save_ipam_records_batch(ipam_records, clear_first=True)
                st.toast(f"✅ Ingested {len(ipam_records)} IP/Prefix records from CSV!", icon="🌐")
        except Exception as e:
            st.error(f"Error reading file: {e}")

def render_ipam_tab(active_model: str):
    st.subheader("🌐 IPAM & Site Subnet Provisioning Engine")
    st.caption("Plan site supernets, allocate non-overlapping VLAN subnets, and export ready-to-import NetBox CSV blocks.")

    # 1. Ingestion Toolbar
    total_ipam_recs = get_total_ipam_count()
    status_label = f"🟢 ({total_ipam_recs} records in IPAM database)" if total_ipam_recs > 0 else "⚪ (No custom file loaded - Template Mode)"

    with st.expander(f"📁 Ingest Scope & Prefixes / IP Address File {status_label}", expanded=(total_ipam_recs == 0)):
        c_file, c_btn, c_clr = st.columns([3, 1.2, 1])
        with c_file:
            uploaded_file = st.file_uploader(
                "Upload Standard VLAN_Prefixes_v2.xlsx or NetBox CSV", 
                type=["xlsx", "csv"], 
                key="ipam_uploader_widget"
            )
        with c_btn:
            st.write("")
            if st.button("📥 Load & Process File", use_container_width=True, key="btn_do_load_ipam"):
                if uploaded_file is not None:
                    handle_ipam_file_upload()
                    st.rerun()
                else:
                    st.warning("Please choose a file first.")
        with c_clr:
            st.write("")
            if total_ipam_recs > 0:
                if st.button("🗑️ Clear DB", use_container_width=True, key="btn_clr_ipam_records"):
                    clear_ipam_records()
                    if "ipam_persisted_rows" in st.session_state:
                        del st.session_state["ipam_persisted_rows"]
                    st.toast("🧹 IPAM database cleared.", icon="🗑️")
                    st.rerun()

    # 2. Site Inputs
    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        site_name = st.text_input(
            "Branch / Site Name",
            value="",
            key="ipam_site_in",
            placeholder="e.g. London Branch, Site-01",
        ).strip()

    # Auto-lookup Scope ID and Site Supernet from stored records
    auto_scope_id = lookup_scope_id(site_name) if site_name else None
    auto_supernet = lookup_site_supernet_from_db(site_name) if site_name else None

    with top2:
        scope_display = str(auto_scope_id) if auto_scope_id is not None else ""
        scope_id = st.text_input(
            "Scope ID (NetBox Site ID)", 
            value=scope_display, 
            key="ipam_scope_in",
            placeholder="e.g. 42",
            help="Auto-discovered from uploaded Scope data, or editable manually."
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
            placeholder="e.g. 10.0.0.0/21",
            help="Top-level container subnet for this branch site."
        ).strip()
        if supernet_in and "/" in supernet_in:
            try:
                sup_net = ipaddress.ip_network(supernet_in, strict=False)
                sup_range = calculate_ip_range_str(sup_net)
                st.caption(f"📍 Supernet Usable Range: **`{sup_range}`**")
            except ValueError:
                st.caption("⚠️ Invalid CIDR format")

    include_opt = st.toggle("Include Optional VLANs (Routing, OT, IoT)", value=True, key="ipam_opt_toggle")
    existing_prefixes = get_existing_prefix_strings()

    # 3. Base Row Template Structure
    selected_templates = [v for v in STANDARD_VLAN_TEMPLATES if include_opt or not v["opt"]]
    
    working_rows = []
    for v in selected_templates:
        working_rows.append({
            "VLAN ID": v["vid"],
            "Role": v["role"],
            "Subnet (CIDR)": "",
            "fallback_subnet": v.get("fallback_subnet", "")
        })

    # Overlay stored rows from previous cycle if present
    if "ipam_persisted_rows" in st.session_state:
        stored = st.session_state["ipam_persisted_rows"]
        for idx, r in enumerate(working_rows):
            if idx < len(stored):
                r["VLAN ID"] = stored[idx].get("VLAN ID", r["VLAN ID"])
                r["Role"] = stored[idx].get("Role", r["Role"])
                r["Subnet (CIDR)"] = stored[idx].get("Subnet (CIDR)", "")

    # Overlay immediate widget edits from st.session_state BEFORE building DataFrame
    widget_state = st.session_state.get("ipam_data_editor", {})
    edited_changes = widget_state.get("edited_rows", {})
    for row_idx_str, changes in edited_changes.items():
        row_idx = int(row_idx_str)
        if row_idx < len(working_rows):
            for col_name, val in changes.items():
                if col_name in working_rows[row_idx]:
                    working_rows[row_idx][col_name] = val

    # Compute chained rows with all new edits
    computed_rows = compute_chained_rows(supernet_in, working_rows)
    st.session_state["ipam_persisted_rows"] = computed_rows

    df_init = pd.DataFrame(computed_rows)[[
        "VLAN ID", "VLAN Name", "Role", "VLAN Description", "Suggest Subnet", "Subnet (CIDR)"
    ]]

    st.markdown("##### 📊 Subnet Allocation Editor (✏️ Click any cell to edit)")

    edited_df = st.data_editor(
        df_init,
        use_container_width=True,
        num_rows="dynamic",
        key="ipam_data_editor",
        column_config={
            "VLAN ID": st.column_config.NumberColumn("VLAN ID", step=1, required=True),
            "VLAN Name": st.column_config.TextColumn("VLAN Name", help="Auto-derived from Role.", disabled=True),
            "Role": st.column_config.TextColumn("Role", help="Type role (e.g. Guest, Corporate WiFi, Audio Visual). Auto-corrects on Enter."),
            "VLAN Description": st.column_config.TextColumn("VLAN Description", help="Auto-looked up from Role.", disabled=True),
            "Suggest Subnet": st.column_config.TextColumn("Suggest Subnet", help="Calculated dynamically based on previous Subnet (CIDR) input.", disabled=True),
            "Subnet (CIDR)": st.column_config.TextColumn("Subnet (CIDR)", help="Type subnet CIDR (e.g. 10.113.64.0/23) and hit Enter.")
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
            pd.DataFrame(final_records)[["VLAN ID", "VLAN Name", "Subnet (CIDR)", "Usable Range", "Status", "Prefix Description"]], 
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

    display_site = site_name or "Site"
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