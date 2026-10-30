#!/usr/bin/env python3
"""
Database diagnostic and repair tool for NetBox Hub.

Checks database integrity, record counts, and can re-ingest from compressed
backup if structured tables are empty.
"""
import sqlite3
import gzip
import json
import sys

DB_PATH = "data/netbox_hub.db"


def check_database():
    """Check database integrity and record counts."""
    print("=" * 60)
    print("NETBOX HUB DATABASE DIAGNOSTIC")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Integrity check
    print("\n[1] Integrity Check:")
    cursor.execute("PRAGMA integrity_check")
    integrity = cursor.fetchone()[0]
    print(f"  Result: {integrity}")

    # Table counts
    print("\n[2] Record Counts:")
    tables = [
        "sites_records",
        "ipam_records",
        "inventory_records",
        "backup_records",
        "interfaces",
        "cables",
        "backup_metadata",
    ]
    counts = {}
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            counts[table] = count
            status = "OK" if count > 0 else "EMPTY"
            print(f"  {table:25s}: {count:>6,} [{status}]")
        except Exception as e:
            print(f"  {table:25s}: ERROR - {e}")
            counts[table] = -1

    # Backup metadata
    print("\n[3] Backup Metadata:")
    try:
        cursor.execute("SELECT filename, uploaded_at, record_count FROM backup_metadata WHERE id = 1")
        row = cursor.fetchone()
        if row:
            print(f"  Filename: {row[0]}")
            print(f"  Uploaded: {row[1]}")
            print(f"  Records: {row[2]:,}")
        else:
            print("  No backup metadata found")
    except Exception as e:
        print(f"  ERROR: {e}")

    # Compressed backup check
    print("\n[4] Compressed Backup:")
    try:
        cursor.execute("SELECT backup_json_compressed FROM backup_metadata WHERE id = 1")
        row = cursor.fetchone()
        if row and row[0]:
            try:
                decompressed = gzip.decompress(row[0])
                data = json.loads(decompressed)
                print(f"  Compressed: {len(row[0])/1024/1024:.1f} MB")
                print(f"  Decompressed: {len(decompressed)/1024/1024:.1f} MB")
                print(f"  Format: {'Full API walk' if 'endpoints' in data else 'Legacy flat'}")
                print(f"  Status: AVAILABLE")
            except Exception as e:
                print(f"  Status: CORRUPT - {e}")
        else:
            print("  Status: NOT FOUND")
    except Exception as e:
        print(f"  ERROR: {e}")

    conn.close()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = sum(v for v in counts.values() if v >= 0)
    print(f"Total records: {total:,}")

    if counts.get("sites_records", 0) == 0 or counts.get("ipam_records", 0) == 0:
        print("\nWARNING: Structured tables are EMPTY!")
        print("Run with --fix to re-ingest from compressed backup.")
        return False
    else:
        print("\nAll structured tables have data.")
        return True


def fix_database():
    """Re-ingest structured tables from compressed backup."""
    print("\n" + "=" * 60)
    print("REPAIRING DATABASE")
    print("=" * 60)

    # Import here to avoid circular imports
    from core.backup_manager import (
        _bucket_payload,
        _ingest_sites,
        _ingest_ipam,
        _ingest_inventory,
        _build_parent_site_map,
        _ingest_structured_interfaces,
        _ingest_structured_cables,
        init_backup_tables,
    )

    # Load compressed backup
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT backup_json_compressed, uploaded_at FROM backup_metadata WHERE id = 1")
    row = cursor.fetchone()
    conn.close()

    if not row or not row[0]:
        print("ERROR: No compressed backup found in database.")
        return False

    try:
        backup_json_bytes = gzip.decompress(row[0])
        backup_data = json.loads(backup_json_bytes.decode("utf-8"))
        print(f"Decompressed backup: {len(backup_json_bytes)/1024/1024:.1f} MB")
    except Exception as e:
        print(f"ERROR: Failed to decompress backup: {e}")
        return False

    try:
        print("Bucketing data...")
        buckets = _bucket_payload(backup_data)
        print(f"  Found {len(buckets)} object types")

        print("Building parent site map...")
        parent_sites = _build_parent_site_map(buckets)
        print(f"  Mapped {len(parent_sites)} devices/VMs to sites")

        print("Ingesting sites...")
        sites_count = _ingest_sites(buckets)
        print(f"  {sites_count} sites")

        print("Ingesting IPAM...")
        ipam_count = _ingest_ipam(buckets)
        print(f"  {ipam_count} IPAM records")

        print("Ingesting inventory...")
        inventory_count = _ingest_inventory(buckets)
        print(f"  {inventory_count['device'] + inventory_count['hypervisor']} devices/hypervisors, {inventory_count['vm']} VMs")

        print("Ingesting interfaces...")
        iface_count = _ingest_structured_interfaces(buckets, parent_sites, row[1])
        print(f"  {iface_count} interfaces")

        print("Ingesting cables...")
        cable_count = _ingest_structured_cables(buckets, parent_sites, row[1])
        print(f"  {cable_count} cables")

        print("\n" + "=" * 60)
        print("REPAIR COMPLETE")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nERROR: Repair failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fix":
        success = fix_database()
        if success:
            print("\nRunning diagnostic to verify fix...")
            check_database()
        sys.exit(0 if success else 1)
    else:
        healthy = check_database()
        sys.exit(0 if healthy else 1)
