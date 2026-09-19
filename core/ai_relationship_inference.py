"""
AI Relationship Inference Engine

Infers implicit relationships from NetBox data to provide intelligent context.

Key relationships:
1. Cluster → Hosts → VMs
   - When querying VMs in a host, infer from cluster membership
   - When querying hosts, show related VMs from the cluster

2. Device → Interfaces → IP Addresses
   - When querying a device, include its interfaces and IPs

3. Site → All resources
   - Devices, VMs, prefixes, VLANs scoped to site

This solves problems like:
- User asks "show VMs in host pwsesx001"
- Host pwsesx001 is in cluster CLS.CV
- VMs are assigned to clusters, not individual hosts
- System should infer: VMs in CLS.CV = VMs on pwsesx001
"""

import json
from typing import Dict, List, Any, Optional
from core.backup_manager import search_backup_records, get_backup_records_by_type


def _extract_cluster_name(host_data: Dict[str, Any]) -> Optional[str]:
    """
    Extract cluster name from host data with comprehensive fallback logic.

    Attempts extraction from:
    1. Top-level 'cluster' field (string or dict)
    2. 'raw_data' JSON blob containing cluster info
    3. String representation of raw_data parsed as serialized JSON
    4. Nested fields within raw_data (e.g. 'cluster.name', 'cluster_group')

    Args:
        host_data: Host device record from NetBox backup

    Returns:
        Cluster name string or None if not found
    """
    if not isinstance(host_data, dict):
        return None

    # Attempt 1: top-level 'cluster' field
    cluster_val = host_data.get('cluster')
    if isinstance(cluster_val, str) and cluster_val.strip():
        return cluster_val.strip()
    if isinstance(cluster_val, dict):
        name = cluster_val.get('name') or cluster_val.get('display')
        if name:
            return str(name).strip()

    # Attempt 2: raw_data dict with nested cluster
    raw = host_data.get('raw_data')
    if isinstance(raw, dict):
        cluster_obj = raw.get('cluster')
        if isinstance(cluster_obj, dict):
            name = cluster_obj.get('name') or cluster_obj.get('display')
            if name:
                return str(name).strip()
        if isinstance(cluster_obj, str) and cluster_obj.strip():
            return cluster_obj.strip()

    # Attempt 2b: raw_data is a JSON-encoded string containing cluster info
    if isinstance(raw, str) and len(raw) > 10:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            cluster_obj = parsed.get('cluster')
            if isinstance(cluster_obj, dict):
                name = cluster_obj.get('name') or cluster_obj.get('display')
                if name:
                    return str(name).strip()
            if isinstance(cluster_obj, str) and cluster_obj.strip():
                return cluster_obj.strip()

        # Attempt 2c: regex scan raw string for JSON-like cluster info
        import re
        patterns = [
            r'"cluster"\s*:\s*\{\s*"name"\s*:\s*"([^"]+)"',
            r'"cluster"\s*:\s*"([^"]+)"',
        ]
        for pat in patterns:
            m = re.search(pat, raw)
            if m:
                return m.group(1).strip()

    return None


def infer_vms_from_host(host_name: str, host_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Infer VMs running on a host by looking at the host's cluster membership.
    
    Logic:
    1. Get the cluster assigned to the host using _extract_cluster_name
    2. Query all VMs in that cluster via direct lookup and search
    3. Return VMs as if they were directly queried from the host
    
    Args:
        host_name: Name or identifier of the host device
        host_data: Host device record from backup
    
    Returns:
        List of VM records inferred to be running on this host
    """
    vms = []
    
    cluster_name = _extract_cluster_name(host_data)
    if not cluster_name:
        return []
    
    cluster_clean = cluster_name.lower().strip()
    
    try:
        # Method 1: Direct query for all VMs, then filter by cluster
        all_vms = get_backup_records_by_type(
            object_type='virtualization_virtual_machines',
            site=None,
            limit=500
        )
        
        if all_vms:
            for vm in all_vms:
                vm_cluster = vm.get('cluster', '')
                if not vm_cluster:
                    continue
                vm_cluster_clean = vm_cluster.lower().strip()
                # Case-insensitive and substring matching: either direction
                if cluster_clean in vm_cluster_clean or vm_cluster_clean in cluster_clean:
                    vms.append(vm)
                elif any(
                    word in cluster_clean and word in vm_cluster_clean
                    for word in cluster_clean.replace('.', ' ').replace('-', ' ').split()
                    if len(word) > 3
                ):
                    vms.append(vm)
        
        # Method 2: Search with cluster as identifier (fallback)
        if not vms:
            vm_results = search_backup_records(
                identifiers=[cluster_name],
                keywords=['virtual', 'machine'],
                site=None,
                limit=200
            )
            
            for vm in vm_results:
                obj_type = vm.get('object_type', '')
                if 'virtual_machine' in obj_type or 'vm' in obj_type:
                    vm_cluster = vm.get('cluster', '')
                    if vm_cluster and cluster_clean in vm_cluster.lower():
                        vms.append(vm)
    
    except Exception as e:
        import traceback
        print(f"DEBUG: Error in infer_vms_from_host: {e}")
        print(traceback.format_exc())
    
    return vms


def infer_hosts_from_cluster(cluster_name: str) -> List[Dict[str, Any]]:
    """
    Infer hosts (ESXi servers) that belong to a cluster.
    
    Logic:
    1. Query devices with role "Hypervisor" or similar
    2. Filter to devices assigned to the specified cluster
    
    Args:
        cluster_name: Name of the cluster
    
    Returns:
        List of host device records
    """
    hosts = []
    
    try:
        # Query devices with hypervisor role
        device_results = search_backup_records(
            identifiers=[cluster_name],
            keywords=['hypervisor', 'esx', 'host'],
            site=None,
            limit=50
        )
        
        for device in device_results:
            device_cluster = device.get('cluster', '')
            if device_cluster and cluster_name.lower() in device_cluster.lower():
                hosts.append(device)
    
    except Exception as e:
        pass
    
    return hosts


def infer_cluster_from_identifier(identifier: str) -> Optional[str]:
    """
    When user mentions a host or VM, try to infer its cluster.
    
    Args:
        identifier: Host name, VM name, or cluster name
    
    Returns:
        Cluster name if found, None otherwise
    """
    try:
        # Search for the identifier
        results = search_backup_records(
            identifiers=[identifier],
            keywords=[],
            site=None,
            limit=5
        )
        
        for result in results:
            cluster = result.get('cluster')
            if cluster:
                return cluster
    
    except Exception as e:
        pass
    
    return None


def build_relationship_context(
    query: str,
    identifiers: List[str],
    keywords: List[str],
    primary_results: List[Dict[str, Any]]
) -> str:
    """
    Build additional context based on inferred relationships.
    
    This enhances the primary results with related data that the user
    likely wants to see based on the query pattern.
    
    Args:
        query: Original user query
        identifiers: Extracted identifiers (host names, cluster names, etc.)
        keywords: Extracted keywords
        primary_results: Results from primary search
    
    Returns:
        Additional context string with inferred relationships
    """
    context_parts = []
    
    query_lower = query.lower()
    
    # Convert keywords list to lowercase strings for matching
    keywords_lower = [k.lower() for k in keywords] if keywords else []
    
    # Pattern 1: "VMs in/on host X" - MOST IMPORTANT
    # Detect: "vm", "virtual machine", "vms" + "host", "esx", "hypervisor"
    vm_pattern = any(word in query_lower for word in ['vm', 'vms', 'virtual machine', 'virtual-machine'])
    host_pattern = any(word in query_lower for word in [' host ', 'in host', 'on host', 'esx', ' at host', 'from host'])
    
    if vm_pattern and host_pattern:
        # User is asking for VMs on a host
        # Find host in primary results - could be a device with hypervisor role
        for result in primary_results:
            obj_type = result.get('object_type', '')
            device_role = result.get('device_role', '').lower() if result.get('device_role') else ''
            role = result.get('role', '').lower() if result.get('role') else ''
            
            # Check if this is a hypervisor/host device
            is_host = ('device' in obj_type) or ('hypervisor' in device_role) or ('hypervisor' in role)
            
            if is_host:
                host_name = result.get('name', '')
                cluster = result.get('cluster', '')
                
                if cluster:
                    context_parts.append(f"\n=== RELATIONSHIP INFERENCE: VMs on Host '{host_name}' ===")
                    context_parts.append(f"✓ Host '{host_name}' is member of cluster: {cluster}")
                    context_parts.append(f"✓ In VMware/vSphere, VMs are assigned to clusters, not individual hosts")
                    context_parts.append(f"✓ All VMs in cluster '{cluster}' can run on any host in that cluster")
                    
                    # Infer VMs from cluster
                    vms = infer_vms_from_host(host_name, result)
                    
                    if vms:
                        context_parts.append(f"\n** VMs in cluster '{cluster}' (accessible via host '{host_name}') **")
                        context_parts.append(f"{'='*70}")
                        for vm in vms:
                            vm_name = vm.get('name', 'N/A')
                            vm_role = vm.get('device_role', vm.get('role', '-'))
                            vm_ip = vm.get('primary_ip', '-')
                            vm_status = vm.get('status', '-')
                            vm_platform = vm.get('platform', '-')
                            context_parts.append(
                                f"  • {vm_name}"
                            )
                            context_parts.append(f"    Role: {vm_role} | IP: {vm_ip} | Platform: {vm_platform} | Status: {vm_status}")
                        context_parts.append(f"\n** TOTAL VMs in cluster '{cluster}': {len(vms)} **")
                        context_parts.append(f"\nIMPORTANT: These {len(vms)} VMs are running in cluster '{cluster}' and are accessible via host '{host_name}'.")
                    else:
                        context_parts.append(f"\n⚠ No VMs found in cluster '{cluster}' (cluster may be empty or data not loaded)")
    
    # Pattern 2: "hosts in cluster X" or "show hosts of cluster X"
    cluster_pattern = any(word in query_lower for word in ['cluster', 'cls.', 'cls-'])
    host_query_pattern = any(word in query_lower for word in ['host', 'hosts', 'esx', 'hypervisor'])
    
    if cluster_pattern and host_query_pattern and not vm_pattern:
        # User is asking for hosts in a cluster
        # Find cluster in primary results
        for result in primary_results:
            obj_type = result.get('object_type', '')
            if 'cluster' in obj_type:
                cluster_name = result.get('name', '')
                
                context_parts.append(f"\n=== RELATIONSHIP INFERENCE: Hosts in Cluster '{cluster_name}' ===")
                
                # Also show VMs for context
                vms = []
                try:
                    all_vms = get_backup_records_by_type(
                        object_type='virtualization_virtual_machines',
                        site=None,
                        limit=100
                    )
                    for vm in all_vms:
                        vm_cluster = vm.get('cluster', '')
                        if vm_cluster and cluster_name.lower() in vm_cluster.lower():
                            vms.append(vm)
                except:
                    pass
                
                if vms:
                    context_parts.append(f"\nVMs in cluster '{cluster_name}': {len(vms)} total")
                    context_parts.append("Sample VMs:")
                    for vm in vms[:5]:
                        vm_name = vm.get('name', 'N/A')
                        context_parts.append(f"  - {vm_name}")
                    if len(vms) > 5:
                        context_parts.append(f"  ... and {len(vms) - 5} more")
    
    # Pattern 3: "devices at site X" - include VMs, prefixes, VLANs
    elif 'site' in keywords_lower or any(word in query_lower for word in ['at site', 'in site', 'site:']):
        site_name = None
        for result in primary_results:
            site = result.get('site')
            if site:
                site_name = site
                break
        
        if site_name:
            context_parts.append(f"\n=== Site Context: '{site_name}' ===")
            context_parts.append("Related resources at this site are included in the results above.")
    
    return "\n".join(context_parts) if context_parts else ""


def enhance_result_with_relationships(
    result: Dict[str, Any],
    query_context: str
) -> Dict[str, Any]:
    """
    Enhance a single result record with inferred relationship data.
    
    Args:
        result: Original result record
        query_context: Original query for context
    
    Returns:
        Enhanced result with additional relationship fields
    """
    enhanced = result.copy()
    obj_type = result.get('object_type', '')
    
    # For devices with clusters, add VM count
    if 'device' in obj_type:
        cluster = result.get('cluster')
        if cluster:
            vms = infer_vms_from_host(result.get('name', ''), result)
            enhanced['_inferred_vm_count'] = len(vms)
            enhanced['_inferred_cluster'] = cluster
    
    # For clusters, add host count and VM count
    elif 'cluster' in obj_type:
        cluster_name = result.get('name', '')
        hosts = infer_hosts_from_cluster(cluster_name)
        enhanced['_inferred_host_count'] = len(hosts)
        
        # Get VM count
        try:
            all_vms = get_backup_records_by_type(
                object_type='virtualization_virtual_machines',
                site=None,
                limit=200
            )
            vm_count = sum(1 for vm in all_vms if vm.get('cluster', '').lower() == cluster_name.lower())
            enhanced['_inferred_vm_count'] = vm_count
        except:
            pass
    
    return enhanced
