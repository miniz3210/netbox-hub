import streamlit as st
import pandas as pd
import openpyxl
from core.ipam_engine import (
    STANDARD_VLAN_TEMPLATES, 
    slice_supernet, 
    slugify, 
    evaluate_subnet_row,
    calculate_remaining_subnets,
    generate_netbox_site_csv, 
    generate_netbox_vlan_group_csv, 
    generate_netbox_vlans_csv, 
    generate_netbox_prefixes_csv
)
from core.db_manager import (
    save_ipam_records_batch, 
    save_sites_batch,
    get_all_ipam_records, 
    clear_ipam_records, 
    get_total_ipam_count,
    lookup_scope_id,
    get_existing_prefix_strings,
    save_records_batch
)
from core.netbox_client import fetch_netbox_full_sync

def handle_ipam_file_upload():
    uploaded = st.session_state.get("ipam_file_uploader")
    if uploaded is not None:
        try:
            filename = uploaded.name.lower()
            if filename.endswith(".xlsx"):
                wb = openpyxl.load_workbook(uploaded, data_only=True)
                # 1. Ingest Scope sheet if present
                if "Scope" in wb.sheetnames:
                    ws_scope = wb["Scope"]
                    scope_records = []
                    for r in range(2, ws_scope.max_row + 1):
                        s_id = ws_scope.cell(row=r, column=1).value
                        s_name = ws_scope.cell(row=r, column=5).value
                        s_slug = ws_scope.cell(row=r, column=6).value
                        if s_id and s_name:
                            scope_records.append({"id": s_id, "name": s_name, "slug": s_slug})
                    if scope_records:
                        save_sites_batch(scope_records, clear_first=False)

                # 2. Ingest Prefixes sheet if present
                if "Prefixes" in wb.sheetnames:
                    ws_pfx = wb["Prefixes"]
                    pfx_records = []
                    for r in range(2, ws_pfx.max_row + 1):
                        pfx_str = ws_pfx.cell(row=r, column=6).value
                        vlan_val = ws_pfx.cell(row=r, column=12).value
                        role_val = ws_pfx.cell(row=r, column=14).value
                        desc_val = ws_pfx.cell(row=r, column=17).value
                        if pfx_str and str(pfx_str).strip():
                            pfx_records.append({
                                "prefix_or_subnet": str(pfx_str).strip(),
                                "vlan_name": str(vlan_val or ""),
                                "role": str(role_val or ""),
                                "description": str(desc_val or "")
                            })
                    if pfx_records:
                        save_ipam_records_batch(pfx_records, clear_first=True)
                st.toast("✅ Ingested Scope IDs & Prefixes from Excel Workbook!", icon="📊")

            else:
                df = pd.read_csv(uploaded)
                cols = {str(c).lower().strip(): c for c in df.columns}
                records = []
                for _, row in df.iterrows():
                    sub = str(row.get(cols.get("prefix", cols.get("subnet", "")), "")).strip()
                    vid = str(row.get(cols.get("vid", cols.get("vlan id", "")), "")).strip()
                    vname = str(row.get(cols.get("name", cols.get("vlan name", "")), "")).strip()
                    role = str(row.get(cols.get("role", cols.get("vlan role", "")), "")).strip()
                    desc = str(row.get(cols.get("description", ""), "")).strip()
                    if sub or vid or vname:
                        records.append({
                            "prefix_or_subnet": sub,
                            "vlan_id": vid,
                            "vlan_name": vname,
                            "role": role,
                            "description": desc
                        })
                if records:
                    save_ipam_records_batch(records, clear_first=True)
                    st.toast(f"✅ Ingested {len(records)} IPAM records!", icon="🌐")
        except Exception as e:
            st.error(f"Error reading file: {e}")

def handle_ipam_db_reset():
    clear_ipam_records()
    st.toast("🗑️ IPAM Database & Scopes Cleared. Restored default templates.", icon="🧹")

def render_ipam_tab(active_model: str):
    st.subheader("🌐 IPAM & Site Subnet Provisioning Engine")
    st.caption("Plan site supernets, allocate non-overlapping VLAN subnets, and export ready-to-import NetBox CSV blocks.")

    # Ingestion Toolbar
    total_ipam_recs = get_total_ipam_count()
    status_label = f"🟢 ({total_ipam_recs} prefixes in DB)" if total_ipam_recs > 0 else "⚪ (Template Mode)"

    with st.expander(f"📥 Ingest Scope & Prefixes (Excel Workbook / CSV / NetBox API) {status_label}", expanded=False):
        tab_file, tab_api = st.tabs(["📄 Upload Excel Workbook / CSV", "🔌 Pull Live NetBox API (Scope + Prefixes)"])
        
        with tab_file:
            c1, c2 = st.columns([3, 1])
            with c1:
                st.file_uploader(
                    "Upload Standard VLAN_Prefixes_v2.xlsx or CSV", 
                    type=["xlsx", "csv"], 
                    key="ipam_file_uploader", 
                    on_change=handle_ipam_file_upload,
                    label_visibility="collapsed"
                )
            with c2:
                if total_ipam_recs > 0:
                    st.button("🗑️ Clear IPAM DB", on_click=handle_ipam_db_reset, use_container_width=True, key="btn_clr_ipam_db")
                else:
                    st.caption("No custom IPAM data loaded.")

        with tab_api:
            a1, a2 = st.columns(2)
            with a1:
                nb_url = st.text_input("NetBox URL", value="http://netbox:8080", key="ipam_nb_url").strip()
            with a2:
                nb_tok = st.text_input("NetBox API Token", type="password", key="ipam_nb_tok").strip()

            if st.button("🚀 Full NetBox Sync (Sites, Devices, IPAM)", use_container_width=True, key="btn_ipam_full_sync"):
                if not nb_url or not nb_tok:
                    st.warning("Please provide NetBox URL and API Token.")
                else:
                    with st.spinner("Syncing Sites, Devices, VMs, and Prefixes from NetBox API..."):
                        try:
                            sites, inv_records, ipam_records = fetch_netbox_full_sync(nb_url, nb_tok)
                            save_sites_batch(sites, clear_first=True) if sites else 0
                            save_records_batch(inv_records, clear_first=True) if inv_records else {"device": 0}
                            ipam_c = save_ipam_records_batch(ipam_records, clear_first=True) if ipam_records else 0
                            st.success(f"✅ Sync Complete! Ingested {len(sites)} Sites (Scopes), {len(inv_records)} Inventory Records & {ipam_c} IPAM Prefixes.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Sync Failed: {e}")

    # Top Inputs & Automatic Scope ID Lookup
    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        site_name = st.text_input("Branch / Site Name", value="Bristol", key="ipam_site_in").strip()
    
    # Automatic Scope ID Lookup (Excel E1 XLOOKUP equivalent)
    auto_scope_id = lookup_scope_id(site_name)
    scope_display_default = str(auto_scope_id) if auto_scope_id is not None else ""

    with top2:
        scope_id = st.text_input(
            "Scope ID (NetBox Site ID)", 
            value=scope_display_default or "42", 
            key="ipam_scope_in",
            help="Auto-discovered via XLOOKUP if site exists in database, or editable manually."
        ).strip()
        if auto_scope_id:
            st.caption(f"🟢 Auto-matched Scope ID: **`{auto_scope_id}`**")
        else:
            st.caption("⚪ Manual Scope ID mode")

    with top3:
        supernet_in = st.text_input("Site Supernet (CIDR)", value="10.113.252.0/23", key="ipam_super_in").strip()

    include_opt = st.toggle("Include Optional VLANs (Routing, OT, IoT)", value=True, key="ipam_opt_toggle")

    # Load existing prefixes from DB for overlap/collision detection
    existing_prefixes = get_existing_prefix_strings()

    # Slicing calculation
    selected_templates = [v for v in STANDARD_VLAN_TEMPLATES if include_opt or not v["opt"]]
    sliced = slice_supernet(supernet_in, selected_templates, site_name, existing_prefixes)

    raw_rows = []
    for r in sliced:
        raw_rows.append({
            "VLAN ID": r["vid"],
            "VLAN Name": r["name"],
            "Role": r["role"],
            "Description": r["desc"],
            "Subnet": r["assigned_subnet"]
        })

    df_init = pd.DataFrame(raw_rows)

    st.markdown("##### 📊 Subnet Allocation Editor (✏️ Click any cell to edit)")
    
    # In-place interactive editor
    edited_df = st.data_editor(
        df_init,
        use_container_width=True,
        num_rows="dynamic",
        key="ipam_data_editor",
        column_config={
            "VLAN ID": st.column_config.NumberColumn("VLAN ID", step=1, required=True),
            "VLAN Name": st.column_config.TextColumn("VLAN Name", required=True),
            "Role": st.column_config.TextColumn("Role"),
            "Description": st.column_config.TextColumn("Description"),
            "Subnet": st.column_config.TextColumn("Subnet (CIDR)", help="Type any valid CIDR like 10.113.254.0/24")
        }
    )

    # Recompute live usable ranges, collision detection, and descriptions
    records_dict = edited_df.to_dict(orient="records")
    allocated_subnets = []
    for r in records_dict:
        sub_str = str(r.get("Subnet", "")).strip()
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
            pd.DataFrame(records_dict)[["VLAN ID", "VLAN Name", "Subnet", "Usable Range", "Status", "Prefix Description"]], 
            use_container_width=True, 
            hide_index=True
        )

    with c_cap:
        st.markdown("##### 📈 Remaining Capacity")
        cap_matrix = calculate_remaining_subnets(supernet_in, allocated_subnets)
        cap_rows = [{"Subnet Size": k, "Available": f"{v} subnets"} for k, v in cap_matrix.items()]
        st.dataframe(pd.DataFrame(cap_rows), use_container_width=True, hide_index=True)

    # NetBox Bulk-Import CSV Generators
    st.markdown("---")
    st.markdown("### 📋 NetBox Bulk-Import CSV Generators")

    csv_site = generate_netbox_site_csv(site_name)
    csv_group = generate_netbox_vlan_group_csv(site_name, scope_id)
    csv_vlans = generate_netbox_vlans_csv(site_name, records_dict)
    csv_prefixes = generate_netbox_prefixes_csv(site_name, scope_id, supernet_in, records_dict)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**1. Import Site (`dcim.site`)**")
        st.code(csv_site, language="csv")
        st.download_button("⬇️ Download Site CSV", csv_site, f"site_{slugify(site_name)}.csv", "text/csv", key="dl_site_csv")

        st.markdown("**3. Import VLANs (`ipam.vlan`)**")
        st.code(csv_vlans, language="csv")
        st.download_button("⬇️ Download VLANs CSV", csv_vlans, f"vlans_{slugify(site_name)}.csv", "text/csv", key="dl_vlans_csv")

    with c2:
        st.markdown("**2. Import VLAN Group (`ipam.vlangroup`)**")
        st.code(csv_group, language="csv")
        st.download_button("⬇️ Download VLAN Group CSV", csv_group, f"vlangroup_{slugify(site_name)}.csv", "text/csv", key="dl_group_csv")

        st.markdown("**4. Import Prefixes (`ipam.prefix`)**")
        st.code(csv_prefixes, language="csv")
        st.download_button("⬇️ Download Prefixes CSV", csv_prefixes, f"prefixes_{slugify(site_name)}.csv", "text/csv", key="dl_prefixes_csv")