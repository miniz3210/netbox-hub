"""
Dynamic Field Helper

Utilities for working with dynamic fields across importers and UI components.
Provides field mapping, validation, and discovery helpers.
"""

import logging
from typing import Dict, List, Any, Optional
from core.field_registry import FieldRegistry
from core.db_manager_wrapper import DatabaseManager

logger = logging.getLogger(__name__)


class DynamicFieldHelper:
    """Helper class for dynamic field operations."""
    
    def __init__(self):
        """Initialize with field registry."""
        try:
            self.registry = FieldRegistry(DatabaseManager())
            self._enabled = True
        except Exception as e:
            logger.warning(f"Failed to initialize dynamic field registry: {e}")
            self._enabled = False
            self.registry = None
    
    def get_custom_fields_for_object(self, object_type: str) -> Dict[str, Dict]:
        """
        Get all custom fields defined for an object type.
        
        Args:
            object_type: NetBox object type (e.g., "virtualization_virtual_machines")
            
        Returns:
            Dict mapping field_name to field definition
        """
        if not self._enabled:
            return {}
        
        try:
            import sqlite3
            conn = DatabaseManager().get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT field_config FROM netbox_schema
                WHERE object_type = ?
            """, (object_type,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row:
                import json
                config = json.loads(row[0])
                return config.get("custom_fields", {})
            
            return {}
        except Exception as e:
            logger.warning(f"Failed to get custom fields for {object_type}: {e}")
            return {}
    
    def validate_custom_field_value(self, object_type: str, field_name: str, 
                                   value: Any) -> tuple[bool, Optional[str]]:
        """
        Validate a custom field value against its definition.
        
        Args:
            object_type: NetBox object type
            field_name: Custom field name
            value: Value to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self._enabled:
            return (True, None)  # Skip validation if registry not available
        
        try:
            field_def = self.registry.get_custom_field_definition(object_type, field_name)
            
            if not field_def:
                return (True, None)  # Unknown fields are allowed
            
            # Check required fields
            if field_def.get("required") and not value:
                return (False, f"Field '{field_name}' is required")
            
            # Type-specific validation
            field_type = field_def.get("type", "text")
            
            if field_type == "integer":
                try:
                    int(value)
                except (ValueError, TypeError):
                    return (False, f"Field '{field_name}' must be an integer")
            
            elif field_type == "boolean":
                if value not in [True, False, "true", "false", "yes", "no", 1, 0]:
                    return (False, f"Field '{field_name}' must be a boolean")
            
            return (True, None)
            
        except Exception as e:
            logger.warning(f"Validation error for {field_name}: {e}")
            return (True, None)  # Allow on error
    
    def map_csv_to_custom_fields(self, csv_headers: List[str], 
                                 object_type: str = "virtualization_virtual_machines") -> Dict[str, str]:
        """
        Map CSV headers to custom field names.
        
        Automatically discovers custom fields from headers like:
        - cf_owner -> owner
        - tag_application -> application
        - custom_backup_status -> backup_status
        
        Args:
            csv_headers: List of CSV column headers
            object_type: Target NetBox object type
            
        Returns:
            Dict mapping csv_header -> custom_field_name
        """
        mapping = {}
        
        # Get known custom fields
        custom_fields = self.get_custom_fields_for_object(object_type)
        known_field_names = set(custom_fields.keys())
        
        for header in csv_headers:
            header_lower = header.lower().strip()
            
            # Remove common prefixes
            for prefix in ['cf_', 'custom_', 'tag_']:
                if header_lower.startswith(prefix):
                    field_name = header_lower[len(prefix):]
                    
                    # Check if this matches a known custom field
                    if field_name in known_field_names:
                        mapping[header] = field_name
                    else:
                        # Also try with underscores removed
                        field_name_normalized = field_name.replace('_', '')
                        for known_field in known_field_names:
                            if known_field.replace('_', '') == field_name_normalized:
                                mapping[header] = known_field
                                break
                    
                    break
        
        return mapping
    
    def get_all_vm_custom_fields(self) -> List[str]:
        """
        Get list of all custom field names for virtual machines.
        
        Returns:
            List of custom field names
        """
        custom_fields = self.get_custom_fields_for_object("virtualization_virtual_machines")
        return list(custom_fields.keys())
    
    def extract_custom_fields_from_dict(self, data: Dict[str, Any], 
                                       object_type: str = "virtualization_virtual_machines") -> Dict[str, Any]:
        """
        Extract custom fields from a flat dictionary.
        
        Looks for keys matching known custom fields and returns them in a separate dict.
        
        Args:
            data: Dictionary with mixed native and custom field data
            object_type: NetBox object type
            
        Returns:
            Dict of custom_field_name -> value
        """
        custom_fields = self.get_custom_fields_for_object(object_type)
        known_field_names = set(custom_fields.keys())
        
        extracted = {}
        
        for key, value in data.items():
            # Check direct match
            if key in known_field_names:
                extracted[key] = value
                continue
            
            # Check with prefix removal
            key_lower = key.lower()
            for prefix in ['cf_', 'custom_', 'tag_']:
                if key_lower.startswith(prefix):
                    field_name = key_lower[len(prefix):]
                    if field_name in known_field_names:
                        extracted[field_name] = value
                        break
        
        return extracted
    
    def add_discovered_field(self, object_type: str, field_name: str, 
                           field_label: str = None, field_type: str = "text") -> bool:
        """
        Add a newly discovered custom field to the registry.
        
        Args:
            object_type: NetBox object type
            field_name: Field identifier
            field_label: Display label (defaults to titleized field_name)
            field_type: Field type
            
        Returns:
            True if successful
        """
        if not self._enabled:
            return False
        
        if not field_label:
            field_label = field_name.replace("_", " ").title()
        
        try:
            return self.registry.add_custom_field_manually(
                object_type=object_type,
                field_name=field_name,
                field_label=field_label,
                field_type=field_type,
                required=False
            )
        except Exception as e:
            logger.error(f"Failed to add discovered field {field_name}: {e}")
            return False


# Singleton instance
_helper_instance = None

def get_field_helper() -> DynamicFieldHelper:
    """Get or create the global DynamicFieldHelper instance."""
    global _helper_instance
    if _helper_instance is None:
        _helper_instance = DynamicFieldHelper()
    return _helper_instance
