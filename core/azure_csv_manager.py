"""
Azure CSV Upload Management

Functions for persisting Azure CSV uploads across page refreshes.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import pandas as pd

from core.db_manager import DB_PATH, init_db


def save_azure_csv_upload(filename: str, csv_data: pd.DataFrame) -> Dict[str, Any]:
    """
    Save Azure CSV upload to database so it persists across refreshes.
    
    Args:
        filename: Original CSV filename
        csv_data: Pandas DataFrame with CSV data
        
    Returns:
        Dict with upload metadata
    """
    try:
        init_db()
        
        # Convert DataFrame to JSON for storage
        csv_json = csv_data.to_json(orient='records')
        row_count = len(csv_data)
        uploaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO azure_csv_uploads 
            (id, filename, uploaded_at, csv_data, row_count)
            VALUES (1, ?, ?, ?, ?)
        """, (filename, uploaded_at, csv_json, row_count))
        
        conn.commit()
        conn.close()
        
        print(f"[azure_csv_manager] Saved CSV: {filename}, {row_count} rows")
        
        return {
            "filename": filename,
            "uploaded_at": uploaded_at,
            "row_count": row_count,
            "success": True
        }
    except Exception as e:
        print(f"[azure_csv_manager] ERROR saving CSV: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


def get_azure_csv_upload() -> Optional[Dict[str, Any]]:
    """
    Retrieve saved Azure CSV upload from database.
    
    Returns:
        Dict with filename, uploaded_at, csv_data (as DataFrame), and row_count
        None if no upload exists
    """
    try:
        init_db()
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT filename, uploaded_at, csv_data, row_count
            FROM azure_csv_uploads
            WHERE id = 1
        """)
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            print("[azure_csv_manager] No saved CSV found in database")
            return None
        
        filename, uploaded_at, csv_json, row_count = row
        
        print(f"[azure_csv_manager] Found saved CSV: {filename}, {row_count} rows")
        
        # Convert JSON back to DataFrame
        try:
            csv_data = pd.read_json(csv_json, orient='records')
            print(f"[azure_csv_manager] Successfully converted JSON to DataFrame")
        except Exception as e:
            print(f"[azure_csv_manager] ERROR converting JSON to DataFrame: {e}")
            return None
        
        return {
            "filename": filename,
            "uploaded_at": uploaded_at,
            "csv_data": csv_data,
            "row_count": row_count,
            "exists": True
        }
    except Exception as e:
        print(f"[azure_csv_manager] ERROR retrieving CSV: {e}")
        import traceback
        traceback.print_exc()
        return None


def clear_azure_csv_upload() -> bool:
    """
    Clear the saved Azure CSV upload from database.
    
    Returns:
        True if cleared successfully
    """
    init_db()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("DELETE FROM azure_csv_uploads WHERE id = 1")
    
    conn.commit()
    conn.close()
    
    return True


def has_azure_csv_upload() -> bool:
    """
    Check if an Azure CSV upload exists in database.
    
    Returns:
        True if upload exists
    """
    init_db()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM azure_csv_uploads WHERE id = 1")
    count = cursor.fetchone()[0]
    
    conn.close()
    
    return count > 0
