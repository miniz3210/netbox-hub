import os
import re
import sqlite3
import pandas as pd
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

DB_PATH = "data/netbox_hub.db"

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Metadata Table (Tracks Sync Source & Last Sync Timestamp)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_metadata (
            module TEXT PRIMARY KEY,
            source TEXT,
            updated_at TEXT
        )
    """)

    # Sites / Scope Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sites_records (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            slug TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Inventory Records (Devices, Hypervisors, VMs)
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

    # IPAM / Prefix Records
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
            record_type TEXT DEFAULT 'prefix',
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Column migration check for existing DBs
    cursor.execute("PRAGMA table_info(ipam_records)")
    columns = [row[1] for row in cursor.fetchall()]
    if "record_type" not in columns:
        cursor.execute("ALTER TABLE ipam_records ADD COLUMN record_type TEXT DEFAULT 'prefix'")

    conn.commit()
    conn.close()

# ── METADATA TRACKING ───────────────────────────────────────────────────

def set_sync_metadata(module: str, source: str):
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    cursor.execute("""
        INSERT OR REPLACE INTO sync_metadata (module, source, updated_at)
        VALUES (?, ?, ?)
    """, (module, source, now_str))
    conn.commit()
    conn.close()

def get_sync_metadata(module: str) -> Dict[str, str]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT source, updated_at FROM sync_metadata WHERE module = ?", (module,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"source": row[0], "updated_at": row[1]}
    return {"source": "None", "updated_at": "Never"}

# ── SMART SITE / SCOPE ID LOOKUP ─────────────────────────────────────────

def save_sites_batch(sites: List[Dict[str, Any]], clear_first: bool = False, source: str = "Agent (PowerShell)") -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if clear_first:
        cursor.execute("DELETE FROM sites_records")
    
    count = 0
    for s in sites:
        site_id = s.get("id") or s.get("ID")
        name = str(s.get("name") or s.get("Name") or "").strip()
        slug = str(s.get("slug") or s.get("Slug") or "").strip()
        if name and name.lower() != "nan":
            cursor.execute("""
                INSERT OR REPLACE INTO sites_records (id, name, slug)
                VALUES (?, ?, ?)
            """, (int(site_id) if str(site_id).isdigit() else None, name, slug))
            count += 1
            
    conn.commit()
    conn.close()
    set_sync_metadata("netbox_sites", source)
    return count

def lookup_scope_id(site_name: str) -> Optional[int]:
    """Smart lookup for Scope ID prioritizing exact and whole-word matches over substrings."""
    if not site_name:
        return None
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    q = site_name.strip().lower()
    
    cursor.execute("SELECT id, name, slug FROM sites_records")
    all_sites = cursor.fetchall()
    conn.close()

    if not all_sites:
        return None

    # 1. Exact match on name or slug
    for sid, sname, sslug in all_sites:
        if (sname and sname.lower() == q) or (sslug and sslug.lower() == q):
            return sid

    # 2. Exact word boundary match
    word_pattern = re.compile(rf'\b{re.escape(q)}\b', re.IGNORECASE)
    word_matches = []
    for sid, sname, sslug in all_sites:
        sname_str = sname or ""
        sslug_str = (sslug or "").replace("-", " ")
        if word_pattern.search(sname_str) or word_pattern.search(sslug_str):
            is_cloud = any(c in sname_str.lower() or c in sslug_str.lower() for c in ["azure", "aws", "gcp", "cloud"])
            word_matches.append((is_cloud, len(sname_str), sid))

    if word_matches:
        word_matches.sort(key=lambda x: (x[0], x[1]))
        return word_matches[0][2]

    # 3. Starts-with match
    starts_matches = []
    for sid, sname, sslug in all_sites:
        sname_str = sname or ""
        sslug_str = sslug or ""
        if sname_str.lower().startswith(q) or sslug_str.lower().startswith(q):
            is_cloud = any(c in sname_str.lower() or c in sslug_str.lower() for c in ["azure", "aws", "gcp", "cloud"])
            starts_matches.append((is_cloud, len(sname_str), sid))

    if starts_matches:
        starts_matches.sort(key=lambda x: (x[0], x[1]))
        return starts_matches[0][2]

    # 4. Substring match (for query length >= 4)
    if len(q) >= 4:
        sub_matches = []
        for sid, sname, sslug in all_sites:
            sname_str = sname or ""
            sslug_str = sslug or ""
            if q in sname_str.lower() or q in sslug_str.lower():
                is_cloud = any(c in sname_str.lower() or c in sslug_str.lower() for c in ["azure", "aws", "gcp", "cloud"])
                sub_matches.append((is_cloud, len(sname_str), sid))
        if sub_matches:
            sub_matches.sort(key=lambda x: (x[0], x[1]))
            return sub_matches[0][2]

    return None

def lookup_site_supernet_from_db(site_name: str) -> Optional[str]:
    if not site_name:
        return None
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    clean = site_name.strip().lower()
    # Also search for site name as a word to avoid partial matches like "York" in "New York"
    pattern_exact = clean
    pattern_like = f"%{clean}%"
    
    # We want to find the largest prefix (lowest CIDR number) that is associated with this site.
    # We search in site, description, role, and vlan_name.
    cursor.execute("""
        SELECT prefix_or_subnet, description, role, site, vlan_name FROM ipam_records 
        WHERE (LOWER(site) = ? OR LOWER(site) LIKE ? OR LOWER(description) LIKE ? OR LOWER(role) LIKE ? OR LOWER(vlan_name) LIKE ?)
        AND prefix_or_subnet LIKE '%/%'
    """, (pattern_exact, pattern_like, pattern_like, pattern_like, pattern_like))
    rows = cursor.fetchall()
    conn.close()

    candidates = []
    for r in rows:
        p_str = r[0]
        desc = (r[1] or "").lower()
        role = (r[2] or "").lower()
        site_val = (r[3] or "").lower()
        vname = (r[4] or "").lower()
        
        if p_str and "/" in p_str:
            try:
                prefix_parts = p_str.split("/")
                mask = int(prefix_parts[1])
                
                # Scoring for relevance
                score = 0
                if clean == site_val: score += 100
                if "site subnet" in desc or "site subnet" in role: score += 50
                if "supernet" in desc or "supernet" in role: score += 40
                if "container" in desc or "container" in role: score += 30
                if clean in site_val: score += 20
                if clean in desc: score += 10
                
                candidates.append({
                    "prefix": p_str,
                    "mask": mask,
                    "score": score
                })
            except (ValueError, IndexError):
                pass

    if candidates:
        # Sort by score (descending) then by mask (ascending, so /16 comes before /24)
        candidates.sort(key=lambda x: (-x["score"], x["mask"]))
        return candidates[0]["prefix"]
    return None

# ── INVENTORY RECORDS ───────────────────────────────────────────────────

def save_records_batch(records: List[Dict[str, Any]], clear_first: bool = False, source: str = "Agent (PowerShell)") -> Dict[str, int]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if clear_first:
        cursor.execute("DELETE FROM inventory_records")

    counts = {"device": 0, "hypervisor": 0, "vm": 0}
    has_devices = False
    has_vms = False
    
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
        
        if cat in ("device", "hypervisor"):
            has_devices = True
        elif cat == "vm":
            has_vms = True

    conn.commit()
    conn.close()
    
    # Set metadata based on what was imported
    if has_devices:
        set_sync_metadata("netbox_devices", source)
    if has_vms:
        set_sync_metadata("netbox_virtual_machines", source)
    
    return counts

def save_universal_csv(file_bytes, filename: str = "", clear_first: bool = False) -> Dict[str, int]:
    df = pd.read_csv(file_bytes)
    cols = {str(c).lower().strip(): c for c in df.columns}

    if "vid" in cols or "q-in-q role" in cols or "q-in-q svlan" in cols or "prefixes" in cols:
        raise ValueError("Invalid file uploaded to Naming. This is a NetBox VLANs export (`netbox_VLANs.csv`). Please upload `netbox_devices.csv` or `netbox_virtual machines.csv`.")

    if "asns" in cols or "facility" in cols or "time zone" in cols:
        raise ValueError("Invalid file uploaded to Naming. This is a NetBox Sites export (`netbox_sites.csv`). Please upload `netbox_devices.csv` or `netbox_virtual machines.csv`.")

    name_col = cols.get("name")
    if not name_col:
        raise ValueError("Invalid CSV: missing 'Name' column.")

    fname_lower = filename.lower()
    is_vm_export = False
    if "vcpus" in cols or "memory" in cols or "disk" in cols:
        is_vm_export = True
    elif "virtual" in fname_lower or "vm" in fname_lower:
        is_vm_export = True
    elif "device type" not in cols and "rack" not in cols and "serial" not in cols and "cluster" in cols:
        is_vm_export = True

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

        if is_vm_export:
            cat = "vm"
        else:
            combined = f"{name} {role} {dtype} {desc}".lower()
            if any(h in combined for h in ["esx", "hypervisor", "infhost", "vmhost", "esxi"]):
                cat = "hypervisor"
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

    return save_records_batch(records, clear_first=clear_first, source="Manual CSV Upload")

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

def save_ipam_records_batch(records: List[Dict[str, Any]], clear_first: bool = False, source: str = "Agent (PowerShell)") -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if clear_first:
        cursor.execute("DELETE FROM ipam_records")

    count = 0
    has_vlans = False
    has_prefixes = False
    
    for r in records:
        raw_prefix = str(r.get("prefix_or_subnet") or r.get("prefix") or r.get("subnet") or r.get("address") or r.get("Prefixes") or "").strip()
        if not raw_prefix or raw_prefix.lower() == "nan":
            continue
        
        cidrs = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b', raw_prefix)
        if not cidrs and "/" in raw_prefix:
            cidrs = [raw_prefix]

        v_id = int(r.get("vlan_id") or r.get("vid") or r.get("VID") or 0) if str(r.get("vlan_id") or r.get("vid") or r.get("VID") or "").isdigit() else None
        rec_type = str(r.get("record_type") or ("vlan" if v_id or r.get("vlan_name") else "prefix")).strip().lower()
        
        if rec_type == "vlan":
            has_vlans = True
        else:
            has_prefixes = True

        for cidr in cidrs:
            cursor.execute("""
                INSERT INTO ipam_records (prefix_or_subnet, vlan_id, vlan_name, role, site, scope_id, description, record_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cidr,
                v_id,
                str(r.get("vlan_name") or r.get("name") or r.get("Name") or "").strip(),
                str(r.get("role") or r.get("Role") or "").strip(),
                str(r.get("site") or r.get("Site") or "").strip(),
                int(r.get("scope_id")) if str(r.get("scope_id") or "").isdigit() else None,
                str(r.get("description") or r.get("desc") or r.get("Description") or "").strip(),
                rec_type
            ))
            count += 1

    conn.commit()
    conn.close()
    
    # Set metadata based on what was imported
    if has_vlans:
        set_sync_metadata("netbox_VLANs", source)
    if has_prefixes:
        set_sync_metadata("netbox_prefixes", source)
    
    return count

def get_existing_prefix_strings() -> List[str]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT prefix_or_subnet FROM ipam_records WHERE prefix_or_subnet != ''")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows if r[0] and "/" in r[0]]

def clear_device_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_records WHERE category IN ('device', 'hypervisor')")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def clear_vm_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_records WHERE category = 'vm'")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def clear_inventory_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_records")
    cursor.execute("DELETE FROM sync_metadata WHERE module = 'naming'")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def clear_sites_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sites_records")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def clear_vlans_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ipam_records WHERE record_type = 'vlan'")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted

def clear_prefixes_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ipam_records WHERE record_type = 'prefix'")
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
    cursor.execute("DELETE FROM sync_metadata WHERE module = 'ipam'")
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

def get_total_vlans_count() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ipam_records WHERE record_type = 'vlan'")
    total = cursor.fetchone()[0]
    conn.close()
    return total

def get_total_prefixes_count() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ipam_records WHERE record_type = 'prefix'")
    total = cursor.fetchone()[0]
    conn.close()
    return total

def get_total_sites_count() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sites_records")
    total = cursor.fetchone()[0]
    conn.close()
    return total

def get_file_sync_metadata() -> Dict[str, str]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sites_records")
    sites = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM ipam_records")
    ipam = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM inventory_records")
    inventory = cursor.fetchone()[0]
    conn.close()
    return {
        "sites_records": str(sites) if sites > 0 else "Never",
        "ipam_records": str(ipam) if ipam > 0 else "Never",
        "inventory_records": str(inventory) if inventory > 0 else "Never",
    }


def get_max_scope_id() -> Optional[int]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(id) FROM sites_records")
    row = cursor.fetchone()
    conn.close()
    return int(row[0]) if row and row[0] is not None else None

def get_site_summary() -> str:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Check if 'description' column exists (it does not exist in sites_records)
    cursor.execute("SELECT name FROM sites_records")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return "No site data ingested."
    return "\n".join([f"- {r[0]}" for r in rows])

def get_ipam_records_by_site(site_name: str) -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ipam_records WHERE site = ? OR site LIKE ?", (site_name, f"%{site_name}%"))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def lookup_vlan_description_from_db(role_name: str) -> Optional[str]:
    """Look up standard VLAN description from ingested database based on Role name."""
    if not role_name:
        return None
    clean_role = role_name.strip()
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            # 1. Exact match on role
            cursor.execute(
                "SELECT description FROM ipam_records WHERE LOWER(role) = LOWER(?) AND description != '' LIMIT 1",
                (clean_role,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]

            # 2. Match on VLAN name if role was stored under vlan_name
            cursor.execute(
                "SELECT description FROM ipam_records WHERE LOWER(vlan_name) = LOWER(?) AND description != '' LIMIT 1",
                (clean_role,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return None
def get_all_site_names() -> List[str]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sites_records")
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]

def get_full_site_inventory_summary(site_name: str) -> str:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    clean = site_name.strip().lower()
    
    # Get IPAM
    cursor.execute("SELECT prefix_or_subnet, vlan_name, role FROM ipam_records WHERE LOWER(site) = ?", (clean,))
    ipam_rows = cursor.fetchall()
    
    # Get Inventory
    cursor.execute("SELECT name, category, model_or_role FROM inventory_records WHERE LOWER(site) = ?", (clean,))
    inv_rows = cursor.fetchall()
    
    conn.close()
    
    summary = [f"### Inventory for {site_name}:"]
    
    summary.append("\n**IP Prefixes/VLANs:**")
    if not ipam_rows: summary.append("- None")
    for r in ipam_rows:
        summary.append(f"- {r['prefix_or_subnet']} | {r['vlan_name']} | {r['role']}")
        
    summary.append("\n**Devices/VMs:**")
    if not inv_rows: summary.append("- None")
    for r in inv_rows:
        summary.append(f"- {r['name']} ({r['category']}) | {r['model_or_role']}")
        
    return "\n".join(summary)
