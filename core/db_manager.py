import os
import sqlite3
import pandas as pd
from typing import List, Dict, Any

DB_PATH = "data/netbox_hub.db"

def init_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
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
    conn.commit()
    conn.close()

def save_universal_csv(file_bytes) -> Dict[str, int]:
    """Ingests a NetBox CSV export, auto-categorizing devices, ESXi hosts, and VMs."""
    init_db()
    df = pd.read_csv(file_bytes)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Wipe old inventory to prevent stale duplication
    cursor.execute("DELETE FROM inventory_records")
    
    cols = {str(c).lower().strip(): c for c in df.columns}
    name_col = cols.get("name", "Name")
    role_col = cols.get("role", cols.get("device role", cols.get("role name", "")))
    type_col = cols.get("device type", cols.get("type", cols.get("device_type", "")))
    mfg_col = cols.get("manufacturer", cols.get("make", ""))
    site_col = cols.get("site", cols.get("location", ""))
    desc_col = cols.get("description", cols.get("comments", ""))
    cluster_col = cols.get("cluster", "")

    counts = {"device": 0, "hypervisor": 0, "vm": 0}

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

        # Auto-classify based on name, role, hardware type, or columns
        combined_text = f"{name} {role} {dtype} {desc}".lower()
        if any(h in combined_text for h in ["esx", "hypervisor", "infhost", "vmhost", "esxi"]):
            cat = "hypervisor"
        elif any(v in combined_text for v in ["virtual machine", "vm", "vcenter", "guest", "app", "server", "srv", "db"]) or ("vcpus" in cols or "memory" in cols or "disk" in cols):
            cat = "vm"
        else:
            cat = "device"

        cursor.execute("""
            INSERT INTO inventory_records (category, name, description, manufacturer, model_or_role, site, cluster)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (cat, name, desc if desc.lower() != "nan" else "", mfg if mfg.lower() != "nan" else "", dtype or role, site if site.lower() != "nan" else "", cluster if cluster.lower() != "nan" else ""))
        
        counts[cat] += 1
        
    conn.commit()
    conn.close()
    return counts

def get_records_by_category(category: str) -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_records WHERE category = ? ORDER BY id ASC", (category,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_all_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_records")
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