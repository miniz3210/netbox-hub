"""
AI Assistant Helper Functions
Provides comprehensive database context for AI queries
"""

from typing import Dict, List, Any
from core.db_manager import (
    get_all_site_names,
    get_records_by_category,
    get_ipam_records_by_site,
    get_total_sites_count,
    get_total_vlans_count,
    get_total_prefixes_count,
    get_total_record_count,
    get_total_ipam_count,
    get_max_scope_id,
    get_site_summary,
    get_full_site_inventory_summary,
    get_existing_prefix_strings
)
import sqlite3

DB_PATH = "data/netbox_hub.db"

def get_all_ipam_records(limit: int = 100) -> List[Dict[str, Any]]:
    """Get all IPAM records (VLANs and Prefixes) from database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM ipam_records LIMIT {limit}")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_sites_detailed() -> List[Dict[str, Any]]:
    """Get all sites with detailed information."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sites_records")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_all_inventory_records(limit: int = 100) -> List[Dict[str, Any]]:
    """Get all inventory records (devices, hypervisors, VMs)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM inventory_records LIMIT {limit}")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def build_comprehensive_ipam_context(prompt: str, site_filter: str = None) -> str:
    """Build comprehensive IPAM context for AI assistant."""
    context = []
    
    # Database statistics
    context.append(f"Database Statistics:")
    context.append(f"- Total Sites: {get_total_sites_count()}")
    context.append(f"- Total VLANs: {get_total_vlans_count()}")
    context.append(f"- Total Prefixes: {get_total_prefixes_count()}")
    context.append(f"- Total IPAM Records: {get_total_ipam_count()}")
    
    # All sites list
    all_sites = get_all_site_names()
    context.append(f"\nAll Sites in Database: {', '.join(all_sites) if all_sites else 'None'}")
    
    # Detect site from prompt
    target_site = site_filter
    if not target_site:
        prompt_lower = prompt.lower()
        for site in all_sites:
            if site.lower() in prompt_lower:
                target_site = site
                break
    
    # Site-specific IPAM data
    if target_site:
        site_ipam = get_ipam_records_by_site(target_site)
        if site_ipam:
            context.append(f"\n=== VLANs and Prefixes for Site: {target_site} ===")
            for record in site_ipam[:100]:  # Show up to 100 records
                vlan_id = record.get('vlan_id', '')
                vlan_name = record.get('vlan_name', '')
                prefix = record.get('prefix_or_subnet', '')
                role = record.get('role', '')
                description = record.get('description', '')
                rec_type = record.get('record_type', 'prefix')
                scope_id = record.get('scope_id', '')
                
                if rec_type == 'vlan' and vlan_id:
                    context.append(f"- VLAN {vlan_id}: {vlan_name} | Subnet: {prefix} | Role: {role} | Scope: {scope_id} | Desc: {description}")
                else:
                    context.append(f"- Prefix: {prefix} | Role: {role} | VLAN: {vlan_name or 'N/A'} | Scope: {scope_id} | Desc: {description}")
        else:
            context.append(f"\nNo VLAN/Prefix records found for site: {target_site}")
    else:
        # Show sample of all IPAM records if no specific site
        all_ipam = get_all_ipam_records(limit=50)
        if all_ipam:
            context.append(f"\n=== Sample IPAM Records (showing {len(all_ipam)}) ===")
            for record in all_ipam:
                site = record.get('site', 'N/A')
                vlan_id = record.get('vlan_id', '')
                vlan_name = record.get('vlan_name', '')
                prefix = record.get('prefix_or_subnet', '')
                role = record.get('role', '')
                rec_type = record.get('record_type', 'prefix')
                
                if rec_type == 'vlan' and vlan_id:
                    context.append(f"- Site: {site} | VLAN {vlan_id}: {vlan_name} | {prefix} | Role: {role}")
                else:
                    context.append(f"- Site: {site} | Prefix: {prefix} | Role: {role}")
    
    # Device/VM inventory for site if mentioned
    if target_site:
        inventory_summary = get_full_site_inventory_summary(target_site)
        if inventory_summary:
            context.append(f"\n=== Inventory for Site: {target_site} ===")
            context.append(inventory_summary)
    
    return "\n".join(context)

def build_comprehensive_naming_context(prompt: str, site_filter: str = None) -> str:
    """Build comprehensive naming/inventory context for AI assistant."""
    context = []
    
    # Database statistics
    all_devices = get_records_by_category("device")
    all_hypervisors = get_records_by_category("hypervisor")
    all_vms = get_records_by_category("vm")
    
    context.append(f"Database Statistics:")
    context.append(f"- Total Devices: {len(all_devices)}")
    context.append(f"- Total Hypervisors: {len(all_hypervisors)}")
    context.append(f"- Total VMs: {len(all_vms)}")
    
    # All sites list
    all_sites = get_all_site_names()
    context.append(f"\nAll Sites in Database: {', '.join(all_sites) if all_sites else 'None'}")
    
    # Detect site from prompt
    target_site = site_filter
    if not target_site:
        prompt_lower = prompt.lower()
        for site in all_sites:
            if site.lower() in prompt_lower:
                target_site = site
                break
    
    # Site-specific or all inventory
    if target_site:
        site_devices = get_records_by_category("device", site_filter=target_site)
        site_hypervisors = get_records_by_category("hypervisor", site_filter=target_site)
        site_vms = get_records_by_category("vm", site_filter=target_site)
        
        context.append(f"\n=== Inventory for Site: {target_site} ===")
        
        if site_devices:
            context.append(f"\nDevices ({len(site_devices)}):")
            for d in site_devices[:50]:
                context.append(f"- {d.get('name', 'N/A')} | Role: {d.get('model_or_role', 'N/A')} | Manufacturer: {d.get('manufacturer', 'N/A')} | Site: {d.get('site', 'N/A')}")
        
        if site_hypervisors:
            context.append(f"\nHypervisors ({len(site_hypervisors)}):")
            for h in site_hypervisors[:50]:
                context.append(f"- {h.get('name', 'N/A')} | Role: {h.get('model_or_role', 'N/A')} | Site: {h.get('site', 'N/A')}")
        
        if site_vms:
            context.append(f"\nVirtual Machines ({len(site_vms)}):")
            for v in site_vms[:50]:
                context.append(f"- {v.get('name', 'N/A')} | Role: {v.get('model_or_role', 'N/A')} | Cluster: {v.get('cluster', 'N/A')} | Site: {v.get('site', 'N/A')}")
        
        if not site_devices and not site_hypervisors and not site_vms:
            context.append(f"No inventory records found for site: {target_site}")
    else:
        # Show sample of all inventory
        context.append(f"\n=== All Devices (showing up to 50) ===")
        for d in all_devices[:50]:
            context.append(f"- {d.get('name', 'N/A')} | Role: {d.get('model_or_role', 'N/A')} | Manufacturer: {d.get('manufacturer', 'N/A')} | Site: {d.get('site', 'N/A')}")
        
        if all_hypervisors:
            context.append(f"\n=== All Hypervisors (showing up to 50) ===")
            for h in all_hypervisors[:50]:
                context.append(f"- {h.get('name', 'N/A')} | Role: {h.get('model_or_role', 'N/A')} | Site: {h.get('site', 'N/A')}")
        
        if all_vms:
            context.append(f"\n=== All VMs (showing up to 50) ===")
            for v in all_vms[:50]:
                context.append(f"- {v.get('name', 'N/A')} | Role: {v.get('model_or_role', 'N/A')} | Cluster: {v.get('cluster', 'N/A')} | Site: {v.get('site', 'N/A')}")
    
    return "\n".join(context)
