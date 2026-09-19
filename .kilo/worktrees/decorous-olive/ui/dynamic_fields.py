"""
UI Field Component Builder

Provides UI components that automatically adapt to dynamic field definitions.
Used by Textual UI tabs to display NetBox objects with custom fields.
"""

import logging
from typing import List, Dict, Any, Optional
from core.field_registry import FieldRegistry
from core.db_manager_wrapper import DatabaseManager

logger = logging.getLogger(__name__)


class UIFieldBuilder:
    """Builds UI field displays from dynamic field registry."""
    
    def __init__(self):
        """Initialize with field registry."""
        try:
            self.registry = FieldRegistry(DatabaseManager())
            self._enabled = True
        except Exception as e:
            logger.warning(f"Failed to initialize field registry: {e}")
            self._enabled = False
            self.registry = None
    
    def get_display_fields(self, object_type: str) -> List[tuple[str, str]]:
        """
        Get display fields for an object type.
        
        Args:
            object_type: NetBox object type
            
        Returns:
            List of (label, field_path) tuples
        """
        if not self._enabled:
            return []
        
        try:
            return self.registry.get_field_spec(object_type)
        except Exception as e:
            logger.warning(f"Failed to get display fields for {object_type}: {e}")
            return []
    
    def format_field_value(self, obj: Dict[str, Any], field_path: str) -> str:
        """
        Extract and format a field value from an object.
        
        Supports nested paths like "site.name" and "vlan.vid".
        
        Args:
            obj: NetBox object dict
            field_path: Dot-separated field path
            
        Returns:
            Formatted string value
        """
        value = self._dig_value(obj, field_path)
        return self._format_value(value)
    
    def _dig_value(self, obj: Dict[str, Any], path: str) -> Any:
        """Navigate nested dictionary by dot-separated path."""
        current = obj
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current
    
    def _format_value(self, value: Any) -> str:
        """Format a value for display."""
        if value is None or value == "":
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, dict):
            # Extract display name from nested object
            for key in ("name", "display", "label", "value", "address"):
                if key in value and value[key]:
                    return str(value[key])
            return ""
        if isinstance(value, (list, tuple)):
            parts = [self._format_value(item) for item in value]
            return ", ".join(p for p in parts if p)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()
    
    def build_summary_text(self, object_type: str, obj: Dict[str, Any], 
                          exclude_site: bool = True) -> str:
        """
        Build a summary text for an object using display fields.
        
        Args:
            object_type: NetBox object type
            obj: Object data
            exclude_site: If True, skip site field to avoid duplication
            
        Returns:
            Formatted summary string
        """
        display_fields = self.get_display_fields(object_type)
        
        if not display_fields:
            # Fall back to showing all fields
            return self._build_generic_summary(obj)
        
        parts = []
        site_value = self.format_field_value(obj, "site") if exclude_site else None
        
        for label, field_path in display_fields:
            if exclude_site and label == "Site":
                continue
            
            value = self.format_field_value(obj, field_path)
            if value:
                parts.append(f"{label}: {value}")
        
        # Add custom fields if not already in display fields
        custom_fields = obj.get("custom_fields", {})
        if isinstance(custom_fields, dict):
            for cf_name, cf_value in custom_fields.items():
                # Check if this custom field is already in display fields
                already_shown = any(fp == cf_name or fp == f"custom_fields.{cf_name}" 
                                   for _, fp in display_fields)
                if not already_shown:
                    formatted = self._format_value(cf_value)
                    if formatted:
                        cf_label = cf_name.replace("_", " ").title()
                        parts.append(f"{cf_label}: {formatted}")
        
        return " | ".join(parts)
    
    def _build_generic_summary(self, obj: Dict[str, Any]) -> str:
        """Build generic summary when no display fields are defined."""
        parts = []
        for key, value in obj.items():
            if key in ("id", "url", "display_url", "display", "name", "tags",
                      "custom_fields", "created", "last_updated", "comments"):
                continue
            formatted = self._format_value(value)
            if formatted:
                label = key.replace("_", " ").title()
                parts.append(f"{label}: {formatted}")
        
        # Add custom fields
        custom_fields = obj.get("custom_fields", {})
        if isinstance(custom_fields, dict):
            for cf_name, cf_value in custom_fields.items():
                formatted = self._format_value(cf_value)
                if formatted:
                    cf_label = cf_name.replace("_", " ").title()
                    parts.append(f"{cf_label}: {formatted}")
        
        return " | ".join(parts)
    
    def get_field_value_safe(self, obj: Dict[str, Any], field_name: str, 
                            default: str = "") -> str:
        """
        Safely get a field value from an object.
        
        Checks both top-level fields and custom_fields.
        
        Args:
            obj: Object data
            field_name: Field name to look for
            default: Default value if not found
            
        Returns:
            Formatted field value or default
        """
        # Try top-level first
        if field_name in obj:
            return self._format_value(obj[field_name])
        
        # Try custom fields
        custom_fields = obj.get("custom_fields", {})
        if isinstance(custom_fields, dict) and field_name in custom_fields:
            return self._format_value(custom_fields[field_name])
        
        # Try nested path
        try:
            value = self._dig_value(obj, field_name)
            if value is not None:
                return self._format_value(value)
        except Exception:
            pass
        
        return default


# Singleton instance
_ui_builder_instance = None

def get_ui_builder() -> UIFieldBuilder:
    """Get or create the global UIFieldBuilder instance."""
    global _ui_builder_instance
    if _ui_builder_instance is None:
        _ui_builder_instance = UIFieldBuilder()
    return _ui_builder_instance
