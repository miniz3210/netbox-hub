"""
Shared Backup State Manager
Provides unified backup inspection state across IPAM and Naming tabs.
"""

import streamlit as st
from typing import Dict, Any, Optional, List
from core.dynamic_backup_inspector import BackupInspector, load_backup_from_file, load_backup_from_json_string


class SharedBackupState:
    """Manages shared backup inspection state across tabs."""
    
    # Session state keys
    INSPECTOR_KEY = "netbox_backup_inspector"
    OBJECT_REGISTRY_KEY = "netbox_object_registry"
    CUSTOM_FIELDS_KEY = "netbox_custom_fields"
    CHOICE_SETS_KEY = "netbox_choice_sets"
    CSV_OVERRIDES_KEY = "netbox_csv_overrides"
    
    @classmethod
    def initialize(cls):
        """Initialize session state keys if not present."""
        if cls.INSPECTOR_KEY not in st.session_state:
            st.session_state[cls.INSPECTOR_KEY] = None
        if cls.OBJECT_REGISTRY_KEY not in st.session_state:
            st.session_state[cls.OBJECT_REGISTRY_KEY] = {}
        if cls.CUSTOM_FIELDS_KEY not in st.session_state:
            st.session_state[cls.CUSTOM_FIELDS_KEY] = {}
        if cls.CHOICE_SETS_KEY not in st.session_state:
            st.session_state[cls.CHOICE_SETS_KEY] = {}
        if cls.CSV_OVERRIDES_KEY not in st.session_state:
            st.session_state[cls.CSV_OVERRIDES_KEY] = {}
    
    @classmethod
    def load_backup(cls, backup_data: Dict[str, Any], filename: str = "NetBox_Backup.json") -> BackupInspector:
        """
        Load a NetBox backup and update shared state.
        
        Args:
            backup_data: Parsed JSON backup data
            filename: Original filename for tracking
            
        Returns:
            BackupInspector instance
        """
        cls.initialize()
        
        inspector = BackupInspector(backup_data, filename)
        
        # Store inspector and refresh all registries
        st.session_state[cls.INSPECTOR_KEY] = inspector
        st.session_state[cls.OBJECT_REGISTRY_KEY] = inspector.inspect_all_objects()
        st.session_state[cls.CUSTOM_FIELDS_KEY] = inspector.inspect_custom_fields()
        st.session_state[cls.CHOICE_SETS_KEY] = inspector.inspect_choice_sets()
        
        return inspector
    
    @classmethod
    def get_inspector(cls) -> Optional[BackupInspector]:
        """Get the current backup inspector instance."""
        cls.initialize()
        return st.session_state.get(cls.INSPECTOR_KEY)
    
    @classmethod
    def has_backup(cls) -> bool:
        """Check if a backup is loaded."""
        return cls.get_inspector() is not None
    
    @classmethod
    def get_object_registry(cls) -> Dict[str, Dict[str, Any]]:
        """
        Get the complete object registry.
        
        Returns:
            Dictionary mapping endpoints to object metadata
        """
        cls.initialize()
        return st.session_state.get(cls.OBJECT_REGISTRY_KEY, {})
    
    @classmethod
    def get_custom_fields(cls) -> Dict[str, Dict[str, Any]]:
        """
        Get all custom field definitions.
        
        Returns:
            Dictionary mapping field names to definitions
        """
        cls.initialize()
        return st.session_state.get(cls.CUSTOM_FIELDS_KEY, {})
    
    @classmethod
    def get_choice_sets(cls) -> Dict[str, Dict[str, Any]]:
        """
        Get all custom field choice sets.
        
        Returns:
            Dictionary mapping choice set names to definitions
        """
        cls.initialize()
        return st.session_state.get(cls.CHOICE_SETS_KEY, {})
    
    @classmethod
    def get_field_choices(cls, field_name: str) -> List[str]:
        """
        Get choice values for a specific custom field.
        
        Args:
            field_name: Custom field name/key
            
        Returns:
            List of choice values
        """
        inspector = cls.get_inspector()
        if inspector:
            return inspector.get_custom_field_choices(field_name)
        
        # Fallback: check cached choice sets
        choice_sets = cls.get_choice_sets()
        for set_data in choice_sets.values():
            if set_data.get("field_key") == field_name:
                return set_data.get("choices", [])
        
        # Fallback: check cached custom fields
        custom_fields = cls.get_custom_fields()
        if field_name in custom_fields:
            return custom_fields[field_name].get("choices", [])
        
        return []
    
    @classmethod
    def get_object_count(cls, endpoint: str) -> int:
        """
        Get the count for a specific object type.
        
        Args:
            endpoint: Object endpoint (e.g., "dcim/devices")
            
        Returns:
            Object count
        """
        registry = cls.get_object_registry()
        if endpoint in registry:
            return registry[endpoint].get("count", 0)
        return 0
    
    @classmethod
    def add_csv_override(cls, endpoint: str, count: int, source: str, timestamp: str):
        """
        Register a CSV override for an object type.
        
        Args:
            endpoint: Object endpoint
            count: Record count from CSV
            source: CSV source filename
            timestamp: Upload timestamp
        """
        cls.initialize()
        overrides = st.session_state[cls.CSV_OVERRIDES_KEY]
        overrides[endpoint] = {
            "count": count,
            "source": source,
            "timestamp": timestamp
        }
        st.session_state[cls.CSV_OVERRIDES_KEY] = overrides
        
        # Update object registry with CSV override
        inspector = cls.get_inspector()
        if inspector:
            from core.dynamic_backup_inspector import merge_csv_overrides
            merged = merge_csv_overrides(inspector, overrides)
            st.session_state[cls.OBJECT_REGISTRY_KEY] = merged
    
    @classmethod
    def generate_backup_summary(cls) -> str:
        """
        Generate formatted backup contents summary.
        
        Returns:
            Markdown-formatted summary
        """
        inspector = cls.get_inspector()
        if inspector:
            return inspector.generate_backup_contents_summary()
        return "No backup loaded."
    
    @classmethod
    def generate_choice_sets_summary(cls) -> str:
        """
        Generate formatted choice sets summary.
        
        Returns:
            Markdown-formatted summary
        """
        inspector = cls.get_inspector()
        if inspector:
            return inspector.generate_choice_sets_summary()
        return "No choice sets found."
    
    @classmethod
    def clear(cls):
        """Clear all backup state."""
        cls.initialize()
        st.session_state[cls.INSPECTOR_KEY] = None
        st.session_state[cls.OBJECT_REGISTRY_KEY] = {}
        st.session_state[cls.CUSTOM_FIELDS_KEY] = {}
        st.session_state[cls.CHOICE_SETS_KEY] = {}
        st.session_state[cls.CSV_OVERRIDES_KEY] = {}
    
    @classmethod
    def get_objects_by_type(cls, endpoint: str) -> List[Dict[str, Any]]:
        """
        Get actual object data for a specific endpoint.
        
        Args:
            endpoint: Object endpoint (e.g., "dcim/devices")
            
        Returns:
            List of objects
        """
        inspector = cls.get_inspector()
        if inspector:
            return inspector.get_object_data(endpoint)
        return []
    
    @classmethod
    def get_custom_field_for_object_type(cls, object_type: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all custom fields applicable to a specific object type.
        
        Args:
            object_type: Object type (e.g., "dcim.device", "virtualization.virtualmachine")
            
        Returns:
            Dictionary of applicable custom fields
        """
        all_fields = cls.get_custom_fields()
        applicable = {}
        
        for field_name, field_def in all_fields.items():
            object_types = field_def.get("object_types", [])
            if not object_types or object_type in object_types:
                applicable[field_name] = field_def
        
        return applicable
