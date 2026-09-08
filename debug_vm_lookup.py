"""
Debug utility to inspect VM data in the database
Usage: Run this from the Streamlit app or Python shell to see actual data structure
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("data/netbox_hub.db")

def debug_vm_data(vm_name: str = "ANZJDE001"):
    """Show exactly what data exists for a VM in the database"""
    
    print(f"\n{'='*80}")
    print(f"DEBUG: Looking up VM '{vm_name}'")
    print(f"{'='*80}\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check backup_records
    print("1. BACKUP_RECORDS TABLE:")
    print("-" * 80)
    cursor.execute("""
        SELECT object_type, name, site, summary, search_blob
        FROM backup_records
        WHERE LOWER(name) = LOWER(?) 
        AND object_type = 'virtualization_virtual_machines'
        LIMIT 1
    """, (vm_name,))
    
    row = cursor.fetchone()
    if row:
        object_type, name, site, summary, search_blob = row
        print(f"Object Type: {object_type}")
        print(f"Name: {name}")
        print(f"Site Column: {site}")
        print(f"\nSummary:")
        print(f"  {summary}")
        print(f"\nSearch Blob (first 500 chars):")
        print(f"  {search_blob[:500] if search_blob else 'N/A'}")
        
        # Parse the summary
        print(f"\n2. PARSED SUMMARY FIELDS:")
        print("-" * 80)
        if summary:
            for field_pair in summary.split('|'):
                field_pair = field_pair.strip()
                if ':' in field_pair:
                    key, value = field_pair.split(':', 1)
                    print(f"  {key.strip():20} -> {value.strip()}")
                elif '=' in field_pair:
                    key, value = field_pair.split('=', 1)
                    print(f"  {key.strip():20} = {value.strip()}")
    else:
        print(f"  ❌ VM '{vm_name}' NOT FOUND in backup_records")
    
    # Check inventory_records
    print(f"\n3. INVENTORY_RECORDS TABLE:")
    print("-" * 80)
    cursor.execute("""
        SELECT name, category, description, manufacturer, model_or_role, site, cluster
        FROM inventory_records
        WHERE LOWER(name) = LOWER(?) AND category = 'vm'
    """, (vm_name,))
    
    row = cursor.fetchone()
    if row:
        name, category, desc, mfg, role, site, cluster = row
        print(f"  Name: {name}")
        print(f"  Category: {category}")
        print(f"  Role (model_or_role): {role}")
        print(f"  Site: {site}")
        print(f"  Cluster: {cluster}")
        print(f"  Description: {desc}")
        print(f"  Manufacturer: {mfg}")
    else:
        print(f"  ❌ VM '{vm_name}' NOT FOUND in inventory_records")
    
    # Check backup_choice_values for custom fields
    print(f"\n4. AVAILABLE CUSTOM FIELD CHOICE VALUES:")
    print("-" * 80)
    cursor.execute("""
        SELECT DISTINCT choice_set, field_name, COUNT(*) as count
        FROM backup_choice_values
        GROUP BY choice_set, field_name
        ORDER BY choice_set
    """)
    
    for row in cursor.fetchall():
        choice_set, field_name, count = row
        print(f"  {choice_set:30} | Field: {field_name:20} | {count} values")
    
    conn.close()
    
    print(f"\n{'='*80}\n")

if __name__ == "__main__":
    import sys
    vm_name = sys.argv[1] if len(sys.argv) > 1 else "ANZJDE001"
    debug_vm_data(vm_name)
