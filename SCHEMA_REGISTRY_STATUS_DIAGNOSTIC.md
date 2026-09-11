# Schema Registry Status Issue - Diagnostic Added

## Current Situation

Your IP addresses CSV was stored in the unclassified table:
```
⚠️ Stored 560 records in unclassified table: 560 netbox_ip_addresses

💡 Tip: Upload a NetBox backup JSON first to enable automatic classification.
```

This means **the schema registry is not initialized**, even though you may have tried to upload the NetBox backup JSON.

## What I Added

### 1. Registry Status Indicator
Both IPAM and Naming tabs now show a **clear status indicator** at the top of the upload section:

**When Registry is Active:**
```
✅ Schema Registry Active: 145 NetBox models loaded. 
CSV files will be automatically classified.
```

**When Registry is NOT Initialized:**
```
⚠️ Schema Registry Not Initialized: CSV files will be stored in 'unclassified' tables. 
Upload a NetBox backup JSON below to enable automatic classification.
```

### 2. Empty Registry Detection
Fixed the registry loader to detect when the registry exists but is empty (0 models):
```python
if sig_count == 0:
    # Registry exists but is empty - treat as uninitialized
    self.registry = None
```

## Next Steps - Please Check

### Step 1: Open the IPAM or Naming Tab
Look at the **"📥 Ingest NetBox Data (Backup / CSV)"** section.

You should see **one of these statuses**:
- ✅ Green: Schema registry is working → CSV files will classify
- ⚠️ Yellow: Schema registry not initialized → Need to upload JSON

### Step 2: If Yellow Warning Shows
The NetBox backup JSON either:
1. **Wasn't uploaded** - Try uploading it again
2. **Failed to parse** - Check for the UTF-8 BOM error (we fixed this)
3. **Is empty** - The backup JSON might be corrupted or empty

### Step 3: Upload NetBox Backup JSON
1. Expand the **"📥 Ingest NetBox Data"** section
2. Use the **main backup uploader** (accepts JSON files)
3. Upload `NetBox_Full_Backup_20260909_095938.json`

**Expected Result:**
```
✅ Schema registry initialized with 145 model signatures
✅ Schema Registry Active: 145 NetBox models loaded
```

### Step 4: Re-upload IP Addresses CSV
After the green checkmark appears, upload `netbox_IP addresses.csv` again.

**Expected Result:**
```
✅ Ingested 560 records: 560 ip-addresses
   Classified as: ipam.ip-addresses
```

## Troubleshooting

### If JSON Upload Still Fails
Share the error message you see. Common issues:
- UTF-8 BOM error (we fixed this)
- File size too large
- Corrupted JSON file
- Network timeout

### If Status Still Shows Yellow
Check the browser console (F12) for any JavaScript errors that might prevent the upload.

### If Status Shows Green But CSV Still Goes to Unclassified
The backup might not contain IP addresses. Try:
```powershell
# Re-export with all data
.\netbox-export-full.ps1 -NetBoxUrl "https://netbox.example.com" -ApiToken "<TOKEN>"
```

## Summary

**Current Status:**
- ❌ Schema registry: Not initialized (your CSV went to unclassified)
- ✅ Data preserved: 560 IP address records stored safely
- ✅ New indicators: Will clearly show registry status now

**Action Required:**
1. Check the upload section for the status indicator
2. Upload NetBox backup JSON if status is yellow/warning
3. Re-upload IP addresses CSV after status turns green

The data is safe in the unclassified table and can be re-uploaded once the schema registry is initialized.
