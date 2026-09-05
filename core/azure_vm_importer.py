"""
Azure Virtual Machine Import Module
Handles parsing Azure VM CSV exports and preparing data for NetBox import.
"""

import csv
import json
import logging
import re
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from core.db_manager import DB_PATH, init_db

logger = logging.getLogger("netbox-hub")


def parse_azure_vm_csv(csv_path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Parse Azure VM CSV export and transform to NetBox-compatible format.
    
    Expected CSV columns:
    - NAME: VM name
    - SUBSCRIPTION: Azure subscription name
    - RESOURCE GROUP: Azure resource group
    - LOCATION: Azure region (e.g., "Australia East")
    - STATUS: Running, Stopped, etc.
    - OPERATING SYSTEM: Windows, Linux
    - SIZE: VM size/SKU (e.g., "Standard_E2as_v4")
    - PUBLIC IP ADDRESS: Public IP or " -"
    - DISKS: Number of attached disks
    - UPDATE STATUS: Update configuration (JSON string)
    - RESOURCE LINK: Azure portal link
    
    Returns:
        Tuple of (vm_records, warnings)
    """
    vm_records = []
    warnings = []
    
    try:
        # Handle UTF-8 BOM if present
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            row_num = 1
            
            for row in reader:
                row_num += 1
                try:
                    vm_name = row.get('NAME', '').strip()
                    
                    if not vm_name:
                        warnings.append(f"Row {row_num}: Missing VM name, skipping")
                        continue
                    
                    # Extract and normalize fields
                    subscription = row.get('SUBSCRIPTION', '').strip()
                    resource_group = row.get('RESOURCE GROUP', '').strip()
                    location = row.get('LOCATION', '').strip()
                    status = row.get('STATUS', '').strip()
                    os_type = row.get('OPERATING SYSTEM', '').strip()
                    size = row.get('SIZE', '').strip()
                    public_ip = row.get('PUBLIC IP ADDRESS', '').strip()
                    disks = row.get('DISKS', '').strip()
                    resource_link = row.get('RESOURCE LINK', '').strip()
                    
                    # Clean up public IP (" -" means no public IP)
                    if public_ip in ['-', ' -', '']:
                        public_ip = None
                    
                    # Build VM record
                    vm_record = {
                        'name': vm_name,
                        'subscription': subscription,
                        'resource_group': resource_group,
                        'location': location,
                        'status': status,
                        'operating_system': os_type,
                        'size': size,
                        'public_ip': public_ip,
                        'disk_count': disks,
                        'resource_link': resource_link,
                        'source': 'Azure CSV Import',
                        'imported_at': datetime.now().isoformat()
                    }
                    
                    vm_records.append(vm_record)
                    
                except Exception as e:
                    warnings.append(f"Row {row_num}: Error parsing - {str(e)}")
                    logger.error(f"Error parsing row {row_num}: {e}")
                    continue
        
        logger.info(f"Parsed {len(vm_records)} Azure VMs from CSV")
        return vm_records, warnings
        
    except Exception as e:
        logger.error(f"Failed to parse Azure VM CSV: {e}")
        raise


def check_vm_exists_in_db(vm_name: str) -> Optional[Dict[str, Any]]:
    """
    Check if a VM with the given name already exists in the database.
    
    Args:
        vm_name: Virtual machine name
        
    Returns:
        Dict with existing VM data if found, None otherwise
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, name, category, description, manufacturer, model_or_role, 
               site, cluster, imported_at
        FROM inventory_records
        WHERE LOWER(name) = LOWER(?) AND category = 'vm'
    """, (vm_name,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'id': row[0],
            'name': row[1],
            'category': row[2],
            'description': row[3],
            'manufacturer': row[4],
            'model_or_role': row[5],
            'site': row[6],
            'cluster': row[7],
            'imported_at': row[8]
        }
    return None


def build_vm_ip_index() -> Dict[str, Dict[str, Any]]:
    """Index every VM IP address held in the ingested NetBox backup.

    Two independent sources are merged, because NetBox records them separately:

    * `virtualization/virtual-machines` carries the VM's `Primary IP`, which the
      backup flattens into the summary as ``Primary IP: 10.0.0.5/24``.
    * `ipam/ip-addresses` carries every assigned address, flattened as
      ``Assigned VM: <name>`` alongside ``Assigned To: <interface>``.

    Returns:
        Mapping of lowercase VM name -> {"primary": str, "assigned": [str]}.
        Empty when no backup has been ingested.
    """
    init_db()
    index: Dict[str, Dict[str, Any]] = {}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        # Primary IP straight off each virtual machine record.
        cursor.execute(
            """
            SELECT name, summary FROM backup_records
            WHERE object_type = 'virtualization_virtual_machines'
              AND summary LIKE '%Primary IP:%'
            """
        )
        for name, summary in cursor.fetchall():
            if not name:
                continue
            match = re.search(r"Primary IP:\s*([^|]+)", summary or "")
            if not match:
                continue
            primary = match.group(1).strip()
            if primary:
                entry = index.setdefault(name.strip().lower(), {"primary": "", "assigned": []})
                entry["primary"] = primary

        # Every address whose assignment resolves back to a virtual machine.
        cursor.execute(
            """
            SELECT name, summary FROM backup_records
            WHERE object_type = 'ipam_ip_addresses'
              AND summary LIKE '%Assigned VM:%'
            """
        )
        for address, summary in cursor.fetchall():
            match = re.search(r"Assigned VM:\s*([^|]+)", summary or "")
            if not match or not address:
                continue
            vm_name = match.group(1).strip()
            if not vm_name:
                continue
            entry = index.setdefault(vm_name.lower(), {"primary": "", "assigned": []})
            clean = address.strip()
            if clean and clean not in entry["assigned"]:
                entry["assigned"].append(clean)
    except sqlite3.Error as exc:
        # backup_records is absent until a backup is ingested.
        logger.debug("VM IP index unavailable: %s", exc)
        return {}
    finally:
        conn.close()

    for entry in index.values():
        entry["assigned"].sort()
    return index


def lookup_vm_ip_addresses(vm_name: str) -> Dict[str, Any]:
    """Resolve the NetBox IP addresses recorded for one VM.

    Returns:
        {"primary": str, "assigned": [str], "display": str} — `display` is the
        primary IP when known, otherwise the first assigned address, otherwise "".
    """
    entry = build_vm_ip_index().get((vm_name or "").strip().lower())
    if not entry:
        return {"primary": "", "assigned": [], "display": ""}
    return _finalize_ip_entry(entry)


def _finalize_ip_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    primary = entry.get("primary", "")
    assigned = list(entry.get("assigned", []))
    display = primary or (assigned[0] if assigned else "")
    return {"primary": primary, "assigned": assigned, "display": display}


def enrich_vms_with_netbox_ips(vm_records: List[Dict[str, Any]]) -> int:
    """Attach NetBox IP data to each parsed Azure VM record, in place.

    Adds `netbox_primary_ip`, `netbox_assigned_ips` and `netbox_ip` to every
    record. Builds the index once, so cost is two queries regardless of VM count.

    Returns:
        The number of VMs for which at least one IP address was found.
    """
    index = build_vm_ip_index()
    matched = 0

    for vm in vm_records:
        entry = index.get((vm.get("name") or "").strip().lower())
        resolved = _finalize_ip_entry(entry) if entry else {"primary": "", "assigned": [], "display": ""}
        vm["netbox_primary_ip"] = resolved["primary"]
        vm["netbox_assigned_ips"] = resolved["assigned"]
        vm["netbox_ip"] = resolved["display"]
        if resolved["display"]:
            matched += 1

    return matched


def map_azure_to_netbox(vm_records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Map Azure VM records to NetBox inventory format.
    
    Mappings:
    - SUBSCRIPTION -> Tenant (Azure)
    - RESOURCE GROUP -> Custom Field: Resource Group
    - LOCATION -> Site (Cloud)
    - SIZE -> Custom Field: Instance Type
    - OPERATING SYSTEM -> Platform
    
    Args:
        vm_records: List of parsed Azure VM records
        
    Returns:
        Tuple of (netbox_records, metadata)
    """
    netbox_records = []
    metadata = {
        'subscriptions': set(),
        'resource_groups': set(),
        'locations': set(),
        'sizes': set(),
        'platforms': set(),
        'new_vms': [],
        'existing_vms': [],
        'vms_with_netbox_ip': 0,
    }

    # Resolve NetBox IP data for the whole batch up front (two queries total).
    ip_index = build_vm_ip_index()

    for vm in vm_records:
        vm_name = vm['name']

        # Attach NetBox IP data so callers and the UI can display it.
        entry = ip_index.get(vm_name.strip().lower())
        resolved = _finalize_ip_entry(entry) if entry else {"primary": "", "assigned": [], "display": ""}
        vm['netbox_primary_ip'] = resolved['primary']
        vm['netbox_assigned_ips'] = resolved['assigned']
        vm['netbox_ip'] = resolved['display']
        if resolved['display']:
            metadata['vms_with_netbox_ip'] += 1

        # Check if VM already exists
        existing_vm = check_vm_exists_in_db(vm_name)
        
        if existing_vm:
            metadata['existing_vms'].append({
                'name': vm_name,
                'existing_data': existing_vm,
                'new_data': vm
            })
        else:
            metadata['new_vms'].append(vm_name)
        
        # Collect unique values for NetBox objects
        if vm['subscription']:
            metadata['subscriptions'].add(vm['subscription'])
        if vm['resource_group']:
            metadata['resource_groups'].add(vm['resource_group'])
        if vm['location']:
            metadata['locations'].add(vm['location'])
        if vm['size']:
            metadata['sizes'].add(vm['size'])
        if vm['operating_system']:
            metadata['platforms'].add(vm['operating_system'])
        
        # Map to NetBox inventory format
        # Map location to site name (e.g., "Australia East" -> "Azure - Australia East")
        site_name = f"Azure - {vm['location']}" if vm['location'] else "Azure - Unknown"
        
        # Build description with Azure metadata
        description_parts = []
        if vm['subscription']:
            description_parts.append(f"Subscription: {vm['subscription']}")
        if vm['resource_group']:
            description_parts.append(f"Resource Group: {vm['resource_group']}")
        if vm['status']:
            description_parts.append(f"Status: {vm['status']}")
        if vm['public_ip']:
            description_parts.append(f"Public IP: {vm['public_ip']}")
        if resolved['display']:
            description_parts.append(f"NetBox IP: {resolved['display']}")
        if vm['disk_count']:
            description_parts.append(f"Disks: {vm['disk_count']}")
        
        netbox_record = {
            'category': 'vm',
            'name': vm_name,
            'description': ' | '.join(description_parts) if description_parts else '',
            'manufacturer': 'Microsoft Azure',
            'model_or_role': vm['size'],  # SIZE maps to model_or_role (Instance Type)
            'site': site_name,  # LOCATION maps to site
            'cluster': vm['resource_group'],  # RESOURCE GROUP maps to cluster
            # Additional Azure-specific metadata stored in description
            'platform': vm['operating_system'],  # For reference (not stored in this table)
            'tenant': vm['subscription'],  # For reference (not stored in this table)
            'netbox_ip': resolved['display'],  # Resolved from NetBox backup, if known
        }
        
        netbox_records.append(netbox_record)
    
    # Convert sets to sorted lists for display
    metadata['subscriptions'] = sorted(list(metadata['subscriptions']))
    metadata['resource_groups'] = sorted(list(metadata['resource_groups']))
    metadata['locations'] = sorted(list(metadata['locations']))
    metadata['sizes'] = sorted(list(metadata['sizes']))
    metadata['platforms'] = sorted(list(metadata['platforms']))
    
    return netbox_records, metadata


def save_azure_vms_to_db(
    netbox_records: List[Dict[str, Any]], 
    update_existing: bool = False,
    source: str = "Azure CSV Import"
) -> Dict[str, int]:
    """
    Save Azure VM records to the NetBox Hub database.
    
    Args:
        netbox_records: List of NetBox-formatted VM records
        update_existing: If True, update existing VMs; if False, skip them
        source: Import source description
        
    Returns:
        Dict with import statistics
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    stats = {
        'inserted': 0,
        'updated': 0,
        'skipped': 0,
        'errors': 0
    }
    
    for record in netbox_records:
        try:
            vm_name = record['name']
            existing = check_vm_exists_in_db(vm_name)
            
            if existing:
                if update_existing:
                    # Update existing VM
                    cursor.execute("""
                        UPDATE inventory_records
                        SET description = ?,
                            manufacturer = ?,
                            model_or_role = ?,
                            site = ?,
                            cluster = ?,
                            imported_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (
                        record['description'],
                        record['manufacturer'],
                        record['model_or_role'],
                        record['site'],
                        record['cluster'],
                        existing['id']
                    ))
                    stats['updated'] += 1
                else:
                    stats['skipped'] += 1
            else:
                # Insert new VM
                cursor.execute("""
                    INSERT INTO inventory_records 
                    (category, name, description, manufacturer, model_or_role, site, cluster)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    record['category'],
                    record['name'],
                    record['description'],
                    record['manufacturer'],
                    record['model_or_role'],
                    record['site'],
                    record['cluster']
                ))
                stats['inserted'] += 1
                
        except Exception as e:
            logger.error(f"Error saving VM {record.get('name', 'unknown')}: {e}")
            stats['errors'] += 1
    
    conn.commit()
    
    # Update sync metadata
    cursor.execute("""
        INSERT OR REPLACE INTO sync_metadata (module, source, updated_at)
        VALUES ('inventory', ?, datetime('now'))
    """, (source,))
    
    conn.commit()
    conn.close()
    
    logger.info(f"Azure VM import complete: {stats}")
    return stats


def generate_netbox_import_summary(metadata: Dict[str, Any]) -> str:
    """
    Generate a human-readable summary of what will be imported to NetBox.
    
    Args:
        metadata: Import metadata from map_azure_to_netbox()
        
    Returns:
        Formatted summary string
    """
    lines = []
    lines.append("=" * 60)
    lines.append("Azure VM Import Summary")
    lines.append("=" * 60)
    lines.append("")
    
    lines.append(f"Total VMs: {len(metadata['new_vms']) + len(metadata['existing_vms'])}")
    lines.append(f"  - New VMs: {len(metadata['new_vms'])}")
    lines.append(f"  - Existing VMs: {len(metadata['existing_vms'])}")
    lines.append("")
    
    lines.append("NetBox Objects to Create/Update:")
    lines.append(f"  - Tenants (Subscriptions): {len(metadata['subscriptions'])}")
    lines.append(f"  - Sites (Locations): {len(metadata['locations'])}")
    lines.append(f"  - Platforms: {len(metadata['platforms'])}")
    lines.append(f"  - Instance Types (Sizes): {len(metadata['sizes'])}")
    lines.append(f"  - Resource Groups: {len(metadata['resource_groups'])}")
    lines.append("")
    
    if metadata['subscriptions']:
        lines.append("Subscriptions:")
        for sub in metadata['subscriptions'][:10]:
            lines.append(f"  - {sub}")
        if len(metadata['subscriptions']) > 10:
            lines.append(f"  ... and {len(metadata['subscriptions']) - 10} more")
        lines.append("")
    
    if metadata['locations']:
        lines.append("Locations (will map to Sites):")
        for loc in metadata['locations']:
            lines.append(f"  - Azure - {loc}")
        lines.append("")
    
    if metadata['platforms']:
        lines.append("Operating Systems (Platforms):")
        for plat in metadata['platforms']:
            lines.append(f"  - {plat}")
        lines.append("")
    
    if metadata['existing_vms']:
        lines.append("Existing VMs (will be updated if selected):")
        for existing in metadata['existing_vms'][:5]:
            lines.append(f"  - {existing['name']}")
        if len(metadata['existing_vms']) > 5:
            lines.append(f"  ... and {len(metadata['existing_vms']) - 5} more")
        lines.append("")
    
    lines.append("=" * 60)
    
    return "\n".join(lines)
