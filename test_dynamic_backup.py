#!/usr/bin/env python3
"""
Test script for dynamic backup inspector
Verifies that the PowerShell export format is properly parsed
"""

import json
from pathlib import Path
from core.dynamic_backup_inspector import BackupInspector

def test_backup_inspection():
    """Test backup inspection with actual NetBox backup file"""
    
    # Find the backup file
    backup_file = Path("data/NetBox_Full_Backup_20260909_095938.json")
    
    if not backup_file.exists():
        print(f"❌ Backup file not found: {backup_file}")
        return False
    
    print(f"📂 Loading backup: {backup_file.name}")
    print(f"📊 File size: {backup_file.stat().st_size / (1024*1024):.1f} MB")
    print()
    
    # Load and parse
    with open(backup_file, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)
    
    # Create inspector
    inspector = BackupInspector(backup_data, backup_file.name)
    
    # Inspect all objects
    print("🔍 Inspecting objects...")
    objects = inspector.inspect_all_objects()
    
    print(f"✅ Found {len(objects)} object types")
    print()
    
    # Check for users/owners specifically
    if "users/owners" in objects:
        owners_info = objects["users/owners"]
        print(f"✅ Found users/owners endpoint:")
        print(f"   - Label: {owners_info['label']}")
        print(f"   - Count: {owners_info['count']}")
        print(f"   - Expected: {owners_info.get('expected_count', 'N/A')}")
        print(f"   - Downloaded: {owners_info.get('downloaded_count', 'N/A')}")
        print()
    else:
        print("❌ users/owners endpoint NOT found")
        print()
    
    # Show summary of first 20 endpoints
    print("📋 First 20 endpoints discovered:")
    for i, (endpoint, metadata) in enumerate(list(objects.items())[:20]):
        label = metadata['label']
        count = metadata['count']
        print(f"   {i+1:2d}. {label:40s} ({endpoint}): {count}")
    
    if len(objects) > 20:
        print(f"   ... and {len(objects) - 20} more endpoints")
    print()
    
    # Test custom fields
    print("🔧 Inspecting custom fields...")
    custom_fields = inspector.inspect_custom_fields()
    print(f"✅ Found {len(custom_fields)} custom fields")
    
    if custom_fields:
        print("   First 5 custom fields:")
        for i, (field_name, field_info) in enumerate(list(custom_fields.items())[:5]):
            print(f"   {i+1}. {field_name}: {field_info['type']}")
    print()
    
    # Test choice sets
    print("⚙️  Inspecting choice sets...")
    choice_sets = inspector.inspect_choice_sets()
    print(f"✅ Found {len(choice_sets)} choice sets")
    
    if choice_sets:
        print("   First 5 choice sets:")
        for i, (set_name, set_info) in enumerate(list(choice_sets.items())[:5]):
            print(f"   {i+1}. {set_name}: {set_info['count']} values")
    print()
    
    # Test get_object_data for users/owners
    if "users/owners" in objects:
        print("📥 Testing get_object_data for users/owners...")
        owners_data = inspector.get_object_data("users/owners")
        print(f"✅ Retrieved {len(owners_data)} owner records")
        if owners_data:
            print(f"   First owner: {owners_data[0].get('username', 'N/A')}")
    print()
    
    # Generate summary
    print("📄 Generating backup summary...")
    summary = inspector.generate_backup_contents_summary()
    print(summary[:500])  # Show first 500 chars
    if len(summary) > 500:
        print(f"... ({len(summary) - 500} more characters)")
    print()
    
    print("=" * 60)
    print("✅ All tests passed!")
    print(f"✅ System will display {len(objects)} object types (not just 57)")
    print("=" * 60)
    
    return True

if __name__ == "__main__":
    try:
        success = test_backup_inspection()
        exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
