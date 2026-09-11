"""
Universal Schema Registry - Dynamic NetBox Backup Introspection Engine

Zero-hardcoding schema discovery: dynamically extracts all model signatures
from uploaded NetBox JSON backup files and builds a runtime registry that
adapts automatically to any NetBox version or custom configuration.
"""

import json
import re
from typing import Dict, List, Set, Tuple, Any, Optional
from collections import defaultdict
import streamlit as st


class UniversalSchemaRegistry:
    """
    Dynamically introspects NetBox backup JSON to extract model signatures
    and build a zero-hardcoding schema registry for universal file routing.
    """
    
    def __init__(self):
        self.model_signatures: Dict[str, Set[str]] = {}
        self.model_sample_records: Dict[str, Dict[str, Any]] = {}
        self.dependency_graph: Dict[str, Set[str]] = {}
        self.field_type_hints: Dict[str, Dict[str, str]] = {}
        
    def introspect_backup_json(self, backup_data: Dict[str, Any]) -> None:
        """
        Extract all model signatures from NetBox backup JSON.
        
        Args:
            backup_data: Parsed NetBox backup JSON (top-level dict with model keys)
        """
        self.model_signatures.clear()
        self.model_sample_records.clear()
        self.dependency_graph.clear()
        self.field_type_hints.clear()
        
        for model_key, records in backup_data.items():
            if not isinstance(records, list) or len(records) == 0:
                continue
            
            # Extract signature from first non-empty record
            signature_set = self._extract_signature(records[0])
            self.model_signatures[model_key] = signature_set
            self.model_sample_records[model_key] = records[0]
            
            # Extract field type hints
            self.field_type_hints[model_key] = self._infer_field_types(records[0])
            
            # Build dependency graph
            self.dependency_graph[model_key] = self._detect_dependencies(records[0])
    
    def _extract_signature(self, record: Dict[str, Any], prefix: str = "") -> Set[str]:
        """
        Recursively extract all field paths from a record, flattening nested objects.
        
        Args:
            record: Sample record dictionary
            prefix: Current field path prefix for nested objects
            
        Returns:
            Set of normalized field names
        """
        signature = set()
        
        for key, value in record.items():
            normalized_key = self._normalize_field_name(key)
            full_path = f"{prefix}{normalized_key}" if prefix else normalized_key
            signature.add(full_path)
            
            # Flatten nested dictionaries (e.g., custom_fields, tags, relationships)
            if isinstance(value, dict) and key not in ['url', 'display']:
                # Include the parent key itself
                nested_sig = self._extract_signature(value, prefix=f"{full_path}.")
                signature.update(nested_sig)
            
            # Handle lists with nested objects
            elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
                nested_sig = self._extract_signature(value[0], prefix=f"{full_path}.")
                signature.update(nested_sig)
        
        return signature
    
    def _normalize_field_name(self, field_name: str) -> str:
        """Normalize field names for comparison: lowercase, no extra whitespace."""
        return str(field_name).strip().lower().replace(" ", "_")
    
    def _infer_field_types(self, record: Dict[str, Any]) -> Dict[str, str]:
        """
        Infer field types from sample record for schema generation.
        
        Returns:
            Dict mapping field names to inferred types (str, int, bool, json, etc.)
        """
        type_map = {}
        
        for key, value in record.items():
            normalized_key = self._normalize_field_name(key)
            
            if value is None:
                type_map[normalized_key] = "text"
            elif isinstance(value, bool):
                type_map[normalized_key] = "boolean"
            elif isinstance(value, int):
                type_map[normalized_key] = "integer"
            elif isinstance(value, float):
                type_map[normalized_key] = "real"
            elif isinstance(value, (dict, list)):
                type_map[normalized_key] = "json"
            else:
                type_map[normalized_key] = "text"
        
        return type_map
    
    def _detect_dependencies(self, record: Dict[str, Any]) -> Set[str]:
        """
        Detect foreign key dependencies by examining field patterns.
        
        Looks for:
        - Fields ending in _id
        - Nested objects with 'id' fields
        - Common reference patterns (site, tenant, device, etc.)
        
        Returns:
            Set of model keys this record depends on
        """
        dependencies = set()
        
        for key, value in record.items():
            normalized_key = self._normalize_field_name(key)
            
            # Pattern 1: Fields ending in _id
            if normalized_key.endswith("_id") and value is not None:
                # Extract potential model name (e.g., site_id -> site)
                model_hint = normalized_key[:-3]  # Remove '_id'
                dependencies.add(model_hint)
            
            # Pattern 2: Nested objects with id field
            if isinstance(value, dict) and 'id' in value and value['id'] is not None:
                # The key itself is likely the model reference
                dependencies.add(normalized_key)
            
            # Pattern 3: Common reference patterns
            if isinstance(value, dict):
                for ref_key in ['site', 'tenant', 'device', 'cluster', 'vrf', 'role', 'type', 'group']:
                    if ref_key in value and isinstance(value[ref_key], dict) and 'id' in value[ref_key]:
                        dependencies.add(ref_key)
        
        return dependencies
    
    def classify_file(self, columns: List[str]) -> Optional[Tuple[str, float]]:
        """
        Classify an uploaded file by comparing its columns to all known model signatures.
        
        Args:
            columns: List of column names from uploaded CSV/Excel
            
        Returns:
            Tuple of (best_matching_model_key, similarity_score) or None if no good match
        """
        if not self.model_signatures:
            return None
        
        # Normalize uploaded columns
        normalized_cols = {self._normalize_field_name(col) for col in columns}
        
        best_match = None
        best_score = 0.0
        threshold = 0.3  # Minimum 30% overlap required
        
        # Track top matches for debugging
        top_matches = []
        
        for model_key, signature in self.model_signatures.items():
            # Compute Jaccard similarity
            intersection = normalized_cols & signature
            union = normalized_cols | signature
            
            if len(union) == 0:
                continue
            
            jaccard_score = len(intersection) / len(union)
            
            # Compute intersection density (favor high coverage of uploaded columns)
            if len(normalized_cols) > 0:
                coverage_score = len(intersection) / len(normalized_cols)
            else:
                coverage_score = 0.0
            
            # Combined score: weighted average favoring coverage
            combined_score = 0.4 * jaccard_score + 0.6 * coverage_score
            
            # Track for debugging
            if combined_score > 0.1:  # Track any reasonable match
                top_matches.append((model_key, combined_score, len(intersection)))
            
            if combined_score > best_score and combined_score >= threshold:
                best_score = combined_score
                best_match = model_key
        
        # Debug: Print top 5 matches
        import streamlit as st
        if not best_match and top_matches:
            top_matches.sort(key=lambda x: x[1], reverse=True)
            debug_info = "\n".join([
                f"  • {model}: {score:.1%} match ({count} common fields)"
                for model, score, count in top_matches[:5]
            ])
            st.info(f"🔍 **Debug:** Top model matches:\n{debug_info}\n\nNone exceeded the 30% threshold.")
        
        return (best_match, best_score) if best_match else None
    
    def get_topological_order(self) -> List[str]:
        """
        Compute topological sort of models based on detected dependencies.
        
        Returns:
            List of model keys in dependency order (parents before children)
        """
        # Kahn's algorithm for topological sorting
        in_degree = defaultdict(int)
        graph = defaultdict(set)
        
        all_models = set(self.model_signatures.keys())
        
        # Build adjacency list and compute in-degrees
        for model, deps in self.dependency_graph.items():
            for dep in deps:
                # Find actual model key that matches dependency hint
                matching_models = [m for m in all_models if dep in m.lower()]
                for matched in matching_models:
                    if matched != model:
                        graph[matched].add(model)
                        in_degree[model] += 1
        
        # Initialize queue with models having no dependencies
        queue = [m for m in all_models if in_degree[m] == 0]
        result = []
        
        while queue:
            current = queue.pop(0)
            result.append(current)
            
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        # Add any remaining models (circular dependencies or isolated nodes)
        remaining = all_models - set(result)
        result.extend(sorted(remaining))
        
        return result
    
    def get_primary_key_field(self, model_key: str) -> str:
        """
        Infer the primary key field for a model.
        
        Args:
            model_key: NetBox model key (e.g., 'dcim.device')
            
        Returns:
            Field name to use as primary key (defaults to 'id')
        """
        if model_key not in self.model_sample_records:
            return "id"
        
        record = self.model_sample_records[model_key]
        
        # Priority order for primary key detection
        candidates = ['id', 'vid', 'slug', 'name', 'pk']
        
        for candidate in candidates:
            if candidate in record and record[candidate] is not None:
                return candidate
        
        # Fallback: first field in record
        return list(record.keys())[0] if record else "id"
    
    def export_to_session_state(self) -> None:
        """Export registry to Streamlit session state for cross-tab access."""
        st.session_state["netbox_runtime_signatures"] = {
            "model_signatures": {k: list(v) for k, v in self.model_signatures.items()},
            "model_sample_records": self.model_sample_records,
            "dependency_graph": {k: list(v) for k, v in self.dependency_graph.items()},
            "field_type_hints": self.field_type_hints
        }
    
    @classmethod
    def load_from_session_state(cls) -> Optional['UniversalSchemaRegistry']:
        """Load registry from Streamlit session state."""
        if "netbox_runtime_signatures" not in st.session_state:
            return None
        
        data = st.session_state["netbox_runtime_signatures"]
        registry = cls()
        registry.model_signatures = {k: set(v) for k, v in data["model_signatures"].items()}
        registry.model_sample_records = data["model_sample_records"]
        registry.dependency_graph = {k: set(v) for k, v in data["dependency_graph"].items()}
        registry.field_type_hints = data["field_type_hints"]
        
        return registry


def initialize_schema_registry_from_backup(backup_json_path: str) -> UniversalSchemaRegistry:
    """
    Initialize schema registry from NetBox backup JSON file.
    
    Args:
        backup_json_path: Path to NetBox backup JSON file
        
    Returns:
        Initialized UniversalSchemaRegistry
    """
    with open(backup_json_path, 'r', encoding='utf-8') as f:
        backup_data = json.load(f)
    
    registry = UniversalSchemaRegistry()
    registry.introspect_backup_json(backup_data)
    registry.export_to_session_state()
    
    return registry


def initialize_schema_registry_from_uploaded_file(uploaded_file) -> UniversalSchemaRegistry:
    """
    Initialize schema registry from uploaded NetBox backup JSON file.
    
    Args:
        uploaded_file: Streamlit UploadedFile object
        
    Returns:
        Initialized UniversalSchemaRegistry
    """
    content = uploaded_file.read()
    # Handle UTF-8 BOM if present (common in Windows-generated JSON files)
    backup_data = json.loads(content.decode('utf-8-sig'))
    
    registry = UniversalSchemaRegistry()
    registry.introspect_backup_json(backup_data)
    registry.export_to_session_state()
    
    return registry
