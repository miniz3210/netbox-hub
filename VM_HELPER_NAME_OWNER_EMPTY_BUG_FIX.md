# VM Helper Name and Owner Empty Bug Fix

## Date: 2026-09-08 16:38 UTC

## Problem
Fields showing empty even though data appears in debug panel:
- **anzjde501** (from database): Name and Owner fields empty, but debug shows Owner="BackOffice"
- **AUDRSQL07** (from Azure CSV): Name and Owner fields empty, but debug shows Owner="Rich Paulson"

## Root Cause

The helper functions `extract_val()` and `get_val_from_row()` were returning empty strings `""` instead of `None` when no value was found. This broke Python's `or` operator fallback chains.

### Why This Broke the Logic

Python's `or` operator treats empty strings as "falsy":
```python
# BROKEN (before fix):
vm_name = get_val_from_row(...) or extract_val(...) or "DEFAULT"
# If get_val_from_row returns "" (empty string), the or chain stops!
# Result: vm_name = "" (empty string shown in field)

# FIXED (after fix):
vm_name = get_val_from_row(...) or extract_val(...) or "DEFAULT"
# If get_val_from_row returns None, Python tries the next option
# Result: vm_name gets value from extract_val or "DEFAULT"
```

### Example Scenario - anzjde501 from Database:
```python
# BEFORE (BROKEN):
vm_name = get_val_from_row(matched_row, ['Name', 'name'])  # Returns ""
         or extract_val(db_vm, ['name'])                    # Never evaluated!
         or clean_target.upper()                            # Never evaluated!
# Result: vm_name = "" (empty!)

# AFTER (FIXED):
vm_name = get_val_from_row(matched_row, ['Name', 'name'])  # Returns None
         or extract_val(db_vm, ['name'])                    # Returns "anzjde501"
         or clean_target.upper()                            # Not needed
# Result: vm_name = "anzjde501" ✅
```

### Example Scenario - AUDRSQL07 from CSV:
```python
# BEFORE (BROKEN):
vm_owner = extract_val(custom_fields, ['owner'])  # Returns ""
          or extract_val(db_vm, ['owner'])        # Never evaluated!
          or get_val_from_row(matched_row, ['Owner', 'owner'])  # Never evaluated!
          or "---------"                          # Never evaluated!
# Result: vm_owner = "" (empty!)

# AFTER (FIXED):
vm_owner = extract_val(custom_fields, ['owner'])  # Returns None
          or extract_val(db_vm, ['owner'])        # Returns None
          or get_val_from_row(matched_row, ['Owner', 'owner'])  # Returns "Rich Paulson"
          or "---------"                          # Not needed
# Result: vm_owner = "Rich Paulson" ✅
```

## Solution Applied

### Fix 1: Updated `extract_val()` Function
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 709-720)

**BEFORE**:
```python
def extract_val(source_dict, candidate_keys):
    if not source_dict or not isinstance(source_dict, dict):
        return ""  # ❌ Returns empty string
    norm_dict = {str(k).strip().lower(): v for k, v in source_dict.items()}
    for k in candidate_keys:
        k_norm = k.strip().lower()
        if k_norm in norm_dict:
            val = norm_dict[k_norm]
            if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                return val
    return ""  # ❌ Returns empty string
```

**AFTER**:
```python
def extract_val(source_dict, candidate_keys):
    """Extract value from dict, return None if not found (allows or-chaining)"""
    if not source_dict or not isinstance(source_dict, dict):
        return None  # ✅ Returns None
    norm_dict = {str(k).strip().lower(): v for k, v in source_dict.items()}
    for k in candidate_keys:
        k_norm = k.strip().lower()
        if k_norm in norm_dict:
            val = norm_dict[k_norm]
            if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                return str(val).strip()  # ✅ Always return string
    return None  # ✅ Returns None
```

### Fix 2: Updated `get_val_from_row()` Function
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 731-742)

**BEFORE**:
```python
def get_val_from_row(row_dict, candidate_keys, default="---------"):
    if not row_dict:
        return default
    row_norm = {str(k).strip().lower(): v for k, v in row_dict.items()}
    for k in candidate_keys:
        k_norm = k.strip().lower()
        if k_norm in row_norm:
            val = row_norm[k_norm]
            if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                return str(val).strip()
    return default  # Could be "---------" which is truthy in or-chains
```

**AFTER**:
```python
def get_val_from_row(row_dict, candidate_keys, default=None):
    """Extract value from row dict, return None if not found (allows or-chaining)"""
    if not row_dict:
        return default
    row_norm = {str(k).strip().lower(): v for k, v in row_dict.items()}
    for k in candidate_keys:
        k_norm = k.strip().lower()
        if k_norm in row_norm:
            val = row_norm[k_norm]
            if val is not None and str(val).strip() not in ["", "nan", "None", "---------"]:
                return str(val).strip()
    return default  # ✅ Default is now None
```

### Fix 3: Updated Field Extraction Logic
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 744-797)

Added proper fallback chains that work with `None` returns:

```python
# Name: Try CSV first, then database, then use search input
vm_name = get_val_from_row(matched_row, ['Name', 'name']) or extract_val(db_vm, ['name']) or clean_target.upper()

# Owner: Try custom fields, then db owner, then CSV owner, then default
vm_owner = extract_val(custom_fields, ['owner']) or extract_val(db_vm, ['owner']) or get_val_from_row(matched_row, ['Owner', 'owner']) or "---------"

# All other fields follow the same pattern...
```

## Key Changes

1. **`extract_val()` now returns `None`** instead of `""` when value not found
2. **`get_val_from_row()` now returns `None`** by default instead of `"---------"`
3. **All fallback chains now work correctly** because `None` is falsy in Python
4. **Final defaults are at the end** of each chain (e.g., `or "---------"`, `or clean_target.upper()`)

## Expected Behavior After Fix

### Scenario 1: anzjde501 (Database)
```
Debug Panel:
{
  "name": "anzjde501",
  "owner": "BackOffice",
  "custom_fields": {
    "owner": "BackOffice"
  }
}

Form Fields:
Name:  anzjde501       ✅ (from db_vm['name'])
Owner: BackOffice      ✅ (from custom_fields['owner'] or db_vm['owner'])
```

### Scenario 2: AUDRSQL07 (Azure CSV)
```
Debug Panel - CSV Row Data:
{
  "Name": "AUDRSQL07",
  "Owner": "Rich Paulson"
}

Form Fields:
Name:  AUDRSQL07       ✅ (from matched_row['Name'])
Owner: Rich Paulson    ✅ (from matched_row['Owner'])
```

### Scenario 3: Mixed Data
```
Debug Panel:
Database: { "name": "VM001", "site": "Azure - East" }
CSV Row:  { "Owner": "John Doe" }

Form Fields:
Name:  VM001           ✅ (from database)
Site:  Azure - East    ✅ (from database)
Owner: John Doe        ✅ (from CSV, database doesn't have owner)
```

## Fallback Chain Logic

Each field follows this pattern:
```
1. Try Database value (if available)
2. Try CSV value (if available)
3. Use default value
```

Because functions return `None` when value not found, the `or` operator continues to the next option until a truthy value is found.

## Files Modified

**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py`
- Lines 709-720: Fixed `extract_val()` to return `None`
- Lines 731-742: Fixed `get_val_from_row()` to return `None`
- Lines 744-797: Updated all field extraction with proper fallback chains

## Testing

Test these specific cases:
1. ✅ **anzjde501** - Database record, should show name and owner
2. ✅ **AUDRSQL07** - CSV record, should show name and owner
3. ✅ **VM in both** - Should prefer database, fall back to CSV
4. ✅ **Unknown VM** - Should show searched name, everything else default

## Summary

The bug was caused by helper functions returning empty strings instead of `None`, which broke Python's `or` operator fallback chains. By returning `None` when no value is found, the chains now work correctly and all fields populate as expected.

**Root Cause**: Empty string `""` is falsy but stops `or` chains
**Solution**: Return `None` instead, which allows chains to continue
**Result**: All fields now populate correctly from database, CSV, or defaults
