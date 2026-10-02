"""
Local Persistent Sanitization Vault.

Stores bidirectional mappings between real sensitive values (IPv4, Hostnames,
Domains, MACs) and deterministic synthetic tokens in a local SQLite database.
Zero external transmission; fully platform-agnostic.
"""

import os
import re
import ipaddress
import sqlite3
from typing import Dict, Tuple, List, Optional, Any

VAULT_DB_PATH = os.path.join("data", "sanitizer_vault.db")

IPV4_PATTERN = re.compile(
    r"\b(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}(?:/\d{1,2})?\b"
)
MAC_PATTERN = re.compile(
    r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"
)
DOMAIN_PATTERN = re.compile(
    r"\b(?:[a-zA-Z0-9-]+\.)+(?:local|internal|corp|lan|adds|domain|net|com|org|io)\b",
    re.IGNORECASE
)


class SanitizerVault:
    def __init__(self, db_path: str = VAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_mappings (
                    real_value TEXT PRIMARY KEY,
                    token TEXT UNIQUE,
                    category TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_counters (
                    category TEXT PRIMARY KEY,
                    next_id INTEGER DEFAULT 1
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_vault_token ON vault_mappings(token)"
            )
            conn.commit()

    def _get_next_token(self, category: str) -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT next_id FROM vault_counters WHERE category = ?",
                (category,)
            )
            row = cursor.fetchone()
            if row:
                next_id = row["next_id"]
                cursor.execute(
                    "UPDATE vault_counters SET next_id = ? WHERE category = ?",
                    (next_id + 1, category),
                )
            else:
                next_id = 1
                cursor.execute(
                    "INSERT INTO vault_counters (category, next_id) VALUES (?, ?)",
                    (category, 2),
                )
            conn.commit()
            return f"<SAFE_{category.upper()}_{next_id}>"

    def get_or_create_token(self, real_val: str, category: str) -> str:
        clean_val = real_val.strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT token FROM vault_mappings WHERE real_value = ?",
                (clean_val,)
            )
            row = cursor.fetchone()
            if row:
                return row["token"]

        token = self._get_next_token(category)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO vault_mappings (real_value, token, category)
                VALUES (?, ?, ?)
                """,
                (clean_val, token, category),
            )
            conn.commit()
        return token

    def sanitize_text(self, text: str) -> Tuple[str, Dict[str, str]]:
        """
        Scan and tokenize sensitive network artifacts (IP, MAC, Domain).
        Returns sanitized text and the replacement audit dictionary for the current pass.
        """
        if not text:
            return "", {}

        audit_map: Dict[str, str] = {}
        processed = text

        # 1. Tokenize MAC addresses
        for match in set(MAC_PATTERN.findall(processed)):
            tok = self.get_or_create_token(match, "MAC")
            audit_map[match] = tok
            processed = processed.replace(match, tok)

        # 2. Tokenize IPv4 addresses / CIDRs
        for match in set(IPV4_PATTERN.findall(processed)):
            tok = self.get_or_create_token(match, "IP")
            audit_map[match] = tok
            processed = processed.replace(match, tok)

        # 3. Tokenize standard domains
        for match in set(DOMAIN_PATTERN.findall(processed)):
            tok = self.get_or_create_token(match, "DOMAIN")
            audit_map[match] = tok
            processed = processed.replace(match, tok)

        return processed, audit_map

    def detokenize_text(self, text: str) -> str:
        """Reverse replace all synthetic tokens back to their original real values."""
        if not text or not isinstance(text, str):
            return text

        tokens = re.findall(r"<SAFE_[A-Z0-9_]+>", text)
        if not tokens:
            return text

        unique_tokens = list(dict.fromkeys(tokens))
        placeholders = ", ".join(["?"] * len(unique_tokens))
        query = f"SELECT token, real_value FROM vault_mappings WHERE token IN ({placeholders})"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(unique_tokens))
            rows = cursor.fetchall()
            mapping = {row["token"]: row["real_value"] for row in rows}

        restored = text
        for tok, real in mapping.items():
            restored = restored.replace(tok, real)
        return restored

    def detokenize_data(self, data: Any) -> Any:
        """Recursively de-tokenize structured dictionaries, lists, or strings."""
        if isinstance(data, str):
            return self.detokenize_text(data)
        elif isinstance(data, dict):
            return {k: self.detokenize_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.detokenize_data(item) for item in data]
        return data

    def get_audit_records(self) -> List[Dict[str, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT real_value, token, category, created_at FROM vault_mappings ORDER BY id DESC"
            )
            return [dict(row) for row in cursor.fetchall()]

    def clear_vault(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM vault_mappings")
            cursor.execute("DELETE FROM vault_counters")
            conn.commit()