import sys
sys.path.insert(0, '/opt/netbox-hub')

print("Testing azure_csv_manager import...")
try:
    from core.azure_csv_manager import save_azure_csv_upload, get_azure_csv_upload, clear_azure_csv_upload
    print("✅ Import successful")
    
    # Test if database table exists
    from core.db_manager import init_db
    init_db()
    print("✅ Database initialized")
    
    # Test get function
    result = get_azure_csv_upload()
    if result:
        print(f"✅ Found saved CSV: {result['filename']}")
    else:
        print("ℹ️  No saved CSV found (this is normal if you haven't uploaded yet)")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
