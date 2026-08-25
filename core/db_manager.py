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
        CREATE TABLE IF NOT EXISTS imported_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            manufacturer TEXT,
            device_type TEXT,
            role TEXT,
            site TEXT,
            status TEXT,
            serial TEXT,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_devices_from_csv(file_bytes) -> int:
    init_db()
    try:
        df = pd.read_csv(file_bytes)
        # Normalize NetBox column names if available
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        count = 0
        for _, row in df.iterrows():
            name = str(row.get("Name", row.get("name", "Unknown")))
            mfg = str(row.get("Manufacturer", row.get("manufacturer", "Unknown")))
            dtype = str(row.get("Device Type", row.get("type", "Unknown")))
            role = str(row.get("Role", row.get("role", "Unknown")))
            site = str(row.get("Site", row.get("site", "Unknown")))
            status = str(row.get("Status", row.get("status", "Active")))
            serial = str(row.get("Serial number", row.get("serial", "")))

            cursor.execute("""
                INSERT INTO imported_devices (name, manufacturer, device_type, role, site, status, serial)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (name, mfg, dtype, role, site, status, serial))
            count += 1
            
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        raise ValueError(f"Failed to parse NetBox CSV: {str(e)}")

def get_imported_devices() -> List[Dict[str, Any]]:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM imported_devices ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]