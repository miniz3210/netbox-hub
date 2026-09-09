#!/usr/bin/env python3
"""
Direct database check for azure_csv_uploads table
Run this to verify if the table exists and has data
"""

import sys
sys.path.insert(0, '/opt/netbox-hub')

from core.db_manager import DB_PATH
import sqlite3

print("=" * 60)
print("Azure CSV Upload Database Check")
print("=" * 60)
print(f"Database path: {DB_PATH}")
print()

try:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if table exists
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='azure_csv_uploads'
    """)
    
    table_exists = cursor.fetchone()
    
    if table_exists:
        print("✅ azure_csv_uploads table EXISTS")
        
        # Check table structure
        cursor.execute("PRAGMA table_info(azure_csv_uploads)")
        columns = cursor.fetchall()
        print("\nTable structure:")
        for col in columns:
            print(f"  - Column: {col[1]}, Type: {col[2]}")
        
        # Check if there's any data
        cursor.execute("SELECT COUNT(*) FROM azure_csv_uploads")
        count = cursor.fetchone()[0]
        print(f"\nTotal rows in table: {count}")
        
        if count > 0:
            print("\nSaved uploads:")
            cursor.execute("SELECT id, filename, row_count, uploaded_at, LENGTH(csv_data) as data_size FROM azure_csv_uploads")
            for row in cursor.fetchall():
                print(f"  ID: {row[0]}")
                print(f"  Filename: {row[1]}")
                print(f"  Row count: {row[2]}")
                print(f"  Uploaded: {row[3]}")
                print(f"  Data size: {row[4]} bytes")
        else:
            print("\n⚠️  Table exists but is EMPTY")
            print("This means:")
            print("  - Table was created successfully")
            print("  - But save_azure_csv_upload() is not being called")
            print("  - Or it's failing silently")
    else:
        print("❌ azure_csv_uploads table DOES NOT EXIST")
        print("\nThis means init_db() hasn't run or table creation failed")
        print("\nAvailable tables:")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for table in cursor.fetchall():
            print(f"  - {table[0]}")
    
    conn.close()
    print("\n" + "=" * 60)
    
except Exception as e:
    print(f"❌ Error accessing database: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
