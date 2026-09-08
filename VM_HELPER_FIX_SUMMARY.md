# VM Helper Fix Summary

## Date: 2026-09-08

## Problem
The VM Helper form (NetBox VM Manual Form) was showing `---------` for fields like `Subscription`, `Resource Group`, `Size`, `Azure IP`, `Owner`, and `NetBox Tags` when searching for VMs like `ANZJDE001`, even though these values were clearly visible in Section 2's preview table.

## Root Cause
The code was storing the wrong DataFrame in `st.session_state.azure_preview_table_df`:
- **Line 265 (OLD)**: Stored `df_preview = pd.DataFrame(vm_records)` which contained raw dictionary keys like `'subscription'`, `'resource_group'`, `'size'`, etc.
- **Section 2 Preview Table**: Actually rendered from `vm_status_list` with properly formatted column names like `'Subscription'`, `'Resource Group'`, `'Size'`, `'Azure IP'`, `'Owner'`, `'NetBox Tags'`

The VM Helper extraction logic was searching for the formatted column names (e.g., `'Subscription'`), but the DataFrame only contained raw keys (e.g., `'subscription'`), causing all lookups to fail.

## Solution Applied

### Change 1: Store Raw VM Records Separately (Line 265-266)
```python
# OLD:
st.session_state.azure_preview_table_df = df_preview.copy()

# NEW:
# Store raw VM records for later use
st.session_state.azure_raw_vm_records = vm_records
```

### Change 2: Store Actual Preview Table DataFrame (Line 374-376)
```python
# NEW: Added after vm_status_list is built
# Store the actual preview table DataFrame with proper column names
st.session_state.azure_preview_table_df = pd.DataFrame(vm_status_list)
```

This ensures `azure_preview_table_df` contains the **exact same data structure** as what's displayed in Section 2's preview table, with matching column names:
- `'Name'`
- `'Subscription'`
- `'Resource Group'`
- `'Location'`
- `'Status'`
- `'Operating System'`
- `'Role'`
- `'Size'`
- `'Azure IP'`
- `'VNet'`
- `'Subnet'`
- `'Owner'`
- `'NetBox Tags'`
- `'NetBox IP'`
- `'In Database'`

## Verification
The VM Helper extraction logic (lines 725-775) already searches for these exact column names using case-insensitive matching:
- ✅ `['Subscription', 'tenant', 'Tenant']` → Will now match `'Subscription'`
- ✅ `['Azure IP', 'ip', 'Primary IPv4']` → Will now match `'Azure IP'`
- ✅ `['Size', 'Instance Type', 'cf_instance_type']` → Will now match `'Size'`
- ✅ `['Resource Group', 'Resource Groups', 'cf_resource_group']` → Will now match `'Resource Group'`
- ✅ `['Owner', 'owner']` → Will now match `'Owner'`
- ✅ `['NetBox Tags', 'Tags', 'tags']` → Will now match `'NetBox Tags'`

## Expected Behavior After Fix
When searching for `ANZJDE001` in the VM Helper:
1. ✅ **Name**: `ANZJDE001`
2. ✅ **Tenant**: Subscription value from the preview table
3. ✅ **Primary IPv4**: Azure IP from the preview table
4. ✅ **Instance Type**: Size from the preview table
5. ✅ **Resource Groups**: Resource Group from the preview table
6. ✅ **Owner**: Owner from the preview table
7. ✅ **Tags**: NetBox Tags from the preview table

## Files Modified
- `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 265-266, 374-376)

## Testing Recommendation
1. Upload an Azure VM CSV export
2. Wait for Section 2 preview table to render
3. Scroll to VM Helper section at the bottom
4. Search for `ANZJDE001`
5. Verify all fields now populate correctly with values visible in Section 2's table
