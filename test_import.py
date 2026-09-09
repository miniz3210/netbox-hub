try:
    from core.azure_csv_manager import save_azure_csv_upload, get_azure_csv_upload, clear_azure_csv_upload
    print("✅ azure_csv_manager imports successfully")
    
    from ui.tabs.azure_tab import render_azure_tab
    print("✅ azure_tab imports successfully")
    
    print("\n✅ All imports working!")
except Exception as e:
    print(f"❌ Import error: {e}")
    import traceback
    traceback.print_exc()
