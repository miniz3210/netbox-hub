"""
Universal File Uploader - Zero-Hardcoding CSV/Excel Ingestion Engine

Automatically classifies and routes any uploaded file by comparing its schema
against dynamically discovered NetBox model signatures. No hardcoded routing rules.
"""

import io
import re
import sqlite3
from typing import Dict, List, Any, Optional, Tuple, Set
from datetime import datetime
import pandas as pd
import openpyxl
import streamlit as st

from core.universal_schema_registry import UniversalSchemaRegistry


class UniversalUploader:
    """
    Zero-hardcoding universal file uploader that routes files based on
    dynamic schema matching against NetBox backup introspection.
    """
    
    def __init__(self, db_path: str = "data/netbox_hub.db"):
        self.db_path = db_path
        self.registry: Optional[UniversalSchemaRegistry] = None
        self._load_registry()
    
    def _load_registry(self) -> None:
        """Load schema registry from session state if available."""
        self.registry = UniversalSchemaRegistry.load_from_session_state()
        
        # Debug: Check if registry is actually populated
        if self.registry and hasattr(self.registry, 'model_signatures'):
            sig_count = len(self.registry.model_signatures) if self.registry.model_signatures else 0
            if sig_count == 0:
                # Registry exists but is empty - treat as uninitialized
                self.registry = None
    
    def process_uploaded_files(self, uploaded_files: List[Any]) -> Dict[str, Any]:
        """
        Process multiple uploaded CSV/Excel files with automatic routing.
        
        Args:
            uploaded_files: List of Streamlit UploadedFile objects
            
        Returns:
            Dict with processing results: {
                'total_records': int,
                'by_model': {model_key: count},
                'errors': [error_messages]
            }
        """
        if not isinstance(uploaded_files, list):
            uploaded_files = [uploaded_files]
        
        results = {
            'total_records': 0,
            'by_model': {},
            'errors': []
        }
        
        uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        for file_obj in uploaded_files:
            try:
                file_result = self._process_single_file(file_obj, uploaded_at)
                results['total_records'] += file_result['record_count']
                
                for model, count in file_result['by_model'].items():
                    results['by_model'][model] = results['by_model'].get(model, 0) + count
                
            except Exception as e:
                results['errors'].append(f"• **{file_obj.name}**: {str(e)}")
        
        return results
    
    def process_uploaded_files_with_overrides(
        self, 
        uploaded_files: List[Any], 
        endpoint_overrides: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Process multiple uploaded CSV/Excel files with user-confirmed endpoint classifications.
        
        Args:
            uploaded_files: List of Streamlit UploadedFile objects
            endpoint_overrides: Dict mapping filename to confirmed endpoint (e.g., 'tenancy/contact-groups')
            
        Returns:
            Dict with processing results: {
                'total_records': int,
                'by_model': {model_key: count},
                'errors': [error_messages]
            }
        """
        if not isinstance(uploaded_files, list):
            uploaded_files = [uploaded_files]
        
        results = {
            'total_records': 0,
            'by_model': {},
            'errors': []
        }
        
        uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        for file_obj in uploaded_files:
            try:
                # Use confirmed endpoint if provided
                confirmed_endpoint = endpoint_overrides.get(file_obj.name)
                
                file_result = self._process_single_file(
                    file_obj, 
                    uploaded_at, 
                    override_endpoint=confirmed_endpoint
                )
                results['total_records'] += file_result['record_count']
                
                for model, count in file_result['by_model'].items():
                    results['by_model'][model] = results['by_model'].get(model, 0) + count
                
            except Exception as e:
                results['errors'].append(f"• **{file_obj.name}**: {str(e)}")
        
        return results
    
    def _process_single_file(self, file_obj: Any, uploaded_at: str, override_endpoint: Optional[str] = None) -> Dict[str, Any]:
        """
        Process a single uploaded file.
        
        Args:
            file_obj: Streamlit UploadedFile object
            uploaded_at: Upload timestamp
            override_endpoint: Optional user-confirmed endpoint to use instead of auto-classification
        
        Returns:
            Dict with file processing results
        """
        filename = file_obj.name.lower()
        content = file_obj.getvalue()
        
        result = {
            'record_count': 0,
            'by_model': {},
            'classified_as': None
        }
        
        if filename.endswith('.xlsx'):
            result = self._process_excel_file(content, file_obj.name, uploaded_at, override_endpoint)
        elif filename.endswith('.csv'):
            result = self._process_csv_file(content, file_obj.name, uploaded_at, override_endpoint)
        else:
            raise ValueError(f"Unsupported file format: {filename}")
        
        return result
    
    def _process_excel_file(self, content: bytes, filename: str, uploaded_at: str, override_endpoint: Optional[str] = None) -> Dict[str, Any]:
        """Process Excel file with multiple sheets."""
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        
        result = {
            'record_count': 0,
            'by_model': {}
        }
        
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            
            # Extract headers from first row
            headers = []
            for cell in ws[1]:
                if cell.value:
                    headers.append(str(cell.value))
            
            if not headers:
                continue
            
            # Use override endpoint if provided, otherwise classify
            if override_endpoint:
                model_key = override_endpoint
            else:
                classification = self._classify_columns(headers)
                if not classification:
                    continue
                model_key, confidence = classification
            
            # Extract records
            records = []
            for row_idx in range(2, ws.max_row + 1):
                record = {}
                for col_idx, header in enumerate(headers, start=1):
                    cell_value = ws.cell(row=row_idx, column=col_idx).value
                    record[header] = cell_value
                
                # Skip empty rows
                if any(v is not None and str(v).strip() for v in record.values()):
                    records.append(record)
            
            if records:
                count = self._store_records(model_key, records, filename, uploaded_at)
                result['record_count'] += count
                result['by_model'][model_key] = result['by_model'].get(model_key, 0) + count
        
        return result
    
    def _process_csv_file(self, content: bytes, filename: str, uploaded_at: str, override_endpoint: Optional[str] = None) -> Dict[str, Any]:
        """Process CSV file."""
        # Detect encoding and delimiter
        content_str = content.decode('utf-8', errors='replace')
        first_line = content_str.splitlines()[0] if content_str else ""
        
        delim = ','
        if ';' in first_line and first_line.count(';') > first_line.count(','):
            delim = ';'
        
        # Parse CSV
        df = pd.read_csv(io.StringIO(content_str), sep=delim)
        
        # Use override endpoint if provided, otherwise classify
        if override_endpoint:
            model_key = override_endpoint
            confidence = 1.0  # User confirmed
        else:
            # Classify file
            columns = [str(col).strip() for col in df.columns]
            classification = self._classify_columns(columns)
            
            if not classification:
                # Fallback: Store in generic unclassified table when registry not initialized
                if not self.registry or not self.registry.model_signatures:
                    # Schema registry not initialized - store in generic table
                    model_key = f"unclassified.{filename.replace('.csv', '').replace('.xlsx', '').replace(' ', '_').lower()}"
                    
                    # Convert DataFrame to records
                    records = []
                    for _, row in df.iterrows():
                        record = {}
                        for col in df.columns:
                            value = row[col]
                            # Handle pandas NaN
                            if pd.isna(value):
                                record[col] = None
                            else:
                                record[col] = value
                        records.append(record)
                    
                    # Store in generic unclassified table
                    count = self._store_records(model_key, records, filename, uploaded_at)
                    
                    return {
                        'record_count': count,
                        'by_model': {model_key: count},
                        'classified_as': model_key,
                        'confidence': 0.0,
                        'warning': 'Stored in unclassified table. Upload NetBox JSON to enable automatic classification.'
                    }
                
                # Registry exists but file doesn't match any model
                # Use intelligent fallback classification with detailed logging
                normalized_cols = {col.strip().lower().replace(" ", "_") for col in columns}
                fallback_result = self._classify_with_intelligent_fallback(
                    filename, columns, normalized_cols
                )
                
                if fallback_result:
                    model_key, confidence = fallback_result
                else:
                    # Last resort: Store as unclassified but don't raise an error
                    model_key = self._generate_unclassified_model_key(filename)
                    st.warning(
                        f"⚠️ **'{filename}' could not be automatically classified**\n\n"
                        f"📋 Detected columns: {', '.join(columns[:5])}"
                        f"{f', ... ({len(columns)} total)' if len(columns) > 5 else ''}\n\n"
                        f"✅ The file will be stored as `{model_key}` for manual review and can be queried by the AI assistant.",
                        icon="⚠️"
                    )
                    confidence = 0.0
            else:
                model_key, confidence = classification
        
        # Convert DataFrame to records
        records = []
        for _, row in df.iterrows():
            record = {}
            for col in df.columns:
                value = row[col]
                # Handle pandas NaN
                if pd.isna(value):
                    record[col] = None
                else:
                    record[col] = value
            records.append(record)
        
        # Store records
        count = self._store_records(model_key, records, filename, uploaded_at)
        
        return {
            'record_count': count,
            'by_model': {model_key: count},
            'classified_as': model_key,
            'confidence': confidence
        }
    
    def _classify_columns(self, columns: List[str]) -> Optional[Tuple[str, float]]:
        """
        Classify file columns using schema registry with heuristic fallback.
        
        Returns:
            Tuple of (model_key, confidence_score) or None
        """
        # First try schema registry classification
        if not self.registry:
            self._load_registry()
        
        if self.registry:
            result = self.registry.classify_file(columns)
            if result:
                return result
        
        # Fallback: Use heuristic pattern matching for common NetBox exports
        return self._classify_by_heuristics(columns)
    
    def _classify_with_intelligent_fallback(self, filename: str, columns: List[str], 
                                           normalized_cols: Set[str]) -> Optional[Tuple[str, float]]:
        """
        Intelligent fallback classification using multiple strategies.
        
        Args:
            filename: Original filename
            columns: Raw column names
            normalized_cols: Normalized column name set
            
        Returns:
            Tuple of (model_key, confidence_score) or None
        """
        # Strategy 1: Try filename-based classification
        filename_result = self._classify_by_filename(filename)
        if filename_result:
            return filename_result
        
        # Strategy 2: Try NetBox common field patterns
        pattern_result = self._classify_by_common_netbox_patterns(normalized_cols)
        if pattern_result:
            return pattern_result
        
        # Strategy 3: Try registry fuzzy matching with lower threshold
        if self.registry:
            for model_key, signature in self.registry.model_signatures.items():
                intersection = normalized_cols & signature
                if len(normalized_cols) > 0:
                    coverage = len(intersection) / len(normalized_cols)
                    # Lower threshold to 15% for fallback
                    if coverage >= 0.15:
                        return (model_key, coverage)
        
        return None
    
    def _classify_by_filename(self, filename: str) -> Optional[Tuple[str, float]]:
        """
        Classify based on filename patterns dynamically extracted from the schema registry.
        
        This provides high-confidence classification when the filename clearly indicates
        the object type (e.g., 'netbox_owner groups.csv' -> 'users/owner-groups').
        
        Uses the schema registry to build patterns dynamically, making it truly universal
        and compatible with any NetBox version without hardcoding.
        """
        filename_lower = filename.lower().replace('.csv', '').replace('.xlsx', '').replace('netbox_', '').replace('netbox-', '').replace('_', ' ').strip()
        
        # If we have a schema registry, build patterns dynamically from it
        if self.registry and self.registry.model_signatures:
            best_match = None
            best_score = 0.0
            
            # Extract all model keys from the registry and build filename patterns
            for model_key in self.registry.model_signatures.keys():
                # Convert model key to potential filename patterns
                # e.g., "users.owner-groups" or "users/owner-groups" -> ["owner-groups", "owner groups"]
                endpoint = model_key.replace('.', '/').replace('_', '-')
                
                # Extract the last part (object name) for matching
                # e.g., "users/owner-groups" -> "owner-groups"
                parts = endpoint.split('/')
                if len(parts) >= 2:
                    object_name = parts[-1]  # e.g., "owner-groups"
                    
                    # Create normalized variations for exact matching
                    variations = [
                        object_name.replace('-', ' '),  # "owner groups"
                        object_name.replace('-', ''),   # "ownergroups"
                        object_name,                     # "owner-groups"
                    ]
                    
                    # Check for exact matches only (not substring matches)
                    for variation in variations:
                        # Exact match
                        if variation == filename_lower:
                            score = 0.95
                            if score > best_score:
                                best_score = score
                                best_match = endpoint
                        # Exact match with singular/plural variation
                        elif variation + 's' == filename_lower or variation == filename_lower + 's':
                            score = 0.92
                            if score > best_score:
                                best_score = score
                                best_match = endpoint
                        # Exact match ignoring plural 's'
                        elif variation.endswith('s') and variation[:-1] == filename_lower:
                            score = 0.92
                            if score > best_score:
                                best_score = score
                                best_match = endpoint
                        elif filename_lower.endswith('s') and filename_lower[:-1] == variation:
                            score = 0.92
                            if score > best_score:
                                best_score = score
                                best_match = endpoint
            
            # Also check full endpoint path exact match (e.g., "dcim regions" matches "dcim/regions")
            for model_key in self.registry.model_signatures.keys():
                endpoint = model_key.replace('.', '/').replace('_', '-')
                endpoint_lower = endpoint.lower()
                
                # Create full path variations
                full_path_variations = [
                    endpoint_lower.replace('/', ' '),    # "dcim regions"
                    endpoint_lower.replace('/', '-'),    # "dcim-regions"
                    endpoint_lower.replace('/', '_'),    # "dcim_regions"
                ]
                
                for variation in full_path_variations:
                    if variation == filename_lower:
                        return (endpoint, 0.98)  # Very high confidence for exact full path match
            
            if best_match:
                return (best_match, best_score)
        
        # Fallback: Use minimal hardcoded patterns for common cases when no registry is loaded
        # This ensures basic functionality even without a JSON backup
        basic_patterns = [
            # Multi-word patterns (more specific first) - EXACT matches only
            ('owner groups', 'users/owner-groups'),
            ('site groups', 'dcim/site-groups'),
            ('device types', 'dcim/device-types'),
            ('device roles', 'dcim/device-roles'),
            ('rack roles', 'dcim/rack-roles'),
            ('vlan groups', 'ipam/vlan-groups'),
            ('virtual machines', 'virtualization/virtual-machines'),
            ('ip addresses', 'ipam/ip-addresses'),
            ('contact groups', 'tenancy/contact-groups'),
            ('tenant groups', 'tenancy/tenant-groups'),
            
            # Single-word patterns - EXACT matches only
            ('regions', 'dcim/regions'),
            ('devices', 'dcim/devices'),
            ('sites', 'dcim/sites'),
            ('locations', 'dcim/locations'),
            ('racks', 'dcim/racks'),
            ('vlans', 'ipam/vlans'),
            ('prefixes', 'ipam/prefixes'),
            ('clusters', 'virtualization/clusters'),
            ('circuits', 'circuits/circuits'),
            ('tenants', 'tenancy/tenants'),
            ('contacts', 'tenancy/contacts'),
            ('groups', 'users/groups'),
            ('users', 'users/users'),
            ('owners', 'users/owners'),
        ]
        
        # Check for exact matches only
        for pattern, endpoint in basic_patterns:
            if pattern == filename_lower:
                return (endpoint, 0.85)  # Good confidence from exact pattern match
            # Also check singular/plural variations
            elif pattern + 's' == filename_lower or pattern == filename_lower + 's':
                return (endpoint, 0.82)
            elif pattern.endswith('s') and pattern[:-1] == filename_lower:
                return (endpoint, 0.82)
            elif filename_lower.endswith('s') and filename_lower[:-1] == pattern:
                return (endpoint, 0.82)
        
        return None
    
    def _classify_by_common_netbox_patterns(self, normalized_cols: Set[str]) -> Optional[Tuple[str, float]]:
        """
        Classify using common NetBox field patterns that appear across many models.
        """
        # Check for owner groups specifically (has "owners" column)
        if 'owners' in normalized_cols and 'name' in normalized_cols:
            # This is likely owner groups (users/owner-groups in custom NetBox or tenancy/contact-groups)
            return ('users/owner-groups', 0.8)
        
        # Check for user groups specifically (has "users" or "permissions")
        if ('users' in normalized_cols or 'users_count' in normalized_cols or 'permissions' in normalized_cols) and 'name' in normalized_cols:
            return ('users/groups', 0.8)
        
        # Common NetBox identifier patterns
        if 'id' in normalized_cols and 'name' in normalized_cols:
            # Generic NetBox object - try to infer type
            if 'ip' in normalized_cols or 'address' in normalized_cols:
                return ('ipam/ip-addresses', 0.5)
            elif 'prefix' in normalized_cols or 'cidr' in normalized_cols:
                return ('ipam/prefixes', 0.5)
            elif 'vid' in normalized_cols or 'vlan_id' in normalized_cols:
                return ('ipam/vlans', 0.5)
            elif 'device_type' in normalized_cols or 'serial' in normalized_cols:
                return ('dcim/devices', 0.5)
            elif 'slug' in normalized_cols:
                if 'facility' in normalized_cols or 'region' in normalized_cols:
                    return ('dcim/sites', 0.5)
                elif 'group_col' in normalized_cols or 'parent' in normalized_cols:
                    return ('tenancy/tenants', 0.5)
        
        # Check for unique field combinations
        if 'vid' in normalized_cols and ('name' in normalized_cols or 'vlan_name' in normalized_cols):
            return ('ipam/vlans', 0.7)
        
        if 'username' in normalized_cols or 'email' in normalized_cols:
            return ('users/users', 0.7)
        
        if 'asn' in normalized_cols:
            return ('ipam/asns', 0.7)
        
        if 'circuit_id' in normalized_cols:
            return ('circuits/circuits', 0.7)
        
        return None
    
    def _generate_unclassified_model_key(self, filename: str) -> str:
        """
        Generate a descriptive model key for unclassified files.
        """
        base_name = filename.replace('.csv', '').replace('.xlsx', '').replace(' ', '_').lower()
        # Remove 'netbox_' prefix if present for cleaner naming
        base_name = re.sub(r'^netbox[-_]', '', base_name)
        return f"unclassified/{base_name}"
    
    def _classify_by_heuristics(self, columns: List[str]) -> Optional[Tuple[str, float]]:
        """
        Classify files using heuristic pattern matching when schema registry fails.
        
        This handles common NetBox exports that might not be in the backup JSON.
        
        Returns:
            Tuple of (model_key, confidence_score) or None
        """
        # Normalize columns for matching
        normalized_cols = {col.strip().lower().replace(" ", "_") for col in columns}
        
        # Define heuristic patterns for common NetBox models
        # Use slash notation to match NetBox API endpoint conventions (ipam/ip-addresses, dcim/devices)
        heuristic_patterns = {
            'ipam/ip-addresses': {
                'required': {'ip_address', 'status'},
                'optional': {'vrf', 'tenant', 'dns_name', 'description', 'assigned'},
                'threshold': 2  # Must have at least 2 required fields
            },
            'ipam/prefixes': {
                'required': {'prefix', 'status'},
                'optional': {'vrf', 'tenant', 'site', 'vlan', 'role', 'description'},
                'threshold': 2
            },
            'ipam/vlans': {
                'required': {'vid', 'name'},
                'optional': {'site', 'group', 'tenant', 'status', 'role', 'description'},
                'threshold': 2
            },
            'dcim/sites': {
                'required': {'name', 'slug'},
                'optional': {'status', 'region', 'tenant', 'facility', 'description'},
                'threshold': 2
            },
            'dcim/devices': {
                'required': {'name', 'device_type'},
                'optional': {'site', 'location', 'rack', 'status', 'role', 'tenant', 'serial'},
                'threshold': 2
            },
            'virtualization/virtual-machines': {
                'required': {'name', 'status'},
                'optional': {'cluster', 'site', 'tenant', 'platform', 'vcpus', 'memory', 'disk'},
                'threshold': 2
            },
            'users/groups': {
                'required': {'name'},
                'optional': {'description', 'users', 'permissions'},
                'threshold': 1
            },
            'users/users': {
                'required': {'username'},
                'optional': {'email', 'first_name', 'last_name', 'is_active', 'is_staff', 'groups'},
                'threshold': 1
            },
            'tenancy/tenant-groups': {
                'required': {'name', 'slug'},
                'optional': {'description', 'parent'},
                'threshold': 1
            },
            'tenancy/tenants': {
                'required': {'name', 'slug'},
                'optional': {'group', 'description', 'comments'},
                'threshold': 1
            }
        }
        
        best_match = None
        best_score = 0.0
        
        for model_key, pattern in heuristic_patterns.items():
            required_matches = len(normalized_cols & pattern['required'])
            optional_matches = len(normalized_cols & pattern['optional'])
            total_pattern_fields = len(pattern['required']) + len(pattern['optional'])
            
            # Check if threshold is met
            if required_matches < pattern['threshold']:
                continue
            
            # Calculate confidence score
            total_matches = required_matches + optional_matches
            confidence = total_matches / total_pattern_fields
            
            # Boost confidence for required field matches
            confidence += (required_matches / len(pattern['required'])) * 0.2
            
            if confidence > best_score:
                best_score = confidence
                best_match = model_key
        
        # Return match if confidence is above 30% (lower than registry threshold)
        if best_match and best_score >= 0.30:
            return (best_match, best_score)
        
        return None
    
    def _store_records(self, model_key: str, records: List[Dict[str, Any]], 
                      source_file: str, uploaded_at: str) -> int:
        """
        Store records in dynamically created tables.
        
        Args:
            model_key: NetBox model identifier (e.g., 'dcim.device')
            records: List of record dictionaries
            source_file: Original filename
            uploaded_at: Upload timestamp
            
        Returns:
            Number of records stored
        """
        if not records:
            return 0
        
        # Sanitize table name
        table_name = self._sanitize_table_name(model_key)
        
        # Create dynamic table schema
        self._ensure_table_exists(table_name, records[0])
        
        # Insert records with upsert logic
        count = self._upsert_records(table_name, model_key, records, source_file, uploaded_at)
        
        return count
    
    def _sanitize_table_name(self, model_key: str) -> str:
        """Convert model key to valid SQLite table name."""
        # Replace dots and special chars with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', model_key)
        return f"dynamic_{sanitized}"
    
    def _ensure_table_exists(self, table_name: str, sample_record: Dict[str, Any]) -> None:
        """
        Create table dynamically based on record schema if it doesn't exist.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        
        if cursor.fetchone():
            # Table exists - add any new columns
            self._add_missing_columns(cursor, table_name, sample_record)
        else:
            # Create new table
            self._create_dynamic_table(cursor, table_name, sample_record)
        
        conn.commit()
        conn.close()
    
    def _create_dynamic_table(self, cursor: sqlite3.Cursor, table_name: str, 
                             sample_record: Dict[str, Any]) -> None:
        """Create a new table with inferred schema."""
        columns = ["_row_id INTEGER PRIMARY KEY AUTOINCREMENT"]
        
        # Add metadata columns
        columns.extend([
            "_source_file TEXT",
            "_uploaded_at TEXT",
            "_model_key TEXT"
        ])
        
        # Add data columns with inferred types
        for key, value in sample_record.items():
            col_name = self._sanitize_column_name(key)
            col_type = self._infer_sql_type(value)
            columns.append(f"{col_name} {col_type}")
        
        create_sql = f"CREATE TABLE {table_name} ({', '.join(columns)})"
        cursor.execute(create_sql)
    
    def _add_missing_columns(self, cursor: sqlite3.Cursor, table_name: str, 
                            sample_record: Dict[str, Any]) -> None:
        """Add any new columns that don't exist in the table."""
        # Get existing columns
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_cols = {row[1] for row in cursor.fetchall()}
        
        # Add missing columns
        for key, value in sample_record.items():
            col_name = self._sanitize_column_name(key)
            if col_name not in existing_cols:
                col_type = self._infer_sql_type(value)
                cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
    
    def _sanitize_column_name(self, col_name: str) -> str:
        """Sanitize column name for SQLite, handling reserved keywords."""
        # Replace special chars with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', str(col_name))
        # Ensure it doesn't start with a number
        if sanitized and sanitized[0].isdigit():
            sanitized = f"col_{sanitized}"
        
        sanitized_lower = sanitized.lower()
        
        # SQLite reserved keywords that must be escaped
        sql_reserved_keywords = {
            'abort', 'action', 'add', 'after', 'all', 'alter', 'analyze', 'and', 'as', 'asc',
            'attach', 'autoincrement', 'before', 'begin', 'between', 'by', 'cascade', 'case',
            'cast', 'check', 'collate', 'column', 'commit', 'conflict', 'constraint', 'create',
            'cross', 'current', 'current_date', 'current_time', 'current_timestamp', 'database',
            'default', 'deferrable', 'deferred', 'delete', 'desc', 'detach', 'distinct', 'do',
            'drop', 'each', 'else', 'end', 'escape', 'except', 'exclusive', 'exists', 'explain',
            'fail', 'filter', 'following', 'for', 'foreign', 'from', 'full', 'glob', 'group',
            'having', 'if', 'ignore', 'immediate', 'in', 'index', 'indexed', 'initially', 'inner',
            'insert', 'instead', 'intersect', 'into', 'is', 'isnull', 'join', 'key', 'left',
            'like', 'limit', 'match', 'natural', 'no', 'not', 'nothing', 'notnull', 'null',
            'of', 'offset', 'on', 'or', 'order', 'outer', 'over', 'partition', 'plan', 'pragma',
            'primary', 'query', 'raise', 'range', 'recursive', 'references', 'regexp', 'reindex',
            'release', 'rename', 'replace', 'restrict', 'right', 'rollback', 'row', 'rows',
            'savepoint', 'select', 'set', 'table', 'temp', 'temporary', 'then', 'to', 'transaction',
            'trigger', 'union', 'unique', 'update', 'using', 'vacuum', 'values', 'view', 'virtual',
            'when', 'where', 'window', 'with', 'without'
        }
        
        # If it's a reserved keyword, add suffix to avoid conflicts
        # This is safer than quoting as SQLite has complex quoting rules
        if sanitized_lower in sql_reserved_keywords:
            return f'{sanitized_lower}_col'
        
        return sanitized_lower
    
    def _infer_sql_type(self, value: Any) -> str:
        """Infer SQLite column type from value."""
        if value is None:
            return "TEXT"
        elif isinstance(value, bool):
            return "INTEGER"  # SQLite uses INTEGER for booleans
        elif isinstance(value, int):
            return "INTEGER"
        elif isinstance(value, float):
            return "REAL"
        elif isinstance(value, (dict, list)):
            return "TEXT"  # Store JSON as TEXT
        else:
            return "TEXT"
    
    def _upsert_records(self, table_name: str, model_key: str, records: List[Dict[str, Any]], 
                       source_file: str, uploaded_at: str) -> int:
        """
        Insert or update records with conflict resolution.
        
        Uses "latest upload wins" strategy - newer uploads overwrite existing records.
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Determine primary key field
        pk_field = self._determine_primary_key(model_key, records[0])
        pk_col = self._sanitize_column_name(pk_field)
        
        count = 0
        for record in records:
            # Prepare column names and values
            columns = ["_source_file", "_uploaded_at", "_model_key"]
            values = [source_file, uploaded_at, model_key]
            placeholders = ["?", "?", "?"]
            
            for key, value in record.items():
                col_name = self._sanitize_column_name(key)
                columns.append(col_name)
                
                # Convert complex types to JSON string
                if isinstance(value, (dict, list)):
                    import json
                    values.append(json.dumps(value))
                elif pd.isna(value):
                    values.append(None)
                else:
                    values.append(value)
                
                placeholders.append("?")
            
            # Check if record exists
            pk_value = record.get(pk_field)
            if pk_value is not None:
                cursor.execute(
                    f"SELECT _row_id FROM {table_name} WHERE {pk_col} = ?",
                    (pk_value,)
                )
                existing = cursor.fetchone()
                
                if existing:
                    # Update existing record
                    set_clause = ", ".join([f"{col} = ?" for col in columns])
                    update_sql = f"UPDATE {table_name} SET {set_clause} WHERE _row_id = ?"
                    cursor.execute(update_sql, values + [existing[0]])
                    count += 1
                    continue
            
            # Insert new record
            insert_sql = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(placeholders)})"
            cursor.execute(insert_sql, values)
            count += 1
        
        conn.commit()
        conn.close()
        
        return count
    
    def _determine_primary_key(self, model_key: str, sample_record: Dict[str, Any]) -> str:
        """Determine which field to use as primary key for deduplication."""
        if self.registry:
            pk_field = self.registry.get_primary_key_field(model_key)
            if pk_field in sample_record:
                return pk_field
        
        # Fallback priority order
        for candidate in ['id', 'vid', 'slug', 'name', 'pk']:
            if candidate in sample_record:
                return candidate
        
        # Last resort: first field
        return list(sample_record.keys())[0] if sample_record else 'id'
    
    def get_processing_summary(self, results: Dict[str, Any]) -> str:
        """
        Generate human-readable summary of processing results.
        
        Args:
            results: Results dict from process_uploaded_files
            
        Returns:
            Formatted summary string
        """
        if results['errors']:
            error_summary = "\n".join(results['errors'])
            return f"⚠️ Errors occurred:\n{error_summary}"
        
        if results['total_records'] == 0:
            return "⚪ No records processed"
        
        model_breakdown = "\n".join([
            f"  • {model}: {count} records"
            for model, count in sorted(results['by_model'].items())
        ])
        
        return f"✅ Ingested {results['total_records']} total records:\n{model_breakdown}"
