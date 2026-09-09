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

# Values that never represent a real Azure tag and must be filtered out.
_TAG_PLACEHOLDER_VALUES = {"-", "nan", "null", "none", "no policy"}


def parse_azure_vm_csv(csv_path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse Azure Portal and Azure Resource Graph VM CSV exports."""
    vm_records = []
    warnings = []

    def clean(value: Any) -> str:
        if value is None:
            return ""
        normalized = str(value).strip()
        return "" if normalized.lower() == "nan" else normalized

    try:
        with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            headers = {clean(header).lower(): header for header in (reader.fieldnames or [])}
            is_resource_graph = 'tenant' in headers or 'primaryipv4' in headers

            aliases = {
                'name': ('name',),
                'subscription': ('subscription', 'tenant'),
                'resource_group': ('resource group', 'resource_group', 'cf_resourcegroups'),
                'location': ('location', 'site'),
                'status': ('status',),
                'operating_system': ('operating system', 'operating_system'),
                'platform_value': ('platform',),
                'size': ('size', 'cfinstancetype', 'cf_instancetype'),
                'public_ip': ('public ip address', 'primaryipv4'),
                'disk_count': ('disks',),
                'resource_link': ('resource link',),
                'vnet': ('vnet',),
                'subnet': ('subnet',),
                'owner': ('cf_owner',),
                'role': ('role',),
                'purpose': ('cf_purpose',),
                'organization': ('cf_organization',),
                'subscription_id': ('subscriptionid',),
                'tag_application': ('tag_application',),
                'tag_environment': ('tag_environment',),
                'tag_cost_centre': ('tag_costcentre', 'tag_cost_centre'),
                'tag_business_criticality': ('tag_businesscriticality', 'tag_business_criticality'),
                'tag_deployment_method': ('tag_deploymentmethod', 'tag_deployment_method'),
                'tag_backup': ('tag_backup',),
                'tags': ('tags',),
            }

            def value_for(row: Dict[str, Any], field: str) -> str:
                for alias in aliases[field]:
                    header = headers.get(alias.lower())
                    if header is not None:
                        return clean(row.get(header))
                return ""

            # Dedup dictionary: name (lower) -> record
            vm_map = {}

            for row_num, row in enumerate(reader, start=2):
                try:
                    vm_name = value_for(row, 'name')
                    if not vm_name:
                        warnings.append(f"Row {row_num}: Missing VM name, skipping")
                        continue
                    
                    vm_name_key = vm_name.strip().lower()

                    public_ip = value_for(row, 'public_ip')
                    if public_ip in {'-', ' -'}:
                        public_ip = ''

                    role_val = value_for(row, 'role') or value_for(row, 'tag_application')
                    tag_app = value_for(row, 'tag_application') or role_val

                    vm_record = {
                        'name': vm_name,
                        'subscription': value_for(row, 'subscription'),
                        'resource_group': value_for(row, 'resource_group'),
                        'location': value_for(row, 'location'),
                        'status': value_for(row, 'status'),
                        'operating_system': value_for(row, 'operating_system'),
                        'platform_value': value_for(row, 'platform_value'),
                        'size': value_for(row, 'size'),
                        'public_ip': public_ip or None,
                        'disk_count': value_for(row, 'disk_count'),
                        'resource_link': value_for(row, 'resource_link'),
                        'vnet': value_for(row, 'vnet'),
                        'subnet': value_for(row, 'subnet'),
                        'owner': value_for(row, 'owner'),
                        'role': role_val,
                        'purpose': value_for(row, 'purpose'),
                        'organization': value_for(row, 'organization'),
                        'subscription_id': value_for(row, 'subscription_id'),
                        'tag_application': tag_app,
                        'tag_environment': value_for(row, 'tag_environment'),
                        'tag_cost_centre': value_for(row, 'tag_cost_centre'),
                        'tag_business_criticality': value_for(row, 'tag_business_criticality'),
                        'tag_deployment_method': value_for(row, 'tag_deployment_method'),
                        'tag_backup': value_for(row, 'tag_backup'),
                        'tag_operating_system': value_for(row, 'operating_system'),
                        'tags': value_for(row, 'tags'),
                        'source': 'Azure Resource Graph CSV Import' if is_resource_graph else 'Azure CSV Import',
                        'imported_at': datetime.now().isoformat()
                    }
                    vm_map[vm_name_key] = vm_record
                except Exception as e:
                    warnings.append(f"Row {row_num}: Error parsing - {str(e)}")
                    logger.error(f"Error parsing row {row_num}: {e}")
            
            vm_records = list(vm_map.values())

        if not vm_records:
            warnings.append("No VM records found. Check that the CSV contains a Name column.")
        logger.info(f"Parsed {len(vm_records)} Azure VMs from CSV")
        return vm_records, warnings
    except Exception as e:
        logger.error(f"Failed to parse Azure VM CSV: {e}")
        raise


def check_vm_exists_in_db(vm_name: str) -> Optional[Dict[str, Any]]:
    """
    Check if a VM with the given name already exists in the database.
    Prioritizes the most recently updated data source (JSON backup vs CSV).
    
    Args:
        vm_name: Virtual machine name
        
    Returns:
        Dict with existing VM data if found, None otherwise.
        Fields include: name, role, status, site, tenant, platform, cluster, 
        description, tags, custom_fields, primary_ip, device, owner
    """
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get timestamps for both data sources
    cursor.execute("""
        SELECT updated_at FROM sync_metadata WHERE module = 'netbox_backup'
    """)
    backup_timestamp_row = cursor.fetchone()
    backup_timestamp = backup_timestamp_row[0] if backup_timestamp_row else ""
    
    cursor.execute("""
        SELECT updated_at FROM sync_metadata WHERE module = 'netbox_virtual_machines'
    """)
    csv_timestamp_row = cursor.fetchone()
    csv_timestamp = csv_timestamp_row[0] if csv_timestamp_row else ""
    
    # Try to get data from backup_records (NetBox JSON backup)
    cursor.execute("""
        SELECT site, summary
        FROM backup_records
        WHERE LOWER(name) = LOWER(?) 
        AND object_type = 'virtualization_virtual_machines'
        LIMIT 1
    """, (vm_name,))
    
    backup_row = cursor.fetchone()
    
    # Try to get data from inventory_records (CSV upload)
    cursor.execute("""
        SELECT id, name, category, description, manufacturer, model_or_role, 
               site, cluster, imported_at
        FROM inventory_records
        WHERE LOWER(name) = LOWER(?) AND category = 'vm'
    """, (vm_name,))
    
    csv_row = cursor.fetchone()
    
    # Determine which source to use based on timestamps and data availability
    use_csv = False
    if csv_row and not backup_row:
        # Only CSV data available
        use_csv = True
    elif backup_row and not csv_row:
        # Only JSON backup data available
        use_csv = False
    elif backup_row and csv_row:
        # Both available - compare timestamps to use the most recent
        # Timestamps are in format "YYYY-MM-DD HH:MM:SS UTC"
        use_csv = csv_timestamp > backup_timestamp
    else:
        # No data found in either source
        conn.close()
        return None
    
    # Process CSV data if it's newer or only source available
    if use_csv and csv_row:
        conn.close()
        return {
            'id': csv_row[0],
            'name': csv_row[1],
            'category': csv_row[2],
            'description': csv_row[3],
            'manufacturer': csv_row[4],
            'role': csv_row[5],  # model_or_role maps to role
            'site': csv_row[6],
            'cluster': csv_row[7],
            'imported_at': csv_row[8],
            'tags': [],
            'custom_fields': {},
            'tenant': '',
            'platform': '',
            'primary_ip': '',
            'primary_ip4': '',
            'device': '',
            'owner': '',
            '_source': 'csv'  # Track which source was used
        }
    
    # Process JSON backup data if it's newer or CSV is not available
    backup_row = backup_row  # backup_row is already fetched above
    # Process JSON backup data if it's newer or CSV is not available
    if backup_row:
        site_column, summary = backup_row
        
        # Initialize with site from the dedicated column
        vm_data = {
            'name': vm_name,
            'role': '',
            'status': '',
            'site': site_column or '',  # Site comes from dedicated column
            'tenant': '',
            'platform': '',
            'cluster': '',
            'description': '',
            'tags': [],
            'custom_fields': {},
            'primary_ip': '',
            'primary_ip4': '',
            'device': '',
            'owner': '',
            '_source': 'json'  # Track which source was used
        }
        
        # Parse summary line: "Role: JDE Application | Status: Active | Cluster: vCluster | Tags: tag1, tag2 | Custom Fields: key=value, key=value"
        if summary:
            custom_fields_section = None
            
            for field_pair in summary.split('|'):
                field_pair = field_pair.strip()
                if ':' not in field_pair:
                    continue
                    
                key, value = field_pair.split(':', 1)
                key_clean = key.strip().lower()
                value_clean = value.strip()
                
                if not value_clean or value_clean.lower() in ('none', 'null', '—', '---------'):
                    continue
                
                # Handle Tags (format: "Tags: Tag1, Tag2, Tag3")
                if key_clean == 'tags':
                    vm_data['tags'] = [t.strip() for t in value_clean.split(',') if t.strip()]
                # Handle Custom Fields (format: "Custom Fields: instance_type=Standard_D4s_v3, resource_group=rg-prod")
                elif key_clean == 'custom fields':
                    custom_fields_section = value_clean
                # Standard fields
                elif key_clean == 'role':
                    vm_data['role'] = value_clean
                elif key_clean == 'status':
                    vm_data['status'] = value_clean
                elif key_clean == 'tenant':
                    vm_data['tenant'] = value_clean
                elif key_clean == 'platform':
                    vm_data['platform'] = value_clean
                elif key_clean == 'cluster':
                    vm_data['cluster'] = value_clean
                elif key_clean == 'description':
                    vm_data['description'] = value_clean
                elif key_clean in ('primary ip', 'primary_ip'):
                    vm_data['primary_ip'] = value_clean
                    vm_data['primary_ip4'] = value_clean
                elif key_clean == 'device':
                    vm_data['device'] = value_clean
                elif key_clean == 'owner':
                    vm_data['owner'] = value_clean
            
            # Parse custom fields section separately (format: "key=value, key=value")
            if custom_fields_section:
                for cf_pair in custom_fields_section.split(','):
                    cf_pair = cf_pair.strip()
                    if '=' in cf_pair:
                        cf_key, cf_val = cf_pair.split('=', 1)
                        cf_key_clean = cf_key.strip().lower().replace(' ', '_')
                        cf_val_clean = cf_val.strip()
                        vm_data['custom_fields'][cf_key_clean] = cf_val_clean
                        
                        # If this is the owner field, also set it at the top level
                        if cf_key_clean == 'owner':
                            vm_data['owner'] = cf_val_clean
        
        conn.close()
        return vm_data
    
    # No data found
    conn.close()
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


def _strip_azure_prefix(location: str) -> str:
    """Strip an existing 'Azure - ' prefix from a location string if present."""
    loc = location or ""
    prefix = "azure - "
    if loc.lower().startswith(prefix):
        return loc[len(prefix):].strip()
    return loc.strip()


def _slugify_tag(tag_name: str) -> str:
    """Generate a NetBox-compliant slug from a tag name.

    Lowercases the value, replaces any non-alphanumeric character with a
    hyphen, and collapses repeated hyphens so slugs stay clean and stable.
    """
    if not tag_name:
        return ""
    slug = re.sub(r"[^a-z0-9]+", "-", tag_name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def _build_netbox_tags(vm: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the NetBox tag array for a single parsed Azure VM.

    Tag rules (see the Azure CSV import feature spec):

    1. Operating System: raw value from ``Operating_System`` / ``operating_system``
       (e.g. ``"Windows (Windows Server 2019 Datacenter)"``). No prefix is added.
    2. Key-value formatted tags (``Key:Value``):
       - ``Tag_BusinessCriticality`` -> ``BusinessCriticality:<value>``
       - ``Tag_DeploymentMethod`` / ``Deploymentmethod`` -> ``Deploymentmethod:<value>``
       - ``Tag_Environment`` -> ``Environment:<value>``
    3. Backup policy: raw value from ``Tag_Backup`` (e.g.
       ``"Daily(BackupPolicy-AU-LowDataChange)"``). ``No Policy``, ``-``, empty
       and ``null`` are excluded.
    4. Each tag is emitted as ``{"name": ..., "slug": ...}``.
    """
    tag_names: List[str] = []

    def add_tag(name: str) -> None:
        if not name:
            return
        cleaned = str(name).strip()
        if not cleaned or cleaned.lower() in {"-", "nan", "null", "none", "no policy"}:
            return
        if cleaned not in tag_names:
            tag_names.append(cleaned)

    # 1. Operating System — direct string, no prefix.
    add_tag(vm.get('operating_system'))

    # 2. Key-value formatted tags.
    def add_kv_tag(key: str, val: Any) -> None:
        if val:
            v = str(val).strip()
            if v and v.lower() not in {"-", "nan", "null", "none"}:
                add_tag(f"{key}:{v}")

    add_kv_tag("BusinessCriticality", vm.get('tag_business_criticality'))
    add_kv_tag("Deploymentmethod", vm.get('tag_deployment_method'))
    add_kv_tag("Environment", vm.get('tag_environment'))

    # 3. Backup policy — direct string, excluding placeholders.
    add_tag(vm.get('tag_backup'))

    return [{"name": name, "slug": _slugify_tag(name)} for name in tag_names]


def _dedupe_vm_records(vm_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate VM records by name (case-insensitive, trimmed).

    When the same name appears more than once the latest valid entry wins.
    """
    deduped: Dict[str, Dict[str, Any]] = {}
    for vm in vm_records:
        name = (vm.get('name') or '').strip().lower()
        if not name:
            continue
        deduped[name] = vm
    return list(deduped.values())


def map_azure_to_netbox(vm_records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    netbox_records = []
    metadata = {
        'subscriptions': set(),
        'resource_groups': set(),
        'locations': set(),
        'sizes': set(),
        'platforms': set(),
        'owners': set(),
        'roles': set(),
        'tag_applications': set(),
        'tag_environments': set(),
        'tag_cost_centres': set(),
        'tag_business_criticalities': set(),
        'tag_deployment_methods': set(),
        'tag_backups': set(),
        'tag_operating_systems': set(),
        'new_vms': [],
        'existing_vms': [],
        'vms_with_netbox_ip': 0,
    }

    ip_index = build_vm_ip_index()

    for vm in vm_records:
        vm_name = vm['name']

        entry = ip_index.get(vm_name.strip().lower())
        resolved = _finalize_ip_entry(entry) if entry else {"primary": "", "assigned": [], "display": ""}
        vm['netbox_primary_ip'] = resolved['primary']
        vm['netbox_assigned_ips'] = resolved['assigned']
        vm['netbox_ip'] = resolved['display']
        if resolved['display']:
            metadata['vms_with_netbox_ip'] += 1

        existing_vm = check_vm_exists_in_db(vm_name)

        if existing_vm:
            metadata['existing_vms'].append({
                'name': vm_name,
                'existing_data': existing_vm,
                'new_data': vm
            })
        else:
            metadata['new_vms'].append(vm_name)

        if vm['subscription']:
            metadata['subscriptions'].add(vm['subscription'])
        if vm['resource_group']:
            metadata['resource_groups'].add(vm['resource_group'])
        if vm['size']:
            metadata['sizes'].add(vm['size'])
        if vm.get('platform_value'):
            metadata['platforms'].add(vm['platform_value'])
        elif vm['operating_system']:
            metadata['platforms'].add(vm['operating_system'])
        if vm.get('owner'):
            metadata['owners'].add(vm['owner'])
        role_val = vm.get('role') or vm.get('tag_application')
        if role_val:
            metadata['roles'].add(role_val)
        tag_metadata = {
            'tag_application': ('tag_applications', ''),
            'tag_environment': ('tag_environments', ''),
            'tag_cost_centre': ('tag_cost_centres', 'Cost Centre:'),
            'tag_business_criticality': ('tag_business_criticalities', 'BusinessCriticality:'),
            'tag_deployment_method': ('tag_deployment_methods', 'Deploymentmethod:'),
            'tag_backup': ('tag_backups', ''),
            'tag_operating_system': ('tag_operating_systems', ''),
        }
        for field, (metadata_key, prefix) in tag_metadata.items():
            val = vm.get(field)
            if val:
                val_str = str(val).strip()
                if val_str and val_str.lower() != 'nan':
                    if prefix and not val_str.lower().startswith(prefix.lower()):
                        tag_name = f"{prefix}{val_str}"
                    else:
                        tag_name = val_str
                    metadata[metadata_key].add(tag_name)

        raw_location = _strip_azure_prefix(vm.get('location', ''))
        if raw_location:
            metadata['locations'].add(raw_location)

        site_name = f"Azure - {raw_location}" if raw_location else "Azure - Unknown"

        # Build description with Azure metadata
        description_parts = []
        if vm['subscription']:
            description_parts.append(f"Subscription: {vm['subscription']}")
        if vm['resource_group']:
            description_parts.append(f"Resource Group: {vm['resource_group']}")
        if vm['status']:
            description_parts.append(f"Status: {vm['status']}")
        if vm.get('public_ip'):
            ip_label = "Primary IPv4" if vm.get('vnet') else "Public IP"
            description_parts.append(f"{ip_label}: {vm['public_ip']}")
        if vm.get('vnet'):
            description_parts.append(f"VNet: {vm['vnet']}")
        if vm.get('subnet'):
            description_parts.append(f"Subnet: {vm['subnet']}")
        if vm.get('owner'):
            description_parts.append(f"Owner: {vm['owner']}")
        if resolved['display']:
            description_parts.append(f"NetBox IP: {resolved['display']}")
        if vm.get('disk_count'):
            description_parts.append(f"Disks: {vm['disk_count']}")

        netbox_record = {
            'category': 'vm',
            'name': vm_name,
            'description': ' | '.join(description_parts) if description_parts else '',
            'manufacturer': 'Microsoft Azure',
            'model_or_role': vm['size'],
            'site': site_name,
            'cluster': vm['resource_group'],
            'platform': vm.get('platform_value') or vm['operating_system'],
            'tenant': vm['subscription'],
            'netbox_ip': resolved['display'],
            'netbox_tags': _build_netbox_tags(vm),
        }

        netbox_records.append(netbox_record)

    metadata['subscriptions'] = sorted(list(metadata['subscriptions']))
    metadata['resource_groups'] = sorted(list(metadata['resource_groups']))
    metadata['locations'] = sorted(list(metadata['locations']))
    metadata['sizes'] = sorted(list(metadata['sizes']))
    metadata['platforms'] = sorted(list(metadata['platforms']))
    metadata['owners'] = sorted(list(metadata['owners']))
    metadata['roles'] = sorted(list(metadata['roles']))
    metadata['tag_applications'] = sorted(list(metadata['tag_applications']))
    metadata['tag_environments'] = sorted(list(metadata['tag_environments']))
    metadata['tag_cost_centres'] = sorted(list(metadata['tag_cost_centres']))
    metadata['tag_business_criticalities'] = sorted(list(metadata['tag_business_criticalities']))
    metadata['tag_deployment_methods'] = sorted(list(metadata['tag_deployment_methods']))
    metadata['tag_backups'] = sorted(list(metadata['tag_backups']))
    metadata['tag_operating_systems'] = sorted(list(metadata['tag_operating_systems']))

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
