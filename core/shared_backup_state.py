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
        Newer uploads override older data for matching endpoints.
        
        Args:
            backup_data: Parsed JSON backup data
            filename: Original filename for tracking
            
        Returns:
            BackupInspector instance
        """
        cls.initialize()
        
        inspector = BackupInspector(backup_data, filename)
        
        # Get new objects from inspector
        new_objects = inspector.inspect_all_objects()
        
        # Get existing registry (may have CSV overrides)
        existing_registry = st.session_state.get(cls.OBJECT_REGISTRY_KEY, {})
        
        # Merge: JSON data wins for any endpoint unless CSV is newer
        from datetime import datetime
        
        for endpoint, new_metadata in new_objects.items():
            new_timestamp = new_metadata.get("timestamp", "")
            
            if endpoint in existing_registry:
                # Check if existing data is newer
                existing_timestamp = existing_registry[endpoint].get("timestamp", "")
                
                try:
                    new_dt = datetime.fromisoformat(new_timestamp.replace("UTC", "").strip())
                    existing_dt = datetime.fromisoformat(existing_timestamp.replace("UTC", "").strip())
                    
                    # Keep newer data
                    if new_dt >= existing_dt:
                        existing_registry[endpoint] = new_metadata
                    # else: keep existing (it's newer, e.g., from CSV uploaded after old JSON)
                except:
                    # If timestamp parsing fails, new data wins
                    existing_registry[endpoint] = new_metadata
            else:
                # New endpoint, add it
                existing_registry[endpoint] = new_metadata
        
        # Store inspector and updated registry
        st.session_state[cls.INSPECTOR_KEY] = inspector
        st.session_state[cls.OBJECT_REGISTRY_KEY] = existing_registry
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
        """Check if a backup is loaded or CSV overrides exist."""
        cls.initialize()
        if cls.get_inspector() is not None:
            return True
        registry = st.session_state.get(cls.OBJECT_REGISTRY_KEY, {})
        return bool(registry)
    
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
    def remove_csv_entry(cls, endpoint: str):
        """
        Remove a specific CSV entry from the object registry.
        
        Args:
            endpoint: Object endpoint to remove (e.g., "users/owner-groups")
        """
        cls.initialize()
        
        registry = st.session_state.get(cls.OBJECT_REGISTRY_KEY, {})
        
        # Only remove if it's a CSV entry (not from JSON backup)
        if endpoint in registry:
            metadata = registry[endpoint]
            source_type = metadata.get("source_type", "json")
            
            # Only allow removing CSV entries, not JSON backup entries
            if source_type == "csv" or metadata.get("source", "").lower().endswith((".csv", ".xlsx")):
                del registry[endpoint]
                st.session_state[cls.OBJECT_REGISTRY_KEY] = registry
                return True
        
        return False
    
    @classmethod
    def add_csv_override(cls, endpoint: str, count: int, source: str, timestamp: str):
        """
        Register a CSV override for an object type.
        Latest upload wins - CSV overrides JSON only if CSV is newer.
        
        Args:
            endpoint: Object endpoint
            count: Record count from CSV
            source: CSV source filename
            timestamp: Upload timestamp
        """
        cls.initialize()
        
        from datetime import datetime
        
        # Get existing registry
        registry = st.session_state.get(cls.OBJECT_REGISTRY_KEY, {})
        
        # Check if endpoint exists and compare timestamps
        should_override = True
        if endpoint in registry:
            existing_timestamp = registry[endpoint].get("timestamp", "")
            try:
                csv_dt = datetime.fromisoformat(timestamp.replace("UTC", "").strip())
                existing_dt = datetime.fromisoformat(existing_timestamp.replace("UTC", "").strip())
                
                # Only override if CSV is newer or equal
                should_override = csv_dt >= existing_dt
            except:
                # If timestamp parsing fails, allow override
                should_override = True
        
        if should_override:
            if endpoint in registry:
                # Update existing endpoint with CSV data
                registry[endpoint]["count"] = count
                registry[endpoint]["source"] = source
                registry[endpoint]["timestamp"] = timestamp
                registry[endpoint]["source_type"] = "csv"
            else:
                # Create new entry for CSV-only data
                inspector = cls.get_inspector()
                label = inspector._format_label(endpoint) if inspector else endpoint.replace("/", " ").replace("_", " ").title()
                
                registry[endpoint] = {
                    "label": label,
                    "count": count,
                    "source": source,
                    "timestamp": timestamp,
                    "endpoint": endpoint,
                    "sample_keys": [],
                    "source_type": "csv"
                }
            
            st.session_state[cls.OBJECT_REGISTRY_KEY] = registry
            
            # Also update CSV overrides tracking
            overrides = st.session_state.get(cls.CSV_OVERRIDES_KEY, {})
            overrides[endpoint] = {
                "count": count,
                "source": source,
                "timestamp": timestamp,
                "source_type": "csv"
            }
            st.session_state[cls.CSV_OVERRIDES_KEY] = overrides
    
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
    def clear_csv_only(cls):
        """Clear only CSV entries, preserving JSON backup data."""
        cls.initialize()
        
        registry = st.session_state.get(cls.OBJECT_REGISTRY_KEY, {})
        
        # Remove only CSV entries
        csv_endpoints = [
            endpoint for endpoint, metadata in registry.items()
            if metadata.get("source_type") == "csv" or 
               metadata.get("source", "").lower().endswith((".csv", ".xlsx"))
        ]
        
        for endpoint in csv_endpoints:
            del registry[endpoint]
        
        st.session_state[cls.OBJECT_REGISTRY_KEY] = registry
        st.session_state[cls.CSV_OVERRIDES_KEY] = {}
        
        return len(csv_endpoints)
    
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
