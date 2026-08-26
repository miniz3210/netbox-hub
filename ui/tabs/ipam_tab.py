import streamlit as st
import pandas as pd
from core.ipam_engine import (
    STANDARD_VLAN_TEMPLATES, 
    slice_supernet, 
    slugify, 
    generate_netbox_site_csv, 
    generate_netbox_vlan_group_csv, 
    generate_netbox_vlans_csv, 
    generate_netbox_prefixes_csv
)

def render_ipam_tab(active_model: str):
    st.subheader("🌐 IPAM & Site Subnet Provisioning Engine")
    st.caption("Plan site supernets, allocate non-overlapping VLAN subnets, and export ready-to-import NetBox CSV blocks.")

    top1, top2, top3 = st.columns([2, 1, 2])
    with top1:
        site_name = st.text_input("Branch / Site Name", value="UK Test site", placeholder="e.g., UK Test site, Dallas Branch", key="ipam_site_in").strip()
    with top2:
        scope_id = st.text_input("Scope ID (NetBox Site ID)", value="42", placeholder="e.g., 42", key="ipam_scope_in").strip()
    with top3:
        supernet_in = st.text_input("Site Supernet (CIDR)", value="10.113.252.0/23", placeholder="e.g., 10.113.252.0/23, 192.168.0.0/22", key="ipam_super_in").strip()

    include_opt = st.toggle("Include Optional VLANs (Routing, OT, IoT)", value=True, key="ipam_opt_toggle")

    selected_templates = [v for v in STANDARD_VLAN_TEMPLATES if include_opt or not v["opt"]]
    sliced_records = slice_supernet(supernet_in, selected_templates)

    st.markdown("##### 📊 Subnet Allocation Preview")
    table_data = []
    for r in sliced_records:
        table_data.append({
            "VLAN ID": r["vid"],
            "VLAN Name": r["name"],
            "Role": r["role"],
            "Subnet": r["assigned_subnet"],
            "Usable Range": r["usable_range"],
            "Status": r["status"]
        })
    st.dataframe(pd.DataFrame(table_data), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("### 📋 NetBox Bulk-Import CSV Generators")
    
    csv_site = generate_netbox_site_csv(site_name)
    csv_group = generate_netbox_vlan_group_csv(site_name, scope_id)
    csv_vlans = generate_netbox_vlans_csv(site_name, sliced_records)
    csv_prefixes = generate_netbox_prefixes_csv(site_name, scope_id, supernet_in, sliced_records)

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