# VM Helper Database Lookup Fix

## Date: 2026-09-08

## Problem
When users enter a VM hostname (e.g., "ANZJDE001") in the "Enter VM Name / Hostname" search field, the VM Helper form was displaying empty fields (`---------`) even though the VM exists in the NetBox database with complete data including:
- Role: "JDE Application"
- Tenant: "ESWine-Application"
- Tags: "Acion1 True", "Cortex True", "JDE", "Prod", "SPA", "Windows Server 2019"
- And other fields

## Root Causes

### 1. Limited Database Query
The `check_vm_exists_in_db()` function only queried the `inventory_records` table, which contains minimal fields:
- id, name, category, description, manufacturer, model_or_role, site, cluster, imported_at

This table does **not** contain:
- ❌ Tenant
- ❌ Platform
- ❌ Tags
- ❌ Custom Fields (Instance Type, Resource Group, Owner)
- ❌ Primary IP
- ❌ Device
- ❌ Status

### 2. Missing NetBox Backup Data Integration
The complete VM data is stored in the `backup_records` table (from NetBox JSON backup), which includes all fields in a structured summary format:
- **Summary**: `"Role: JDE Application | Status: Active | Site: Azure - Australia East | Tenant: ESWine-Application | Platform: Windows Server | Tags: Acion1 True, Cortex True, JDE, Prod, SPA, Windows Server 2019 | Custom Fields: Instance Type: Standard_D4s_v3, Resource Group: rg-prod, Owner: IT Team"`

## Solution Applied

### Change 1: Enhanced `check_vm_exists_in_db()` Function
**File**: `/opt/netbox-hub/core/azure_vm_importer.py` (Lines 140-272)

Created a comprehensive two-tier lookup strategy:

#### Tier 1: NetBox Backup Records (Primary)
```python
# Query backup_records table for complete VM data
cursor.execute("""
    SELECT summary, search_blob
    FROM backup_records
    WHERE LOWER(name) = LOWER(?) 
    AND object_type = 'virtualization_virtual_machines'
    LIMIT 1
""", (vm_name,))
```

**Parses the summary field** which contains pipe-separated key-value pairs:
- `Role: JDE Application`
- `Status: Active`
- `Site: Azure - Australia East`
- `Tenant: ESWine-Application`
- `Platform: Windows Server`
- `Cluster: vCluster-01`
- `Primary IP: 10.20.30.40/24`
- `Device: ESX-HOST-01`
- `Tags: Tag1, Tag2, Tag3`
- `Custom Fields: field1: value1, field2: value2`

#### Tier 2: Inventory Records (Fallback)
If no backup record exists, falls back to the original `inventory_records` table query (limited data but better than nothing).

### Change 2: VM Helper Extraction Priority
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 737-776)

**Changed extraction priority** to prioritize database values over CSV values:

**BEFORE** (CSV-first):
```python
vm_role = get_val_from_row(matched_row, ['Role']) or extract_val(db_vm, ['role'])
vm_tenant = get_val_from_row(matched_row, ['Tenant']) or extract_val(db_vm, ['tenant'])
```

**AFTER** (Database-first):
```python
vm_role = extract_val(db_vm, ['role']) or get_val_from_row(matched_row, ['Role'])
vm_tenant = extract_val(db_vm, ['tenant']) or get_val_from_row(matched_row, ['Tenant'])
```

This ensures that when a VM exists in the database, the helper shows the **authoritative NetBox data** first, with CSV data as fallback.

### Change 3: Enhanced Tag Formatting
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 715-722)

```python
def format_tags(tags_val):
    if not tags_val:
        return "—"
    if isinstance(tags_val, list):
        # Filter out empty tags
        tag_names = [t.get('name', str(t)) if isinstance(t, dict) else str(t) for t in tags_val if t]
        return ", ".join(tag_names) if tag_names else "—"
    if isinstance(tags_val, str) and tags_val.strip() not in ["", "nan", "None", "—"]:
        return tags_val.strip()
    return "—"
```

Now properly handles:
- ✅ List of tags from database: `['Tag1', 'Tag2', 'Tag3']`
- ✅ Comma-separated string from summary: `"Tag1, Tag2, Tag3"`
- ✅ Empty or None values

### Change 4: Database Tags Priority
**File**: `/opt/netbox-hub/ui/tabs/azure_tab.py` (Lines 744-750)

```python
# Tags: prioritize database tags
db_tags = db_vm.get('tags', []) if db_vm else []
if db_tags:
    vm_tags_display = format_tags(db_tags)
else:
    raw_tags = get_val_from_row(matched_row, ['NetBox Tags', 'Tags', 'tags'], default="—")
    vm_tags_display = format_tags(raw_tags)
```

## Data Flow

### When User Searches for "ANZJDE001":

1. **Database Lookup** → `check_vm_exists_in_db("ANZJDE001")`
   - Queries `backup_records` table
   - Finds: `"Role: JDE Application | Status: Active | Site: Azure - Australia East | Tenant: ESWine-Application | Platform: Windows Server | Tags: Acion1 True, Cortex True, JDE, Prod, SPA, Windows Server 2019 | Custom Fields: Instance Type: Standard_D4s_v3, Resource Group: rg-prod"`
   - Parses into structured dict

2. **CSV Lookup** → Searches `azure_preview_table_df`
   - Finds matching row from uploaded Azure CSV (if available)

3. **Field Population** → Prioritizes database data:
   ```python
   Role:            db_vm['role']           → "JDE Application"
   Status:          db_vm['status']         → "Active"
   Tenant:          db_vm['tenant']         → "ESWine-Application"
   Site:            db_vm['site']           → "Azure - Australia East"
   Platform:        db_vm['platform']       → "Windows Server"
   Tags:            db_vm['tags']           → "Acion1 True, Cortex True, JDE, Prod, SPA, Windows Server 2019"
   Primary IP:      db_vm['primary_ip']     → "10.20.30.40/24"
   Instance Type:   custom_fields['instance_type'] → "Standard_D4s_v3"
   Resource Group:  custom_fields['resource_group'] → "rg-prod"
   Owner:           db_vm['owner']          → "IT Team"
   ```

## Expected Behavior After Fix

### Scenario 1: VM Exists in NetBox Database
When user enters "ANZJDE001":
- ✅ **Name**: ANZJDE001
- ✅ **Role**: JDE Application
- ✅ **Status**: Active
- ✅ **Tenant**: ESWine-Application
- ✅ **Site**: Azure - Australia East
- ✅ **Platform**: Windows Server
- ✅ **Tags**: Acion1 True, Cortex True, JDE, Prod, SPA, Windows Server 2019
- ✅ **Primary IPv4**: 10.20.30.40/24
- ✅ **Instance Type**: Standard_D4s_v3
- ✅ **Resource Groups**: rg-prod
- ✅ **Owner**: IT Team
- ✅ **Cluster**: vCluster-01 (if available)
- ✅ **Device**: ESX-HOST-01 (if available)
- ✅ **Description**: Application server for JDE (if available)

### Scenario 2: VM Only in Uploaded CSV
When user enters a new VM name:
- ✅ All fields populate from the uploaded Azure CSV data
- ✅ Shows: "🔵 Source: Uploaded Azure CSV (New VM Staging Data)"

### Scenario 3: VM in Both Database and CSV
When user enters a VM that exists in both:
- ✅ Database fields take priority (authoritative NetBox data)
- ✅ CSV fills in any missing database fields
- ✅ Shows: "🟢 Source: Matched in Database (Enriched with Azure CSV Data)"

## Files Modified

1. `/opt/netbox-hub/core/azure_vm_importer.py`
   - Lines 140-272: Complete rewrite of `check_vm_exists_in_db()` function

2. `/opt/netbox-hub/ui/tabs/azure_tab.py`
   - Lines 715-722: Enhanced `format_tags()` function
   - Lines 737-776: Changed extraction priority to database-first
   - Lines 744-750: Added database tags priority logic

## Prerequisites

This fix requires that:
1. ✅ A NetBox JSON backup has been uploaded via "Ingest NetBox Data" tab
2. ✅ The backup contains `virtualization/virtual-machines` data
3. ✅ The backup was successfully ingested into the `backup_records` table

## Testing Recommendation

1. Upload a NetBox JSON backup containing VM data
2. Go to Azure VM Import tab
3. Scroll to "Enter VM Name / Hostname" search field at the bottom
4. Enter a VM name that exists in NetBox (e.g., "ANZJDE001")
5. Verify all fields populate correctly with NetBox database values
6. Check that Tags show as comma-separated list
7. Check that Custom Fields (Instance Type, Resource Group, Owner) populate correctly
