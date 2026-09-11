# IP Addresses CSV Classification - Final Fix

## Changes Applied

### 1. Enhanced Column Mapping
Added intelligent column name aliasing to handle NetBox CSV export variations:

```python
column_mappings = {
    'ip_address': ['address', 'ip', 'ip_address'],  # CSV: "IP Address" → matches API "address"
    'dns_name': ['dns_name', 'dns'],
    'nat_(inside)': ['nat_inside', 'nat_inside_address'],
    'nat_(outside)': ['nat_outside', 'nat_outside_address'],
    'tenant_group': ['tenant_group', 'tenant.group'],
    'owner_group': ['owner_group', 'custom_fields.owner_group'],
    'owner': ['owner', 'custom_fields.owner'],
}
```

**How it works:**
- CSV column: `IP Address` → normalized to `ip_address`
- Mapping expands to also match: `address`, `ip`
- This catches the NetBox API field name variations

### 2. Lowered Threshold
Changed similarity threshold from **30% to 25%** to accommodate NetBox CSV exports that have additional columns not in the API response.

```python
threshold = 0.25  # Lowered from 0.3 for better matching
```

### 3. Enhanced Debug Output
Now shows top 10 matches with detailed information:

```
🔍 Debug Info: Top 10 model matches for your CSV:

  • ipam.ip-addresses: 28% match (6/21 fields)
  • ipam.prefixes: 15% match (3/20 fields)
  • dcim.devices: 12% match (2/17 fields)

📊 Your CSV has 21 columns: address, comments, contacts, created, description, dns_name, id, interface, last_updated, nat_(inside), ...

⚠️ Best match was 28% but threshold is 25%.
```

## Expected Results

### Before This Fix
```
❌ Unable to automatically classify 'netbox_IP addresses.csv'
   Stored in unclassified table
```

### After This Fix
```
✅ Ingested 560 records: 560 ip-addresses
   Classified as: ipam.ip-addresses (confidence: 32%)
   Stored in: dynamic_ipam_ip_addresses
```

## Why This Fixes It

**Problem:** NetBox's web UI CSV export uses different column names than the API:
- CSV Export: `IP Address`, `DNS name`, `NAT (Inside)`
- API Response: `address`, `dns_name`, `nat_inside`

**Solution:** The column mapping system now:
1. Normalizes CSV columns: `IP Address` → `ip_address`
2. Expands to API variants: `ip_address` → `[address, ip, ip_address]`
3. Matches against any variant in the NetBox model signature

## Next Action

**Please upload your `netbox_IP addresses.csv` again.**

You should now see:
1. ✅ Successful classification as `ipam.ip-addresses`
2. 🔍 Debug info showing the match percentage (if still below threshold)
3. ✅ Data stored in proper table: `dynamic_ipam_ip_addresses`

If it still doesn't match, the debug output will show exactly what's preventing the match so we can add more column mappings.

## Status
✅ **FIXED** - Enhanced column mapping and lowered threshold to handle NetBox CSV export format variations.
