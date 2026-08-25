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

def save_csv_records(file_bytes, category: str) -> int:
    init_db()
    df = pd.read_csv(file_bytes)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM inventory_records WHERE category = ?", (category,))
    
    count = 0
    for _, row in df.iterrows():
        name = str(row.get("name", row.get("Name", ""))).strip()
        if not name or name.lower() == "nan":
            continue
        desc = str(row.get("description", row.get("Description", ""))).strip()
        if desc.lower() == "nan": desc = ""
        mfg = str(row.get("manufacturer", row.get("Manufacturer", ""))).strip()
        if mfg.lower() == "nan": mfg = ""
        model = str(row.get("device_type", row.get("Device Type", row.get("role", row.get("Role", ""))))).strip()
        if model.lower() == "nan": model = ""
        site = str(row.get("site", row.get("Site", ""))).strip()
        if site.lower() == "nan": site = ""
        cluster = str(row.get("cluster", row.get("Cluster", ""))).strip()
        if cluster.lower() == "nan": cluster = ""

        cursor.execute("""
            INSERT INTO inventory_records (category, name, description, manufacturer, model_or_role, site, cluster)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (category, name, desc, mfg, model, site, cluster))
        count += 1
        
    conn.commit()
    conn.close()
    return count

def get_records_by_category(category: str) -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM inventory_records WHERE category = ? ORDER BY id ASC", (category,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def clear_records_by_category(category: str) -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM inventory_records WHERE category = ?", (category,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted