#!/usr/bin/env python3
"""
Debug script to check owner groups data in the database.
"""

import sqlite3
from pathlib import Path

db_path = Path("/opt/netbox-hub/data/netbox_hub.db")

if not db_path.exists():
    print(f"❌ Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

print("=" * 80)
print("DATABASE TABLES WITH 'OWNER' OR 'GROUP' IN NAME")
print("=" * 80)

# Find all tables with 'owner' or 'group' in the name
cursor.execute("""
    SELECT name FROM sqlite_master 
    WHERE type='table' 
    AND (LOWER(name) LIKE '%owner%' OR LOWER(name) LIKE '%group%')
    ORDER BY name
""")

tables = cursor.fetchall()

if not tables:
    print("⚠️  No tables found with 'owner' or 'group' in the name")
else:
    for (table_name,) in tables:
        print(f"\n📋 Table: {table_name}")
        print("-" * 80)
        
        # Get table schema
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        col_names = [col[1] for col in columns]
        print(f"   Columns: {', '.join(col_names)}")
        
        # Get row count
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"   Row count: {count}")
        
        # Show first 5 rows if any exist
        if count > 0:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 5")
            rows = cursor.fetchall()
            print(f"\n   📊 Sample data (first {len(rows)} rows):")
            for i, row in enumerate(rows, 1):
                print(f"      Row {i}:")
                for col_name, value in zip(col_names, row):
                    if value is not None and str(value).strip():
                        print(f"         {col_name}: {value}")

print("\n" + "=" * 80)
print("SHARED BACKUP STATE CHECK")
print("=" * 80)

# Check if there's backup state data
cursor.execute("""
    SELECT name FROM sqlite_master 
    WHERE type='table' 
    AND name LIKE '%backup%'
    ORDER BY name
""")

backup_tables = cursor.fetchall()
if backup_tables:
    for (table_name,) in backup_tables:
        print(f"\n📦 {table_name}")
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"   Records: {count}")

conn.close()

print("\n" + "=" * 80)
print("✅ Diagnostic complete!")
print("=" * 80)
