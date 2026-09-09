"""
Field Configuration Manager CLI

Command-line interface for managing NetBox Hub's dynamic field registry.
Allows viewing, adding, and configuring custom fields without code changes.
"""

import argparse
import json
import sys
from typing import List, Tuple
from core.field_registry import FieldRegistry
from core.db_manager_wrapper import DatabaseManager
from core.backup_manager import init_backup_tables


def list_object_types(registry: FieldRegistry):
    """List all discovered NetBox object types."""
    object_types = registry.get_all_object_types()
    
    if not object_types:
        print("No object types discovered yet. Upload a NetBox backup to auto-discover schema.")
        return
    
    print(f"\n{'Object Type':<40} {'Fields':<10} {'Custom':<8} {'Version':<12} {'Updated'}")
    print("=" * 100)
    
    for obj in object_types:
        enabled = "✓" if obj["is_enabled"] else "✗"
        print(f"{enabled} {obj['object_type']:<38} {obj['field_count']:<10} "
              f"{obj['custom_fields']:<8} {obj['netbox_version'] or 'Unknown':<12} {obj['updated_at']}")
    
    print(f"\nTotal: {len(object_types)} object types")


def show_fields(registry: FieldRegistry, object_type: str):
    """Show all fields for an object type."""
    from core.db_manager_wrapper import DatabaseManager
    
    conn = DatabaseManager().get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT field_config, netbox_version FROM netbox_schema
        WHERE object_type = ?
    """, (object_type,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        print(f"Object type '{object_type}' not found.")
        print("\nRun 'python field_manager.py list' to see available object types.")
        return
    
    config = json.loads(row[0])
    version = row[1] or "Unknown"
    
    print(f"\nObject Type: {object_type}")
    print(f"NetBox Version: {version}")
    print("=" * 80)
    
    # Show native fields
    native_fields = config.get("native_fields", {})
    if native_fields:
        print(f"\nNative Fields ({len(native_fields)}):")
        print(f"{'Field Name':<30} {'Label':<30} {'Type':<15}")
        print("-" * 80)
        for field_name, field_info in sorted(native_fields.items()):
            print(f"{field_name:<30} {field_info.get('label', ''):<30} {field_info.get('type', ''):<15}")
    
    # Show relationships
    relationships = config.get("relationships", {})
    if relationships:
        print(f"\nRelationship Fields ({len(relationships)}):")
        print(f"{'Field Name':<30} {'Label':<30} {'Type':<15}")
        print("-" * 80)
        for field_name, field_info in sorted(relationships.items()):
            print(f"{field_name:<30} {field_info.get('label', ''):<30} {field_info.get('type', ''):<15}")
    
    # Show custom fields
    custom_fields = config.get("custom_fields", {})
    if custom_fields:
        print(f"\nCustom Fields ({len(custom_fields)}):")
        print(f"{'Field Name':<25} {'Label':<25} {'Type':<12} {'Required':<10} {'Choice Set'}")
        print("-" * 100)
        for field_name, field_info in sorted(custom_fields.items()):
            required = "Yes" if field_info.get('required') else "No"
            choice_set = field_info.get('choice_set') or ""
            print(f"{field_name:<25} {field_info.get('label', ''):<25} "
                  f"{field_info.get('type', ''):<12} {required:<10} {choice_set}")
    
    # Show display fields (what's currently shown in UI)
    display_fields = config.get("display_fields", [])
    if display_fields:
        print(f"\nDisplay Fields (shown in UI):")
        for i, (label, field_name) in enumerate(display_fields, 1):
            print(f"  {i}. {label} ({field_name})")


def add_custom_field(registry: FieldRegistry, object_type: str, field_name: str,
                     field_label: str, field_type: str, choice_set: str = None,
                     required: bool = False):
    """Add a custom field to the registry."""
    success = registry.add_custom_field_manually(
        object_type=object_type,
        field_name=field_name,
        field_label=field_label,
        field_type=field_type,
        choice_set=choice_set,
        required=required
    )
    
    if success:
        print(f"✓ Successfully added custom field '{field_name}' to {object_type}")
    else:
        print(f"✗ Failed to add custom field '{field_name}'")
        sys.exit(1)


def update_display_fields(registry: FieldRegistry, object_type: str, field_spec: str):
    """
    Update which fields are displayed in UI.
    
    field_spec format: "Label1:field_name1,Label2:field_name2,..."
    """
    try:
        display_fields = []
        for pair in field_spec.split(','):
            pair = pair.strip()
            if ':' not in pair:
                print(f"Error: Invalid field specification '{pair}'. Expected format: 'Label:field_name'")
                sys.exit(1)
            label, field_name = pair.split(':', 1)
            display_fields.append((label.strip(), field_name.strip()))
        
        success = registry.update_display_fields(object_type, display_fields)
        
        if success:
            print(f"✓ Updated display fields for {object_type}")
            print(f"  Total fields: {len(display_fields)}")
        else:
            print(f"✗ Failed to update display fields")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


def export_schema(registry: FieldRegistry, output_file: str):
    """Export entire schema to JSON file."""
    object_types = registry.get_all_object_types()
    
    export_data = {}
    conn = DatabaseManager().get_connection()
    cursor = conn.cursor()
    
    for obj in object_types:
        cursor.execute("""
            SELECT field_config FROM netbox_schema
            WHERE object_type = ?
        """, (obj["object_type"],))
        
        row = cursor.fetchone()
        if row:
            export_data[obj["object_type"]] = json.loads(row[0])
    
    conn.close()
    
    with open(output_file, 'w') as f:
        json.dump(export_data, f, indent=2)
    
    print(f"✓ Exported schema for {len(export_data)} object types to {output_file}")


def discover_from_backup(registry: FieldRegistry, backup_file: str):
    """Manually run schema discovery on an existing backup file."""
    import time
    from pathlib import Path
    
    if not Path(backup_file).exists():
        print(f"✗ Backup file not found: {backup_file}")
        sys.exit(1)
    
    print(f"Loading backup file: {backup_file}")
    file_size_mb = Path(backup_file).stat().st_size / (1024 * 1024)
    print(f"File size: {file_size_mb:.1f} MB")
    print()
    
    # Load and parse backup
    from core.backup_manager import _load_payload, _bucket_payload, _payload_source_info
    
    start_time = time.time()
    print("[1/3] Parsing JSON...")
    
    with open(backup_file, 'rb') as f:
        payload = _load_payload(f)
    
    parse_time = time.time() - start_time
    print(f"✓ Parsed in {parse_time:.1f}s")
    
    print("\n[2/3] Bucketing data...")
    buckets = _bucket_payload(payload)
    source_info = _payload_source_info(payload)
    
    bucket_time = time.time() - start_time - parse_time
    print(f"✓ Bucketed in {bucket_time:.1f}s")
    print(f"  Found {len(buckets)} object types")
    
    # Run discovery
    print("\n[3/3] Discovering schema...")
    discover_start = time.time()
    
    netbox_version = source_info.get("netbox_version") if source_info else None
    stats = registry.discover_schema_from_backup(buckets, netbox_version)
    
    discover_time = time.time() - discover_start
    total_time = time.time() - start_time
    
    print(f"✓ Discovery completed in {discover_time:.1f}s")
    print()
    print("=" * 60)
    print("Discovery Summary:")
    print("=" * 60)
    print(f"  Object types discovered:  {stats.get('objects_discovered', 0)}")
    print(f"  Total fields discovered:  {stats.get('fields_discovered', 0)}")
    print(f"  Custom fields found:      {stats.get('custom_fields_found', 0)}")
    print(f"  Schemas updated:          {stats.get('updated_objects', 0)}")
    print(f"  NetBox version:           {netbox_version or 'Unknown'}")
    print()
    print(f"Total time:                 {total_time:.1f}s")
    print(f"  - JSON parsing:           {parse_time:.1f}s")
    print(f"  - Data bucketing:         {bucket_time:.1f}s")
    print(f"  - Schema discovery:       {discover_time:.1f}s")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="NetBox Hub Field Configuration Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all discovered object types
  python field_manager.py list
  
  # Show fields for a specific object type
  python field_manager.py show virtualization_virtual_machines
  
  # Add a custom field
  python field_manager.py add virtualization_virtual_machines \\
      --field backup_status --label "Backup Status" --type select \\
      --choice-set "Backup Status Set"
  
  # Update display fields
  python field_manager.py update virtualization_virtual_machines \\
      --fields "Name:name,Status:status,Owner:owner,Backup:backup_status"
  
  # Export schema to JSON
  python field_manager.py export --output schema_backup.json
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    # List command
    subparsers.add_parser('list', help='List all discovered object types')
    
    # Show command
    show_parser = subparsers.add_parser('show', help='Show fields for an object type')
    show_parser.add_argument('object_type', help='Object type (e.g., virtualization_virtual_machines)')
    
    # Add command
    add_parser = subparsers.add_parser('add', help='Add a custom field')
    add_parser.add_argument('object_type', help='Object type to add field to')
    add_parser.add_argument('--field', required=True, help='Field name (snake_case)')
    add_parser.add_argument('--label', required=True, help='Display label')
    add_parser.add_argument('--type', default='text', 
                           choices=['text', 'integer', 'boolean', 'select', 'multiselect', 'url', 'json'],
                           help='Field type')
    add_parser.add_argument('--choice-set', help='Choice set name (for select/multiselect types)')
    add_parser.add_argument('--required', action='store_true', help='Mark field as required')
    
    # Update command
    update_parser = subparsers.add_parser('update', help='Update display fields for UI')
    update_parser.add_argument('object_type', help='Object type to update')
    update_parser.add_argument('--fields', required=True, 
                              help='Comma-separated field spec: "Label1:field1,Label2:field2,..."')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export schema to JSON')
    export_parser.add_argument('--output', default='netbox_schema_export.json',
                              help='Output file path')
    
    # Discover command
    discover_parser = subparsers.add_parser('discover', 
                                           help='Manually discover schema from backup file')
    discover_parser.add_argument('backup_file', 
                                help='Path to NetBox backup JSON file')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Initialize database tables
    init_backup_tables()
    
    # Create registry instance
    db_manager = DatabaseManager()
    registry = FieldRegistry(db_manager)
    
    # Execute command
    if args.command == 'list':
        list_object_types(registry)
    
    elif args.command == 'show':
        show_fields(registry, args.object_type)
    
    elif args.command == 'add':
        add_custom_field(
            registry, args.object_type, args.field, args.label,
            args.type, args.choice_set, args.required
        )
    
    elif args.command == 'update':
        update_display_fields(registry, args.object_type, args.fields)
    
    elif args.command == 'export':
        export_schema(registry, args.output)
    
    elif args.command == 'discover':
        discover_from_backup(registry, args.backup_file)


if __name__ == '__main__':
    main()
