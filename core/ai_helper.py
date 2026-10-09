"""
AI Assistant Helper Functions
Provides comprehensive database context for AI queries

UPDATED: Now supports two-pass query system for efficient intent routing
- Legacy functions maintained for backward compatibility
- New two-pass system available via query_with_two_pass_routing()
"""

import re
import gzip
import json
import difflib
from typing import Dict, List, Any, Optional, Tuple
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
    verify_full_database,
)
import sqlite3

DB_PATH = "data/netbox_hub.db"

# ── Compressed backup JSON cache ──────────────────────────────────────────
# Avoids repeated gzip.decompress() + json.loads() on every AI query by
# caching the fully-decoded payload in-process.  Cache is invalidated when the
# stored filename or record count changes (i.e. a new backup was uploaded).
_cached_backup_payload: Optional[Dict[str, Any]] = None
_cached_backup_meta: Tuple[str, int] = ("", 0)


def _get_cached_backup_payload() -> Optional[Dict[str, Any]]:
    """Return the decompressed backup JSON dict, loading/caching it once per upload."""
    global _cached_backup_payload, _cached_backup_meta
    try:
        meta = get_backup_metadata()
    except Exception:
        return None
    current_meta = (meta.get("filename") or "", meta.get("record_count") or 0)
    if current_meta == _cached_backup_meta and _cached_backup_payload is not None:
        return _cached_backup_payload
    # Cache miss or upload changed — decompress fresh
    try:
        init_backup_tables()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(backup_metadata)")
        cols = [c[1] for c in cursor.fetchall()]
        if "backup_json_compressed" in cols:
            cursor.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                raw = gzip.decompress(row[0]).decode("utf-8")
                payload = json.loads(raw)
                _cached_backup_payload = payload
                _cached_backup_meta = current_meta
                return payload
        conn.close()
    except Exception:
        pass
    # Failed to load — clear cache so we don't keep retrying the same bad state
    _cached_backup_payload = None
    _cached_backup_meta = current_meta
    return None


def invalidate_backup_cache() -> None:
    """Clear the in-process backup payload cache (call after upload or clear)."""
    global _cached_backup_payload, _cached_backup_meta
    _cached_backup_payload = None
    _cached_backup_meta = ("", 0)

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
    ("dcim_cables", ("cable", "cables", "patch", "cabling", "connected", "connection", "connections", "connect", "connects", "wired", "linked", "physical connection")),
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


def build_session_backup_context(prompt: str, max_rows: int = 50) -> str:
    """Build context from SharedBackupState for objects not indexed in backup_records database.
    
    This handles cases where NetBox JSON backup data is loaded into session state but not
    fully indexed in the backup_records table (e.g., users/users, custom endpoints).
    """
    from core.shared_backup_state import SharedBackupState
    
    registry = SharedBackupState.get_object_registry()
    if not registry:
        return ""
    
    prompt_lower = (prompt or "").lower()
    context = []
    context.append("=== SESSION BACKUP DATA (In-Memory Registry) ===")
    
    # Find endpoints mentioned in the prompt
    relevant_endpoints = []
    for endpoint, metadata in registry.items():
        # Check if endpoint parts are in the prompt
        endpoint_parts = re.split(r'[/_\-.]', endpoint.lower())
        if any(part in prompt_lower for part in endpoint_parts if len(part) > 2):
            relevant_endpoints.append((endpoint, metadata))
    
    # If no matches found, show all endpoints with data
    if not relevant_endpoints:
        relevant_endpoints = [(ep, meta) for ep, meta in registry.items() if meta.get('count', 0) > 0]
    
    # Limit to top 10 endpoints
    relevant_endpoints = relevant_endpoints[:10]
    
    if not relevant_endpoints:
        return ""
    
    # Show data from relevant endpoints
    for endpoint, metadata in relevant_endpoints:
        count = metadata.get('count', 0)
        label = metadata.get('label', endpoint)
        source_type = metadata.get('source_type', 'json')
        
        context.append(f"\n--- {label} ({endpoint}) ---")
        context.append(f"Total records: {count} | Source: {source_type}")
        
        # Try to get actual object data from the inspector
        inspector = SharedBackupState.get_inspector()
        if inspector and source_type == 'json':
            try:
                objects = inspector.get_object_data(endpoint)
                if objects:
                    show_count = min(len(objects), 20)
                    context.append(f"Sample data (showing {show_count} of {len(objects)}):")
                    
                    for obj in objects[:show_count]:
                        # Format object as key summary
                        name = obj.get('name') or obj.get('username') or obj.get('display') or obj.get('id', 'N/A')
                        
                        # Build summary from available fields
                        summary_parts = []
                        for key in ['email', 'role', 'group', 'description', 'status', 'type']:
                            if key in obj and obj[key]:
                                summary_parts.append(f"{key}={obj[key]}")
                        
                        summary = ', '.join(summary_parts) if summary_parts else str(obj)[:100]
                        context.append(f"  - {name}: {summary}")
            except Exception as e:
                context.append(f"  (Unable to retrieve detailed data: {e})")
    
    return "\n".join(context)

def build_backup_context(prompt: str, site_filter: str = None, max_rows: int = 60) -> str:
    """Build AI context from the uploaded NetBox master backup (JSON).

    Returns an empty string when no backup is uploaded.
    When the backup is disabled (enabled=0), data is still available but
    marked as excluded so the AI knows it may not be authoritative.
    """
    from core.shared_backup_state import SharedBackupState

    # Check both database backup and SharedBackupState
    # Use has_loaded() instead of is_backup_active() so disabled backups
    # still provide data (the enabled flag only controls automatic inclusion)
    meta = get_backup_metadata()
    has_db_backup = meta.get("loaded", False)
    has_session_backup = SharedBackupState.has_backup()

    if not has_db_backup and not has_session_backup:
        return ""

    counts = get_backup_object_counts() if has_db_backup else {}

    context: List[str] = []
    context.append("=== NETBOX MASTER BACKUP (FULL DATABASE) ===")
    filename = meta.get("filename") or "NetBox Master Backup"
    uploaded_at = meta.get("uploaded_at") or "Unknown"
    record_count = meta.get("record_count", 0)
    context.append(f"Source File: {filename} | Uploaded: {uploaded_at} | Objects: {record_count}")

    # Include structured table counts for interfaces and cables
    try:
        from core.query_executor import get_table_schema
        structured_info = []
        for tbl in ("interfaces", "cables"):
            try:
                schema = get_table_schema(tbl)
                if schema.get("row_count", 0) > 0:
                    structured_info.append(f"{tbl}: {schema['row_count']} rows")
            except Exception:
                pass
        if structured_info:
            context.append(f"Structured Tables: {', '.join(structured_info)}")
    except Exception:
        pass

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
        else:
            # Fuzzy hostname fallback (v3.9.21): fetch full device context
            known_names = _collect_known_hostnames()
            fuzzy_results, fuzzy_ctx = try_fuzzy_hostname_lookup(
                query=identifiers[0],
                known_names=known_names,
                search_func=lambda ids, site="", limit=10: search_backup_records(
                    ids, keywords, site=site, limit=limit
                ),
                site_filter=target_site,
                max_rows=max_rows,
            )
            if fuzzy_ctx:
                context.append(f"\n{fuzzy_ctx}")
            elif fuzzy_results:
                context.append(f"\n--- Objects Matching Your Query ({len(fuzzy_results)}) ---")
                for row in fuzzy_results:
                    site_part = f" | Site: {row.get('site', '')}" if row.get("site") else ""
                    context.append(f"- [{row.get('object_label', 'unknown')}] {row.get('name', 'N/A')}{site_part} | {row.get('summary', '')}")

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

    # 5. Query SharedBackupState for data that might not be indexed in backup_records
    # This handles cases where data is in session state but not fully indexed in the database
    if has_session_backup:
        session_context = build_session_backup_context(prompt, max_rows)
        if session_context:
            context.append("")
            context.append(session_context)

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

def get_all_dynamic_tables() -> List[tuple]:
    """Get list of all dynamic tables with their model keys and record counts."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Find all tables starting with 'dynamic_'
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name LIKE 'dynamic_%' 
        ORDER BY name
    """)
    tables = cursor.fetchall()
    
    result = []
    for (table_name,) in tables:
        # Get count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        
        # Get model key from first record
        cursor.execute(f"SELECT _model_key FROM {table_name} LIMIT 1")
        row = cursor.fetchone()
        model_key = row[0] if row else table_name.replace('dynamic_', '')
        
        result.append((table_name, model_key, count))
    
    conn.close()
    return result

def get_dynamic_table_records(table_name: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Get records from a specific dynamic table."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()

def _collect_known_serials() -> List[str]:
    """Extract the exact *serial* attribute directly from structured device records.

    Reads the ``serial`` key from the cached (or freshly decompressed) NetBox
    backup JSON and returns distinct, non-trivial serial strings sorted by
    length descending so the longest serials are matched first by the vault.

    No regex guessing is performed on raw search blobs — only the explicit
    ``serial`` field of each record is used.
    """
    trivial = {"n/a", "none", ""}
    seen: set = set()
    serials: List[str] = []

    # 1. Try in-memory cache first (avoids DB + gzip + json overhead).
    payload = _get_cached_backup_payload()
    endpoints: List[Any] = payload.get("endpoints") if isinstance(payload, dict) else []
    records_map: Dict[str, Any] = {
        k: v for k, v in (payload or {}).items()
        if isinstance(v, list) and k not in ("metadata", "endpoints", "summary")
    } if isinstance(payload, dict) else {}

    # Collect serials only from the dcim/devices endpoint and its flat-key
    # equivalent; virtual-machine records carry no serial in NetBox and are
    # intentionally skipped here.
    target_types = ("dcim/devices",)
    flat_keys = ("dcim_devices",)
    for ep in endpoints:
        path = ep.get("path", "")
        if path not in target_types:
            continue
        for rec in ep.get("records", []):
            s = (rec.get("serial") or "").strip()
            if s and s.lower() not in trivial and s not in seen:
                seen.add(s)
                serials.append(s)

    for fk in flat_keys:
        for rec in records_map.get(fk, []):
            s = (rec.get("serial") or "").strip()
            if s and s.lower() not in trivial and s not in seen:
                seen.add(s)
                serials.append(s)

    return sorted(serials, key=len, reverse=True)


def _collect_known_hostnames() -> List[str]:
    """Pull all registered device, hypervisor and VM names from the inventory DB
    and supplement with VM names from the cached NetBox backup JSON.

    Names are returned sorted by length descending so the longest (most specific)
    hostnames are tokenised first, minimising accidental partial replacements.
    Site names are explicitly excluded so they remain visible in AI responses.
    """
    try:
        site_names = set(get_all_site_names())
    except Exception:
        site_names = set()

    names: List[str] = []
    seen: set = set()

    # 1. Inventory DB — devices, hypervisors, VMs.
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        for cat in ("device", "hypervisor", "vm"):
            cursor.execute(
                f"SELECT name FROM inventory_records WHERE category = ? AND name IS NOT NULL AND name != ''",
                (cat,),
            )
            for row in cursor.fetchall():
                n = str(row["name"]).strip()
                if n and n not in seen and n not in site_names:
                    seen.add(n)
                    names.append(n)
        conn.close()
    except Exception:
        pass

    # 2. Backup JSON — virtualization_virtual_machines (flat key) and the
    #    virtualization/virtual-machines endpoint.  This catches VMs that may
    #    exist in the master backup but were not yet indexed into inventory_records.
    payload = _get_cached_backup_payload()
    if isinstance(payload, dict):
        # Flat-key layout.
        vms_flat = payload.get("virtualization_virtual_machines", [])
        if isinstance(vms_flat, list):
            for rec in vms_flat:
                n = (rec.get("name") or "").strip()
                if n and n not in seen and n not in site_names:
                    seen.add(n)
                    names.append(n)

        # API-walk endpoint layout.
        for ep in (payload.get("endpoints") or []):
            if ep.get("path") == "virtualization/virtual-machines":
                for rec in ep.get("records", []):
                    n = (rec.get("name") or "").strip()
                    if n and n not in seen and n not in site_names:
                        seen.add(n)
                        names.append(n)
                break

    return sorted(names, key=len, reverse=True)


def _sanitize_context_text(text: str) -> Tuple[str, str]:
    """Sanitize a context string via the vault, returning (sanitized_text, session_id)."""
    from core.vault import SanitizerVault
    vault = SanitizerVault()
    known_hostnames = _collect_known_hostnames()
    known_serials = _collect_known_serials()
    return vault.sanitize(text, known_hostnames=known_hostnames, known_serials=known_serials)


def build_dynamic_tables_context(prompt: str) -> str:
    """Build context from dynamically uploaded CSV tables."""
    dynamic_tables = get_all_dynamic_tables()
    
    if not dynamic_tables:
        return ""
    
    context = []
    context.append("\n=== UPLOADED CSV DATA (Dynamic Tables) ===")
    
    # Summary of all dynamic tables
    summary_parts = []
    for table_name, model_key, count in dynamic_tables:
        # Clean up display name
        display_key = model_key.replace('/', ' / ').replace('.', ' . ')
        summary_parts.append(f"{display_key} ({count} records)")
    
    context.append(f"Available Data: {', '.join(summary_parts)}")
    
    # Check if prompt mentions any of these tables
    prompt_lower = (prompt or "").lower()
    relevant_tables = []
    
    for table_name, model_key, count in dynamic_tables:
        # Check if any part of the model key is mentioned in the prompt
        model_parts = re.split(r'[/_\-.]', model_key.lower())
        if any(part in prompt_lower for part in model_parts if len(part) > 2):
            relevant_tables.append((table_name, model_key, count))
    
    # If no specific match, show all tables with data
    if not relevant_tables:
        relevant_tables = [(t, k, c) for t, k, c in dynamic_tables if c > 0]
    
    # Show data from relevant tables
    for table_name, model_key, count in relevant_tables[:5]:  # Limit to 5 tables
        records = get_dynamic_table_records(table_name, limit=50)
        if not records:
            continue
        
        display_key = model_key.replace('/', ' / ').replace('.', ' . ')
        context.append(f"\n--- {display_key} (total: {count}; showing {len(records)}) ---")
        
        for rec in records:
            # Skip internal metadata fields in display
            display_rec = {k: v for k, v in rec.items() 
                          if not k.startswith('_') and v is not None}
            
            if display_rec:
                # Format as key=value pairs
                parts = [f"{k}={v}" for k, v in display_rec.items()]
                context.append(f"- {', '.join(parts)}")
    
    return "\n".join(context)

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
        site_ipam = get_ipam_records_by_site(target_site)[:200]
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

    # Add dynamic CSV tables context
    dynamic_context = build_dynamic_tables_context(prompt)
    if dynamic_context:
        context.append("")
        context.append(dynamic_context)

    full_text = "\n".join(context)
    sanitized_text, _ = _sanitize_context_text(full_text)
    return sanitized_text

def build_comprehensive_naming_context(prompt: str, site_filter: str = None) -> str:
    """Build comprehensive naming/inventory context for AI assistant.

    All record fetches are hard-capped to prevent token overflow when the
    database contains thousands of devices / VLANs / prefixes.
    """
    context = []
    MAX_PREVIEW = 50  # per-category cap for "all records" preview

    # Database statistics
    all_devices = get_records_by_category("device")[:MAX_PREVIEW]
    all_hypervisors = get_records_by_category("hypervisor")[:MAX_PREVIEW]
    all_vms = get_records_by_category("vm")[:MAX_PREVIEW]
    
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

    # Add dynamic CSV tables context
    dynamic_context = build_dynamic_tables_context(prompt)
    if dynamic_context:
        context.append("")
        context.append(dynamic_context)

    full_text = "\n".join(context)
    sanitized_text, _ = _sanitize_context_text(full_text)
    return sanitized_text


# ============================================================================
# TWO-PASS QUERY SYSTEM - NEW EFFICIENT ROUTING
# ============================================================================

def query_with_two_pass_routing(
    user_query: str,
    ai_call_func,
    max_rows: int = 60,
    enable_metrics: bool = False
) -> Tuple[str, Optional[Any]]:
    """
    Execute AI query using two-pass system for efficient intent routing.
    
    This is the new recommended approach that:
    1. Classifies intent with <1000 token prompt (Pass 1)
    2. Queries only relevant endpoints (Pass 2)
    3. Generates response with targeted context
    
    Args:
        user_query: User's natural language question
        ai_call_func: AI call function with signature (prompt, model, custom_system_msg) -> response
        max_rows: Maximum records to retrieve
        enable_metrics: Whether to return performance metrics
    
    Returns:
        Tuple of (final_response, metrics or None)
    
    Example:
        >>> from core.ai_client import call_ai
        >>> response, metrics = query_with_two_pass_routing(
        ...     "Show me all switches at Site-HQ",
        ...     lambda prompt, model: call_ai(prompt, model, None)
        ... )
    """
    from core.ai_two_pass_system import query_with_two_pass
    
    return query_with_two_pass(user_query, ai_call_func, max_rows, enable_metrics)


def get_two_pass_system_status() -> Dict[str, Any]:
    """
    Get status and configuration of the two-pass system.
    
    Returns information about:
    - Pass 1 token budget compliance
    - Endpoint coverage statistics
    - System version and configuration
    """
    from core.ai_two_pass_system import get_system_info
    
    return get_system_info()


def validate_two_pass_token_budget() -> Dict[str, Any]:
    """
    Validate that Pass 1 stays under 1000 token budget.
    
    Returns:
        Dict with validation results including:
        - estimated_tokens: Estimated token count for Pass 1
        - under_budget: Boolean indicating if under 1000 tokens
        - budget_remaining: Tokens remaining in budget
        - status: "PASS" or "FAIL"
    """
    from core.ai_intent_router import validate_pass1_token_budget
    
    return validate_pass1_token_budget()


# ── SAFE HOSTNAME FUZZY MATCHING (v3.9.20) ─────────────────────────────────

_FUZZY_CUTOFF = 0.82
_FUZZY_MAX_CANDIDATES = 3
_FUZZY_SUGGESTION_TEMPLATE = (
    "[FUZZY_SUGGESTION]: Exact object '{query}' not found. "
    "Did you mean one of: {candidates} ?"
)


def _extract_trailing_seq(name: str) -> str:
    """Return the trailing numeric sequence from *name* (e.g. '01' from 'sw01')."""
    m = re.search(r'(\d+)$', name)
    return m.group(1) if m else ""


def _names_differ_only_by_trailing_number(a: str, b: str) -> bool:
    """Return True when *a* and *b* are identical except for a trailing digit
    sequence.  Used by the suffix guard to reject near-misses like SW1 vs SW2.
    """
    if a.lower() == b.lower():
        return False
    al, bl = a.lower(), b.lower()
    da, db = _extract_trailing_seq(al), _extract_trailing_seq(bl)
    if not da or not db:
        return False
    prefix_a = al[: len(al) - len(da)]
    prefix_b = bl[: len(bl) - len(db)]
    return prefix_a == prefix_b and da != db


def fuzzy_match_hostname(
    query: str,
    known_names: Optional[List[str]] = None,
    cutoff: float = _FUZZY_CUTOFF,
    max_candidates: int = _FUZZY_MAX_CANDIDATES,
) -> List[Dict[str, Any]]:
    """Compute fuzzy-match candidates for *query* against *known_names*.

    Guardrails enforced:
      - Similarity ratio must be >= *cutoff* (default 0.82).
      - Names differing ONLY by trailing digit sequence are rejected (suffix
        guard) to prevent mixing up Node1/Node2 or Prod/Test.
      - At most *max_candidates* top-ranked suggestions are returned.

    Returns a list of dicts with keys ``{name, ratio, token}`` so that callers
    can pass the matched names through the standard Zero-Leakage tokenization
    cycle via ``SanitizerVault.sanitize()``.
    """
    if not known_names:
        return []
    query_clean = query.strip()
    if not query_clean:
        return []

    # Build ratio map, apply suffix guard, filter by cutoff.
    scored: List[Tuple[str, float]] = []
    for name in known_names:
        name_str = str(name).strip()
        if not name_str:
            continue
        # Exact match → skip (caller should handle that separately)
        if name_str.lower() == query_clean.lower():
            continue
        # Suffix guard: reject if names differ only by trailing digits
        if _names_differ_only_by_trailing_number(query_clean, name_str):
            continue
        ratio = difflib.SequenceMatcher(None, query_clean.lower(), name_str.lower()).ratio()
        if ratio >= cutoff:
            scored.append((name_str, ratio))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:max_candidates]

    return [{"name": n, "ratio": round(r, 4), "token": n} for n, r in top]


def build_fuzzy_suggestion(
    query: str,
    candidates: List[Dict[str, Any]],
) -> str:
    """Format a FUZZY_SUGGESTION block from *candidates* for the AI context.

    The query term itself is returned as a token entry so the caller can route
    it through the vault sanitization cycle.
    """
    if not candidates:
        return ""
    names = [c["name"] for c in candidates]
    return _FUZZY_SUGGESTION_TEMPLATE.format(query=query, candidates=", ".join(names))


_SYSTEM_NOTICE_TEMPLATE = (
    "[SYSTEM NOTICE]: Exact name '{query}' not found. "
    "Automatically providing context for the closest matching object: '{candidate}'."
)


def _fetch_device_context(name: str, site_filter: str = "", limit: int = 200) -> List[Dict[str, Any]]:
    """Return full backup + inventory records for *name* (device/VM/interfaces/IPs).

    Searches both ``backup_records`` (for NetBox objects like interfaces and IP
    addresses associated with the device) and ``inventory_records`` (for the
    device/VM profile itself.

    Args:
        name: Device/VM name to search for.
        site_filter: Optional site name to narrow the search.
        limit: Maximum total rows to return (hard cap to prevent token overflow).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    rows: List[Dict[str, Any]] = []
    pattern = f"%{name}%"
    where_site = f" AND LOWER(site) LIKE ?" if site_filter else ""
    params: List[Any] = [pattern]
    if site_filter:
        params.append(f"%{site_filter.strip().lower()}%")

    # 1. Backup records — device, interfaces, IPs, VRFs, NAT, etc.
    # Allocate budget: 60% for backup, 40% for inventory
    backup_limit = min(limit * 3 // 5, limit)
    cursor.execute(
        f"SELECT object_type, object_label, name, site, summary FROM backup_records "
        f"WHERE (LOWER(name) LIKE ? OR search_blob LIKE ?){where_site} "
        f"ORDER BY object_type, name LIMIT {backup_limit}",
        params + [pattern] + (params[1:] if site_filter else []),
    )
    for row in cursor.fetchall():
        rows.append(dict(row))

    # 2. Inventory records — device/VM profile (remaining budget)
    inventory_limit = max(1, limit - len(rows))
    cursor.execute(
        f"SELECT category, name, model_or_role, site, cluster, description "
        f"FROM inventory_records WHERE LOWER(name) LIKE ?{where_site} "
        f"ORDER BY category, name LIMIT {inventory_limit}",
        params,
    )
    for row in cursor.fetchall():
        rows.append(dict(row))

    conn.close()
    return rows


def try_fuzzy_hostname_lookup(
    query: str,
    known_names: Optional[List[str]] = None,
    search_func=None,
    site_filter: str = "",
    max_rows: int = 10,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Attempt an exact lookup first; on miss, fall back to fuzzy matching.

    When a high-confidence fuzzy candidate is found, the function fetches the
    **full device context** (interfaces, IPs, VRF, NAT, site, inventory profile)
    for that candidate and returns it inside a structured context block prefixed
    with a ``[SYSTEM NOTICE]`` header.  Both the misspelled query and the
    matched hostname are registered in the vault so downstream sanitization
    remains consistent.

    Returns (results, context_block) where *results* is the list of matched
    backup records and *context_block* is the formatted context string (or
    ``None`` when an exact match succeeded).
    """
    if search_func is not None:
        results = search_func([query], site=site_filter, limit=max_rows)
        if results:
            return results, None

    # Exact miss — run fuzzy matcher
    suggestions = fuzzy_match_hostname(query, known_names=known_names)
    if not suggestions:
        return [], None

    # Take the top candidate (highest similarity ratio)
    best = suggestions[0]
    best_name = best["name"]

    # Register both the misspelled query and the matched hostname in the vault
    from core.vault import SanitizerVault
    vault = SanitizerVault()
    vault._get_or_create_token(query, "HOST", None)
    vault._get_or_create_token(best_name, "HOST", None)

    # Fetch full device context for the matched hostname
    context_rows = _fetch_device_context(best_name, site_filter=site_filter, limit=100)

    if not context_rows:
        # No context available — fall back to suggestion-only message
        return [], build_fuzzy_suggestion(query, suggestions)

    # Build structured context block
    notice = _SYSTEM_NOTICE_TEMPLATE.format(query=query, candidate=best_name)
    lines = [notice, ""]

    # Group rows by object type for readable output
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in context_rows:
        obj_type = row.get("object_type") or row.get("category") or "unknown"
        by_type.setdefault(obj_type, []).append(row)

    for obj_type, type_rows in by_type.items():
        label = obj_type.replace("_", " ").title()
        lines.append(f"--- {label} for '{best_name}' ---")
        for r in type_rows[:20]:
            name = r.get("name", "")
            site = r.get("site", "")
            summary = r.get("summary", "")
            model = r.get("model_or_role", "")
            cluster = r.get("cluster", "")
            desc = r.get("description", "")

            parts = [f"name={name}"]
            if site:
                parts.append(f"site={site}")
            if model:
                parts.append(f"model={model}")
            if cluster:
                parts.append(f"cluster={cluster}")
            if desc:
                parts.append(f"description={desc}")
            if summary:
                parts.append(f"details={summary}")

            lines.append(f"  - {' | '.join(parts)}")
        lines.append("")

    context_block = "\n".join(lines)
    return context_rows, context_block

