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

    # ═══════════════════════════════════════════════════════════════════════
    # Site Inputs (Branch + Scope ID + Site Supernet) — all default to empty
    # ═══════════════════════════════════════════════════════════════════════
    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        site_name_in = st.text_input(
            "Branch / Site Name",
            value="",
            key="ipam_site_in",
            placeholder="e.g. bristol, weybridge, london-1",
        ).strip()

    # Display name is title-cased ("bristol" -> "Bristol") for human
    # reading; the lowercase `matched_site_name` is preserved for
    # every NetBox export (CSV generators, slug, scope, etc).
    display_site_name = site_name_in.title() if site_name_in else ""

    # Try Live API Search or Local DB lookup ONLY for display purposes
    # (we never auto-fill the Subnet (CIDR) cell from this).
    resolved_scope_id = None
    resolved_supernet = None
    matched_site_name = site_name_in
    if site_name_in:
        if st.session_state.get("ipam_nb_url") and st.session_state.get("ipam_nb_tok"):
            nb_url_val = st.session_state["ipam_nb_url"].strip()
            nb_tok_val = st.session_state["ipam_nb_tok"].strip()
            s_id, s_name, _slug, s_pfx, _ = lookup_site_and_supernet_live(
                nb_url_val, nb_tok_val, site_name_in
            )
            if s_id is not None:
                resolved_scope_id = s_id
                matched_site_name = s_name or site_name_in
                if s_pfx:
                    resolved_supernet = s_pfx
        if resolved_scope_id is None:
            resolved_scope_id = lookup_scope_id(site_name_in)

    with top2:
        scope_default_val = str(resolved_scope_id) if resolved_scope_id is not None else ""
        scope_id = st.text_input(
            "Scope ID (NetBox Site ID)",
            value=scope_default_val,
            key="ipam_scope_in",
            placeholder="Auto-detected or enter ID",
            help="Auto-discovered directly from NetBox API or database.",
        ).strip()
        if resolved_scope_id:
            st.caption(f"🟢 Matched **`{display_site_name}`** (ID: `{resolved_scope_id}`)")
        else:
            st.caption("⚪ Manual Scope ID mode (Not Found)")

    with top3:
        # Site Supernet is fully manual — default empty.
        supernet_in = st.text_input(
            "Site Supernet (CIDR)",
            value="",
            key="ipam_super_in",
            placeholder="e.g. 10.113.252.0/23",
            help="Top-level container subnet for this branch site. Leave blank if unknown.",
        ).strip()
        if supernet_in and "/" in supernet_in:
            try:
                sup_net = ipaddress.ip_network(supernet_in, strict=False)
                sup_range = calculate_ip_range_str(sup_net)
                st.caption(f"📍 Supernet Usable Range: **`{sup_range}`**")
            except ValueError:
                st.caption("⚠️ Invalid CIDR format")
        if resolved_supernet:
            st.caption(f"💡 Auto-detected Site Supernet from NetBox: **`{resolved_supernet}`**")

    include_opt = st.toggle("Include Optional VLANs (Routing, OT, IoT)", value=True, key="ipam_opt_toggle")

    existing_prefixes = get_existing_prefix_strings()

    # ═══════════════════════════════════════════════════════════════════════
    # VLAN Role → VLAN Name / VLAN Description lookup map
    # ═══════════════════════════════════════════════════════════════════════
    VLAN_NAME_MAP = {
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
    VLAN_DESC_MAP = dict(VLAN_NAME_MAP)  # same map drives both columns

    # ═══════════════════════════════════════════════════════════════════════
    # Build the initial editor table — ALL fields empty by default
    # ═══════════════════════════════════════════════════════════════════════
    selected_templates = [v for v in STANDARD_VLAN_TEMPLATES if include_opt or not v["opt"]]
    raw_rows = []
    for v in selected_templates:
        raw_rows.append({
            "VLAN ID": v["vid"],
            "VLAN Name": v["name"],   # shown as the suggested VLAN name
            "Role": v["role"],         # user-editable role code
            "VLAN Description": v["name"],
            "Next Available": "",
            "Subnet (CIDR)": "",
        })

    df_editor = pd.DataFrame(raw_rows)
    for col in ("VLAN Name", "Role", "VLAN Description", "Next Available", "Subnet (CIDR)"):
        df_editor[col] = df_editor[col].astype("string").fillna("")

    st.markdown("##### 📊 Subnet Allocation Editor (✏️ Click any cell to edit)")

    edited_df = st.data_editor(
        df_editor,
        use_container_width=True,
        num_rows="dynamic",
        key="ipam_data_editor",
        column_config={
            "VLAN ID": st.column_config.NumberColumn("VLAN ID", step=1, required=True),
            "VLAN Name": st.column_config.TextColumn(
                "VLAN Name",
                help="Auto-filled from VLAN Role. Editable.",
            ),
            "Role": st.column_config.TextColumn(
                "Role",
                help="Type a role code (e.g. VIN_Corp, Management). Auto-fills Name + Description.",
            ),
            "VLAN Description": st.column_config.TextColumn(
                "VLAN Description",
                help="Auto-filled from VLAN Role lookup. Editable.",
            ),
            "Next Available": st.column_config.TextColumn(
                "Next Available",
                help="Computed dynamically from the previous row's Subnet. Read-only suggestion.",
                disabled=True,
            ),
            "Subnet (CIDR)": st.column_config.TextColumn(
                "Subnet (CIDR)",
                help="Type any valid CIDR (e.g. 10.113.252.0/24). Live usable range shows below.",
            ),
        },
    )

    # ═══════════════════════════════════════════════════════════════════════
    # Post-process: auto-fill Name/Description from Role, compute Next Available
    # ═══════════════════════════════════════════════════════════════════════
    records_dict = edited_df.to_dict(orient="records")
    allocated_subnets = []
    last_subnet_net = None      # ipaddress.IPv4Network of the previous row

    for idx, r in enumerate(records_dict):
        # --- Normalise Subnet cell to a clean string -----------------------
        raw_sub = r.get("Subnet (CIDR)", "")
        if raw_sub is None:
            sub_str = ""
        else:
            try:
                if isinstance(raw_sub, float):
                    import math
                    if math.isnan(raw_sub):
                        sub_str = ""
                    else:
                        sub_str = str(raw_sub).strip()
                else:
                    sub_str = str(raw_sub).strip()
            except Exception:
                sub_str = ""
        if sub_str.lower() in ("nan", "none", "null", "<na>"):
            sub_str = ""
        r["Subnet"] = sub_str
        allocated_subnets.append(sub_str)

        # --- Normalise role / name / description ---------------------------
        role_val = str(r.get("Role", "") or "").strip()
        name_val = str(r.get("VLAN Name", "") or "").strip()
        desc_val = str(r.get("VLAN Description", "") or "").strip()
        if role_val.lower() in ("nan", "none", "null"):
            role_val = ""

        # --- Auto-fill VLAN Name from Role if blank ------------------------
        if not name_val and role_val in VLAN_NAME_MAP:
            r["VLAN Name"] = VLAN_NAME_MAP[role_val]
            name_val = VLAN_NAME_MAP[role_val]
        elif not name_val:
            r["VLAN Name"] = role_val
            name_val = role_val

        # --- Auto-fill VLAN Description from Role if blank -----------------
        if not desc_val and role_val in VLAN_DESC_MAP:
            r["VLAN Description"] = VLAN_DESC_MAP[role_val]
            desc_val = VLAN_DESC_MAP[role_val]
        elif not desc_val:
            r["VLAN Description"] = name_val
            desc_val = name_val

        # --- Compute Next Available: last_subnet_net.broadcast_address + 1 -
        next_avail = ""
        if last_subnet_net is not None:
            try:
                next_int = int(last_subnet_net.broadcast_address) + 1
                # Use the same prefix length as the previous row
                next_avail = str(ipaddress.ip_network(
                    f"{next_int}/{last_subnet_net.prefixlen}", strict=False
                ).network_address)
            except Exception:
                next_avail = ""
        r["Next Available"] = next_avail

        # --- CIDR validity check ------------------------------------------
        is_valid_cidr = False
        if sub_str and sub_str.count("/") == 1:
            head, _, tail = sub_str.partition("/")
            if head.count(".") == 3 and tail.isdigit() and 0 <= int(tail) <= 32:
                if all(p.isdigit() and 0 <= int(p) <= 255 for p in head.split(".")):
                    is_valid_cidr = True

        # --- Compute Usable Range & Status ---------------------------------
        if not sub_str:
            r["Usable Range"] = "—"
            r["Status"] = "Unassigned"
            last_subnet_net = None
        elif "x" in sub_str.lower():
            r["Usable Range"] = "RFC1918 Custom Pool"
            r["Status"] = "Special Pool"
            last_subnet_net = None
        elif not is_valid_cidr:
            r["Usable Range"] = "—"
            r["Status"] = "Pending Input"
            last_subnet_net = None
        else:
            try:
                # Direct parse — we already validated the CIDR, so just
                # use ipaddress.ip_network and compute the range.
                net = ipaddress.ip_network(sub_str, strict=False)
                # Re-parse with strict CIDR alignment
                net = ipaddress.ip_network(f"{net.network_address}/{net.prefixlen}", strict=False)
                range_str = f"{net.network_address} - {net.broadcast_address}"
                is_in_use = check_prefix_collision(net, existing_prefixes)
                status = "OK"
                if is_in_use:
                    status = "⚠️ [IN-USE]"
                    range_str += " ⚠️ [IN-USE]"
                elif supernet_in and "/" in supernet_in:
                    try:
                        sup = ipaddress.ip_network(supernet_in, strict=False)
                        if not net.subnet_of(sup):
                            status = "Outside Supernet"
                    except ValueError:
                        pass
                r["Usable Range"] = range_str
                r["Status"] = status
                last_subnet_net = net
            except Exception:
                r["Usable Range"] = "—"
                r["Status"] = "Pending Input"
                last_subnet_net = None

        # --- Compute Prefix Description for preview / NetBox prefixes CSV
        try:
            vlan_id_int = int(r.get("VLAN ID")) if r.get("VLAN ID") not in (None, "") else None
        except (TypeError, ValueError):
            vlan_id_int = None
        if vlan_id_int is not None:
            r["Prefix Description"] = f"{display_site_name} {name_val} -- VLAN {vlan_id_int}"
        else:
            r["Prefix Description"] = f"{display_site_name} {name_val}".strip()

    # ═══════════════════════════════════════════════════════════════════════
    # Live preview: editable editor already shows Next Available + Subnet
    # ═══════════════════════════════════════════════════════════════════════
    c_prev, c_cap = st.columns([3, 1.2])
    with c_prev:
        st.markdown("##### 🔍 Live Usable IP Ranges & Collision Status")
        preview_cols = ["Subnet", "Usable Range", "Status", "Prefix Description"]
        preview_df = pd.DataFrame(records_dict)
        for col in preview_cols:
            if col not in preview_df.columns:
                preview_df[col] = ""
        st.dataframe(preview_df[preview_cols], use_container_width=True, hide_index=True)

    with c_cap:
        st.markdown("##### 📈 Remaining Capacity")
        cap_matrix = calculate_remaining_subnets(supernet_in, allocated_subnets)
        cap_rows = [{"Subnet Size": k, "Available": f"{v} subnets"} for k, v in cap_matrix.items()]
        st.dataframe(pd.DataFrame(cap_rows), use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════════════════════════════════════
    # NetBox Bulk-Import CSV Generators
    # ═══════════════════════════════════════════════════════════════════════
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