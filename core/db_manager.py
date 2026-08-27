import os
import sqlite3
import pandas as pd
from typing import List, Dict, Any, Optional

DB_PATH = "data/netbox_hub.db"

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Sites / Scope Table (Stores NetBox Site Name <-> Scope ID mapping)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sites_records (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            slug TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 2. Shared Inventory Records (Devices, Hypervisors, VMs)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            name TEXT,
            description TEXT,
            manufacturer TEXT,
            model_or_role TEXT,
            site TEXT,
            cluster TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. Dedicated IPAM / Prefix Records (Used for overlap/collision checks)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ipam_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prefix_or_subnet TEXT,
            vlan_id INTEGER,
            vlan_name TEXT,
            role TEXT,
            site TEXT,
            scope_id INTEGER,
            description TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# ── SITE / SCOPE ID LOOKUP (Excel E1 XLOOKUP equivalent) ─────────────────

def save_sites_batch(sites: List[Dict[str, Any]], clear_first: bool = False) -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if clear_first:
        cursor.execute("DELETE FROM sites_records")
    
    count = 0
    for s in sites:
        site_id = s.get("id")
        name = str(s.get("name") or "").strip()
        slug = str(s.get("slug") or "").strip()
        if name:
            cursor.execute("""
                INSERT OR REPLACE INTO sites_records (id, name, slug)
                VALUES (?, ?, ?)
            """, (int(site_id) if str(site_id).isdigit() else None, name, slug))
            count += 1
            
    conn.commit()
    conn.close()
    return count

def lookup_scope_id(site_name: str) -> Optional[int]:
    """Looks up the Scope integer ID matching the site name or slug."""
    if not site_name:
        return None
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    clean = site_name.strip()
    
    cursor.execute("SELECT id FROM sites_records WHERE LOWER(name) = LOWER(?) LIMIT 1", (clean,))
    row = cursor.fetchone()
    if not row or row[0] is None:
        cursor.execute("SELECT id FROM sites_records WHERE LOWER(slug) = LOWER(?) LIMIT 1", (clean,))
        row = cursor.fetchone()
    if not row or row[0] is None:
        cursor.execute("SELECT id FROM sites_records WHERE LOWER(name) LIKE LOWER(?) LIMIT 1", (f"%{clean}%",))
        row = cursor.fetchone()
        
    conn.close()
    return row[0] if (row and row[0] is not None) else None

def lookup_site_supernet_from_db(site_name: str) -> Optional[str]:
    """Finds the top-level container prefix (/21, /23, etc.) for a site from stored IPAM records."""
    if not site_name:
        return None
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    clean = site_name.strip().lower()
    
    cursor.execute("""
        SELECT prefix_or_subnet, description, role FROM ipam_records 
        WHERE LOWER(site) LIKE ? OR LOWER(description) LIKE ?
    """, (f"%{clean}%", f"%{clean}%"))
    rows = cursor.fetchall()
    conn.close()

    candidates = []
    for r in rows:
        p_str = r[0]
        desc = (r[1] or "").lower()
        role = (r[2] or "").lower()
        if p_str and "/" in p_str:
            try:
                cidr = int(p_str.split("/")[1])
                is_supernet_role = "site subnet" in role or "site subnet" in desc
                candidates.append((cidr, is_supernet_role, p_str))
            except ValueError:
                pass

    if candidates:
        candidates.sort(key=lambda x: (not x[1], x[0]))
        return candidates[0][2]
    return None

def get_all_sites() -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sites_records ORDER BY name ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── INVENTORY RECORDS (Naming Tab) ──────────────────────────────────────

def save_records_batch(records: List[Dict[str, Any]], clear_first: bool = True) -> Dict[str, int]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if clear_first:
        cursor.execute("DELETE FROM inventory_records")

    counts = {"device": 0, "hypervisor": 0, "vm": 0}
    for r in records:
        cat = r.get("category", "device")
        cursor.execute("""
            INSERT INTO inventory_records (category, name, description, manufacturer, model_or_role, site, cluster)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            cat,
            r.get("name", "").strip(),
            r.get("description", "").strip(),
            r.get("manufacturer", "").strip(),
            r.get("model_or_role", "").strip(),
            r.get("site", "").strip(),
            r.get("cluster", "").strip()
        ))
        counts[cat] = counts.get(cat, 0) + 1

    conn.commit()
    conn.close()
    return counts

def save_universal_csv(file_bytes) -> Dict[str, int]:
    df = pd.read_csv(file_bytes)
    cols = {str(c).lower().strip(): c for c in df.columns}
    name_col = cols.get("name", "Name")
    role_col = cols.get("role", cols.get("device role", cols.get("role name", "")))
    type_col = cols.get("device type", cols.get("type", cols.get("device_type", "")))
    mfg_col = cols.get("manufacturer", cols.get("make", ""))
    site_col = cols.get("site", cols.get("location", ""))
    desc_col = cols.get("description", cols.get("comments", ""))
    cluster_col = cols.get("cluster", "")

    records = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if not name or name.lower() == "nan":
            continue

        role = str(row.get(role_col, "")).strip() if role_col else ""
        dtype = str(row.get(type_col, "")).strip() if type_col else ""
        mfg = str(row.get(mfg_col, "")).strip() if mfg_col else ""
        site = str(row.get(site_col, "")).strip() if site_col else ""
        desc = str(row.get(desc_col, "")).strip() if desc_col else ""
        cluster = str(row.get(cluster_col, "")).strip() if cluster_col else ""

        for k in [role, dtype, mfg, site, desc, cluster]:
            if k.lower() == "nan": 
                k = ""

        combined = f"{name} {role} {dtype} {desc}".lower()
        if any(h in combined for h in ["esx", "hypervisor", "infhost", "vmhost", "esxi"]):
            cat = "hypervisor"
        elif any(v in combined for v in ["virtual machine", "vm", "vcenter", "guest", "app", "server", "srv", "db"]) or ("vcpus" in cols or "memory" in cols or "disk" in cols):
            cat = "vm"
        else:
            cat = "device"

        records.append({
            "category": cat,
            "name": name,
            "description": desc if desc.lower() != "nan" else "",
            "manufacturer": mfg if mfg.lower() != "nan" else "",
            "model_or_role": dtype or role,
            "site": site if site.lower() != "nan" else "",
            "cluster": cluster if cluster.lower() != "nan" else ""
        })

    return save_records_batch(records, clear_first=True)

def get_records_by_category(category: str, site_filter: str = "") -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    clean_filter = site_filter.strip().lower()
    if clean_filter:
        pattern = f"%{clean_filter}%"
        cursor.execute("""
            SELECT * FROM inventory_records 
            WHERE category = ? AND (LOWER(site) LIKE ? OR LOWER(name) LIKE ?)
            ORDER BY id ASC
        """, (category, pattern, pattern))
        rows = cursor.fetchall()
        if rows:
            conn.close()
            return [dict(r) for r in rows]

    cursor.execute("SELECT * FROM inventory_records WHERE category = ? ORDER BY id ASC", (category,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# ── DEDICATED IPAM & PREFIXES ───────────────────────────────────────────

def save_ipam_records_batch(records: List[Dict[str, Any]], clear_first: bool = True) -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if clear_first:
        cursor.execute("DELETE FROM ipam_records")

    count = 0
    for r in records:
        cursor.execute("""
            INSERT INTO ipam_records (prefix_or_subnet, vlan_id, vlan_name, role, site, scope_id, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(r.get("prefix_or_subnet") or r.get("prefix") or r.get("subnet") or r.get("address") or "").strip(),
            int(r.get("vlan_id") or r.get("vid") or 0) if str(r.get("vlan_id") or r.get("vid") or "").isdigit() else None,
            str(r.get("vlan_name") or r.get("name") or "").strip(),
            str(r.get("role") or "").strip(),
            str(r.get("site") or "").strip(),
            int(r.get("scope_id")) if str(r.get("scope_id") or "").isdigit() else None,
            str(r.get("description") or r.get("desc") or "").strip()
        ))
        count += 1

    conn.commit()
    conn.close()
    return count

def get_existing_prefix_strings() -> List[str]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT prefix_or_subnet FROM ipam_records WHERE prefix_or_subnet != ''")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows if r[0] and "/" in r[0]]

def get_all_ipam_records() -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ipam_records ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_all_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_records")
    cursor.execute("DELETE FROM sites_records")
    cursor.execute("DELETE FROM ipam_records")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def clear_ipam_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ipam_records")
    cursor.execute("DELETE FROM sites_records")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def get_total_record_count() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM inventory_records")
    total = cursor.fetchone()[0]
    conn.close()
    return total

def get_total_ipam_count() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ipam_records")
    total = cursor.fetchone()[0]
    conn.close()
    return total