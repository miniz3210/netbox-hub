"""
AI Assistant Helper Functions
Provides comprehensive database context for AI queries
"""

import re
from typing import Dict, List, Any
from core.db_manager import (
    get_all_site_names,
    get_records_by_category,
    get_ipam_records_by_site,
    get_total_sites_count,
    get_total_vlans_count,
    get_total_prefixes_count,
    get_total_record_count,
    get_total_ipam_count,
    get_max_scope_id,
    get_site_summary,
    get_full_site_inventory_summary,
    get_existing_prefix_strings
)
from core.backup_manager import (
    OBJECT_LABELS,
    count_backup_records,
    get_backup_metadata,
    get_backup_object_counts,
    get_backup_records_by_type,
    get_backup_site_names,
    get_choice_set_summary,
    get_choice_values_for_field,
    is_backup_active,
    search_backup_records,
)
import sqlite3

DB_PATH = "data/netbox_hub.db"

# Terms that map a natural-language question onto backup object collections.
BACKUP_TOPIC_HINTS: List[tuple] = [
    ("dcim_devices", ("device", "devices", "switch", "switches", "firewall", "firewalls", "router", "routers", "access point", "appliance", "hardware", "serial", "asset tag")),
    ("dcim_interfaces", ("interface", "interfaces", "port", "ports", "uplink", "uplinks", "lag", "trunk", "vmnic", "ethernet")),
    ("dcim_racks", ("rack", "racks", "cabinet", "rack unit")),
    ("dcim_device_types", ("device type", "device types", "model", "models", "part number", "chassis")),
    ("dcim_device_roles", ("device role", "device roles", "roles")),
    ("dcim_manufacturers", ("manufacturer", "manufacturers", "vendor", "vendors", "make")),
    ("dcim_platforms", ("platform", "platforms", "os version", "firmware", "operating system")),
    ("dcim_regions", ("region", "regions", "country", "countries", "continent")),
    ("dcim_sites", ("site", "sites", "branch", "branches", "office", "offices", "winery", "wineries", "location", "locations", "address", "timezone", "time zone")),
    ("dcim_locations", ("location", "locations", "floor", "room", "building")),
    ("dcim_virtual_chassis", ("virtual chassis", "stack", "stacks", "vc")),
    ("dcim_modules", ("module", "modules", "line card", "sfp")),
    ("dcim_cables", ("cable", "cables", "patch", "cabling")),
    ("ipam_prefixes", ("prefix", "prefixes", "subnet", "subnets", "cidr", "supernet", "scope id", "network range")),
    ("ipam_vlans", ("vlan", "vlans", "vid", "vlan group", "broadcast domain")),
    ("ipam_vlan_groups", ("vlan group", "vlan groups")),
    ("ipam_ip_addresses", ("ip", "ip address", "ip addresses", "gateway", "dns name", "dhcp", "dns server", "host address")),
    ("ipam_ip_ranges", ("ip range", "ip ranges", "dhcp pool", "dhcp scope", "address pool")),
    ("ipam_aggregates", ("aggregate", "aggregates", "rir block")),
    ("ipam_asns", ("asn", "asns", "as number", "autonomous system")),
    ("ipam_vrfs", ("vrf", "vrfs", "route distinguisher")),
    ("ipam_roles", ("ipam role", "prefix role", "vlan role")),
    ("virtualization_virtual_machines", ("vm", "vms", "virtual machine", "virtual machines", "guest", "vcpu", "vcpus", "memory")),
    ("virtualization_clusters", ("cluster", "clusters", "vcenter")),
    ("virtualization_cluster_groups", ("cluster group", "cluster groups")),
    ("virtualization_virtual_disks", ("virtual disk", "virtual disks", "vmdk")),
    ("tenancy_tenants", ("tenant", "tenants", "tenancy", "business unit", "subscription", "subscriptions")),
    ("tenancy_tenant_groups", ("tenant group", "tenant groups")),
    ("circuits_circuits", ("circuit", "circuits", "wan link", "isp link", "commit rate")),
    ("circuits_providers", ("provider", "providers", "carrier", "carriers", "isp")),
    ("wireless_wireless_lans", ("wireless lan", "wireless lans", "ssid", "ssids", "wlan")),
    ("extras_tags", ("tag", "tags")),
    ("extras_custom_fields", ("custom field", "custom fields")),
    ("extras_custom_field_choice_sets", (
        "choice set", "choice sets", "instance type", "instance types",
        "resource group", "resource groups", "custom field choice",
    )),
]

# Custom field choice sets surfaced verbatim when the question names them.
CHOICE_FIELD_HINTS: List[tuple] = [
    ("instance_type", ("instance type", "instance types", "vm size", "vm sizes", "sku", "skus")),
    ("resource_group", ("resource group", "resource groups")),
    ("organization", ("organization", "organisations", "organizations")),
    ("owner", ("owner", "owners")),
    ("tier", ("tier", "tiers")),
    ("runtime", ("runtime", "runtimes")),
]

STOPWORDS = {
    "the", "and", "for", "with", "what", "which", "where", "when", "who", "how",
    "are", "is", "was", "were", "does", "did", "can", "you", "please", "show",
    "list", "give", "tell", "about", "all", "any", "many", "much", "have", "has",
    "from", "into", "that", "this", "these", "those", "there", "their", "them",
    "our", "your", "its", "not", "but", "get", "find", "look", "lookup", "data",
    "database", "netbox", "backup", "info", "information", "details", "detail",
    "count", "total", "number", "site", "sites", "name", "names", "please",
}


def _split_terms(prompt: str, limit: int = 8) -> tuple:
    """Split a prompt into specific identifiers and general keywords.

    Identifiers (hostnames, CIDRs, IPs, VLAN IDs, asset tags) must match exactly;
    keywords only rank results so a plain-English question still returns rows.
    """
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._/:\-]{1,}", prompt or "")
    identifiers: List[str] = []
    keywords: List[str] = []

    for token in tokens:
        clean = token.strip(".,;:!?").lower()
        if len(clean) < 3 or clean in STOPWORDS:
            continue
        is_identifier = (
            any(ch.isdigit() for ch in clean)
            or any(ch in "._/:-" for ch in clean)
        )
        bucket = identifiers if is_identifier else keywords
        if clean not in bucket:
            bucket.append(clean)

    return identifiers[:limit], keywords[:limit]


def _detect_backup_topics(prompt: str) -> List[str]:
    """Map the prompt onto the most relevant backup object types.

    Hints are matched on word boundaries so short hints like `vid` do not fire on
    unrelated words (e.g. "providers").
    """
    prompt_lower = (prompt or "").lower()
    topics: List[str] = []
    for object_type, hints in BACKUP_TOPIC_HINTS:
        for hint in hints:
            if re.search(rf"\b{re.escape(hint.strip())}\b", prompt_lower):
                topics.append(object_type)
                break
    return topics


def _detect_choice_fields(prompt: str) -> List[str]:
    """Map the prompt onto custom field choice sets it explicitly asks about."""
    prompt_lower = (prompt or "").lower()
    fields: List[str] = []
    for field_name, hints in CHOICE_FIELD_HINTS:
        for hint in hints:
            if re.search(rf"\b{re.escape(hint.strip())}\b", prompt_lower):
                fields.append(field_name)
                break
    return fields


def build_backup_context(prompt: str, site_filter: str = None, max_rows: int = 60) -> str:
    """Build AI context from the uploaded NetBox master backup (JSON).

    Returns an empty string when no backup is uploaded or the operator disabled it.
    """
    if not is_backup_active():
        return ""

    meta = get_backup_metadata()
    counts = get_backup_object_counts()

    context: List[str] = []
    context.append("=== NETBOX MASTER BACKUP (FULL DATABASE) ===")
    context.append(f"Source File: {meta['filename']} | Uploaded: {meta['uploaded_at']} | Objects: {meta['record_count']}")

    source = meta.get("source_info") or {}
    if source:
        source_bits = []
        if source.get("netbox_url"):
            source_bits.append(f"NetBox URL: {source['netbox_url']}")
        if source.get("netbox_version"):
            source_bits.append(f"NetBox Version: {source['netbox_version']}")
        if source.get("successful_endpoints") is not None:
            source_bits.append(
                f"Endpoints Captured: {source['successful_endpoints']}/"
                f"{source.get('endpoints_processed', '?')}"
            )
        if source_bits:
            context.append(" | ".join(source_bits))

    if counts:
        inventory_line = ", ".join(
            f"{OBJECT_LABELS.get(k, k.replace('_', ' ').title())}: {v}"
            for k, v in counts.items()
        )
        context.append(f"Backup Contents: {inventory_line}")

    backup_sites = get_backup_site_names()
    if backup_sites:
        context.append(f"Backup Sites ({len(backup_sites)}): {', '.join(backup_sites)}")

    # Custom field choice sets (Instance Type Set, Resource Group Set, ...) are the
    # authoritative allowed-value lists, so advertise them for every question.
    choice_sets = get_choice_set_summary()
    if choice_sets:
        summary_line = ", ".join(
            f"{row['choice_set']} ({row.get('fields') or '-'}): {row['value_count']} values"
            for row in choice_sets
        )
        context.append(f"Custom Field Choice Sets: {summary_line}")

    # Resolve a site from the prompt when the caller did not pass one.
    target_site = (site_filter or "").strip()
    if not target_site:
        prompt_lower = (prompt or "").lower()
        for site in backup_sites:
            if site.lower() in prompt_lower:
                target_site = site
                break

    topics = _detect_backup_topics(prompt)[:4]
    identifiers, keywords = _split_terms(prompt)

    # 0. Full choice value lists when the question names a choice-backed field.
    for field_name in _detect_choice_fields(prompt)[:3]:
        values = get_choice_values_for_field(field_name)
        if not values:
            continue
        context.append(
            f"\n--- Allowed Values for Custom Field `{field_name}` "
            f"(total: {len(values)}) ---"
        )
        context.append(", ".join(values))

    # 1. Exact identifier hits across every object type (hostnames, IPs, CIDRs).
    if identifiers:
        matches = search_backup_records(identifiers, keywords, site=target_site, limit=max_rows)
        if not matches and target_site:
            matches = search_backup_records(identifiers, keywords, limit=max_rows)
        if matches:
            context.append(f"\n--- Objects Matching Your Query ({len(matches)}) ---")
            for row in matches:
                site_part = f" | Site: {row['site']}" if row["site"] else ""
                context.append(f"- [{row['object_label']}] {row['name']}{site_part} | {row['summary']}")

    # 2. Topic-scoped listings so counting and listing questions get real data.
    if topics:
        per_topic = max(10, max_rows // max(len(topics), 1))
        for object_type in topics:
            scoped_total = count_backup_records(object_type, site=target_site)
            scope_site = target_site
            if scoped_total == 0 and target_site:
                scope_site = ""
                scoped_total = count_backup_records(object_type)
            rows = get_backup_records_by_type(
                object_type, site=scope_site, limit=per_topic, keywords=keywords
            )
            if not rows:
                continue
            label = OBJECT_LABELS.get(object_type, object_type.replace("_", " ").title())
            scope = f" at {scope_site}" if scope_site else ""
            context.append(
                f"\n--- {label} Records{scope} "
                f"(total in backup{scope}: {scoped_total}; showing {len(rows)}) ---"
            )
            for row in rows:
                site_part = f" | Site: {row['site']}" if row["site"] else ""
                context.append(f"- {row['name']}{site_part} | {row['summary']}")

    # 3. Keyword-ranked fallback when the question named no object type.
    if not topics and not identifiers and keywords:
        matches = search_backup_records([], keywords, site=target_site, limit=max_rows)
        if matches:
            context.append(f"\n--- Objects Matching Your Query ({len(matches)}) ---")
            for row in matches:
                site_part = f" | Site: {row['site']}" if row["site"] else ""
                context.append(f"- [{row['object_label']}] {row['name']}{site_part} | {row['summary']}")

    # 4. Site profile fallback so site questions always return something useful.
    if target_site and not topics and not identifiers:
        site_rows = search_backup_records([], [], site=target_site, limit=max_rows)
        if site_rows:
            context.append(f"\n--- Backup Objects for Site: {target_site} (showing {len(site_rows)}) ---")
            for row in site_rows:
                context.append(f"- [{row['object_label']}] {row['name']} | {row['summary']}")

    return "\n".join(context)


def get_all_ipam_records(limit: int = 100) -> List[Dict[str, Any]]:
    """Get all IPAM records (VLANs and Prefixes) from database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM ipam_records LIMIT {limit}")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_sites_detailed() -> List[Dict[str, Any]]:
    """Get all sites with detailed information."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sites_records")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_inventory_records(limit: int = 100) -> List[Dict[str, Any]]:
    """Get all inventory records (devices, hypervisors, VMs)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM inventory_records LIMIT {limit}")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def build_comprehensive_ipam_context(prompt: str, site_filter: str = None) -> str:
    """Build comprehensive IPAM context for AI assistant."""
    context = []
    
    # Database statistics
    context.append(f"Database Statistics:")
    context.append(f"- Total Sites: {get_total_sites_count()}")
    context.append(f"- Total VLANs: {get_total_vlans_count()}")
    context.append(f"- Total Prefixes: {get_total_prefixes_count()}")
    context.append(f"- Total IPAM Records: {get_total_ipam_count()}")
    
    # All sites list
    all_sites = get_all_site_names()
    context.append(f"\nAll Sites in Database: {', '.join(all_sites) if all_sites else 'None'}")
    
    # Detect site from prompt
    target_site = site_filter
    if not target_site:
        prompt_lower = prompt.lower()
        for site in all_sites:
            if site.lower() in prompt_lower:
                target_site = site
                break
    
    # Site-specific IPAM data
    if target_site:
        site_ipam = get_ipam_records_by_site(target_site)
        if site_ipam:
            context.append(f"\n=== VLANs and Prefixes for Site: {target_site} ===")
            for record in site_ipam[:100]:  # Show up to 100 records
                vlan_id = record.get('vlan_id', '')
                vlan_name = record.get('vlan_name', '')
                prefix = record.get('prefix_or_subnet', '')
                role = record.get('role', '')
                description = record.get('description', '')
                rec_type = record.get('record_type', 'prefix')
                scope_id = record.get('scope_id', '')
                
                if rec_type == 'vlan' and vlan_id:
                    context.append(f"- VLAN {vlan_id}: {vlan_name} | Subnet: {prefix} | Role: {role} | Scope: {scope_id} | Desc: {description}")
                else:
                    context.append(f"- Prefix: {prefix} | Role: {role} | VLAN: {vlan_name or 'N/A'} | Scope: {scope_id} | Desc: {description}")
        else:
            context.append(f"\nNo VLAN/Prefix records found for site: {target_site}")
    else:
        # Show sample of all IPAM records if no specific site
        all_ipam = get_all_ipam_records(limit=50)
        if all_ipam:
            context.append(f"\n=== Sample IPAM Records (showing {len(all_ipam)}) ===")
            for record in all_ipam:
                site = record.get('site', 'N/A')
                vlan_id = record.get('vlan_id', '')
                vlan_name = record.get('vlan_name', '')
                prefix = record.get('prefix_or_subnet', '')
                role = record.get('role', '')
                rec_type = record.get('record_type', 'prefix')
                
                if rec_type == 'vlan' and vlan_id:
                    context.append(f"- Site: {site} | VLAN {vlan_id}: {vlan_name} | {prefix} | Role: {role}")
                else:
                    context.append(f"- Site: {site} | Prefix: {prefix} | Role: {role}")
    
    # Device/VM inventory for site if mentioned
    if target_site:
        inventory_summary = get_full_site_inventory_summary(target_site)
        if inventory_summary:
            context.append(f"\n=== Inventory for Site: {target_site} ===")
            context.append(inventory_summary)

    backup_context = build_backup_context(prompt, site_filter=target_site)
    if backup_context:
        context.append("")
        context.append(backup_context)

    return "\n".join(context)

def build_comprehensive_naming_context(prompt: str, site_filter: str = None) -> str:
    """Build comprehensive naming/inventory context for AI assistant."""
    context = []
    
    # Database statistics
    all_devices = get_records_by_category("device")
    all_hypervisors = get_records_by_category("hypervisor")
    all_vms = get_records_by_category("vm")
    
    context.append(f"Database Statistics:")
    context.append(f"- Total Devices: {len(all_devices)}")
    context.append(f"- Total Hypervisors: {len(all_hypervisors)}")
    context.append(f"- Total VMs: {len(all_vms)}")
    
    # All sites list
    all_sites = get_all_site_names()
    context.append(f"\nAll Sites in Database: {', '.join(all_sites) if all_sites else 'None'}")
    
    # Detect site from prompt
    target_site = site_filter
    if not target_site:
        prompt_lower = prompt.lower()
        for site in all_sites:
            if site.lower() in prompt_lower:
                target_site = site
                break
    
    # Site-specific or all inventory
    if target_site:
        site_devices = get_records_by_category("device", site_filter=target_site)
        site_hypervisors = get_records_by_category("hypervisor", site_filter=target_site)
        site_vms = get_records_by_category("vm", site_filter=target_site)
        
        context.append(f"\n=== Inventory for Site: {target_site} ===")
        
        if site_devices:
            context.append(f"\nDevices ({len(site_devices)}):")
            for d in site_devices[:50]:
                context.append(f"- {d.get('name', 'N/A')} | Role: {d.get('model_or_role', 'N/A')} | Manufacturer: {d.get('manufacturer', 'N/A')} | Site: {d.get('site', 'N/A')}")
        
        if site_hypervisors:
            context.append(f"\nHypervisors ({len(site_hypervisors)}):")
            for h in site_hypervisors[:50]:
                context.append(f"- {h.get('name', 'N/A')} | Role: {h.get('model_or_role', 'N/A')} | Site: {h.get('site', 'N/A')}")
        
        if site_vms:
            context.append(f"\nVirtual Machines ({len(site_vms)}):")
            for v in site_vms[:50]:
                context.append(f"- {v.get('name', 'N/A')} | Role: {v.get('model_or_role', 'N/A')} | Cluster: {v.get('cluster', 'N/A')} | Site: {v.get('site', 'N/A')}")
        
        if not site_devices and not site_hypervisors and not site_vms:
            context.append(f"No inventory records found for site: {target_site}")
    else:
        # Show sample of all inventory
        context.append(f"\n=== All Devices (showing up to 50) ===")
        for d in all_devices[:50]:
            context.append(f"- {d.get('name', 'N/A')} | Role: {d.get('model_or_role', 'N/A')} | Manufacturer: {d.get('manufacturer', 'N/A')} | Site: {d.get('site', 'N/A')}")
        
        if all_hypervisors:
            context.append(f"\n=== All Hypervisors (showing up to 50) ===")
            for h in all_hypervisors[:50]:
                context.append(f"- {h.get('name', 'N/A')} | Role: {h.get('model_or_role', 'N/A')} | Site: {h.get('site', 'N/A')}")
        
        if all_vms:
            context.append(f"\n=== All VMs (showing up to 50) ===")
            for v in all_vms[:50]:
                context.append(f"- {v.get('name', 'N/A')} | Role: {v.get('model_or_role', 'N/A')} | Cluster: {v.get('cluster', 'N/A')} | Site: {v.get('site', 'N/A')}")

    backup_context = build_backup_context(prompt, site_filter=target_site)
    if backup_context:
        context.append("")
        context.append(backup_context)

    return "\n".join(context)
