import streamlit as st
import pandas as pd
from core.ipam_engine import (
    STANDARD_VLAN_TEMPLATES, 
    slice_supernet, 
    slugify, 
    evaluate_subnet_entry,
    generate_netbox_site_csv, 
    generate_netbox_vlan_group_csv, 
    generate_netbox_vlans_csv, 
    generate_netbox_prefixes_csv
)
from core.db_manager import (
    save_ipam_records_batch, 
    get_all_ipam_records, 
    clear_ipam_records, 
    get_total_ipam_count,
    save_records_batch
)
from core.netbox_client import fetch_netbox_full_sync

def handle_ipam_csv_upload():
    uploaded = st.session_state.get("ipam_csv_uploader")
    if uploaded is not None:
        try:
            df = pd.read_csv(uploaded)
            cols = {str(c).lower().strip(): c for c in df.columns}
            records = []
            for _, row in df.iterrows():
                sub = str(row.get(cols.get("prefix", cols.get("subnet", cols.get("suggest subnet", ""))), "")).strip()
                vid = str(row.get(cols.get("vid", cols.get("vlan id", cols.get("vlan", ""))), "")).strip()
                vname = str(row.get(cols.get("name", cols.get("vlan name", "")), "")).strip()
                role = str(row.get(cols.get("role", cols.get("vlan role", "")), "")).strip()
                desc = str(row.get(cols.get("description", cols.get("vlan description", "")), "")).strip()
                if sub or vid or vname:
                    records.append({
                        "prefix_or_subnet": sub,
                        "vlan_id": vid,
                        "vlan_name": vname,
                        "role": role,
                        "description": desc
                    })
            if records:
                cnt = save_ipam_records_batch(records, clear_first=True)
                st.toast(f"✅ Ingested {cnt} IPAM records into dedicated database!", icon="🌐")
        except Exception as e:
            st.error(f"Error reading IPAM CSV: {e}")

def handle_ipam_db_reset():
    clear_ipam_records()
    st.toast("🗑️ IPAM Database Cleared. Restored default templates.", icon="🧹")

def render_ipam_tab(active_model: str):
    st.subheader("🌐 IPAM & Site Subnet Provisioning Engine")
    st.caption("Plan site supernets, allocate non-overlapping VLAN subnets, and export ready-to-import NetBox CSV blocks.")

    # Ingestion Toolbar (CSV vs. Shared API)
    total_ipam_recs = get_total_ipam_count()
    status_label = f"🟢 ({total_ipam_recs} records in IPAM DB)" if total_ipam_recs > 0 else "⚪ (Template Mode)"

    with st.expander(f"📥 IPAM Ingestion Toolbar {status_label}", expanded=False):
        tab_csv, tab_api = st.tabs(["📄 Upload IPAM / Prefix CSV", "🔌 Full Sync via NetBox API (Shared DB)"])
        
        with tab_csv:
            c1, c2 = st.columns([3, 1])
            with c1:
                st.file_uploader(
                    "Upload IPAM CSV / Excel Export", 
                    type=["csv"], 
                    key="ipam_csv_uploader", 
                    on_change=handle_ipam_csv_upload,
                    label_visibility="collapsed"
                )
            with c2:
                if total_ipam_recs > 0:
                    st.button("🗑️ Clear IPAM DB", on_click=handle_ipam_db_reset, use_container_width=True, key="btn_clr_ipam_db")
                else:
                    st.caption("No custom IPAM CSV active.")

        with tab_api:
            a1, a2 = st.columns(2)
            with a1:
                nb_url = st.text_input("NetBox URL", value="http://netbox:8080", key="ipam_nb_url").strip()
            with a2:
                nb_tok = st.text_input("NetBox API Token", type="password", key="ipam_nb_tok").strip()

            if st.button("🚀 Full NetBox Sync (DCIM + IPAM)", use_container_width=True, key="btn_ipam_full_sync"):
                if not nb_url or not nb_tok:
                    st.warning("Please provide NetBox URL and API Token.")
                else:
                    with st.spinner("Syncing Devices, VMs, Prefixes, and VLANs from NetBox API..."):
                        try:
                            inv_records, ipam_records = fetch_netbox_full_sync(nb_url, nb_tok)
                            inv_c = save_records_batch(inv_records, clear_first=True) if inv_records else {"device": 0}
                            ipam_c = save_ipam_records_batch(ipam_records, clear_first=True) if ipam_records else 0
                            st.success(f"✅ Shared Sync Complete! Ingested {len(inv_records)} Inventory Records & {ipam_c} IPAM Prefixes.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Sync Failed: {e}")

    # Top Inputs
    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        site_name = st.text_input("Branch / Site Name", value="UK Test site", key="ipam_site_in").strip()
    with top2:
        scope_id = st.text_input("Scope ID (NetBox Site ID)", value="42", key="ipam_scope_in").strip()
    with top3:
        supernet_in = st.text_input("Site Supernet (CIDR)", value="10.113.252.0/23", key="ipam_super_in").strip()

    include_opt = st.toggle("Include Optional VLANs (Routing, OT, IoT)", value=True, key="ipam_opt_toggle")

    # Load starting records: from IPAM DB if present, else standard template
    db_items = get_all_ipam_records()
    if db_items:
        raw_rows = []
        for d in db_items:
            raw_rows.append({
                "VLAN ID": d.get("vlan_id") or 0,
                "VLAN Name": d.get("vlan_name") or "",
                "Role": d.get("role") or "",
                "Description": d.get("description") or "",
                "Subnet": d.get("prefix_or_subnet") or ""
            })
    else:
        selected_templates = [v for v in STANDARD_VLAN_TEMPLATES if include_opt or not v["opt"]]
        sliced = slice_supernet(supernet_in, selected_templates)
        raw_rows = []
        for r in sliced:
            raw_rows.append({
                "VLAN ID": r["vid"],
                "VLAN Name": r["name"],
                "Role": r["role"],
                "Description": r["desc"],
                "Subnet": r["assigned_subnet"]
            })

    # Prepare DataFrame for interactive data_editor
    df_init = pd.DataFrame(raw_rows)

    st.markdown("##### 📊 Subnet Allocation Editor (✏️ Click any cell to edit)")
    
    # st.data_editor allows full in-place editing of Subnet, VLAN ID, Name, Role
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

    # Recompute live usable ranges and statuses
    records_dict = edited_df.to_dict(orient="records")
    for r in records_dict:
        eval_res = evaluate_subnet_entry(str(r.get("Subnet", "")), supernet_in)
        r["Usable Range"] = eval_res["usable_range"]
        r["Status"] = eval_res["status"]

    # Show live recalculation summary
    with st.expander("🔍 Live Usable IP Ranges & Status", expanded=True):
        st.dataframe(pd.DataFrame(records_dict)[["VLAN ID", "VLAN Name", "Subnet", "Usable Range", "Status"]], use_container_width=True, hide_index=True)

    # NetBox Import CSV Blocks
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