# Dynamic Field System for NetBox Hub

## Overview

The NetBox Hub dynamic field system automatically discovers and adapts to NetBox schema changes without requiring code modifications. When NetBox versions update (adding new native fields/objects) or when custom fields are added, the UI and data processing adapt automatically.

## Architecture

### Core Components

1. **Field Registry** (`core/field_registry.py`)
   - Stores field definitions in `netbox_schema` database table
   - Auto-discovers fields from NetBox backup JSON
   - Provides configuration interface for field visibility
   - Caches field specs for performance

2. **Dynamic Field Helper** (`core/dynamic_field_helper.py`)
   - Utilities for CSV import field mapping
   - Custom field validation
   - Field discovery and extraction

3. **UI Field Builder** (`ui/dynamic_fields.py`)
   - Builds UI displays from dynamic field registry
   - Formats field values consistently
   - Generates object summaries

4. **Field Manager CLI** (`field_manager.py`)
   - Command-line tool for viewing and managing fields
   - Add custom fields manually
   - Configure display fields

## How It Works

### Automatic Schema Discovery

When you upload a NetBox backup JSON:

1. The system analyzes the JSON structure
2. Extracts native fields from sample records
3. Discovers custom field definitions from `extras_custom_fields`
4. Maps custom fields to applicable object types
5. Stores everything in the `netbox_schema` table
6. Selects intelligent default display fields

### Field Storage

Fields are stored in the `netbox_schema` table with this structure:

```sql
CREATE TABLE netbox_schema (
    id INTEGER PRIMARY KEY,
    object_type TEXT UNIQUE,          -- e.g., "virtualization_virtual_machines"
    field_config TEXT,                 -- JSON with field definitions
    netbox_version TEXT,               -- NetBox version (e.g., "3.7.0")
    is_enabled INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT
)
```

The `field_config` JSON contains:

```json
{
  "native_fields": {
    "name": {"name": "name", "label": "Name", "type": "text"},
    "status": {"name": "status", "label": "Status", "type": "text"}
  },
  "relationships": {
    "site": {"name": "site", "label": "Site", "type": "object", "is_relationship": true}
  },
  "custom_fields": {
    "owner": {
      "name": "owner",
      "label": "Owner",
      "type": "select",
      "choice_set": "Owner Set",
      "required": false
    }
  },
  "display_fields": [
    ["Name", "name"],
    ["Status", "status"],
    ["Site", "site"],
    ["Owner", "owner"]
  ]
}
```

## Usage

### For End Users

#### View Discovered Object Types

```bash
python field_manager.py list
```

Output:
```
Object Type                              Fields     Custom   Version      Updated
========================================================================================
✓ virtualization_virtual_machines        15         3        3.7.0        2026-09-09
✓ dcim_sites                            12         0        3.7.0        2026-09-09
✓ ipam_prefixes                         18         2        3.7.0        2026-09-09
```

#### View Fields for an Object Type

```bash
python field_manager.py show virtualization_virtual_machines
```

#### Add a Custom Field Manually

```bash
python field_manager.py add virtualization_virtual_machines \
    --field backup_status \
    --label "Backup Status" \
    --type select \
    --choice-set "Backup Status Set"
```

#### Update Display Fields

```bash
python field_manager.py update virtualization_virtual_machines \
    --fields "Name:name,Status:status,Site:site,Owner:owner,Backup:backup_status"
```

#### Export Schema

```bash
python field_manager.py export --output schema_backup.json
```

### For Developers

#### Using FieldRegistry

```python
from core.field_registry import FieldRegistry
from core.db_manager_wrapper import DatabaseManager

# Initialize
registry = FieldRegistry(DatabaseManager())

# Get display fields for UI
display_fields = registry.get_field_spec("virtualization_virtual_machines")
# Returns: [("Name", "name"), ("Status", "status"), ...]

# Get custom field definition
field_def = registry.get_custom_field_definition(
    "virtualization_virtual_machines", 
    "owner"
)
# Returns: {"name": "owner", "label": "Owner", "type": "select", ...}

# Add custom field programmatically
registry.add_custom_field_manually(
    object_type="virtualization_virtual_machines",
    field_name="backup_enabled",
    field_label="Backup Enabled",
    field_type="boolean"
)
```

#### Using DynamicFieldHelper

```python
from core.dynamic_field_helper import get_field_helper

helper = get_field_helper()

# Get all custom fields for VMs
custom_fields = helper.get_custom_fields_for_object("virtualization_virtual_machines")

# Map CSV headers to custom fields
csv_headers = ["Name", "Status", "cf_owner", "tag_application"]
mapping = helper.map_csv_to_custom_fields(csv_headers)
# Returns: {"cf_owner": "owner", "tag_application": "application"}

# Extract custom fields from flat dict
data = {
    "name": "vm01",
    "status": "active",
    "owner": "IT Team",
    "resource_group": "Production"
}
custom = helper.extract_custom_fields_from_dict(data)
# Returns: {"owner": "IT Team", "resource_group": "Production"}

# Validate custom field value
valid, error = helper.validate_custom_field_value(
    "virtualization_virtual_machines",
    "owner",
    "IT Team"
)
```

#### Using UIFieldBuilder

```python
from ui.dynamic_fields import get_ui_builder

builder = get_ui_builder()

# Get display fields
display_fields = builder.get_display_fields("virtualization_virtual_machines")

# Build summary text for an object
vm_object = {
    "name": "vm01",
    "status": "active",
    "site": {"name": "DC1"},
    "custom_fields": {"owner": "IT Team"}
}
summary = builder.build_summary_text("virtualization_virtual_machines", vm_object)
# Returns: "Status: active | Site: DC1 | Owner: IT Team"

# Get field value safely
owner = builder.get_field_value_safe(vm_object, "owner", default="Unknown")
```

## Migration

For existing installations:

```bash
# Run migration
python migrate_dynamic_fields.py

# Check if migration needed (without running)
python migrate_dynamic_fields.py --check

# Rollback (for testing)
python migrate_dynamic_fields.py --rollback
```

The migration:
1. Creates the `netbox_schema` table
2. Preserves existing data
3. Prepares system for automatic discovery on next backup upload

## Integration Points

### Backup Manager

**File:** `core/backup_manager.py`

- `save_netbox_backup()` automatically triggers schema discovery at line 833
- `_summarize()` uses dynamic field specs at line 413

### Azure VM Importer

**File:** `core/azure_vm_importer.py`

Use `DynamicFieldHelper` to map CSV columns to custom fields:

```python
from core.dynamic_field_helper import get_field_helper

helper = get_field_helper()
mapping = helper.map_csv_to_custom_fields(csv_headers)
```

### UI Components

**Files:** `ui/tabs/*.py`, `ui/components.py`

Use `UIFieldBuilder` for displaying objects:

```python
from ui.dynamic_fields import get_ui_builder

builder = get_ui_builder()
summary = builder.build_summary_text(object_type, obj_data)
```

## Benefits

### No Code Changes Required

When NetBox adds new fields or you add custom fields:
- ✅ Upload new backup → fields discovered automatically
- ✅ UI adapts to show new fields
- ✅ Importers recognize new custom fields
- ✅ Validation rules applied automatically
- ❌ No code changes needed
- ❌ No restart required

### Version Compatibility

Track which NetBox version each schema came from:

```python
object_types = registry.get_all_object_types()
for obj in object_types:
    print(f"{obj['object_type']}: NetBox {obj['netbox_version']}")
```

### User Control

Users can customize which fields appear in the UI:

```bash
# Show only the fields you care about
python field_manager.py update virtualization_virtual_machines \
    --fields "Name:name,Owner:owner,Environment:environment"
```

### CSV Import Intelligence

The system automatically maps CSV columns to custom fields:

```
CSV Header        → Custom Field
cf_owner          → owner
tag_application   → application
custom_backup     → backup
```

## API Reference

### FieldRegistry Methods

- `discover_schema_from_backup(backup_data, netbox_version)` - Auto-discover from JSON
- `get_field_spec(object_type)` - Get display fields
- `get_all_object_types()` - List all discovered types
- `get_custom_field_definition(object_type, field_name)` - Get field metadata
- `add_custom_field_manually(...)` - Add field without backup
- `update_display_fields(object_type, fields)` - Configure UI display

### DynamicFieldHelper Methods

- `get_custom_fields_for_object(object_type)` - Get all custom fields
- `map_csv_to_custom_fields(headers, object_type)` - Auto-map CSV columns
- `extract_custom_fields_from_dict(data, object_type)` - Extract from flat dict
- `validate_custom_field_value(object_type, field_name, value)` - Validate value
- `add_discovered_field(object_type, field_name, ...)` - Add from discovery

### UIFieldBuilder Methods

- `get_display_fields(object_type)` - Get UI field list
- `format_field_value(obj, field_path)` - Format single value
- `build_summary_text(object_type, obj)` - Build object summary
- `get_field_value_safe(obj, field_name, default)` - Safe field access

## Performance

The field registry uses in-memory caching:
- First access: ~10ms (database query)
- Cached access: <1ms
- Cache invalidates on schema updates

## Troubleshooting

### Schema Not Discovered

Check backup upload result:
```python
result = save_netbox_backup(file_bytes, filename)
print(result.get('schema_discovery'))
```

### Missing Custom Fields

Verify custom field definitions exist in backup:
```bash
# Check backup JSON contains extras_custom_fields
jq '.extras_custom_fields | length' backup.json
```

### Field Not Showing in UI

Check display field configuration:
```bash
python field_manager.py show virtualization_virtual_machines
```

Update if needed:
```bash
python field_manager.py update virtualization_virtual_machines \
    --fields "Name:name,YourField:your_field"
```

## Future Enhancements

Potential additions:
- Field validation rules (regex, ranges)
- Field dependencies and conditionals
- Bulk field import from schema file
- Field usage analytics
- API endpoint for external tools
- Webhook notifications on schema changes

## See Also

- NetBox Custom Fields: https://docs.netbox.dev/en/stable/customization/custom-fields/
- NetBox API: https://docs.netbox.dev/en/stable/integrations/rest-api/
