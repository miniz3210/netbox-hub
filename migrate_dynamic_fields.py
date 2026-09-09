"""
Migration Script for Dynamic Field Registry

Migrates existing NetBox Hub installations to use the new dynamic field system.
Creates the netbox_schema table and optionally seeds it with default configurations.
"""

import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from core.backup_manager import init_backup_tables, get_backup_metadata
from core.field_registry import FieldRegistry
from core.db_manager_wrapper import DatabaseManager


def check_migration_needed() -> bool:
    """Check if migration is needed."""
    from core.db_manager import DB_PATH
    
    if not Path(DB_PATH).exists():
        print("No existing database found. Migration not needed.")
        return False
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if netbox_schema table exists
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='netbox_schema'
    """)
    
    exists = cursor.fetchone() is not None
    conn.close()
    
    return not exists


def run_migration():
    """Execute the migration."""
    print("=" * 80)
    print("NetBox Hub Dynamic Field Registry Migration")
    print("=" * 80)
    print()
    
    # Step 1: Check if migration is needed
    print("[1/4] Checking migration status...")
    if not check_migration_needed():
        print("✓ Migration already completed or not needed.")
        return
    
    print("✓ Migration needed. Proceeding...")
    print()
    
    # Step 2: Create new tables
    print("[2/4] Creating netbox_schema table...")
    try:
        init_backup_tables()
        print("✓ Table created successfully.")
    except Exception as e:
        print(f"✗ Failed to create table: {e}")
        sys.exit(1)
    
    print()
    
    # Step 3: Check for existing backup data
    print("[3/4] Checking for existing NetBox backup data...")
    try:
        metadata = get_backup_metadata()
        
        if metadata.get("loaded"):
            print(f"✓ Found existing backup: {metadata.get('filename')}")
            print(f"  Uploaded: {metadata.get('uploaded_at')}")
            print(f"  Records: {metadata.get('record_count')}")
            print()
            print("  To auto-discover schema from this backup, re-upload it through the UI")
            print("  or use the backup manager API to trigger schema discovery.")
        else:
            print("  No existing backup found.")
            print("  Schema discovery will run automatically when you upload a NetBox backup.")
    except Exception as e:
        print(f"  Warning: Could not check backup metadata: {e}")
    
    print()
    
    # Step 4: Seed default configurations (optional)
    print("[4/4] Migration summary...")
    print("✓ Database schema updated successfully.")
    print()
    print("Next steps:")
    print("  1. Upload or re-upload a NetBox backup JSON to auto-discover schema")
    print("  2. Use 'python field_manager.py list' to view discovered object types")
    print("  3. Use 'python field_manager.py show <object_type>' to view fields")
    print("  4. Customize display fields with 'python field_manager.py update'")
    print()
    print("=" * 80)
    print("Migration completed successfully!")
    print("=" * 80)


def rollback_migration():
    """Rollback the migration (for testing/development)."""
    print("=" * 80)
    print("Rolling back Dynamic Field Registry Migration")
    print("=" * 80)
    print()
    
    from core.db_manager import DB_PATH
    
    if not Path(DB_PATH).exists():
        print("No database found. Nothing to rollback.")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        print("Dropping netbox_schema table...")
        cursor.execute("DROP TABLE IF EXISTS netbox_schema")
        conn.commit()
        print("✓ Rollback completed.")
    except Exception as e:
        print(f"✗ Rollback failed: {e}")
        sys.exit(1)
    finally:
        conn.close()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Migrate NetBox Hub to use dynamic field registry system"
    )
    parser.add_argument(
        '--rollback',
        action='store_true',
        help='Rollback the migration (removes netbox_schema table)'
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='Check if migration is needed without running it'
    )
    
    args = parser.parse_args()
    
    if args.rollback:
        confirm = input("Are you sure you want to rollback? This will remove all field configurations. (yes/no): ")
        if confirm.lower() == 'yes':
            rollback_migration()
        else:
            print("Rollback cancelled.")
        return
    
    if args.check:
        if check_migration_needed():
            print("Migration is needed.")
            sys.exit(1)
        else:
            print("Migration not needed.")
            sys.exit(0)
        return
    
    run_migration()


if __name__ == '__main__':
    main()
