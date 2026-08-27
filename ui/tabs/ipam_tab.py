import ipaddress
import streamlit as st
import pandas as pd
import openpyxl
from core.ipam_engine import (
    STANDARD_VLAN_TEMPLATES,
    slice_supernet,
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
    get_all_ipam_records, 
    clear_ipam_records, 
    get_total_ipam_count,
    lookup_scope_id,
    get_existing_prefix_strings,
    save_records_batch
)
from core.netbox_client import fetch_netbox_full_sync, lookup_site_and_supernet_live

def handle_ipam_file_upload():
    uploaded = st.session_state.get("ipam_file_uploader")
    if uploaded is not None:
        try:
            filename = uploaded.name.lower()
            if filename.endswith(".xlsx"):
                wb = openpyxl.load_workbook(uploaded, data_only=True)
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
    status_label = f"🟢 ({total_ipam_recs} prefixes in DB)" if total_ipam_recs > 0 else "⚪ (Live API / Template Mode)"

    with st.expander(f"📥 NetBox Connection & Data Ingest {status_label}", expanded=True):
        # Read (or initialise) the NetBox credentials from session state first
        # so they are in scope for the buttons below.
        st.session_state.setdefault("ipam_nb_url", "https://ipam.aw.ads/")
        st.session_state.setdefault("ipam_nb_tok", "")

        tab_api, tab_file = st.tabs(["🔌 Live NetBox API (Real-Time Search)", "📄 Upload Excel / CSV Backup"])

        with tab_api:
            a1, a2 = st.columns(2)
            with a1:
                st.text_input("NetBox URL", value="https://ipam.aw.ads/", key="ipam_nb_url")
            with a2:
                st.text_input("NetBox API Token", value="", type="password", key="ipam_nb_tok", help="Token e.g. 0ae9237edd...")

            nb_url_val = st.session_state["ipam_nb_url"].strip()
            nb_tok_val = st.session_state["ipam_nb_tok"].strip()

            c_sync1, c_sync2 = st.columns([2.5, 1])
            with c_sync1:
                if st.button("🚀 Full NetBox Sync (Cache all Sites, Devices, Prefixes to local DB)", use_container_width=True, key="btn_ipam_full_sync"):
                    if not nb_url_val or not nb_tok_val:
                        st.warning("Please provide NetBox URL and API Token.")
                    else:
                        with st.spinner("Syncing Sites, Devices, VMs, and Prefixes from NetBox API..."):
                            try:
                                sites, inv_records, ipam_records = fetch_netbox_full_sync(nb_url_val, nb_tok_val)
                                if sites:
                                    save_sites_batch(sites, clear_first=True)
                                if inv_records:
                                    save_records_batch(inv_records, clear_first=True)
                                ipam_c = save_ipam_records_batch(ipam_records, clear_first=True) if ipam_records else 0
                                st.success(f"✅ Full Sync Complete! Cached {len(sites)} Sites, {len(inv_records)} Devices/VMs & {ipam_c} Prefixes.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Sync Failed: {e}")
            with c_sync2:
                if total_ipam_recs > 0:
                    st.button("🗑️ Clear Local Cache", on_click=handle_ipam_db_reset, use_container_width=True, key="btn_clr_ipam_db")

        with tab_file:
            st.file_uploader(
                "Upload Standard VLAN_Prefixes_v2.xlsx or CSV", 
                type=["xlsx", "csv"], 
                key="ipam_file_uploader", 
                on_change=handle_ipam_file_upload,
                label_visibility="collapsed"
            )

    # Top Inputs & Live Search
    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        # Default: empty.  Type a site name (e.g. "bristol") to begin.
        site_name_in = st.text_input(
            "Branch / Site Name",
            value="",
            key="ipam_site_in",
            placeholder="e.g. bristol, weybridge, london-1",
        ).strip()

    # Display name is title-cased (e.g. "bristol" -> "Bristol") for human
    # reading, but the lowercase `matched_site_name` is preserved for
    # every NetBox export (CSV generators, slug, scope, etc).
    display_site_name = site_name_in.title() if site_name_in else ""

    # Step A: Perform Live API Search or Local DB lookup
    resolved_scope_id = None
    resolved_supernet = None
    matched_site_name = site_name_in

    if site_name_in:
        # 1. Try Live API Search if URL & Token are supplied (session-state-backed)
        if st.session_state.get("ipam_nb_url") and st.session_state.get("ipam_nb_tok"):
            nb_url_val = st.session_state["ipam_nb_url"].strip()
            nb_tok_val = st.session_state["ipam_nb_tok"].strip()
            s_id, s_name, s_slug, s_pfx, _ = lookup_site_and_supernet_live(nb_url_val, nb_tok_val, site_name_in)
            if s_id is not None:
                resolved_scope_id = s_id
                matched_site_name = s_name or site_name_in
                if s_pfx:
                    resolved_supernet = s_pfx

        # 2. Fallback to local DB lookup
        if resolved_scope_id is None:
            resolved_scope_id = lookup_scope_id(site_name_in)

    with top2:
        scope_default_val = str(resolved_scope_id) if resolved_scope_id is not None else ""
        scope_id = st.text_input(
            "Scope ID (NetBox Site ID)", 
            value=scope_default_val, 
            key="ipam_scope_in",
            placeholder="Auto-detected or enter ID",
            help="Auto-discovered directly from NetBox API or database."
        ).strip()
        if resolved_scope_id:
            st.caption(f"🟢 Matched **`{display_site_name}`** (ID: `{resolved_scope_id}`)")
        else:
            st.caption("⚪ Manual Scope ID mode (Not Found)")

    with top3:
        supernet_default_val = resolved_supernet or "10.113.252.0/23"
        supernet_in = st.text_input(
            "Site Supernet (CIDR)",
            value=supernet_default_val,
            key="ipam_super_in",
            help="Top-level container subnet for this branch site."
        ).strip()
        # ── Site Supernet Usable Range ───────────────────────────────────────
        if supernet_in and "/" in supernet_in:
            try:
                sup_net = ipaddress.ip_network(supernet_in, strict=False)
                sup_range = calculate_ip_range_str(sup_net)
                st.caption(f"📍 Supernet Usable Range: **`{sup_range}`**")
            except ValueError:
                st.caption("⚠️ Invalid CIDR format")
        if resolved_supernet:
            st.caption(f"🟢 Auto-detected Site Supernet: **`{resolved_supernet}`**")
        else:
            st.caption("⚪ Standard default /23 container")

    include_opt = st.toggle("Include Optional VLANs (Routing, OT, IoT)", value=True, key="ipam_opt_toggle")

    # Load existing prefixes for collision detection
    existing_prefixes = get_existing_prefix_strings()

    # ── Static role → VLAN description map (user-edited via cell) ─────────
    # If the user types a role not in this map, the auto-fill falls back
    # to the role text itself.  The user can still override any cell.
    VLAN_DESC_MAP = {
        "VIN_Corp": "Corporate WiFi",
        "Wired Workstations": "Workstations",
        "Management": "Management",
        "Printers": "Printers",
        "AV equipment": "Audio Visual",
        "VIN_Guest": "Guests",
        "VIN_Mobi": "Mobiles",
        "Routing interface VLANs": "Routing",
        "OT": "OT",
        "IoT/Security": "IoT",
    }

    # Dynamic Slicing
    selected_templates = [v for v in STANDARD_VLAN_TEMPLATES if include_opt or not v["opt"]]
    sliced = slice_supernet(supernet_in, selected_templates, matched_site_name, existing_prefixes)

    raw_rows = []
    for r in sliced:
        # VLAN Description defaults from the role-to-description map.
        # Example: "VIN_Corp" -> "Corporate WiFi".
        # The user can still overwrite any cell manually.
        role_text = r["role"]
        default_desc = VLAN_DESC_MAP.get(role_text, role_text)
        raw_rows.append({
            "VLAN ID": r["vid"],
            "VLAN Name": r["name"],
            "Role": role_text,
            "Description": default_desc,
            "Next Available": r["assigned_subnet"],   # suggestion, user can override
            "Subnet (CIDR)": str(r["assigned_subnet"] or ""),  # actual value, user types here
        })

    df_init = pd.DataFrame(raw_rows)

    st.markdown("##### 📊 Subnet Allocation Editor (✏️ Click any cell to edit)")

    df_editor = pd.DataFrame(raw_rows)
    # Force string dtype on the Subnet cell so user input like "10.113.248.0/24"
    # is never coerced to NaN / float by pandas.
    df_editor["Subnet (CIDR)"] = df_editor["Subnet (CIDR)"].astype("string").fillna("")

    edited_df = st.data_editor(
        df_editor,
        use_container_width=True,
        num_rows="dynamic",
        key="ipam_data_editor",
        column_config={
            "VLAN ID": st.column_config.NumberColumn("VLAN ID", step=1, required=True),
            "VLAN Name": st.column_config.TextColumn("VLAN Name", required=True),
            "Role": st.column_config.TextColumn("Role"),
            # VLAN Description — free-text, used in NetBox VLANs CSV.
            "Description": st.column_config.TextColumn(
                "VLAN Description",
                help="Free-text VLAN description (NetBox: ipam.vlan.description).",
            ),
            "Next Available": st.column_config.TextColumn(
                "Next Available",
                help="Suggested next free subnet in supernet (read-only suggestion).",
                disabled=True,
            ),
            "Subnet (CIDR)": st.column_config.TextColumn(
                "Subnet (CIDR)",
                help="Type any valid CIDR like 10.113.240.0/24",
            ),
        },
    )

    records_dict = edited_df.to_dict(orient="records")
    allocated_subnets = []
    for r in records_dict:
        # `Sub` is the alias used in the preview/CSV; we normalise it
        # to "Subnet" for downstream code that reads r["Subnet"].
        sub_str = str(r.get("Subnet (CIDR)", "")).strip()
        if sub_str.lower() in ("nan", "none", "null"):
            sub_str = ""
        r["Subnet"] = sub_str
        allocated_subnets.append(sub_str)
        vlan_id_val = r.get("VLAN ID")
        vlan_role_val = str(r.get("Role", "")).strip()

        # ── VLAN Description auto-fill (only when blank) ─────────────────
        # Default = the VLAN Role text (e.g. "Corporate WiFi").  The user
        # can still overwrite this cell manually.
        existing_vlan_desc = str(r.get("Description", "")).strip()
        if not existing_vlan_desc:
            r["Description"] = vlan_role_val or ""

        # ── Prefix Description auto-fill (only when blank) ──────────────
        # Formula: "{Site} {Role} -- VLAN {VLAN ID}"
        # e.g.  "Bristol Corporate WiFi -- VLAN 300"
        # Used in the "Live Usable IP Ranges" preview and the NetBox Prefixes CSV.
        existing_pfx_desc = str(r.get("Prefix Description", "")).strip()
        if not existing_pfx_desc:
            if vlan_id_val:
                r["Prefix Description"] = f"{display_site_name} {vlan_role_val} -- VLAN {vlan_id_val}"
            else:
                r["Prefix Description"] = f"{display_site_name} {vlan_role_val}"

        # ── Usable Range & Status ───────────────────────────────────────
        # Robust CIDR validation so "Invalid CIDR" never surfaces for
        # mid-typing or malformed inputs.  We require:
        #   • exactly one "/"
        #   • prefix length 0-32
        #   • 4 dotted decimal octets, each 0-255
        is_valid_cidr = False
        if sub_str and sub_str.count("/") == 1:
            head, _, tail = sub_str.partition("/")
            if head.count(".") == 3:
                octets = head.split(".")
                if all(o.isdigit() and 0 <= int(o) <= 255 for o in octets) and tail.isdigit() and 0 <= int(tail) <= 32:
                    is_valid_cidr = True

        if not sub_str:
            r["Usable Range"] = "—"
            r["Status"] = "Unassigned"
        elif "x" in sub_str.lower():
            r["Usable Range"] = "RFC1918 Custom Pool"
            r["Status"] = "Special Pool"
        elif not is_valid_cidr:
            r["Usable Range"] = "—"
            r["Status"] = "Pending Input"
        else:
            eval_res = evaluate_subnet_row(
                sub_str,
                vlan_id_val,
                vlan_role_val,
                matched_site_name,
                supernet_in,
                existing_prefixes,
            )
            r["Usable Range"] = eval_res["usable_range"]
            r["Status"] = eval_res["status"]

    # ── Live preview: Prefix Description shown separately from VLAN Description
    c_prev, c_cap = st.columns([3, 1.2])
    with c_prev:
        st.markdown("##### 🔍 Live Usable IP Ranges & Collision Status")
        st.dataframe(
            pd.DataFrame(records_dict)[[
                "Subnet", "Usable Range", "Status", "Prefix Description"
            ]],
            use_container_width=True,
            hide_index=True,
        )

    with c_cap:
        st.markdown("##### 📈 Remaining Capacity")
        cap_matrix = calculate_remaining_subnets(supernet_in, allocated_subnets)
        cap_rows = [{"Subnet Size": k, "Available": f"{v} subnets"} for k, v in cap_matrix.items()]
        st.dataframe(pd.DataFrame(cap_rows), use_container_width=True, hide_index=True)

    # NetBox Bulk-Import CSV Generators
    st.markdown("---")
    st.markdown("### 📋 NetBox Bulk-Import CSV Generators")

    csv_site = generate_netbox_site_csv(matched_site_name)
    csv_group = generate_netbox_vlan_group_csv(matched_site_name, scope_id)
    csv_vlans = generate_netbox_vlans_csv(matched_site_name, records_dict)
    csv_prefixes = generate_netbox_prefixes_csv(matched_site_name, scope_id, supernet_in, records_dict)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**1. Import Site (`dcim.site`)**")
        st.code(csv_site, language="csv")
        st.download_button("⬇️ Download Site CSV", csv_site, f"site_{slugify(matched_site_name)}.csv", "text/csv", key="dl_site_csv")

        st.markdown("**3. Import VLANs (`ipam.vlan`)**")
        st.code(csv_vlans, language="csv")
        st.download_button("⬇️ Download VLANs CSV", csv_vlans, f"vlans_{slugify(matched_site_name)}.csv", "text/csv", key="dl_vlans_csv")

    with c2:
        st.markdown("**2. Import VLAN Group (`ipam.vlangroup`)**")
        st.code(csv_group, language="csv")
        st.download_button("⬇️ Download VLAN Group CSV", csv_group, f"vlangroup_{slugify(matched_site_name)}.csv", "text/csv", key="dl_group_csv")

        st.markdown("**4. Import Prefixes (`ipam.prefix`)**")
        st.code(csv_prefixes, language="csv")
        st.download_button("⬇️ Download Prefixes CSV", csv_prefixes, f"prefixes_{slugify(matched_site_name)}.csv", "text/csv", key="dl_prefixes_csv")