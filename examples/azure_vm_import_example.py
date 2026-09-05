#!/usr/bin/env python3
"""
Azure VM Import - Example Usage Script
Demonstrates how to use the Azure VM import functionality programmatically.
"""

from core.azure_vm_importer import (
    parse_azure_vm_csv,
    map_azure_to_netbox,
    save_azure_vms_to_db,
    generate_netbox_import_summary,
    check_vm_exists_in_db
)

def example_import_workflow(csv_path: str):
    """
    Example workflow for importing Azure VMs.
    
    Args:
        csv_path: Path to Azure VM CSV export
    """
    
    print("=" * 70)
    print("Azure VM Import Example Workflow")
    print("=" * 70)
    print()
    
    # Step 1: Parse CSV
    print("📂 Step 1: Parsing Azure VM CSV...")
    vm_records, warnings = parse_azure_vm_csv(csv_path)
    print(f"   ✅ Parsed {len(vm_records)} VMs")
    
    if warnings:
        print(f"   ⚠️  {len(warnings)} warnings:")
        for warning in warnings[:5]:
            print(f"      - {warning}")
        if len(warnings) > 5:
            print(f"      ... and {len(warnings) - 5} more")
    print()
    
    # Step 2: Map to NetBox format
    print("🔄 Step 2: Mapping to NetBox format...")
    netbox_records, metadata = map_azure_to_netbox(vm_records)
    print(f"   ✅ Mapped {len(netbox_records)} VM records")
    print(f"   📊 Statistics:")
    print(f"      - New VMs: {len(metadata['new_vms'])}")
    print(f"      - Existing VMs: {len(metadata['existing_vms'])}")
    print(f"      - Subscriptions: {len(metadata['subscriptions'])}")
    print(f"      - Locations: {len(metadata['locations'])}")
    print(f"      - VM Sizes: {len(metadata['sizes'])}")
    print()
    
    # Step 3: Show summary
    print("📋 Step 3: Import Summary")
    print("-" * 70)
    summary = generate_netbox_import_summary(metadata)
    print(summary)
    print()
    
    # Step 4: Check specific VM
    print("🔍 Step 4: Checking sample VM...")
    if vm_records:
        sample_vm = vm_records[0]['name']
        existing = check_vm_exists_in_db(sample_vm)
        if existing:
            print(f"   ℹ️  VM '{sample_vm}' already exists:")
            print(f"      - Site: {existing['site']}")
            print(f"      - Cluster: {existing['cluster']}")
            print(f"      - Size: {existing['model_or_role']}")
        else:
            print(f"   ✅ VM '{sample_vm}' is new")
    print()
    
    # Step 5: Import (example - commented out to prevent accidental execution)
    print("💾 Step 5: Import to Database")
    print("   ⚠️  Import step skipped in example mode")
    print("   To actually import, uncomment the following lines:")
    print()
    print("   # Import without updating existing VMs")
    print("   # stats = save_azure_vms_to_db(netbox_records, update_existing=False)")
    print()
    print("   # Or import with updates to existing VMs")
    print("   # stats = save_azure_vms_to_db(netbox_records, update_existing=True)")
    print()
    print("   # Show results")
    print("   # print(f'Inserted: {stats[\"inserted\"]}')")
    print("   # print(f'Updated: {stats[\"updated\"]}')")
    print("   # print(f'Skipped: {stats[\"skipped\"]}')")
    print("   # print(f'Errors: {stats[\"errors\"]}')")
    print()
    
    # Uncomment below to actually import
    """
    print("💾 Step 5: Importing to Database...")
    stats = save_azure_vms_to_db(
        netbox_records,
        update_existing=False,  # Set to True to update existing VMs
        source="Azure CSV Import - Example Script"
    )
    
    print(f"   ✅ Import completed!")
    print(f"   📊 Results:")
    print(f"      - Inserted: {stats['inserted']}")
    print(f"      - Updated: {stats['updated']}")
    print(f"      - Skipped: {stats['skipped']}")
    print(f"      - Errors: {stats['errors']}")
    print()
    """
    
    print("=" * 70)
    print("✅ Example workflow completed!")
    print("=" * 70)


if __name__ == "__main__":
    import sys
    
    # Default to the sample CSV if no argument provided
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "data/AzureVirtualMachines (2).csv"
    
    try:
        example_import_workflow(csv_file)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
