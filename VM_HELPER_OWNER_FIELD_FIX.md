# VM Helper Owner Field Fix

## Date: 2026-09-08 (Final Fix)

## Problem
When searching for "ANZJDE001" in "Enter VM Name / Hostname":
- ❌ **Owner** field was empty, should show "BackOffice"
- ❌ **Owner group** was not visible in debug panel

## Root Cause Analysis

### Owner Field Storage in NetBox
NetBox stores ownership in two possible places:

1. **Custom Field** - `cf_owner` or just `owner` in custom_fields section
   - Format in backup summary: `"Custom Fields: instance_type=Standard_D4s_v3, owner=BackOffice, resource_group=rg-prod"`
   
2. **Native Ownership** - NetBox 3.7+ has built-in ownership tracking
   - Stored as top-level `owner` field (object reference)
   - May include `owner_group` as well

The backup summary stores custom fields with format: `key=value, key=value`

## Solution Applied

### Fix 1: Extract Owner from Custom Fields
**File**: `/opt/netbox-hub/core/azure_vm_importer.py` (Lines 189-199)

```python
# Parse custom fields section separately (format: "key=value, key=value")
if custom_fields_section:
    for cf_pair in custom_fields_section.split(','):
        cf_pair = cf_pair.strip()
        if '=' in cf_pair:
            cf_key, cf_val = cf_pair.split('=', 1)
            cf_key_clean = cf_key.strip().lower().replace(' ', '_')
            cf_val_clean = cf_val.strip()
            vm_data['custom_fields'][cf_key_clean] = cf_val_clean
            
            # If this is the owner field, also set it at the top level
            if cf_key_clean == 'owner':
                vm_data['owner'] = cf_val_clean
```

**Key Change**: When parsing custom fields, if we find an `owner` field, we now:
1. Store it in `custom_fields['owner']` ✅
2. **ALSO** store it at top level as `vm_data['owner']` ✅

This ensures the owner value is accessible from both locations.

### Fix 2: Multi-Source Owner Lookup in UI
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 786-797)

```python
# Custom fields: prioritize database values
custom_fields = db_vm.get('custom_fields', {}) if (db_vm and isinstance(db_vm.get('custom_fields'), dict)) else {}

# Owner: check custom fields first, then top-level owner field, then CSV
vm_owner = extract_val(custom_fields, ['owner']) or extract_val(db_vm, ['owner']) or get_val_from_row(matched_row, ['Owner', 'owner']) or "---------"
if isinstance(vm_owner, dict):
    vm_owner = vm_owner.get('name', '---------')

# Owner group: NetBox native ownership feature (may not be in backup)
vm_owner_group = "---------"  # Not typically stored in minimal backups
```

**Lookup Priority**:
1. `custom_fields['owner']` (custom field) → Should find "BackOffice" here ✅
2. `db_vm['owner']` (top-level field)
3. CSV row data
4. Default: "---------"

### Fix 3: Enhanced Debug Output
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 709-727)

Added detailed custom fields inspection:
```python
with st.expander("🔍 Debug: Data Sources", expanded=False):
    st.write("**Database VM Data:**")
    if db_vm:
        st.json(db_vm)
        st.write("**Custom Fields Extracted:**")
        cf_debug = db_vm.get('custom_fields', {})
        if cf_debug:
            st.json(cf_debug)
            st.write(f"Available keys: {list(cf_debug.keys())}")
        else:
            st.write("No custom fields found")
```

This shows:
- Full `db_vm` structure
- Isolated `custom_fields` dictionary
- List of available custom field keys

## Expected Debug Output

After the fix, when searching for "ANZJDE001", the debug panel should show:

```json
{
  "name": "ANZJDE001",
  "role": "JDE Application",
  "status": "Active",
  "site": "Azure - Australia East",
  "tenant": "ESWine-Application",
  "platform": "Windows Server",
  "tags": ["Acion1 True", "Cortex True", "JDE", "Prod", "SPA", "Windows Server 2019"],
  "custom_fields": {
    "instance_type": "Standard_D4s_v3",
    "resource_group": "rg-prod-001",
    "owner": "BackOffice"
  },
  "owner": "BackOffice"
}
```

**Custom Fields Extracted:**
```json
{
  "instance_type": "Standard_D4s_v3",
  "resource_group": "rg-prod-001",
  "owner": "BackOffice"
}
```

**Available keys:** `['instance_type', 'resource_group', 'owner']`

## Expected Field Values After Fix

| Field | Expected Value | Source |
|-------|---------------|--------|
| **Name** | ANZJDE001 | Database |
| **Role** | JDE Application | Database summary |
| **Status** | Active | Database summary |
| **Site** | Azure - Australia East | Database site column |
| **Tenant** | ESWine-Application | Database summary |
| **Platform** | Windows Server | Database summary |
| **Tags** | Acion1 True, Cortex True, JDE, Prod, SPA, Windows Server 2019 | Database summary (Tags section) |
| **Primary IPv4** | 10.x.x.x/24 | Database summary |
| **Instance Type** | Standard_D4s_v3 | custom_fields['instance_type'] |
| **Resource Groups** | rg-prod-001 | custom_fields['resource_group'] |
| **Owner** | BackOffice | custom_fields['owner'] OR db_vm['owner'] ✅ |
| **Owner group** | --------- | Not in minimal backups |

## Owner Group Note

"Owner group" is part of NetBox's native ownership feature (users.ownergroup). This is typically **not** included in minimal CSV exports, only in full API JSON exports. The field will show "---------" unless:
1. A full NetBox JSON backup is uploaded (includes `users/owners` and `users/ownergroups`)
2. The VM has an owner_group assigned
3. The backup includes the native ownership data structure

For most cases, the custom field "Owner" (like "BackOffice") is sufficient.

## Verification Steps

1. **Search for ANZJDE001**
2. **Expand Debug Panel** → "🔍 Debug: Data Sources"
3. **Check Custom Fields section**:
   - Should see: `"owner": "BackOffice"`
   - Should see in available keys list
4. **Check Form Fields**:
   - ✅ **Owner (Native Ownership)**: Should show "BackOffice"
   - ✅ **Owner group**: Will show "---------" (expected if not in backup)

## Files Modified

1. **`/opt/netbox-hub/core/azure_vm_importer.py`** (Lines 189-199)
   - Added logic to promote custom field `owner` to top-level `vm_data['owner']`

2. **`/opt/netbox-hub/ui/tabs/azure_tab.py`** (Lines 709-727, 786-797, 849-850)
   - Enhanced debug output to show custom fields separately
   - Fixed owner extraction to check custom_fields first
   - Added owner_group variable and display

## Diagnostic: If Owner Still Empty

Check the debug panel:

1. **Does `custom_fields` contain `owner` key?**
   - If YES → The fix should work
   - If NO → The backup summary doesn't include owner in custom fields

2. **Is there an `owner` field at top level in `db_vm`?**
   - If YES → Should be extracted
   - If NO → Owner not in this VM's backup data

3. **Check the exact key name**:
   - Look at "Available keys" list
   - Owner might be stored as: `owner`, `cf_owner`, `Owner`, etc.
   - We can adjust extraction logic based on actual key name

4. **Raw Summary Check**:
   - If you can see the full `db_vm` JSON in debug
   - Look for custom fields section
   - Verify the format matches our parser expectations

The enhanced debug output will tell us exactly what's in the database and why owner might not be extracting correctly.
