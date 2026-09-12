# Intelligent File Classification System

## Overview

The NetBox Hub Universal Uploader now features a **future-proof, multi-layered classification system** that can automatically handle ANY NetBox CSV/Excel export, even models that have never been seen before.

## Classification Strategy (4 Layers)

### Layer 1: Schema Registry (Highest Accuracy)
- **Source**: Learned from uploaded NetBox backup JSON
- **Threshold**: 25% column match
- **Coverage**: All models present in the backup JSON
- **Confidence**: 80-100%

When you upload a NetBox backup JSON, the system dynamically learns ALL model signatures and can perfectly classify any CSV export from those models.

### Layer 2: Heuristic Pattern Matching
- **Source**: Pre-defined patterns for common NetBox models
- **Threshold**: 30% confidence
- **Coverage**: 10+ common models (IPAM, DCIM, Virtualization, Tenancy, Users)
- **Confidence**: 50-70%

Hardcoded patterns for the most common NetBox exports:
- `ipam/ip-addresses` - IP addresses
- `ipam/prefixes` - Subnets/prefixes
- `ipam/vlans` - VLANs
- `dcim/sites` - Sites
- `dcim/devices` - Devices
- `virtualization/virtual-machines` - VMs
- `users/groups`, `users/users` - User management
- `tenancy/tenants`, `tenancy/tenant-groups` - Tenancy

### Layer 3: Intelligent Fallback (Multi-Strategy)

#### Strategy 3.1: Filename-Based Classification
Maps filename patterns to NetBox endpoints:
- `netbox_devices.csv` → `dcim/devices`
- `netbox_IP addresses.csv` → `ipam/ip-addresses`
- `netbox_virtual_machines.csv` → `virtualization/virtual-machines`

**Coverage**: 25+ filename patterns

#### Strategy 3.2: Common NetBox Field Patterns
Recognizes distinctive field combinations:
- `id` + `name` + `ip` → `ipam/ip-addresses`
- `id` + `name` + `device_type` → `dcim/devices`
- `vid` + `name` → `ipam/vlans`
- `username` / `email` → `users/users`
- `asn` → `ipam/asns`
- `circuit_id` → `circuits/circuits`

#### Strategy 3.3: Fuzzy Registry Matching
- **Threshold**: Lowered to 15% (from 25%)
- Finds the closest match even with partial column overlap

### Layer 4: Graceful Unclassified Storage (Never Reject)
- **Fallback**: Always stores files, never rejects them
- **Storage**: `unclassified/{filename}` tables
- **Queryable**: AI assistant can still access the data
- **User Notification**: Clear warning with file details

## Examples

### Example 1: Standard NetBox Export (Layer 1)
```
File: netbox_devices.csv
Columns: Name, Device Type, Site, Status, Role, ...
Result: ✅ Classified as dcim/devices (92% confidence, schema registry)
```

### Example 2: IP Addresses (Layer 2)
```
File: netbox_IP addresses.csv
Columns: IP Address, VRF, Status, Role, Tenant, ...
Result: ✅ Classified as ipam/ip-addresses (65% confidence, heuristic)
```

### Example 3: Custom/Unusual Export (Layer 3)
```
File: my_custom_export.csv
Columns: id, name, serial, rack_position, ...
Result: ✅ Classified as dcim/devices (45% confidence, filename + field patterns)
```

### Example 4: Unknown Model (Layer 4)
```
File: netbox_custom_model.csv
Columns: foo, bar, baz
Result: ⚠️ Stored as unclassified/custom_model (queryable by AI)
```

## Benefits

### 1. **Future-Proof**
- Automatically handles new NetBox models via JSON introspection
- No code changes needed for new NetBox versions
- Self-learning from backup JSON uploads

### 2. **Never Rejects Files**
- Every file is stored and usable
- No frustrating "file not supported" errors
- AI assistant can query all uploaded data

### 3. **Intelligent Deduplication**
- Files classified to the same model are merged
- "Latest upload wins" strategy for conflicts
- Prevents duplicate data from JSON + CSV uploads

### 4. **User-Friendly**
- Clear feedback on classification results
- Confidence scores show classification quality
- Helpful suggestions when low confidence

## Usage Tips

### For Best Results:
1. **Upload NetBox backup JSON first** - Initialize the schema registry
2. **Then upload CSV files** - They'll be classified using learned signatures
3. **Use standard NetBox export filenames** - Helps filename-based classification
4. **Include common columns** - `id`, `name`, `status` improve matching

### Troubleshooting:
- **Low confidence warning?** → Upload the NetBox backup JSON to improve accuracy
- **Stored as unclassified?** → Check if it's a valid NetBox export; the AI can still query it
- **Duplicate entries?** → Clear database and re-upload in order: JSON first, then CSVs

## Technical Details

### Model Key Normalization
- Uses NetBox API endpoint format: `app/model` (e.g., `ipam/ip-addresses`)
- Consistent with backup JSON structure
- Enables automatic deduplication

### Table Naming
- Dynamic tables: `dynamic_ipam_ip_addresses`
- Unclassified tables: `dynamic_unclassified_filename`
- Metadata columns: `_source_file`, `_uploaded_at`, `_model_key`

### Primary Key Detection
Priority order: `id` → `vid` → `slug` → `name` → `pk` → first column

## Future Enhancements

Potential improvements for even better classification:
- [ ] Machine learning-based classification
- [ ] User-defined custom mappings
- [ ] Column name aliasing (e.g., "Device Name" → "name")
- [ ] Multi-file relationship detection
- [ ] Validation against NetBox API schema

---

Last Updated: 2026-09-11
Version: 2.0 (Intelligent Multi-Layer Classification)
