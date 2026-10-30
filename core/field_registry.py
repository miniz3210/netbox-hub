"""
Dynamic Field Registry System for NetBox Hub

This module provides automatic schema discovery and field management without requiring
code changes when NetBox versions update or custom fields are added.

Architecture:
- Stores field definitions in database (netbox_schema table)
- Auto-discovers fields from NetBox backup JSON
- Provides configuration interface for field visibility and formatting
- Falls back to sensible defaults for new object types
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


class FieldRegistry:
    """Manages dynamic field discovery and configuration for NetBox objects."""
    
    def __init__(self, db_manager):
        """
        Initialize field registry with database connection.
        
        Args:
            db_manager: DatabaseManager instance for schema storage
        """
        self.db = db_manager
        self._cache = {}  # In-memory cache for field specs
        self._schema_version = None
        
    def discover_schema_from_backup(self, backup_data: Dict[str, List[Dict]], 
                                   netbox_version: str = None,
                                   max_object_types: int = 100) -> Dict[str, int]:
        """
        Automatically discover fields from NetBox backup JSON.
        
        OPTIMIZED: Only analyzes custom field definitions and samples one record
        per object type. Very fast even for large backups.
        
        Args:
            backup_data: Parsed NetBox backup JSON
            netbox_version: NetBox version string (e.g., "3.7.0")
            max_object_types: Max object types to process (default 100)
            
        Returns:
            Dict with discovery statistics (objects_found, fields_discovered, etc.)
        """
        stats = {
            "objects_discovered": 0,
            "fields_discovered": 0,
            "custom_fields_found": 0,
            "updated_objects": 0,
            "skipped_empty": 0
        }
        
        logger.info(f"Starting fast schema discovery (NetBox version: {netbox_version})")
        
        # Extract custom field definitions (this is fast, only reads extras_custom_fields)
        custom_fields = self._extract_custom_fields(backup_data)
        stats["custom_fields_found"] = len(custom_fields)
        
        # Batch prepare database updates for speed
        updates = []
        processed = 0
        
        # Discover fields for each object type (only sample first record)
        for object_type, records in backup_data.items():
            if not records or not isinstance(records, list) or len(records) == 0:
                stats["skipped_empty"] += 1
                continue
            
            processed += 1
            if processed > max_object_types:
                break
                
            stats["objects_discovered"] += 1
            
            # Only sample first record - no need to analyze thousands
            sample_record = records[0]
            discovered_fields = self._analyze_record_structure(sample_record, object_type)
            
            # Check if this object has custom fields
            object_custom_fields = custom_fields.get(object_type, [])
            
            # Prepare update (don't execute yet)
            field_count = len(discovered_fields["native"]) + len(discovered_fields["relationships"]) + len(object_custom_fields)
            updates.append({
                "object_type": object_type,
                "native_fields": discovered_fields["native"],
                "custom_fields": object_custom_fields,
                "relationships": discovered_fields["relationships"],
                "netbox_version": netbox_version,
                "field_count": field_count
            })
            
            stats["fields_discovered"] += field_count
        
        # Batch store all schemas at once
        if updates:
            self._batch_store_schemas(updates)
            stats["updated_objects"] = len(updates)
        
        # Clear cache to force reload
        self._cache.clear()
        
        logger.info(f"Fast schema discovery complete: {stats}")
        return stats
    
    def _extract_custom_fields(self, backup_data: Dict) -> Dict[str, List[Dict]]:
        """
        Extract custom field definitions from backup.
        
        Returns:
            Dict mapping object_type to list of custom field definitions
        """
        custom_fields_by_object = {}
        
        # Look for custom field definitions in backup
        cf_definitions = backup_data.get("extras_custom_fields", [])
        
        for cf in cf_definitions:
            # Get content types this custom field applies to
            content_types = cf.get("content_types", [])
            field_info = {
                "name": cf.get("name"),
                "label": cf.get("label") or cf.get("name"),
                "type": cf.get("type"),
                "required": cf.get("required", False),
                "choice_set": cf.get("choice_set"),
                "default": cf.get("default"),
                "description": cf.get("description")
            }
            
            # Map to object types
            for ct in content_types:
                if isinstance(ct, dict):
                    # Format: {"app_label": "dcim", "model": "site"}
                    app_label = ct.get("app_label")
                    model = ct.get("model")
                    object_type = f"{app_label}_{model}s" if model else None
                elif isinstance(ct, str):
                    # Direct string reference
                    object_type = ct
                else:
                    continue
                    
                if object_type:
                    if object_type not in custom_fields_by_object:
                        custom_fields_by_object[object_type] = []
                    custom_fields_by_object[object_type].append(field_info)
        
        return custom_fields_by_object
    
    def _analyze_record_structure(self, record: Dict, object_type: str) -> Dict:
        """
        Analyze a single record to discover field structure.
        
        Returns:
            Dict with 'native' fields and 'relationships'
        """
        native_fields = []
        relationships = []
        
        for field_name, field_value in record.items():
            if field_name in ["id", "url", "display", "custom_fields"]:
                continue
                
            field_info = {
                "name": field_name,
                "label": field_name.replace("_", " ").title(),
                "type": self._infer_field_type(field_value)
            }
            
            # Check if it's a relationship (nested object with id/name)
            if isinstance(field_value, dict) and "id" in field_value:
                field_info["is_relationship"] = True
                relationships.append(field_info)
            else:
                native_fields.append(field_info)
        
        return {
            "native": native_fields,
            "relationships": relationships
        }
    
    def _infer_field_type(self, value: Any) -> str:
        """Infer field type from value."""
        if value is None:
            return "text"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, int):
            return "integer"
        elif isinstance(value, float):
            return "decimal"
        elif isinstance(value, dict):
            return "object"
        elif isinstance(value, list):
            return "array"
        else:
            return "text"
    
    def _store_object_schema(self, object_type: str, native_fields: List[Dict],
                            custom_fields: List[Dict], relationships: List[Dict],
                            netbox_version: str = None) -> int:
        """
        Store discovered schema in database.
        
        Returns:
            Number of fields stored
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # Check if object type already exists
        cursor.execute("""
            SELECT id, field_config FROM netbox_schema 
            WHERE object_type = ?
        """, (object_type,))
        
        existing = cursor.fetchone()
        
        # Build complete field configuration
        all_fields = native_fields + relationships
        field_config = {
            "native_fields": {f["name"]: f for f in native_fields},
            "relationships": {f["name"]: f for f in relationships},
            "custom_fields": {f["name"]: f for f in custom_fields},
            "display_fields": self._select_default_display_fields(all_fields, custom_fields)
        }
        
        now = datetime.utcnow().isoformat()
        
        if existing:
            # Update existing schema, preserve user customizations
            old_config = json.loads(existing[1]) if existing[1] else {}
            
            # Keep user-defined display_fields if they exist
            if "display_fields" in old_config:
                field_config["display_fields"] = old_config["display_fields"]
            
            cursor.execute("""
                UPDATE netbox_schema 
                SET field_config = ?, 
                    netbox_version = ?,
                    updated_at = ?
                WHERE object_type = ?
            """, (json.dumps(field_config), netbox_version, now, object_type))
        else:
            # Insert new schema
            cursor.execute("""
                INSERT INTO netbox_schema 
                (object_type, field_config, netbox_version, is_enabled, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
            """, (object_type, json.dumps(field_config), netbox_version, now, now))
        
        conn.commit()
        
        return len(all_fields) + len(custom_fields)
    
    def _batch_store_schemas(self, updates: List[Dict]) -> None:
        """
        Batch store multiple schemas at once for performance.
        
        Args:
            updates: List of dicts with object_type, native_fields, custom_fields, etc.
        """
        if not updates:
            return
        
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        now = datetime.utcnow().isoformat()
        
        # Get all existing schemas in one query
        object_types = [u["object_type"] for u in updates]
        placeholders = ",".join("?" * len(object_types))
        cursor.execute(f"""
            SELECT object_type, field_config FROM netbox_schema
            WHERE object_type IN ({placeholders})
        """, object_types)
        
        existing_configs = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Prepare batch inserts and updates
        inserts = []
        update_queries = []
        
        for update in updates:
            object_type = update["object_type"]
            native_fields = update["native_fields"]
            custom_fields = update["custom_fields"]
            relationships = update["relationships"]
            netbox_version = update["netbox_version"]
            
            all_fields = native_fields + relationships
            field_config = {
                "native_fields": {f["name"]: f for f in native_fields},
                "relationships": {f["name"]: f for f in relationships},
                "custom_fields": {f["name"]: f for f in custom_fields},
                "display_fields": self._select_default_display_fields(all_fields, custom_fields)
            }
            
            if object_type in existing_configs:
                # Preserve user customizations
                old_config = json.loads(existing_configs[object_type]) if existing_configs[object_type] else {}
                if "display_fields" in old_config:
                    field_config["display_fields"] = old_config["display_fields"]
                
                update_queries.append((json.dumps(field_config), netbox_version, now, object_type))
            else:
                inserts.append((object_type, json.dumps(field_config), netbox_version, now, now))
        
        # Execute batch operations
        if update_queries:
            cursor.executemany("""
                UPDATE netbox_schema 
                SET field_config = ?, netbox_version = ?, updated_at = ?
                WHERE object_type = ?
            """, update_queries)
        
        if inserts:
            cursor.executemany("""
                INSERT INTO netbox_schema 
                (object_type, field_config, netbox_version, is_enabled, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
            """, inserts)
        
        conn.commit()
        logger.info(f"Batch stored {len(inserts)} new and updated {len(update_queries)} existing schemas")
    
    def _select_default_display_fields(self, native_fields: List[Dict], 
                                      custom_fields: List[Dict]) -> List[Tuple[str, str]]:
        """
        Select intelligent default fields for display.
        
        Prioritizes: name, status, site, tenant, description, and custom fields
        """
        priority_fields = ["name", "status", "site", "role", "tenant", "type", 
                          "device", "cluster", "primary_ip", "description"]
        
        display_fields = []
        
        # Add priority native fields first
        all_native = {f["name"]: f for f in native_fields}
        for field_name in priority_fields:
            if field_name in all_native:
                field = all_native[field_name]
                display_fields.append((field["label"], field["name"]))
        
        # Add other important native fields (limit to 10 total native)
        for field in native_fields:
            if field["name"] not in priority_fields and len(display_fields) < 10:
                display_fields.append((field["label"], field["name"]))
        
        # Add all custom fields (users explicitly added these)
        for cf in custom_fields:
            display_fields.append((cf["label"], cf["name"]))
        
        return display_fields
    
    def get_field_spec(self, object_type: str) -> List[Tuple[str, str]]:
        """
        Get display field specification for an object type.
        
        Returns:
            List of (label, field_name) tuples for display
        """
        # Check cache
        if object_type in self._cache:
            return self._cache[object_type]
        
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT field_config FROM netbox_schema
            WHERE object_type = ? AND is_enabled = 1
        """, (object_type,))
        
        row = cursor.fetchone()
        
        if row:
            config = json.loads(row[0])
            field_spec = config.get("display_fields", [])
            
            # Convert to tuple format if stored as list
            if field_spec and isinstance(field_spec[0], list):
                field_spec = [tuple(f) for f in field_spec]
            
            self._cache[object_type] = field_spec
            return field_spec
        
        # Return empty list if not found (will show all fields as fallback)
        return []
    
    def get_all_object_types(self) -> List[Dict]:
        """
        Get list of all discovered object types with metadata.
        
        Returns:
            List of dicts with object_type, netbox_version, field_count, etc.
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT object_type, field_config, netbox_version, is_enabled, updated_at
            FROM netbox_schema
            ORDER BY object_type
        """)
        
        results = []
        for row in cursor.fetchall():
            config = json.loads(row[1]) if row[1] else {}
            native_count = len(config.get("native_fields", {}))
            custom_count = len(config.get("custom_fields", {}))
            
            results.append({
                "object_type": row[0],
                "netbox_version": row[2],
                "is_enabled": bool(row[3]),
                "field_count": native_count + custom_count,
                "native_fields": native_count,
                "custom_fields": custom_count,
                "updated_at": row[4]
            })
        
        return results
    
    def update_display_fields(self, object_type: str, 
                             display_fields: List[Tuple[str, str]]) -> bool:
        """
        Update display fields configuration for an object type.
        
        Args:
            object_type: NetBox object type (e.g., "dcim_sites")
            display_fields: List of (label, field_name) tuples
            
        Returns:
            True if successful
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT field_config FROM netbox_schema
            WHERE object_type = ?
        """, (object_type,))
        
        row = cursor.fetchone()
        if not row:
            logger.error(f"Object type {object_type} not found in schema")
            return False
        
        config = json.loads(row[0])
        config["display_fields"] = display_fields
        
        cursor.execute("""
            UPDATE netbox_schema
            SET field_config = ?, updated_at = ?
            WHERE object_type = ?
        """, (json.dumps(config), datetime.utcnow().isoformat(), object_type))
        
        conn.commit()
        
        # Clear cache
        if object_type in self._cache:
            del self._cache[object_type]
        
        logger.info(f"Updated display fields for {object_type}")
        return True
    
    def get_custom_field_definition(self, object_type: str, field_name: str) -> Optional[Dict]:
        """
        Get custom field definition including choice sets.
        
        Returns:
            Dict with field metadata or None if not found
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT field_config FROM netbox_schema
            WHERE object_type = ?
        """, (object_type,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        config = json.loads(row[0])
        custom_fields = config.get("custom_fields", {})
        
        return custom_fields.get(field_name)
    
    def add_custom_field_manually(self, object_type: str, field_name: str, 
                                 field_label: str, field_type: str = "text",
                                 choice_set: str = None, required: bool = False) -> bool:
        """
        Manually add a custom field to the registry (for fields not in backup).
        
        Args:
            object_type: NetBox object type
            field_name: Field identifier (snake_case)
            field_label: Display label
            field_type: Field type (text, integer, boolean, select, etc.)
            choice_set: Name of choice set if field_type is select
            required: Whether field is required
            
        Returns:
            True if successful
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT field_config FROM netbox_schema
            WHERE object_type = ?
        """, (object_type,))
        
        row = cursor.fetchone()
        
        if not row:
            # Create new object type entry
            field_config = {
                "native_fields": {},
                "relationships": {},
                "custom_fields": {},
                "display_fields": []
            }
        else:
            field_config = json.loads(row[0])
        
        # Add custom field
        field_config["custom_fields"][field_name] = {
            "name": field_name,
            "label": field_label,
            "type": field_type,
            "choice_set": choice_set,
            "required": required
        }
        
        # Add to display fields if not already there
        display_fields = field_config.get("display_fields", [])
        if (field_label, field_name) not in display_fields:
            display_fields.append((field_label, field_name))
            field_config["display_fields"] = display_fields
        
        now = datetime.utcnow().isoformat()
        
        if row:
            cursor.execute("""
                UPDATE netbox_schema
                SET field_config = ?, updated_at = ?
                WHERE object_type = ?
            """, (json.dumps(field_config), now, object_type))
        else:
            cursor.execute("""
                INSERT INTO netbox_schema
                (object_type, field_config, is_enabled, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?)
            """, (object_type, json.dumps(field_config), now, now))
        
        conn.commit()
        
        # Clear cache
        if object_type in self._cache:
            del self._cache[object_type]
        
        logger.info(f"Added custom field {field_name} to {object_type}")
        return True
