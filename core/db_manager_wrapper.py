"""
Database Manager Wrapper for Field Registry

Provides a simple wrapper class to connect FieldRegistry to the database.
"""

import sqlite3
from core.db_manager import DB_PATH


class DatabaseManager:
    """Simple database manager for field registry operations."""
    
    def __init__(self, db_path: str = None):
        """
        Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file (defaults to DB_PATH)
        """
        self.db_path = db_path or DB_PATH
    
    def get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        return sqlite3.connect(self.db_path)
