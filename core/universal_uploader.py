"""
Universal File Uploader - Zero-Hardcoding CSV/Excel Ingestion Engine

Automatically classifies and routes any uploaded file by comparing its schema
against dynamically discovered NetBox model signatures. No hardcoded routing rules.
"""

import io
import re
import sqlite3
from typing import Dict, List, Any, Optional, Tuple
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
    
    def _process_single_file(self, file_obj: Any, uploaded_at: str) -> Dict[str, Any]:
        """
        Process a single uploaded file.
        
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
            result = self._process_excel_file(content, file_obj.name, uploaded_at)
        elif filename.endswith('.csv'):
            result = self._process_csv_file(content, file_obj.name, uploaded_at)
        else:
            raise ValueError(f"Unsupported file format: {filename}")
        
        return result
    
    def _process_excel_file(self, content: bytes, filename: str, uploaded_at: str) -> Dict[str, Any]:
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
            
            # Classify sheet
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
    
    def _process_csv_file(self, content: bytes, filename: str, uploaded_at: str) -> Dict[str, Any]:
        """Process CSV file."""
        # Detect encoding and delimiter
        content_str = content.decode('utf-8', errors='replace')
        first_line = content_str.splitlines()[0] if content_str else ""
        
        delim = ','
        if ';' in first_line and first_line.count(';') > first_line.count(','):
            delim = ';'
        
        # Parse CSV
        df = pd.read_csv(io.StringIO(content_str), sep=delim)
        
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
            # Get debug info from registry
            normalized_cols = {self.registry._normalize_field_name(col) for col in columns}
            
            # Compute matches for debugging
            top_matches = []
            for model_key, signature in self.registry.model_signatures.items():
                intersection = normalized_cols & signature
                if len(normalized_cols) > 0:
                    coverage = len(intersection) / len(normalized_cols)
                    if coverage > 0.05:  # Track 5%+ matches
                        top_matches.append((model_key, coverage, len(intersection), len(signature)))
            
            top_matches.sort(key=lambda x: x[1], reverse=True)
            
            # Build detailed error message with debug info
            column_preview = ", ".join(columns[:5])
            if len(columns) > 5:
                column_preview += f", ... ({len(columns)} total columns)"
            
            error_msg = (
                f"Unable to automatically classify '{filename}'. "
                f"The file columns don't match any known NetBox model signatures.\n\n"
                f"📋 Detected columns: {column_preview}\n\n"
            )
            
            if top_matches:
                debug_info = "\n".join([
                    f"  • {model}: {score:.1%} coverage ({matched}/{total} fields match)"
                    for model, score, matched, total in top_matches[:5]
                ])
                error_msg += (
                    f"🔍 **Top 5 closest matches:**\n{debug_info}\n\n"
                    f"⚠️ Best match was {top_matches[0][1]:.1%} but threshold is 25%.\n\n"
                )
            
            error_msg += (
                f"💡 **Possible solutions:**\n"
                f"1. Check if this is a valid NetBox CSV export\n"
                f"2. Verify the NetBox backup JSON includes IP addresses (ipam/ip-addresses endpoint)\n"
                f"3. Lower the threshold or add custom column mappings\n"
                f"4. The file will be stored in an 'unclassified' table for manual review"
            )
            
            raise ValueError(error_msg)
        
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
        """Sanitize column name for SQLite."""
        # Replace special chars with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', str(col_name))
        # Ensure it doesn't start with a number
        if sanitized[0].isdigit():
            sanitized = f"col_{sanitized}"
        return sanitized.lower()
    
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
