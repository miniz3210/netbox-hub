"""
Dynamic NetBox Backup Inspector
Provides schema-agnostic inspection and loading of NetBox backup JSON files.
Eliminates hardcoded object lists and custom field mappings.
"""

import json
import re
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from pathlib import Path
import logging

logger = logging.getLogger("netbox-hub")


class BackupInspector:
    """Dynamically inspect and parse NetBox backup JSON files."""
    
    def __init__(self, backup_data: Dict[str, Any], source_filename: str = "NetBox_Backup.json"):
        """
        Initialize the backup inspector.
        
        Args:
            backup_data: Parsed JSON backup data
            source_filename: Original filename for tracking
        """
        self.backup_data = backup_data
        self.source_filename = source_filename
        self.ingestion_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._object_registry = {}
        self._custom_fields_registry = {}
        self._choice_sets_registry = {}
        
    def inspect_all_objects(self) -> Dict[str, Dict[str, Any]]:
        """
        Dynamically inspect all object types in the backup.
        
        Returns:
            Dictionary mapping object type keys to their metadata:
            {
                "dcim/devices": {
                    "label": "Devices",
                    "count": 150,
                    "source": "NetBox_Full_Backup_20260911.json",
                    "timestamp": "2026-09-11 09:03:00",
                    "endpoint": "dcim/devices",
                    "sample_keys": ["name", "device_type", "site", ...]
                }
            }
        """
        if self._object_registry:
            return self._object_registry
            
        registry = {}
        
        # Iterate through all top-level keys in the backup
        for key, value in self.backup_data.items():
            # Skip metadata keys
            if key in ["_metadata", "metadata", "version", "timestamp"]:
                continue
                
            # Determine if this is an object collection
            if isinstance(value, list):
                count = len(value)
                label = self._format_label(key)
                
                # Extract sample keys from first item
                sample_keys = []
                if count > 0 and isinstance(value[0], dict):
                    sample_keys = list(value[0].keys())[:10]  # First 10 keys
                
                registry[key] = {
                    "label": label,
                    "count": count,
                    "source": self.source_filename,
                    "timestamp": self.ingestion_timestamp,
                    "endpoint": key,
                    "sample_keys": sample_keys,
                    "data": value  # Reference to actual data
                }
            elif isinstance(value, dict):
                # Handle nested structure (e.g., {"dcim": {"devices": [...], "sites": [...]}})
                for sub_key, sub_value in value.items():
                    if isinstance(sub_value, list):
                        count = len(sub_value)
                        endpoint = f"{key}/{sub_key}"
                        label = self._format_label(endpoint)
                        
                        sample_keys = []
                        if count > 0 and isinstance(sub_value[0], dict):
                            sample_keys = list(sub_value[0].keys())[:10]
                        
                        registry[endpoint] = {
                            "label": label,
                            "count": count,
                            "source": self.source_filename,
                            "timestamp": self.ingestion_timestamp,
                            "endpoint": endpoint,
                            "sample_keys": sample_keys,
                            "data": sub_value
                        }
        
        self._object_registry = registry
        return registry
    
    def inspect_custom_fields(self) -> Dict[str, Dict[str, Any]]:
        """
        Dynamically inspect all custom field definitions.
        
        Returns:
            Dictionary mapping custom field keys to their definitions:
            {
                "instance_type": {
                    "name": "Instance Type",
                    "label": "Instance Type",
                    "type": "select",
                    "object_types": ["dcim.device", "virtualization.virtualmachine"],
                    "choices": [...],
                    "required": False
                }
            }
        """
        if self._custom_fields_registry:
            return self._custom_fields_registry
            
        registry = {}
        
        # Look for custom fields in common locations
        cf_locations = [
            "extras/custom-fields",
            "extras/custom_fields",
            "custom-fields",
            "custom_fields"
        ]
        
        for location in cf_locations:
            data = self._get_nested_value(location)
            if data and isinstance(data, list):
                for field in data:
                    if not isinstance(field, dict):
                        continue
                        
                    field_name = field.get("name") or field.get("slug") or field.get("key")
                    if not field_name:
                        continue
                    
                    registry[field_name] = {
                        "name": field.get("name", field_name),
                        "label": field.get("label", self._format_label(field_name)),
                        "type": field.get("type", "text"),
                        "object_types": field.get("object_types", []),
                        "choices": field.get("choices", []),
                        "required": field.get("required", False),
                        "description": field.get("description", ""),
                        "source": self.source_filename,
                        "timestamp": self.ingestion_timestamp
                    }
                break  # Stop after finding custom fields
        
        self._custom_fields_registry = registry
        return registry
    
    def inspect_choice_sets(self) -> Dict[str, Dict[str, Any]]:
        """
        Dynamically inspect all custom field choice sets.
        
        Returns:
            Dictionary mapping choice set names to their definitions:
            {
                "instance_type_choices": {
                    "name": "Instance Type Choices",
                    "field_key": "instance_type",
                    "choices": ["t2.micro", "t2.small", ...],
                    "count": 25,
                    "source": "NetBox_Full_Backup.json",
                    "timestamp": "2026-09-11 09:03:00"
                }
            }
        """
        if self._choice_sets_registry:
            return self._choice_sets_registry
            
        registry = {}
        
        # Look for choice sets in common locations
        cs_locations = [
            "extras/custom-field-choice-sets",
            "extras/custom_field_choice_sets",
            "custom-field-choice-sets",
            "custom_field_choice_sets",
            "choice-sets",
            "choice_sets"
        ]
        
        for location in cs_locations:
            data = self._get_nested_value(location)
            if data and isinstance(data, list):
                for choice_set in data:
                    if not isinstance(choice_set, dict):
                        continue
                    
                    set_name = choice_set.get("name") or choice_set.get("slug")
                    if not set_name:
                        continue
                    
                    choices = choice_set.get("choices", [])
                    extra_choices = choice_set.get("extra_choices", [])
                    all_choices = choices + extra_choices
                    
                    # Extract choice values (might be list of dicts or list of strings)
                    choice_values = []
                    for choice in all_choices:
                        if isinstance(choice, dict):
                            choice_values.append(choice.get("value") or choice.get("name") or str(choice))
                        else:
                            choice_values.append(str(choice))
                    
                    registry[set_name] = {
                        "name": set_name,
                        "label": self._format_label(set_name),
                        "field_key": choice_set.get("custom_field") or set_name.replace("_choices", "").replace("-choices", ""),
                        "choices": choice_values,
                        "count": len(choice_values),
                        "source": self.source_filename,
                        "timestamp": self.ingestion_timestamp,
                        "raw_data": choice_set
                    }
                break  # Stop after finding choice sets
        
        self._choice_sets_registry = registry
        return registry
    
    def get_object_data(self, endpoint: str) -> List[Dict[str, Any]]:
        """
        Get the actual data for a specific object type.
        
        Args:
            endpoint: Object endpoint (e.g., "dcim/devices", "ipam/prefixes")
            
        Returns:
            List of objects
        """
        objects = self.inspect_all_objects()
        if endpoint in objects:
            return objects[endpoint].get("data", [])
        return []
    
    def get_custom_field_choices(self, field_name: str) -> List[str]:
        """
        Get choices for a specific custom field.
        
        Args:
            field_name: Custom field name/key
            
        Returns:
            List of choice values
        """
        # First check choice sets
        choice_sets = self.inspect_choice_sets()
        for set_name, set_data in choice_sets.items():
            if set_data.get("field_key") == field_name:
                return set_data.get("choices", [])
        
        # Fall back to custom field definition
        custom_fields = self.inspect_custom_fields()
        if field_name in custom_fields:
            return custom_fields[field_name].get("choices", [])
        
        return []
    
    def generate_backup_contents_summary(self) -> str:
        """
        Generate a formatted summary of backup contents.
        
        Returns:
            Markdown-formatted string summarizing all objects
        """
        objects = self.inspect_all_objects()
        
        if not objects:
            return "No objects found in backup."
        
        lines = []
        lines.append(f"**Backup contents** ({len(objects)} object types):")
        lines.append("")
        
        # Sort by label for consistent display
        sorted_objects = sorted(objects.items(), key=lambda x: x[1]["label"])
        
        for endpoint, metadata in sorted_objects:
            label = metadata["label"]
            count = metadata["count"]
            source = metadata["source"]
            timestamp = metadata["timestamp"]
            lines.append(f"• **{label}** (`{endpoint}`): {count} — {source} {timestamp}")
        
        return "\n".join(lines)
    
    def generate_choice_sets_summary(self) -> str:
        """
        Generate a formatted summary of custom field choice sets.
        
        Returns:
            Markdown-formatted string summarizing all choice sets
        """
        choice_sets = self.inspect_choice_sets()
        
        if not choice_sets:
            return "No custom field choice sets found in backup."
        
        lines = []
        lines.append(f"**Custom field choice sets** ({len(choice_sets)}):")
        lines.append("")
        
        # Sort by name for consistent display
        sorted_sets = sorted(choice_sets.items(), key=lambda x: x[1]["name"])
        
        for set_name, set_data in sorted_sets:
            label = set_data["label"]
            field_key = set_data["field_key"]
            count = set_data["count"]
            source = set_data["source"]
            timestamp = set_data["timestamp"]
            lines.append(f"• **{label}** → `{field_key}` : {count} values — {source} {timestamp}")
        
        return "\n".join(lines)
    
    def _format_label(self, key: str) -> str:
        """
        Convert endpoint/key to human-readable label.
        
        Args:
            key: Endpoint key (e.g., "dcim/devices", "ipam_prefixes")
            
        Returns:
            Formatted label (e.g., "Devices", "Prefixes")
        """
        # Remove common prefixes
        key = key.replace("netbox_", "").replace("extras/", "").replace("extras_", "")
        
        # Split by / or _
        parts = re.split(r'[/_-]', key)
        
        # Remove category prefix if redundant (e.g., "dcim devices" -> "devices")
        if len(parts) > 1:
            parts = parts[1:]  # Use the second part (e.g., "devices" from "dcim/devices")
        
        # Title case each part
        formatted_parts = []
        for part in parts:
            # Handle special acronyms
            if part.upper() in ["IPAM", "DCIM", "VM", "VMS", "IP", "VLAN", "VRF", "ASN", "API"]:
                formatted_parts.append(part.upper())
            else:
                formatted_parts.append(part.capitalize())
        
        return " ".join(formatted_parts)
    
    def _get_nested_value(self, path: str) -> Any:
        """
        Get value from nested dictionary using path notation.
        
        Args:
            path: Dot or slash-separated path (e.g., "extras/custom-fields")
            
        Returns:
            Value at path or None
        """
        parts = re.split(r'[/.]', path)
        value = self.backup_data
        
        for part in parts:
            if isinstance(value, dict):
                # Try exact key match
                if part in value:
                    value = value[part]
                # Try with underscores
                elif part.replace("-", "_") in value:
                    value = value[part.replace("-", "_")]
                # Try with hyphens
                elif part.replace("_", "-") in value:
                    value = value[part.replace("_", "-")]
                else:
                    return None
            else:
                return None
        
        return value


def load_backup_from_file(file_path: str) -> Optional[BackupInspector]:
    """
    Load and inspect a NetBox backup JSON file.
    
    Args:
        file_path: Path to backup JSON file
        
    Returns:
        BackupInspector instance or None if loading fails
    """
    try:
        path = Path(file_path)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return BackupInspector(data, path.name)
    except Exception as e:
        logger.error(f"Failed to load backup from {file_path}: {e}")
        return None


def load_backup_from_json_string(json_string: str, filename: str = "backup.json") -> Optional[BackupInspector]:
    """
    Load and inspect a NetBox backup from JSON string.
    
    Args:
        json_string: JSON string content
        filename: Source filename for tracking
        
    Returns:
        BackupInspector instance or None if loading fails
    """
    try:
        data = json.loads(json_string)
        return BackupInspector(data, filename)
    except Exception as e:
        logger.error(f"Failed to parse backup JSON: {e}")
        return None


def merge_csv_overrides(inspector: BackupInspector, csv_metadata: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Merge CSV upload metadata with backup object registry.
    
    Args:
        inspector: BackupInspector instance
        csv_metadata: Metadata from CSV uploads {endpoint: {count, source, timestamp}}
        
    Returns:
        Merged object registry with CSV overrides where applicable
    """
    registry = inspector.inspect_all_objects().copy()
    
    for endpoint, csv_meta in csv_metadata.items():
        if endpoint in registry:
            # Override with CSV data
            registry[endpoint]["count"] = csv_meta.get("count", registry[endpoint]["count"])
            registry[endpoint]["source"] = csv_meta.get("source", registry[endpoint]["source"])
            registry[endpoint]["timestamp"] = csv_meta.get("timestamp", registry[endpoint]["timestamp"])
            registry[endpoint]["source_type"] = "csv"
        else:
            # Add new CSV-only entry
            registry[endpoint] = {
                "label": inspector._format_label(endpoint),
                "count": csv_meta.get("count", 0),
                "source": csv_meta.get("source", "CSV Upload"),
                "timestamp": csv_meta.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "endpoint": endpoint,
                "sample_keys": [],
                "source_type": "csv"
            }
    
    return registry
