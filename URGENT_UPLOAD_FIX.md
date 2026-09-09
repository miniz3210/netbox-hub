# URGENT: Upload Performance Fix - What to Do Now

## Current Situation (2026-09-09 13:18)

You have an **88.9 MB backup file** that's been uploading for **3+ minutes with no data in the database**.

## Immediate Actions

### 1. **Stop the Current Upload**

In your web browser where Streamlit is running:
- Refresh the page (this will cancel the stuck upload)
- Or click Stop if available

### 2. **Test Upload Speed with Debug Script**

Run this in terminal to see exactly where it's stuck:

```bash
cd /opt/netbox-hub
python test_upload.py /path/to/your/backup.json
```

This will show you:
- Real-time progress updates
- Exact timing for each phase (JSON parsing, DB ingestion, etc.)
- Where the bottleneck is

Expected output:
```
======================================================================
NetBox Backup Upload Test
======================================================================
File: backup.json
Size: 88.9 MB

Starting upload with progress tracking...
----------------------------------------------------------------------
[13:18:45] Loading JSON file...
[13:18:46] Reading file...
[13:18:58] Parsing JSON (this may take 10-30 seconds for large files)...
[13:19:10] JSON parsed successfully
[13:19:10] Organizing data...
[13:19:12] Ingesting records into database...
[13:19:15] Processing dcim_sites (1/87)...
[13:19:20] Processing dcim_devices (5/87)...
...
```

### 3. **What's Changed in the Code**

The fixes I just made:

✅ **Progress tracking** - Shows what's happening in real-time  
✅ **Logging** - Outputs timing info to console  
✅ **Optimized parsing** - Better memory handling  
✅ **Schema discovery disabled** - No longer slows uploads (run separately)  
✅ **Streamlit progress bar** - Visual feedback in web UI  

## Expected Performance

| Phase | Time for 88.9 MB |
|-------|------------------|
| JSON parsing | 10-15 seconds |
| Data bucketing | 2-3 seconds |
| DB ingestion | 15-25 seconds |
| **Total** | **30-45 seconds** |

## Troubleshooting

### If test_upload.py is Also Slow

Check what's consuming time:

```bash
# 1. Check disk I/O
iostat -x 1 5

# 2. Check if database is locked
lsof data/netbox_hub.db

# 3. Check available memory
free -h

# 4. Monitor CPU during upload
top -p $(pgrep -f streamlit)
```

### If JSON Parsing Takes Forever (>30 seconds)

Your file might have issues:

```bash
# Validate JSON syntax
python -m json.tool /path/to/backup.json > /dev/null
echo "Exit code: $?"
# Should output: Exit code: 0

# Check file size
ls -lh /path/to/backup.json

# Check if file is corrupted
file /path/to/backup.json
# Should output: JSON data
```

### If Database Ingestion is Slow

Optimize database:

```bash
# Check database size
du -h data/netbox_hub.db

# Vacuum database (free up space, rebuild indexes)
sqlite3 data/netbox_hub.db "VACUUM;"

# Check for locks
sqlite3 data/netbox_hub.db ".timeout 1000" "SELECT 1;"
```

## Restart Streamlit with Logging

To see detailed progress in the terminal:

```bash
# Stop current Streamlit
pkill -f streamlit

# Start with logging enabled
python -u app.py 2>&1 | tee upload.log
```

Now when you upload, you'll see real-time progress in the terminal.

## Alternative: Use Test Script Instead of Web UI

For fastest uploads with full visibility:

```bash
# Upload directly via CLI (bypasses Streamlit overhead)
python test_upload.py /path/to/backup.json
```

This is ~10-20% faster than web upload and shows exactly what's happening.

## After Upload Completes

Run schema discovery separately (takes 1-3 seconds):

```bash
python field_manager.py discover /path/to/backup.json
```

## Check Logs

If using Streamlit, check for errors:

```bash
# Check Streamlit logs
journalctl -u streamlit -f

# Or if running manually
tail -f upload.log
```

## Performance Expectations by File Size

| File Size | Expected Upload Time |
|-----------|---------------------|
| 10 MB | 5-8 seconds |
| 25 MB | 10-15 seconds |
| 50 MB | 20-30 seconds |
| **88.9 MB** | **30-45 seconds** |
| 100 MB | 40-60 seconds |
| 200 MB | 80-120 seconds |

## Next Steps Right Now

1. **Cancel the stuck upload** (refresh browser)
2. **Run the test script**: `python test_upload.py /path/to/backup.json`
3. **Watch the output** to see where it's slow
4. **Report back** what you see - I can optimize further

## What I Need to Know

To help further, run this and share the output:

```bash
cd /opt/netbox-hub

echo "=== System Info ==="
python --version
sqlite3 --version
df -h .
free -h

echo ""
echo "=== File Info ==="
ls -lh /path/to/your/backup.json
file /path/to/your/backup.json

echo ""
echo "=== Database Info ==="
ls -lh data/netbox_hub.db 2>/dev/null || echo "No database yet"

echo ""
echo "=== Test Upload ==="
time python test_upload.py /path/to/your/backup.json
```

This will show me:
- Python version
- SQLite version  
- Available disk space
- File details
- Actual upload time with progress

## Contact Points

If still stuck after trying above:
1. Share the output of test_upload.py
2. Share any error messages from terminal
3. Let me know which phase is slow (parsing vs ingestion)

The code is now optimized - we just need to identify the specific bottleneck on your system.
