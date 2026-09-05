"""
NetBox Object Existence Checker & Import Script Generator

Compares the NetBox objects required by an Azure VM export against what already
exists in the local NetBox Hub database (populated from a NetBox backup or CSV
export), reports what is missing, and renders the import payloads NetBox expects.

Object sources in the local database:
  - Tenants          -> backup_records.object_type = 'tenancy_tenants'
  - Sites            -> sites_records + backup_records 'dcim_sites'
  - Platforms        -> backup_records 'dcim_platforms'
  - Clusters         -> backup_records 'virtualization_clusters'
  - Instance Types   -> custom field 'instance_type' on virtual machines
  - Resource Groups  -> custom field 'resource_group' on virtual machines
"""

import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Set, Tuple

from core.db_manager import DB_PATH, init_db

logger = logging.getLogger("netbox-hub")

# Custom field names carrying Azure metadata on NetBox virtual machines
INSTANCE_TYPE_FIELD = "instance_type"
RESOURCE_GROUP_FIELD = "resource_group"

# Azure "OPERATING SYSTEM" values map onto existing NetBox platform names
PLATFORM_ALIASES = {
    "windows": "Windows Server",
    "windows server": "Windows Server",
    "linux": "Linux",
}


def slugify(text: str) -> str:
    """NetBox-compatible slug: lowercase, single-hyphen separated, alphanumeric only."""
    if not text:
        return ""
    text = str(text).lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def normalize_platform_name(os_name: str) -> str:
    """Map an Azure OS string onto the NetBox platform naming used in this tenant."""
    clean = (os_name or "").strip()
    if not clean:
        return ""
    return PLATFORM_ALIASES.get(clean.lower(), clean)


def _fetch_backup_names(object_type: str) -> Set[str]:
    """Return the set of object names of one type held in the ingested backup."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT name FROM backup_records WHERE object_type = ? AND name IS NOT NULL AND name != ''",
            (object_type,),
        )
        return {row[0].strip() for row in cursor.fetchall() if row[0] and row[0].strip()}
    except sqlite3.Error as exc:
        logger.warning("Backup lookup failed for %s: %s", object_type, exc)
        return set()
    finally:
        conn.close()


def _parse_custom_fields(summary: str) -> Dict[str, str]:
    """Pull the `Custom Fields: key=value, key=value` tail out of a backup summary."""
    if not summary:
        return {}
    match = re.search(r"Custom Fields:\s*(.*)$", summary)
    if not match:
        return {}

    fields: Dict[str, str] = {}
    for pair in match.group(1).split(","):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields


def get_existing_custom_field_values(field_name: str) -> Set[str]:
    """Collect the distinct values a VM custom field already holds in NetBox."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    values: Set[str] = set()
    try:
        cursor.execute(
            """
            SELECT summary FROM backup_records
            WHERE object_type IN ('virtualization_virtual_machines', 'dcim_devices')
              AND summary LIKE '%Custom Fields:%'
            """
        )
        target = field_name.strip().lower()
        for (summary,) in cursor.fetchall():
            value = _parse_custom_fields(summary).get(target)
            if value:
                values.add(value)
    except sqlite3.Error as exc:
        logger.warning("Custom field lookup failed for %s: %s", field_name, exc)
    finally:
        conn.close()
    return values


def get_existing_tenants() -> Set[str]:
    """Tenant names already present in NetBox."""
    return _fetch_backup_names("tenancy_tenants")


def get_existing_platforms() -> Set[str]:
    """Platform names already present in NetBox."""
    return _fetch_backup_names("dcim_platforms")


def get_existing_clusters() -> Set[str]:
    """Cluster names already present in NetBox."""
    return _fetch_backup_names("virtualization_clusters")


def get_existing_sites() -> Set[str]:
    """Site names from both the sites table and the ingested backup."""
    sites = _fetch_backup_names("dcim_sites")

    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM sites_records WHERE name IS NOT NULL AND name != ''")
        sites.update(row[0].strip() for row in cursor.fetchall() if row[0] and row[0].strip())
    except sqlite3.Error as exc:
        logger.warning("Site lookup failed: %s", exc)
    finally:
        conn.close()
    return sites


def _split_existing_missing(
    required: List[str], existing: Set[str]
) -> Tuple[List[str], List[str]]:
    """Case-insensitively partition required values into (existing, missing)."""
    existing_lower = {value.strip().lower() for value in existing}
    found: List[str] = []
    missing: List[str] = []
    for value in required:
        clean = (value or "").strip()
        if not clean:
            continue
        if clean.lower() in existing_lower:
            found.append(clean)
        else:
            missing.append(clean)
    return sorted(set(found)), sorted(set(missing))


def analyze_netbox_objects(metadata: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Compare the objects an Azure export needs against the local NetBox database.

    Args:
        metadata: the metadata dict produced by ``map_azure_to_netbox``.

    Returns:
        Mapping of category key -> {label, netbox_object, existing, missing, total}.
    """
    site_names = [f"Azure - {loc}" for loc in metadata.get("locations", [])]
    platform_names = [
        normalize_platform_name(os_name) for os_name in metadata.get("platforms", [])
    ]

    checks = [
        ("tenants", "Tenants (Subscriptions)", "tenancy.tenant",
         list(metadata.get("subscriptions", [])), get_existing_tenants()),
        ("sites", "Sites (Locations)", "dcim.site",
         site_names, get_existing_sites()),
        ("platforms", "Platforms (Operating Systems)", "dcim.platform",
         platform_names, get_existing_platforms()),
        ("instance_types", "Instance Types (Custom Field Choices)", "extras.customfieldchoiceset",
         list(metadata.get("sizes", [])), get_existing_custom_field_values(INSTANCE_TYPE_FIELD)),
        ("resource_groups", "Resource Groups (Custom Field Choices)", "extras.customfieldchoiceset",
         list(metadata.get("resource_groups", [])), get_existing_custom_field_values(RESOURCE_GROUP_FIELD)),
    ]

    results: Dict[str, Dict[str, Any]] = {}
    for key, label, netbox_object, required, existing in checks:
        found, missing = _split_existing_missing(required, existing)
        results[key] = {
            "label": label,
            "netbox_object": netbox_object,
            "existing": found,
            "missing": missing,
            "total": len(found) + len(missing),
        }
    return results


def generate_tenants_csv(
    tenant_names: List[str], description: str = "Azure Subscription"
) -> str:
    """Render the `name,slug,description` CSV NetBox expects for tenant import."""
    lines = ["name,slug,description"]
    for name in tenant_names:
        clean = (name or "").strip()
        if not clean:
            continue
        lines.append(f"{clean},{slugify(clean)},{description}")
    return "\n".join(lines)


def generate_sites_csv(site_names: List[str], region: str = "Azure", group: str = "Cloud") -> str:
    """Render the site import CSV for the missing cloud sites."""
    lines = ["name,slug,status,region,group"]
    for name in site_names:
        clean = (name or "").strip()
        if not clean:
            continue
        lines.append(f"{clean},{slugify(clean)},active,{region},{group}")
    return "\n".join(lines)


def generate_platforms_csv(platform_names: List[str]) -> str:
    """Render the platform import CSV for the missing platforms."""
    lines = ["name,slug"]
    for name in platform_names:
        clean = (name or "").strip()
        if not clean:
            continue
        lines.append(f"{clean},{slugify(clean)}")
    return "\n".join(lines)


def generate_choice_set(values: List[str]) -> str:
    """Render `value:label` lines for a NetBox custom field choice set."""
    lines = []
    for value in values:
        clean = (value or "").strip()
        if not clean:
            continue
        lines.append(f"{clean}:{clean}")
    return "\n".join(lines)


def generate_import_scripts(analysis: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """Build the import payload for every category that still has missing objects.

    Returns:
        Mapping of category key -> {label, format, filename, content, count}.
    """
    scripts: Dict[str, Dict[str, str]] = {}

    tenants = analysis.get("tenants", {}).get("missing", [])
    if tenants:
        scripts["tenants"] = {
            "label": "Tenants",
            "format": "csv",
            "filename": "netbox-tenants-import.csv",
            "content": generate_tenants_csv(tenants),
            "count": len(tenants),
            "instructions": "NetBox → Organization → Tenants → Import → paste as CSV",
        }

    sites = analysis.get("sites", {}).get("missing", [])
    if sites:
        scripts["sites"] = {
            "label": "Sites",
            "format": "csv",
            "filename": "netbox-sites-import.csv",
            "content": generate_sites_csv(sites),
            "count": len(sites),
            "instructions": "NetBox → Organization → Sites → Import → paste as CSV",
        }

    platforms = analysis.get("platforms", {}).get("missing", [])
    if platforms:
        scripts["platforms"] = {
            "label": "Platforms",
            "format": "csv",
            "filename": "netbox-platforms-import.csv",
            "content": generate_platforms_csv(platforms),
            "count": len(platforms),
            "instructions": "NetBox → Devices → Platforms → Import → paste as CSV",
        }

    instance_types = analysis.get("instance_types", {}).get("missing", [])
    if instance_types:
        scripts["instance_types"] = {
            "label": "Instance Type Set",
            "format": "choices",
            "filename": "netbox-instance-type-choices.txt",
            "content": generate_choice_set(instance_types),
            "count": len(instance_types),
            "instructions": (
                "NetBox → Customization → Custom Field Choice Sets → Instance Type "
                "→ Extra choices → append these lines"
            ),
        }

    resource_groups = analysis.get("resource_groups", {}).get("missing", [])
    if resource_groups:
        scripts["resource_groups"] = {
            "label": "Resource Group Set",
            "format": "choices",
            "filename": "netbox-resource-group-choices.txt",
            "content": generate_choice_set(resource_groups),
            "count": len(resource_groups),
            "instructions": (
                "NetBox → Customization → Custom Field Choice Sets → Resource Group "
                "→ Extra choices → append these lines"
            ),
        }

    return scripts


def generate_combined_import_bundle(scripts: Dict[str, Dict[str, str]]) -> str:
    """Concatenate every generated payload into one annotated text bundle."""
    if not scripts:
        return "# All required NetBox objects already exist. Nothing to import.\n"

    blocks: List[str] = [
        "# NetBox Import Bundle - generated by NetBox Hub",
        "# Each section below is pasted into its own NetBox import form.",
        "",
    ]
    for data in scripts.values():
        blocks.append("=" * 70)
        blocks.append(f"{data['label']}:  ({data['count']} missing)")
        blocks.append(f"# {data['instructions']}")
        blocks.append("=" * 70)
        blocks.append(data["content"])
        blocks.append("")
    return "\n".join(blocks)
