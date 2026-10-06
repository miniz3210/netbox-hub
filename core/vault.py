"""
Local Persistent Sanitization Vault.

Stores bidirectional mappings between real sensitive values (RFC-1918 IPv4/CIDR,
internal FQDN suffixes) and deterministic synthetic tokens in a local SQLite
database. Used as transparent gateway middleware: text is sanitised before it
leaves the process and restored after the LLM response returns.

Token format:
  - Private IPv4 bare addresses              -> <SAFE_IP_N>
  - Private IPv4 CIDR prefixes               -> <SAFE_NET_N>
  - Internal domain / FQDN suffixes          -> <SAFE_DOMAIN_N>
"""

import os
import re
import ipaddress
import sqlite3
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("netbox-hub")

VAULT_DB_PATH = os.path.join("data", "sanitizer_vault.db")
MAX_ENTRIES = 5_000
TTL_DAYS = 7
PRUNE_FRACTION = 0.20

# Per-octet private-IP token used to build both the CIDR and bare-IP patterns.
_PRIV_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"

# Private IPv4 CIDR pattern — requires a slash (e.g. 10.113.64.0/24, 172.16.0.0/12, 192.168.1.0/21).
_CIDR_NET_10 = r"10\." + _PRIV_OCTET + r"\." + _PRIV_OCTET + r"\." + _PRIV_OCTET
_CIDR_NET_172 = r"172\.(?:1[6-9]|2\d|3[01])\." + _PRIV_OCTET + r"\." + _PRIV_OCTET
_CIDR_NET_192 = r"192\.168\." + _PRIV_OCTET + r"\." + _PRIV_OCTET
_IPV4_CIDR_RE = re.compile(r"\b(" + _CIDR_NET_10 + r"|" + _CIDR_NET_172 + r"|" + _CIDR_NET_192 + r")/\d{1,2}\b")

# Private IPv4 bare-address pattern — same three RFC-1918 ranges, no slash.
_IPV4_PRIV_BARE_RE = re.compile(r"\b(" + _CIDR_NET_10 + r"|" + _CIDR_NET_172 + r"|" + _CIDR_NET_192 + r")\b")

# Internal domain / FQDN suffix pattern. Static well-known suffixes plus any
# dynamically loaded from naming_rules.yaml corp_domain_* fields.
_STATIC_DOMAIN_SUFFIXES = ["local", "internal", "corp", "adds", "lan", "ot"]

_TOKEN_RE = re.compile(r"<SAFE_(IP|NET|DOMAIN)_(\d+)>")


def _is_private_ipv4(addr_str: str) -> bool:
    """Return True if *addr_str* is a private IPv4 address (RFC 1918)."""
    try:
        addr = ipaddress.IPv4Address(addr_str.strip())
        return any(
            addr in net
            for net in (
                ipaddress.IPv4Network("10.0.0.0/8"),
                ipaddress.IPv4Network("172.16.0.0/12"),
                ipaddress.IPv4Network("192.168.0.0/16"),
            )
        )
    except ValueError:
        return False


def _extract_domain_suffixes() -> List[str]:
    """Pull configured internal domain suffixes from naming_rules.yaml."""
    suffixes: List[str] = []
    try:
        from config.naming_rules import load_naming_rules
        rules = load_naming_rules()
    except Exception:
        rules = {}
    pv = (
        rules.get("pattern_variables")
        if isinstance(rules.get("pattern_variables"), dict)
        else {}
    )
    for key in (
        "corp_domain_it",
        "corp_domain_ot_primary",
        "corp_domain_ot_secondary",
        "corp_domain_local",
    ):
        val = pv.get(key)
        if isinstance(val, dict):
            placeholder = val.get("placeholder", "")
        else:
            placeholder = str(val or "")
        if placeholder and placeholder not in suffixes:
            suffixes.append(placeholder)
    return suffixes


class SanitizerVault:
    """SQLite-backed bidirectional token vault with LRU pruning."""

    def __init__(self, db_path: str = VAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        self._domain_re: Optional[re.Pattern] = None

    # -------------------------------------------------------------------------
    # DB helpers
    # -------------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_mappings (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    real_value   TEXT NOT NULL,
                    token        TEXT NOT NULL,
                    category     TEXT NOT NULL,
                    session_id   TEXT,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_counters (
                    category TEXT PRIMARY KEY,
                    next_id  INTEGER DEFAULT 1
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_vault_real ON vault_mappings(real_value)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vault_token ON vault_mappings(token)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vault_last_used ON vault_mappings(last_used_at)"
            )
            conn.commit()

    def _get_next_token(self, category: str) -> str:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT next_id FROM vault_counters WHERE category = ?", (category,)
            ).fetchone()
            if row:
                next_id = row["next_id"]
                conn.execute(
                    "UPDATE vault_counters SET next_id = ? WHERE category = ?",
                    (next_id + 1, category),
                )
            else:
                next_id = 1
                conn.execute(
                    "INSERT INTO vault_counters (category, next_id) VALUES (?, ?)",
                    (category, 2),
                )
            conn.commit()
            return f"<SAFE_{category}_{next_id}>"

    def _get_or_create_token(self, real_val: str, category: str, session_id: Optional[str] = None) -> str:
        clean = real_val.strip()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT token FROM vault_mappings WHERE real_value = ?", (clean,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE vault_mappings SET last_used_at = CURRENT_TIMESTAMP WHERE real_value = ?",
                    (clean,),
                )
                conn.commit()
                return row["token"]
        token = self._get_next_token(category)
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO vault_mappings (real_value, token, category, session_id, created_at, last_used_at)"
                " VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (clean, token, category, session_id),
            )
            conn.commit()
        return token

    # -------------------------------------------------------------------------
    # Domain regex management
    # -------------------------------------------------------------------------

    def _build_domain_regex(self) -> re.Pattern:
        if self._domain_re is not None:
            return self._domain_re
        suffixes = _extract_domain_suffixes()
        all_suffixes = list(dict.fromkeys(_STATIC_DOMAIN_SUFFIXES + suffixes))
        escaped = [re.escape(s) for s in all_suffixes]
        pat = (
            r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?\.)+"
            + "(?:" + "|".join(escaped) + r")\b"
        )
        self._domain_re = re.compile(pat, re.IGNORECASE)
        return self._domain_re

    # -------------------------------------------------------------------------
    # Pruning
    # -------------------------------------------------------------------------

    def _prune(self) -> None:
        """Evict oldest / least-recently-used records if cap exceeded or TTL hit."""
        with self._get_connection() as conn:
            total = (
                conn.execute("SELECT COUNT(*) AS cnt FROM vault_mappings")
                .fetchone()["cnt"]
            )
            if total <= MAX_ENTRIES:
                # Compute TTL cutoff in Python so it can be passed as a bound param.
                from datetime import datetime, timedelta
                ttl_cutoff = (datetime.now() - timedelta(days=TTL_DAYS)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                old_count = (
                    conn.execute(
                        "SELECT COUNT(*) AS cnt FROM vault_mappings WHERE last_used_at < ?",
                        (ttl_cutoff,),
                    )
                    .fetchone()["cnt"]
                )
                if old_count == 0:
                    return
                evict = max(1, int(old_count * PRUNE_FRACTION))
                conn.execute(
                    "DELETE FROM vault_mappings WHERE id IN ("
                    "  SELECT id FROM vault_mappings"
                    "  WHERE last_used_at < ?"
                    "  ORDER BY last_used_at ASC"
                    "  LIMIT ?"
                    ")",
                    (ttl_cutoff, evict),
                )
                conn.commit()
                logger.info(
                    f"Pruned {conn.total_changes} stale vault entries (TTL > {TTL_DAYS}d)"
                )
                self._reseed_counters(conn)
                return

        # Over cap: evict PRUNE_FRACTION of the oldest LRU records
        evict_count = max(1, int(total * PRUNE_FRACTION))
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM vault_mappings WHERE id IN ("
                "  SELECT id FROM vault_mappings"
                "  ORDER BY last_used_at ASC"
                "  LIMIT ?"
                ")",
                (evict_count,),
            )
            conn.commit()
            evicted = conn.total_changes
        logger.info(
            f"Pruned {evicted} oldest LRU vault entries (cap {MAX_ENTRIES} exceeded)"
        )
        self._reseed_counters(conn)

    def _reseed_counters(self, conn: sqlite3.Connection) -> None:
        """Re-seed per-category counters so the next token ID is monotonically increasing."""
        for cat in ("IP", "NET", "DOMAIN"):
            max_id_row = conn.execute(
                "SELECT MAX(CAST(REPLACE(REPLACE(token, '<SAFE_', ''), '>', '') AS INT)) AS mx "
                "FROM vault_mappings WHERE category = ?",
                (cat,),
            ).fetchone()
            max_id = max_id_row["mx"] if max_id_row and max_id_row["mx"] is not None else 0
            row = conn.execute(
                "SELECT next_id FROM vault_counters WHERE category = ?", (cat,)
            ).fetchone()
            if row:
                if row["next_id"] <= max_id:
                    conn.execute(
                        "UPDATE vault_counters SET next_id = ? WHERE category = ?",
                        (max_id + 1, cat),
                    )
            else:
                conn.execute(
                    "INSERT INTO vault_counters (category, next_id) VALUES (?, ?)",
                    (cat, max_id + 1),
                )
            conn.commit()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def sanitize(
        self, text: str, session_id: Optional[str] = None
    ) -> Tuple[str, str]:
        """Replace private IPs/CIDRs and internal domains with safe tokens.

        Returns:
            (sanitized_text, session_id)
        """
        if not text:
            return "", session_id or ""

        self._prune()

        processed = text
        used_tokens: List[Tuple[str, str]] = []

        # 1. Private CIDRs first (longer match, preserves subnet mask)
        cidr_matches: Dict[str, str] = {}
        for m in _IPV4_CIDR_RE.finditer(processed):
            ip_part = m.group(1)
            suffix = m.group(0)[len(ip_part):]  # e.g. "/24"
            if _is_private_ipv4(ip_part):
                full = ip_part + suffix
                if full not in cidr_matches:
                    tok = self._get_or_create_token(full, "NET", session_id)
                    cidr_matches[full] = tok

        for orig in sorted(cidr_matches, key=len, reverse=True):
            processed = processed.replace(orig, cidr_matches[orig], 1)
            used_tokens.append((orig, cidr_matches[orig]))

        # 2. Bare private IPs (skip if already inside a SAFE_NET token)
        bare_matches: Dict[str, str] = {}
        for m in _IPV4_PRIV_BARE_RE.finditer(processed):
            ip_str = m.group(1)
            if _is_private_ipv4(ip_str) and ip_str not in bare_matches:
                # Skip if embedded in an existing token
                start, end = m.span()
                surrounding = processed[max(0, start - 20): end + 20]
                if "<SAFE_" in surrounding and ">" in surrounding:
                    continue
                tok = self._get_or_create_token(ip_str, "IP", session_id)
                bare_matches[ip_str] = tok

        for orig in sorted(bare_matches, key=len, reverse=True):
            processed = processed.replace(orig, bare_matches[orig], 1)
            used_tokens.append((orig, bare_matches[orig]))

        # 3. Internal domain / FQDN suffixes
        domain_re = self._build_domain_regex()
        domain_matches: Dict[str, str] = {}
        for m in domain_re.findall(processed):
            if m not in domain_matches:
                tok = self._get_or_create_token(m, "DOMAIN", session_id)
                domain_matches[m] = tok
        for orig in sorted(domain_matches, key=len, reverse=True):
            processed = processed.replace(orig, domain_matches[orig], 1)
            used_tokens.append((orig, domain_matches[orig]))

        # Deterministic session token from all tokens consumed in this pass
        if session_id is None:
            session_id = (
                f"sess_{abs(hash(tuple(t[1] for t in used_tokens))) & 0xFFFFFFFF:08x}"
            )

        return processed, session_id

    def restore(self, text: str, session_id: Optional[str] = None) -> str:
        """Inverse-substitute sanitized tokens back to original values.

        When *session_id* is given, session-scoped mappings are consulted
        first; a token not found in that session falls back to the global
        (session_id IS NULL) mappings.
        """
        if not text or not isinstance(text, str):
            return text

        tokens_found = _TOKEN_RE.findall(text)
        if not tokens_found:
            return text

        unique_tokens = sorted(
            set(f"<SAFE_{cat}_{num}>" for cat, num in tokens_found),
            key=len,
            reverse=True,
        )
        placeholders = ", ".join(["?"] * len(unique_tokens))

        mapping: Dict[str, str] = {}

        if session_id is not None:
            # Priority: session-scoped entries, ordered by longest token first.
            with self._get_connection() as conn:
                rows = conn.execute(
                    f"SELECT token, real_value FROM vault_mappings "
                    f"WHERE token IN ({placeholders}) AND session_id = ? "
                    f"ORDER BY LENGTH(token) DESC",
                    (*unique_tokens, session_id),
                ).fetchall()
            mapping.update({row["token"]: row["real_value"] for row in rows})

            # Fallback: global (unscoped) entries for tokens missing from the session.
            missing = [t for t in unique_tokens if t not in mapping]
            if missing:
                mp = ", ".join(["?"] * len(missing))
                with self._get_connection() as conn:
                    rows = conn.execute(
                        f"SELECT token, real_value FROM vault_mappings "
                        f"WHERE token IN ({mp}) AND session_id IS NULL",
                        tuple(missing),
                    ).fetchall()
                mapping.update({row["token"]: row["real_value"] for row in rows})
        else:
            with self._get_connection() as conn:
                rows = conn.execute(
                    f"SELECT token, real_value FROM vault_mappings WHERE token IN ({placeholders})",
                    tuple(unique_tokens),
                ).fetchall()
            mapping = {row["token"]: row["real_value"] for row in rows}

        restored = text
        for tok in unique_tokens:
            if tok in mapping:
                restored = restored.replace(tok, mapping[tok])
        return restored

    def get_audit_records(self) -> List[Dict[str, str]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT real_value, token, category, created_at, last_used_at "
                "FROM vault_mappings ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def clear_vault(self) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM vault_mappings")
            conn.execute("DELETE FROM vault_counters")
            conn.commit()
        self._domain_re = None

    def entry_count(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM vault_mappings").fetchone()
            return row["cnt"]

    def get_vault_stats(self) -> dict:
        """Return total count and per-category counts for all active vault mappings."""
        with self._get_connection() as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM vault_mappings"
            ).fetchone()
            ip_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM vault_mappings WHERE category = 'IP'"
            ).fetchone()
            net_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM vault_mappings WHERE category = 'NET'"
            ).fetchone()
            domain_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM vault_mappings WHERE category = 'DOMAIN'"
            ).fetchone()
        return {
            "total": total_row["cnt"],
            "ips": ip_row["cnt"],
            "nets": net_row["cnt"],
            "domains": domain_row["cnt"],
        }

    def get_recent_mappings(self, limit: int = 20) -> List[Dict[str, str]]:
        """Return the *limit*-most recent vault mapping records ordered by creation."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT real_value, token, category, created_at, last_used_at "
                "FROM vault_mappings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session_token_map(self, session_id: str) -> Dict[str, str]:
        """Return ``{token: real_value}`` for vault mappings belonging to *session_id*."""
        if not session_id:
            return {}
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT token, real_value FROM vault_mappings WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return {row["token"]: row["real_value"] for row in rows}
