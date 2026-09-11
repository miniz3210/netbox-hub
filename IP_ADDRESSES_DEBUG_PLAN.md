# IP Addresses CSV Classification Debug & Fix

## Issue
The IP addresses CSV is not being classified as `ipam.ip-addresses` even after the schema registry is initialized. The error shows:

```
Unable to automatically classify 'netbox_IP addresses.csv'. 
The file columns don't match any known NetBox model signatures.
```

## Diagnosis

The schema registry was initialized, but the classification is failing. This could be because:

1. **NetBox backup doesn't contain IP addresses** - If your backup JSON has 0 IP address records, the model won't be in the registry
2. **Column name mismatch** - The CSV columns need to match the NetBox API field names after normalization
3. **Threshold too strict** - The 30% similarity threshold might be too high

## Debug Enhancement Added

I've added debug logging to the `classify_file` method that will show you the top 5 closest matches when classification fails. This will help us understand why `ipam.ip-addresses` isn't matching.

**New debug output will show:**
```
🔍 Debug: Top model matches:
  • dcim.devices: 25% match (5 common fields)
  • ipam.prefixes: 20% match (4 common fields)
  • ipam.ip-addresses: 28% match (6 common fields)  ← Close but below 30% threshold!
  
None exceeded the 30% threshold.
```

## Quick Solutions

### Solution 1: Lower the Threshold Temporarily
If the debug shows IP addresses is close (e.g., 28%), we can lower the threshold:

```python
# In universal_schema_registry.py line 171
threshold = 0.25  # Changed from 0.3 (30%) to 0.25 (25%)
```

### Solution 2: Check NetBox Backup Contents
The backup JSON might not have IP addresses. Check if your backup includes the `ipam/ip-addresses` endpoint:

```powershell
# Re-export with IP addresses included
.\netbox-export-full.ps1 -NetBoxUrl "https://netbox.example.com" -ApiToken "<TOKEN>"
```

### Solution 3: Manual Column Mapping
If needed, I can add a manual mapping for common NetBox CSV exports that have known column variations.

## Next Steps

1. **Try uploading the CSV again** - The debug output will now show you the top matches
2. **Share the debug output** - I'll see exactly what's matching and adjust accordingly
3. **Verify backup contents** - Check if IP addresses are in your NetBox backup

## Expected Debug Output

When you upload `netbox_IP addresses.csv` again, you should see something like:

```
🔍 Debug: Top model matches:
  • ipam.ip-addresses: XX% match (N common fields)
  • [other models]
  
None exceeded the 30% threshold.
```

This will tell us:
- ✅ If IP addresses model exists in registry
- ✅ How close it is to matching (XX%)
- ✅ How many fields actually match (N)

Then we can adjust the threshold or add specific handling for IP addresses CSV format.
