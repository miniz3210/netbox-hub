"""
NetBox Master Backup (JSON) Ingest & Lookup Layer

Consumes the full `NetBox_Backup_*.json` export (the same payload the PowerShell
sync agent pushes) and makes every object available to the app and the AI
Assistant. The upload populates the regular Sites / IPAM / Inventory tables so
existing lookups keep working, and additionally stores a flattened, searchable
row per NetBox object so the AI Assistant can answer questions about any part of
the master database.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.db_manager import (
    DB_PATH,
    init_db,
    save_ipam_records_batch,
    save_records_batch,
    save_sites_batch,
    set_sync_metadata,
)

BACKUP_SOURCE = "NetBox Backup (JSON)"

HYPERVISOR_HINTS = ("esx", "hypervisor", "infhost", "vmhost", "esxi")

# Canonical object type -> human label used in AI context and UI counters
OBJECT_LABELS: Dict[str, str] = {
    "dcim_sites": "Site",
    "dcim_regions": "Region",
    "dcim_racks": "Rack",
    "dcim_manufacturers": "Manufacturer",
    "dcim_device_types": "Device Type",
    "dcim_device_roles": "Device Role",
    "dcim_platforms": "Platform",
    "dcim_devices": "Device",
    "dcim_interfaces": "Interface",
    "ipam_vrfs": "VRF",
    "ipam_vlans": "VLAN",
    "ipam_prefixes": "Prefix",
    "ipam_ip_addresses": "IP Address",
    "ipam_aggregates": "Aggregate",
    "ipam_roles": "IPAM Role",
    "ipam_vlan_groups": "VLAN Group",
    "virtualization_clusters": "Cluster",
    "virtualization_virtual_machines": "Virtual Machine",
    "virtualization_interfaces": "VM Interface",
    "tenancy_tenants": "Tenant",
    "circuits_providers": "Circuit Provider",
    "circuits_circuits": "Circuit",
}

# Ordered (label, dotted path) pairs rendered into each record's summary line.
FIELD_SPECS: Dict[str, List[Any]] = {
    "dcim_sites": [
        ("Slug", "slug"), ("Status", "status"), ("Region", "region"),
        ("Group", "group"), ("Tenant", "tenant"), ("Facility", "facility"),
        ("Time Zone", "time_zone"), ("Address", "physical_address"),
        ("ASNs", "asns"), ("Tags", "tags"), ("Devices", "device_count"),
        ("VMs", "virtualmachine_count"), ("Prefixes", "prefix_count"),
        ("VLANs", "vlan_count"), ("Racks", "rack_count"),
        ("Description", "description"),
    ],
    "dcim_regions": [
        ("Slug", "slug"), ("Parent", "parent"), ("Sites", "site_count"),
        ("Prefixes", "prefix_count"), ("Description", "description"),
    ],
    "dcim_racks": [
        ("Site", "site"), ("Location", "location"), ("Status", "status"),
        ("Role", "role"), ("Tenant", "tenant"), ("Height", "u_height"),
        ("Width", "width"), ("Serial", "serial"), ("Asset Tag", "asset_tag"),
        ("Description", "description"),
    ],
    "dcim_manufacturers": [
        ("Slug", "slug"), ("Device Types", "devicetype_count"),
        ("Platforms", "platform_count"), ("Description", "description"),
    ],
    "dcim_device_types": [
        ("Manufacturer", "manufacturer"), ("Model", "model"), ("Slug", "slug"),
        ("Part Number", "part_number"), ("Height", "u_height"),
        ("Default Platform", "default_platform"), ("Description", "description"),
    ],
    "dcim_device_roles": [
        ("Slug", "slug"), ("VM Role", "vm_role"), ("Parent", "parent"),
        ("Devices", "device_count"), ("VMs", "virtualmachine_count"),
        ("Description", "description"),
    ],
    "dcim_platforms": [
        ("Slug", "slug"), ("Manufacturer", "manufacturer"),
        ("Devices", "device_count"), ("VMs", "virtualmachine_count"),
        ("Description", "description"),
    ],
    "dcim_devices": [
        ("Role", "role"), ("Manufacturer", "device_type.manufacturer"),
        ("Model", "device_type.model"), ("Site", "site"),
        ("Location", "location"), ("Rack", "rack"), ("Position", "position"),
        ("Status", "status"), ("Platform", "platform"), ("Tenant", "tenant"),
        ("Cluster", "cluster"), ("Serial", "serial"),
        ("Asset Tag", "asset_tag"), ("Primary IP", "primary_ip"),
        ("Description", "description"),
    ],
    "dcim_interfaces": [
        ("Device", "device"), ("Type", "type"), ("Label", "label"),
        ("Enabled", "enabled"), ("Mode", "mode"), ("MTU", "mtu"),
        ("MAC", "primary_mac_address"), ("LAG", "lag"),
        ("Untagged VLAN", "untagged_vlan"), ("Description", "description"),
    ],
    "virtualization_interfaces": [
        ("Virtual Machine", "virtual_machine"), ("Enabled", "enabled"),
        ("MTU", "mtu"), ("MAC", "mac_address"), ("Mode", "mode"),
        ("Untagged VLAN", "untagged_vlan"), ("Description", "description"),
    ],
    "ipam_vrfs": [
        ("RD", "rd"), ("Tenant", "tenant"),
        ("IP Addresses", "ipaddress_count"), ("Description", "description"),
    ],
    "ipam_vlans": [
        ("VID", "vid"), ("Site", "site"), ("Group", "group"),
        ("Status", "status"), ("Role", "role"), ("Tenant", "tenant"),
        ("Prefixes", "prefix_count"), ("Description", "description"),
    ],
    "ipam_prefixes": [
        ("Prefix", "prefix"), ("Family", "family"), ("Scope", "scope"),
        ("Scope ID", "scope_id"), ("VRF", "vrf"), ("VLAN", "vlan.vid"),
        ("VLAN Name", "vlan.name"), ("Status", "status"), ("Role", "role"),
        ("Tenant", "tenant"), ("Pool", "is_pool"),
        ("Description", "description"),
    ],
    "ipam_ip_addresses": [
        ("Address", "address"), ("Family", "family"), ("Status", "status"),
        ("Role", "role"), ("VRF", "vrf"), ("Tenant", "tenant"),
        ("DNS Name", "dns_name"), ("Assigned To", "assigned_object"),
        ("Assigned Device", "assigned_object.device"),
        ("Assigned VM", "assigned_object.virtual_machine"),
        ("Assigned Type", "assigned_object_type"),
        ("NAT Inside", "nat_inside"), ("Description", "description"),
    ],
    "virtualization_clusters": [
        ("Type", "type"), ("Group", "group"), ("Status", "status"),
        ("Scope", "scope"), ("Tenant", "tenant"), ("Description", "description"),
    ],
    "virtualization_virtual_machines": [
        ("Role", "role"), ("Status", "status"), ("Site", "site"),
        ("Cluster", "cluster"), ("Device", "device"), ("Platform", "platform"),
        ("Tenant", "tenant"), ("vCPUs", "vcpus"), ("Memory", "memory"),
        ("Disk", "disk"), ("Primary IP", "primary_ip"),
        ("Description", "description"),
    ],
    "tenancy_tenants": [
        ("Slug", "slug"), ("Group", "group"), ("Sites", "site_count"),
        ("Devices", "device_count"), ("Prefixes", "prefix_count"),
        ("Description", "description"),
    ],
    "circuits_providers": [
        ("Slug", "slug"), ("Accounts", "accounts"), ("ASNs", "asns"),
        ("Circuits", "circuit_count"), ("Description", "description"),
    ],
    "circuits_circuits": [
        ("CID", "cid"), ("Provider", "provider"), ("Type", "type"),
        ("Status", "status"), ("Tenant", "tenant"),
        ("Commit Rate", "commit_rate"), ("Install Date", "install_date"),
        ("Side A", "termination_a"), ("Side Z", "termination_z"),
        ("Description", "description"),
    ],
}

# Keys searched for a display name, in priority order.
NAME_KEYS = ("name", "display", "prefix", "address", "cid", "model", "rd", "vid")


# ── SCHEMA ──────────────────────────────────────────────────────────────

def init_backup_tables() -> None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            object_type TEXT,
            object_label TEXT,
            object_id INTEGER,
            name TEXT,
            site TEXT,
            summary TEXT,
            search_blob TEXT,
            imported_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_metadata (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            filename TEXT,
            uploaded_at TEXT,
            record_count INTEGER,
            object_counts TEXT,
            enabled INTEGER DEFAULT 1
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_backup_type ON backup_records(object_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_backup_site ON backup_records(site)")
    conn.commit()
    conn.close()


# ── VALUE HELPERS ───────────────────────────────────────────────────────

def _flatten_value(value: Any) -> str:
    """Render a NetBox API value (nested object, choice, list or scalar) as text."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, dict):
        for key in ("name", "display", "label", "address", "prefix", "cid", "model", "value"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_flatten_value(item) for item in value]
        return ", ".join(p for p in parts if p)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\r\n", " ").replace("\n", " ").strip()


def _dig(obj: Dict[str, Any], path: str) -> Any:
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _canonical_type(raw_key: str) -> str:
    return str(raw_key).strip().replace("/", "_").replace("-", "_").lower()


def _label_for(object_type: str) -> str:
    if object_type in OBJECT_LABELS:
        return OBJECT_LABELS[object_type]
    return object_type.replace("_", " ").title()


def _record_name(object_type: str, obj: Dict[str, Any]) -> str:
    for key in NAME_KEYS:
        val = obj.get(key)
        text = _flatten_value(val)
        if text:
            return text
    return f"{_label_for(object_type)} #{obj.get('id', '?')}"


def _custom_fields_text(obj: Dict[str, Any]) -> str:
    fields = obj.get("custom_fields")
    if not isinstance(fields, dict):
        return ""
    parts = []
    for key, value in fields.items():
        text = _flatten_value(value)
        if text:
            parts.append(f"{key}={text}")
    return ", ".join(parts)


def _resolve_site(obj: Dict[str, Any], device_sites: Dict[int, str]) -> str:
    site = _flatten_value(obj.get("site"))
    if site:
        return site

    if obj.get("scope_type") == "dcim.site":
        site = _flatten_value(obj.get("scope"))
        if site:
            return site

    for parent_key in ("device", "virtual_machine"):
        parent = obj.get(parent_key)
        if isinstance(parent, dict):
            site = device_sites.get(parent.get("id"))
            if site:
                return site

    assigned = obj.get("assigned_object")
    if isinstance(assigned, dict):
        for parent_key in ("device", "virtual_machine"):
            parent = assigned.get(parent_key)
            if isinstance(parent, dict):
                site = device_sites.get(parent.get("id"))
                if site:
                    return site

    return ""


def _summarize(object_type: str, obj: Dict[str, Any], site: str) -> str:
    spec = FIELD_SPECS.get(object_type)
    parts: List[str] = []
    site_lower = site.strip().lower()

    if spec:
        for label, path in spec:
            text = _flatten_value(_dig(obj, path))
            if not text:
                continue
            # The site is rendered as its own column, so don't repeat it here.
            if label == "Site" and text.strip().lower() == site_lower:
                continue
            parts.append(f"{label}: {text}")
    else:
        for key, value in obj.items():
            if key in ("id", "url", "display_url", "display", "name", "tags",
                       "custom_fields", "created", "last_updated", "comments"):
                continue
            text = _flatten_value(value)
            if text:
                parts.append(f"{key.replace('_', ' ').title()}: {text}")

    tags = _flatten_value(obj.get("tags"))
    if tags:
        parts.append(f"Tags: {tags}")

    custom = _custom_fields_text(obj)
    if custom:
        parts.append(f"Custom Fields: {custom}")

    return " | ".join(parts)


# ── PARSING ─────────────────────────────────────────────────────────────

def _load_payload(file_bytes: Any) -> Dict[str, Any]:
    raw = file_bytes.read() if hasattr(file_bytes, "read") else file_bytes
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid NetBox backup JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid NetBox backup: expected a JSON object keyed by NetBox endpoints.")
    return payload


def _bucket_payload(payload: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for raw_key, value in payload.items():
        if not isinstance(value, list):
            continue
        canonical = _canonical_type(raw_key)
        if canonical in ("sync_key",):
            continue
        rows = [item for item in value if isinstance(item, dict)]
        if rows:
            buckets.setdefault(canonical, []).extend(rows)
    if not buckets:
        raise ValueError(
            "No NetBox object collections found. Expected keys such as "
            "`dcim_sites`, `dcim_devices`, `ipam_vlans` or `ipam_prefixes`."
        )
    return buckets


def _build_parent_site_map(buckets: Dict[str, List[Dict[str, Any]]]) -> Dict[int, str]:
    parent_sites: Dict[int, str] = {}
    for key in ("dcim_devices", "virtualization_virtual_machines"):
        for obj in buckets.get(key, []):
            obj_id = obj.get("id")
            site = _flatten_value(obj.get("site"))
            if obj_id is not None and site:
                parent_sites[obj_id] = site
    return parent_sites


def _vlan_site(vlan: Dict[str, Any], site_names: Dict[str, str]) -> str:
    site = _flatten_value(vlan.get("site"))
    if site:
        return site
    # NetBox installs commonly scope VLANs by group only (e.g. "Adelaide VLAN Group").
    group = _flatten_value(vlan.get("group"))
    if group:
        candidate = group
        for suffix in (" VLAN Group", " Vlan Group", " VLANs", " VLAN"):
            if candidate.endswith(suffix):
                candidate = candidate[: -len(suffix)]
                break
        matched = site_names.get(candidate.strip().lower())
        if matched:
            return matched
    return ""


# ── CORE TABLE INGEST (Sites / IPAM / Inventory) ─────────────────────────

def _ingest_sites(buckets: Dict[str, List[Dict[str, Any]]]) -> int:
    records = []
    for site in buckets.get("dcim_sites", []):
        s_id = site.get("id")
        s_name = _flatten_value(site.get("name"))
        if s_id is None or not s_name:
            continue
        records.append({"id": s_id, "name": s_name, "slug": _flatten_value(site.get("slug"))})
    if not records:
        return 0
    return save_sites_batch(records, clear_first=True, source=BACKUP_SOURCE)


def _ingest_ipam(buckets: Dict[str, List[Dict[str, Any]]]) -> int:
    prefixes = buckets.get("ipam_prefixes", [])
    vlans = buckets.get("ipam_vlans", [])
    if not prefixes and not vlans:
        return 0

    site_names = {
        _flatten_value(s.get("name")).strip().lower(): _flatten_value(s.get("name"))
        for s in buckets.get("dcim_sites", [])
        if _flatten_value(s.get("name"))
    }
    site_ids = {
        _flatten_value(s.get("name")).strip().lower(): s.get("id")
        for s in buckets.get("dcim_sites", [])
        if _flatten_value(s.get("name"))
    }

    records: List[Dict[str, Any]] = []

    # VLAN ID (NetBox PK) -> prefixes assigned to that VLAN
    vlan_prefix_map: Dict[int, List[str]] = {}
    for pfx in prefixes:
        vlan_obj = pfx.get("vlan")
        prefix_str = _flatten_value(pfx.get("prefix"))
        if isinstance(vlan_obj, dict) and vlan_obj.get("id") is not None and prefix_str:
            vlan_prefix_map.setdefault(vlan_obj["id"], []).append(prefix_str)

    for pfx in prefixes:
        prefix_str = _flatten_value(pfx.get("prefix"))
        if not prefix_str:
            continue
        vlan_obj = pfx.get("vlan") if isinstance(pfx.get("vlan"), dict) else {}
        site = _flatten_value(pfx.get("scope")) if pfx.get("scope_type") == "dcim.site" else _flatten_value(pfx.get("site"))
        records.append({
            "prefix_or_subnet": prefix_str,
            "vlan_id": vlan_obj.get("vid"),
            "vlan_name": _flatten_value(vlan_obj.get("name")),
            "role": _flatten_value(pfx.get("role")),
            "site": site,
            "scope_id": pfx.get("scope_id"),
            "description": _flatten_value(pfx.get("description")),
            "record_type": "prefix",
        })

    for vlan in vlans:
        vid = vlan.get("vid")
        site = _vlan_site(vlan, site_names)
        scope_id = None
        if isinstance(vlan.get("site"), dict):
            scope_id = vlan["site"].get("id")
        elif site:
            scope_id = site_ids.get(site.strip().lower())

        base = {
            "vlan_id": vid,
            "vlan_name": _flatten_value(vlan.get("name")),
            "role": _flatten_value(vlan.get("role")),
            "site": site,
            "scope_id": scope_id,
            "description": _flatten_value(vlan.get("description")),
            "record_type": "vlan",
        }

        matched = vlan_prefix_map.get(vlan.get("id"), [])
        if matched:
            for prefix_str in matched:
                records.append({**base, "prefix_or_subnet": prefix_str})
        else:
            records.append({**base, "prefix_or_subnet": ""})

    if not records:
        return 0
    return save_ipam_records_batch(records, clear_first=True, source=BACKUP_SOURCE)


def _ingest_inventory(buckets: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    devices = buckets.get("dcim_devices", [])
    vms = buckets.get("virtualization_virtual_machines", [])
    if not devices and not vms:
        return {"device": 0, "hypervisor": 0, "vm": 0}

    records: List[Dict[str, Any]] = []

    for dev in devices:
        name = _flatten_value(dev.get("name"))
        if not name:
            continue
        model = _flatten_value(_dig(dev, "device_type.model"))
        role = _flatten_value(dev.get("role") or dev.get("device_role"))
        desc = _flatten_value(dev.get("description"))
        combined = f"{name} {role} {model} {desc}".lower()
        category = "hypervisor" if any(h in combined for h in HYPERVISOR_HINTS) else "device"
        records.append({
            "category": category,
            "name": name,
            "description": desc,
            "manufacturer": _flatten_value(_dig(dev, "device_type.manufacturer")),
            "model_or_role": model or role,
            "site": _flatten_value(dev.get("site")),
            "cluster": _flatten_value(dev.get("cluster")),
        })

    for vm in vms:
        name = _flatten_value(vm.get("name"))
        if not name:
            continue
        role = _flatten_value(vm.get("role"))
        records.append({
            "category": "vm",
            "name": name,
            "description": _flatten_value(vm.get("description")),
            "manufacturer": "Virtual Machine",
            "model_or_role": role or "VM",
            "site": _flatten_value(vm.get("site")),
            "cluster": _flatten_value(vm.get("cluster")),
        })

    if not records:
        return {"device": 0, "hypervisor": 0, "vm": 0}
    return save_records_batch(records, clear_first=True, source=BACKUP_SOURCE)


# ── FULL-FIDELITY BACKUP ROWS ───────────────────────────────────────────

def _ingest_backup_rows(
    buckets: Dict[str, List[Dict[str, Any]]],
    uploaded_at: str,
) -> Dict[str, int]:
    parent_sites = _build_parent_site_map(buckets)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM backup_records")

    counts: Dict[str, int] = {}
    for object_type, rows in buckets.items():
        label = _label_for(object_type)
        payload = []
        for obj in rows:
            site = _resolve_site(obj, parent_sites)
            name = _record_name(object_type, obj)
            summary = _summarize(object_type, obj, site)
            blob = " ".join([label, name, site, summary]).lower()
            payload.append((object_type, label, obj.get("id"), name, site, summary, blob, uploaded_at))
        if payload:
            cursor.executemany("""
                INSERT INTO backup_records
                    (object_type, object_label, object_id, name, site, summary, search_blob, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, payload)
            counts[object_type] = len(payload)

    conn.commit()
    conn.close()
    return counts


# ── PUBLIC API ──────────────────────────────────────────────────────────

def save_netbox_backup(file_bytes: Any, filename: str = "") -> Dict[str, Any]:
    """Ingest a NetBox master backup JSON file.

    Populates the Sites / IPAM / Inventory tables (replacing existing records)
    and stores a searchable row for every NetBox object in the backup.
    """
    init_backup_tables()

    payload = _load_payload(file_bytes)
    buckets = _bucket_payload(payload)
    uploaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    object_counts = _ingest_backup_rows(buckets, uploaded_at)
    total = sum(object_counts.values())

    sites = _ingest_sites(buckets)
    ipam = _ingest_ipam(buckets)
    inventory = _ingest_inventory(buckets)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO backup_metadata
            (id, filename, uploaded_at, record_count, object_counts, enabled)
        VALUES (1, ?, ?, ?, ?, 1)
    """, (filename or "NetBox_Backup.json", uploaded_at, total, json.dumps(object_counts)))
    conn.commit()
    conn.close()

    set_sync_metadata("netbox_backup", BACKUP_SOURCE)
    set_sync_metadata("ipam", BACKUP_SOURCE)
    set_sync_metadata("naming", BACKUP_SOURCE)

    return {
        "total": total,
        "object_counts": object_counts,
        "sites": sites,
        "ipam": ipam,
        "devices": inventory.get("device", 0) + inventory.get("hypervisor", 0),
        "vms": inventory.get("vm", 0),
        "uploaded_at": uploaded_at,
        "filename": filename,
    }


def get_backup_metadata() -> Dict[str, Any]:
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT filename, uploaded_at, record_count, object_counts, enabled
        FROM backup_metadata WHERE id = 1
    """)
    row = cursor.fetchone()
    conn.close()

    if not row:
        return {
            "filename": "", "uploaded_at": "Never", "record_count": 0,
            "object_counts": {}, "enabled": False, "loaded": False,
        }

    try:
        object_counts = json.loads(row[3] or "{}")
    except json.JSONDecodeError:
        object_counts = {}

    return {
        "filename": row[0] or "",
        "uploaded_at": row[1] or "Never",
        "record_count": int(row[2] or 0),
        "object_counts": object_counts,
        "enabled": bool(row[4]),
        "loaded": int(row[2] or 0) > 0,
    }


def set_backup_enabled(enabled: bool) -> None:
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE backup_metadata SET enabled = ? WHERE id = 1", (1 if enabled else 0,))
    conn.commit()
    conn.close()


def clear_backup_records() -> int:
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM backup_records")
    deleted = cursor.rowcount
    cursor.execute("DELETE FROM backup_metadata")
    cursor.execute("DELETE FROM sync_metadata WHERE module = 'netbox_backup'")
    conn.commit()
    conn.close()
    return deleted


def get_backup_object_counts() -> Dict[str, int]:
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT object_type, COUNT(*) FROM backup_records
        GROUP BY object_type ORDER BY COUNT(*) DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def is_backup_active() -> bool:
    meta = get_backup_metadata()
    return bool(meta["loaded"] and meta["enabled"])


def search_backup_records(
    required: List[str],
    optional: Optional[List[str]] = None,
    object_types: Optional[List[str]] = None,
    site: str = "",
    limit: int = 60,
) -> List[Dict[str, Any]]:
    """Search flattened backup rows.

    `required` terms must all appear in a row (used for specific identifiers such
    as hostnames, CIDRs and IPs). `optional` terms only influence ranking, so a
    natural-language question still returns its most relevant rows.
    """
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    required = [t.strip().lower() for t in (required or []) if len(t.strip()) >= 2]
    optional = [t.strip().lower() for t in (optional or []) if len(t.strip()) >= 3]
    optional = [t for t in optional if t not in required]

    clauses: List[str] = []
    params: List[Any] = []

    if object_types:
        clauses.append(f"object_type IN ({','.join('?' for _ in object_types)})")
        params.extend(object_types)

    if site:
        clauses.append("LOWER(site) LIKE ?")
        params.append(f"%{site.strip().lower()}%")

    for term in required:
        clauses.append("search_blob LIKE ?")
        params.append(f"%{term}%")

    if optional:
        score_sql = " + ".join(["(search_blob LIKE ?)"] * len(optional))
        params_score = [f"%{t}%" for t in optional]
        # Score params bind before the WHERE params in the SELECT ... WHERE order.
        params = params_score + params
        select = f"SELECT object_label, name, site, summary, ({score_sql}) AS score FROM backup_records"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        if not required:
            # Pure keyword question: only keep rows that matched at least one term.
            where = f"{where} AND score > 0" if where else "WHERE score > 0"
        order = "ORDER BY score DESC, object_type, name"
    else:
        select = "SELECT object_label, name, site, summary, 0 AS score FROM backup_records"
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "ORDER BY object_type, name"

    params.append(limit)
    cursor.execute(f"{select} {where} {order} LIMIT ?", params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_backup_records_by_type(
    object_type: str,
    site: str = "",
    limit: int = 50,
    keywords: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return rows for one object type, ranked by keyword relevance when given."""
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    terms = [t.strip().lower() for t in (keywords or []) if len(t.strip()) >= 3]

    clauses = ["object_type = ?"]
    where_params: List[Any] = [object_type]
    if site:
        clauses.append("LOWER(site) LIKE ?")
        where_params.append(f"%{site.strip().lower()}%")

    where = f"WHERE {' AND '.join(clauses)}"

    if terms:
        score_sql = " + ".join(["(search_blob LIKE ?)"] * len(terms))
        params = [f"%{t}%" for t in terms] + where_params + [limit]
        cursor.execute(
            f"SELECT object_label, name, site, summary, ({score_sql}) AS score "
            f"FROM backup_records {where} ORDER BY score DESC, name LIMIT ?",
            params,
        )
    else:
        cursor.execute(
            f"SELECT object_label, name, site, summary, 0 AS score "
            f"FROM backup_records {where} ORDER BY name LIMIT ?",
            where_params + [limit],
        )

    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_backup_records(object_type: str, site: str = "") -> int:
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if site:
        cursor.execute(
            "SELECT COUNT(*) FROM backup_records WHERE object_type = ? AND LOWER(site) LIKE ?",
            (object_type, f"%{site.strip().lower()}%"),
        )
    else:
        cursor.execute("SELECT COUNT(*) FROM backup_records WHERE object_type = ?", (object_type,))
    total = cursor.fetchone()[0]
    conn.close()
    return int(total)


def get_backup_site_names() -> List[str]:
    init_backup_tables()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT name FROM backup_records
        WHERE object_type = 'dcim_sites' AND name != '' ORDER BY name
    """)
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows]
