# Relationship Inference Testing Guide

## Problem Statement

When a user asks "show me VMs in host esagex11", the system should:
1. Find host `esagex11.example.corp`
2. Detect it's in cluster `CLS.AGE`
3. Query all VMs in cluster `CLS.AGE`
4. Return those VMs as the answer

## Recent Improvements

### 1. Enhanced Pattern Detection (`core/ai_relationship_inference.py`)

**Before**:
- Used keyword list matching (unreliable)
- Required exact keyword matches

**After**:
- Direct query string analysis
- Multiple pattern variations:
  - `' host '`, `'in host'`, `'on host'`, `'at host'`, `'from host'`
  - `'vm'`, `'vms'`, `'virtual machine'`, `'virtual-machine'`
  - Cluster formats: `'cls.'`, `'cls-'`, `'cluster'`

### 2. Improved VM Inference Logic

**Enhanced `infer_vms_from_host()`**:
- Method 1: Query ALL VMs (up to 500), filter by cluster
- Method 2: Flexible cluster name matching (case-insensitive, substring)
- Method 3: Fallback to search if direct query fails
- Debug logging for troubleshooting

### 3. Better Context Formatting

**New output format**:
```
=== RELATIONSHIP INFERENCE: VMs on Host 'esagex11' ===
✓ Host 'esagex11.example.corp' is member of cluster: CLS.AGE
✓ In VMware/vSphere, VMs are assigned to clusters, not individual hosts
✓ All VMs in cluster 'CLS.AGE' can run on any host in that cluster

** VMs in cluster 'CLS.AGE' (accessible via host 'esagex11') **
======================================================================
  • esagedc02
    Role: Domain Controller | IP: 10.27.177.224/24 | Platform: Windows Server | Status: Active
  • esagedp001
    Role: DHCP Server | IP: 10.27.177.225/24 | Platform: Windows Server | Status: Active
  ... (11 total)

** TOTAL VMs in cluster 'CLS.AGE': 11 **

IMPORTANT: These 11 VMs are running in cluster 'CLS.AGE' and are accessible via host 'esagex11'.
```

## Testing Steps

### Test 1: VM Query by Host Name
```
Query: "show me VMs in host esagex11"

Expected Flow:
1. Intent router identifies: dcim/devices + virtualization/virtual-machines
2. Search finds host: esagex11.example.corp
3. Extract cluster: CLS.AGE
4. Pattern match: vm_pattern=True, host_pattern=True
5. Trigger relationship inference
6. Query VMs in CLS.AGE
7. Return 11 VMs with detailed formatting

Expected Result:
✓ Shows all 11 VMs from cluster CLS.AGE
✓ Explains the cluster relationship
✓ Lists each VM with details
```

### Test 2: VM Query by Cluster Name
```
Query: "show me VMs in CLS.AGE"

Expected Flow:
1. Intent router identifies: virtualization/clusters + virtualization/virtual-machines
2. Direct cluster query
3. Return VMs in that cluster

Expected Result:
✓ Shows all 11 VMs from cluster CLS.AGE
```

### Test 3: Host Query by Cluster
```
Query: "show me hosts of cluster CLS.AGE"

Expected Flow:
1. Intent router identifies: virtualization/clusters + dcim/devices
2. Find cluster CLS.AGE
3. Pattern match: cluster_pattern=True, host_query_pattern=True
4. Query devices with cluster=CLS.AGE
5. Also show VM count for context

Expected Result:
✓ Shows hypervisor hosts in CLS.AGE
✓ Shows VM count (11 VMs)
```

## Verification Checklist

### Pattern Detection
- [x] Query string analysis instead of keyword list
- [x] Multiple variations for "host" (in host, on host, at host, from host)
- [x] Multiple variations for "vm" (vm, vms, virtual machine)
- [x] Host role detection (hypervisor, esx, device)

### VM Inference
- [x] Increased query limit (500 VMs)
- [x] Flexible cluster name matching
- [x] Case-insensitive comparison
- [x] Substring matching for cluster names
- [x] Multiple query methods (direct + search fallback)

### Context Building
- [x] Store primary_results for inference
- [x] Pass user_query to context builder
- [x] Call build_relationship_context()
- [x] Enhanced formatting with visual markers

### AI Instructions
- [x] Explicit instruction about cluster-host-VM relationships
- [x] Clear explanation of VMware architecture
- [x] Guidance to use relationship inference section

## Debugging

If inference still doesn't work:

1. **Check if results are found**:
   - Look for "Exact Match Results" in context
   - Verify host has `cluster` field populated

2. **Check pattern matching**:
   - Query: "show me VMs in host esagex11"
   - vm_pattern should be True (contains "vm")
   - host_pattern should be True (contains "in host")

3. **Check VM query**:
   - Verify `virtualization_virtual_machines` object type exists in backup
   - Check cluster name format matches exactly

4. **Enable debug output**:
   - Look for "DEBUG: Error in infer_vms_from_host" messages
   - Check if VMs are returned but not displayed

## Common Issues and Solutions

### Issue 1: No VMs returned despite cluster match
**Cause**: Cluster name mismatch (e.g., "CLS.AGE" vs "cls.age" vs "CLS AGE")
**Solution**: Now using flexible substring matching with case-insensitive comparison

### Issue 2: Pattern not detected
**Cause**: Keywords list not populated correctly
**Solution**: Now analyzing query string directly instead of keywords list

### Issue 3: Results found but not shown to user
**Cause**: Relationship context not passed to AI
**Solution**: Enhanced context formatting with clear markers and explicit instructions

### Issue 4: Host found but cluster field empty
**Cause**: Data not loaded correctly from backup
**Solution**: Check raw_data field as fallback

## Manual Verification

To manually verify the data:

1. **Check if host has cluster**:
   ```
   Query: "show me device esagex11"
   Look for: Cluster: CLS.AGE in output
   ```

2. **Check if VMs exist**:
   ```
   Query: "show me VMs in cluster CLS.AGE"
   Should return: 11 VMs
   ```

3. **Compare results**:
   - If "VMs in cluster CLS.AGE" works but "VMs in host esagex11" doesn't
   - Then issue is in the relationship inference
   - Check primary_results has the host device

## Expected vs Actual

### Before Fix
```
Query: "show me VMs in host esagex11"
Result: "I can see the hypervisor record for esagex11.example.corp, but the 
         provided NetBox data does not include a VM-to-host assignment list..."
❌ Missing the inference logic
```

### After Fix
```
Query: "show me VMs in host esagex11"
Result: 
=== RELATIONSHIP INFERENCE: VMs on Host 'esagex11' ===
✓ Host 'esagex11.example.corp' is member of cluster: CLS.AGE
...
** TOTAL VMs in cluster 'CLS.AGE': 11 **
✓ Inference working correctly
```

## Files Modified

1. `core/ai_relationship_inference.py`
   - Enhanced pattern detection (line ~186-220)
   - Improved VM inference logic (line ~16-95)
   - Better context formatting with visual markers

2. `core/ai_two_pass_system.py`
   - Pass user_query to context builder
   - Store results for inference
   - Import relationship inference module
   - Enhanced AI instructions

## Next Steps for Full Resolution

If after these changes the system still doesn't infer correctly:

1. Add explicit logging to see which code path is taken
2. Verify the primary_results list contains the host device
3. Check if build_relationship_context is actually called
4. Verify the relationship context is added to context_parts
5. Ensure the AI model respects the relationship inference section

The key insight is: **In VMware/vSphere, VMs are assigned to clusters, not individual hosts**. The system now understands this architectural relationship and automatically infers VMs from the host's cluster membership.
