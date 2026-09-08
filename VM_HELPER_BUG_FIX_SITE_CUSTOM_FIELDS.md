# VM Helper Bug Fix - Site and Custom Fields

## Date: 2026-09-08 (Continued)

## Bugs Fixed

### Bug 1: Site Display Incorrect
**Problem**: Site was showing incorrectly due to double prefix processing.
- Database stores: `"Azure - Australia East"`
- Code was stripping prefix: `"Australia East"`
- Then adding it back: `"Azure - Australia East"` ✅

But if the logic wasn't careful, it could cause issues.

**Fix Applied** (`azure_tab.py` lines 807-817):
```python
# Site: use database value as-is if it already has the Azure prefix
if vm_location and vm_location.startswith("Azure - "):
    vm_site = vm_location
elif vm_location:
    raw_loc = _strip_azure_prefix(vm_location)
    vm_site = f"Azure - {raw_loc}"
else:
    vm_site = "Azure - Unknown"
```

### Bug 2: Custom Fields Not Parsing Correctly
**Problem**: Custom fields were stored with `=` separator, not `:`
- Stored format: `"Custom Fields: instance_type=Standard_D4s_v3, resource_group=rg-prod-001"`
- Original parser looked for `:` separator (wrong!)

**Fix Applied** (`azure_vm_importer.py` lines 189-196):
```python
# Parse custom fields section separately (format: "key=value, key=value")
if custom_fields_section:
    for cf_pair in custom_fields_section.split(','):
        cf_pair = cf_pair.strip()
        if '=' in cf_pair:
            cf_key, cf_val = cf_pair.split('=', 1)
            cf_key_clean = cf_key.strip().lower().replace(' ', '_')
            vm_data['custom_fields'][cf_key_clean] = cf_val.strip()
```

### Bug 3: Site Stored in Separate Column
**Problem**: Site is stored in dedicated `site` column, NOT in the summary field.

**Fix Applied** (`azure_vm_importer.py` lines 163-167):
```python
# Query both site column AND summary
cursor.execute("""
    SELECT site, summary
    FROM backup_records
    WHERE LOWER(name) = LOWER(?) 
    AND object_type = 'virtualization_virtual_machines'
    LIMIT 1
""", (vm_name,))

# Initialize with site from the dedicated column
vm_data = {
    'site': site_column or '',  # Site comes from dedicated column
    ...
}
```

### Bug 4: Added Debug Panel
**Fix Applied** (`azure_tab.py` lines 707-717):
```python
# Debug: Show what data sources are available
with st.expander("🔍 Debug: Data Sources", expanded=False):
    st.write("**Database VM Data:**")
    if db_vm:
        st.json(db_vm)
    else:
        st.write("None")
    st.write("**CSV Row Data:**")
    if matched_row:
        st.json(matched_row)
    else:
        st.write("None")
```

This allows you to expand and see the exact data being retrieved from both the database and CSV.

## Expected Data Structure from Database

After the fix, `check_vm_exists_in_db("ANZJDE001")` should return:

```python
{
    'name': 'ANZJDE001',
    'role': 'JDE Application',
    'status': 'Active',
    'site': 'Azure - Australia East',  # From site column
    'tenant': 'ESWine-Application',
    'platform': 'Windows Server',
    'cluster': 'vCluster-01',
    'description': 'JDE Application Server',
    'tags': ['Acion1 True', 'Cortex True', 'JDE', 'Prod', 'SPA', 'Windows Server 2019'],
    'custom_fields': {
        'instance_type': 'Standard_D4s_v3',
        'resource_group': 'rg-prod-001',
        'owner': 'IT Team'
    },
    'primary_ip': '10.20.30.40/24',
    'primary_ip4': '10.20.30.40/24',
    'device': 'ESX-HOST-01',
    'owner': 'IT Team'
}
```

## Verification Steps

1. **Check the Debug Panel**: 
   - Enter "ANZJDE001" in the search field
   - Expand "🔍 Debug: Data Sources"
   - Verify `db_vm` contains all fields
   - Check `custom_fields` dict has `instance_type` and `resource_group`

2. **Verify Field Population**:
   - ✅ **Site**: Should show "Azure - Australia East"
   - ✅ **Instance Type**: Should show value from `custom_fields['instance_type']`
   - ✅ **Resource Groups**: Should show value from `custom_fields['resource_group']` or `custom_fields['resource_groups']`
   - ✅ **Tags**: Should show comma-separated list
   - ✅ **Role**: Should show "JDE Application"
   - ✅ **Tenant**: Should show "ESWine-Application"

## Remaining Diagnostics

If fields are still empty after this fix, check the debug panel to see:

1. **Is `db_vm` populated?**
   - If None → NetBox backup not ingested or VM not in backup
   - If populated → Check the structure

2. **Does `custom_fields` exist in `db_vm`?**
   - If missing → Custom Fields section not in backup summary
   - If empty dict → Custom Fields section present but no values

3. **What are the exact key names in `custom_fields`?**
   - NetBox stores them as lowercase with underscores
   - Example: `instance_type` not `Instance Type`
   - Example: `resource_group` or `resource_groups`

## Files Modified

1. `/opt/netbox-hub/core/azure_vm_importer.py`
   - Lines 163-167: Query site column separately
   - Lines 175: Initialize site from dedicated column
   - Lines 189-196: Parse custom fields with `=` separator

2. `/opt/netbox-hub/ui/tabs/azure_tab.py`
   - Lines 707-717: Added debug data sources panel
   - Lines 807-817: Fixed site display logic

## Next Steps if Issues Persist

Use the debug panel to:
1. Check if database contains the VM
2. Verify custom_fields structure
3. Confirm key names match the extraction logic
