"""
Local Persistent Sanitization Vault.

Stores bidirectional mappings between real sensitive values (IPv4/CIDR,
internal FQDN suffixes, hardware serial numbers, MAC addresses, and hostnames)
and deterministic synthetic tokens in a local SQLite database. Used as
transparent gateway middleware: text is sanitised before it leaves the process
and restored after the LLM response returns.

Token format:
  - IPv4 bare addresses (all)                        -> <SAFE_IP_N>
  - IPv4 CIDR prefixes (all)                         -> <SAFE_NET_N>
  - Internal domain / FQDN suffixes                  -> <SAFE_DOMAIN_N>
  - Hardware serial numbers                          -> <SAFE_SERIAL_N>
  - MAC addresses                                    -> <SAFE_MAC_N>
  - Hostnames (device / VM names supplied by caller) -> <SAFE_HOST_N>
"""

import os
import re
import ipaddress
import sqlite3
import hashlib
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("netbox-hub")

VAULT_DB_PATH = os.path.join("data", "sanitizer_vault.db")
MAX_ENTRIES = 5_000
TTL_DAYS = 7
PRUNE_FRACTION = 0.20

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Per-octet private-IP token used to build both the CIDR and bare-IP patterns.
_PRIV_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"

# Any valid IPv4 octet (0-255).
_ANY_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d|\d)"
_IPV4_ANY = rf"\b{_ANY_OCTET}\.{_ANY_OCTET}\.{_ANY_OCTET}\.{_ANY_OCTET}\b"

# Private IPv4 CIDR pattern — requires a slash (e.g. 10.113.64.0/24).
_CIDR_NET_10 = r"10\." + _PRIV_OCTET + r"\." + _PRIV_OCTET + r"\." + _PRIV_OCTET
_CIDR_NET_172 = r"172\.(?:1[6-9]|2\d|3[01])\." + _PRIV_OCTET + r"\." + _PRIV_OCTET
_CIDR_NET_192 = r"192\.168\." + _PRIV_OCTET + r"\." + _PRIV_OCTET
_PRIV_CIDR_RE = re.compile(
    r"\b(" + _CIDR_NET_10 + r"|" + _CIDR_NET_172 + r"|" + _CIDR_NET_192 + r")/\d{1,2}\b"
)

# ALL IPv4 CIDR pattern (private + public) — requires a slash.
_ALL_CIDR_RE = re.compile(rf"\b({_IPV4_ANY})/\d{{1,2}}\b")

# Private IPv4 bare-address pattern.
_PRIV_BARE_RE = re.compile(
    r"\b(" + _CIDR_NET_10 + r"|" + _CIDR_NET_172 + r"|" + _CIDR_NET_192 + r")\b"
)

# ALL IPv4 bare-address pattern (private + public).
_ALL_BARE_RE = re.compile(_IPV4_ANY)

# Internal domain / FQDN suffix pattern. Static well-known suffixes plus any
# dynamically loaded from naming_rules.yaml corp_domain_* fields.
_STATIC_DOMAIN_SUFFIXES = ["local", "internal", "corp", "adds", "lan", "ot"]

# Vendor-specific hardware serial patterns.
#   Meraki / Cisco: 4-4-4 alphanumeric with hyphens  e.g. Q2GW-C2KF-22EN
_MERAKI_SERIAL_RE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}\b")
#   Juniper / Generic: starts with E or Q followed by 11-13 alphanums  e.g. EZ3025AX0068
_JUNIPER_SERIAL_RE = re.compile(r"\b[EeQq][A-Z0-9]{11,13}\b")
#   Standard Cisco Enterprise 11-char serials: 3 uppercase letters + 8 alphanums  e.g. FGL2614L1QU, FVH28332J63
_CISCO_ENT_SERIAL_RE = re.compile(r"\b[A-Z]{3}[0-9A-Z]{8}\b")
#   Delimited hardware serials (e.g. Palo Alto): 6 digits - 6 digits - 4 digits  e.g. 024609-001020-4253
_DELIMITED_SERIAL_RE = re.compile(r"\b\d{6}-\d{6}-\d{4}\b")
_SERIAL_RE = re.compile(
    rf"(?:{_MERAKI_SERIAL_RE.pattern}|{_JUNIPER_SERIAL_RE.pattern}|{_CISCO_ENT_SERIAL_RE.pattern}|{_DELIMITED_SERIAL_RE.pattern})"
)

# MAC address patterns — 6 pairs of hex digits separated by colons or hyphens,
# or 12 contiguous hex chars (no separators).
_MAC_COLON_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
_MAC_HYPHEN_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}\b")
_MAC_BARE_RE = re.compile(r"\b[0-9A-Fa-f]{12}\b")
_MAC_RE = re.compile(rf"(?:{_MAC_COLON_RE.pattern}|{_MAC_HYPHEN_RE.pattern}|{_MAC_BARE_RE.pattern})")

_TOKEN_RE = re.compile(r"<SAFE_(IP|NET|DOMAIN|SERIAL|MAC|HOST)_(\d+)>")


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
        for cat in ("IP", "NET", "DOMAIN", "SERIAL", "MAC", "HOST"):
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
        self,
        text: str,
        session_id: Optional[str] = None,
        known_hostnames: Optional[List[str]] = None,
        known_serials: Optional[List[str]] = None,
    ) -> Tuple[str, str]:
        """Replace IPs/CIDRs, domains, serials, MACs and hostnames with safe tokens.

        Args:
            text: Input text to sanitise.
            session_id: Optional caller-supplied session ID. A deterministic ID is
                generated automatically when omitted.
            known_hostnames: Optional list of device/VM names that should be
                tokenised as ``<SAFE_HOST_N>`` regardless of their lexical shape.
            known_serials: Optional list of real serial strings harvested from
                NetBox. Each is replaced via exact word match before the generic
                vendor-regex serial pass runs.

        Returns:
            (sanitized_text, session_id)
        """
        if not text:
            return "", session_id or ""

        self._prune()

        processed = text
        used_tokens: List[Tuple[str, str]] = []

        # 1. Known serials — exact word-boundary replacements, longest first.
        #    This ensures 100% coverage for every real serial regardless of
        #    vendor formatting, before the fragile regex fallback runs.
        if known_serials:
            serial_matches: Dict[str, str] = {}
            for s in sorted(known_serials, key=len, reverse=True):
                if not s or s in serial_matches:
                    continue
                pat = re.compile(r"\b" + re.escape(s) + r"\b", re.IGNORECASE)
                for m in pat.finditer(processed):
                    if s not in serial_matches:
                        tok = self._get_or_create_token(s, "SERIAL", session_id)
                        serial_matches[s] = tok
                        break
            for orig in sorted(serial_matches, key=len, reverse=True):
                processed = processed.replace(orig, serial_matches[orig], 1)
                used_tokens.append((orig, serial_matches[orig]))

        # 2. Private CIDRs first (longer match, preserves subnet mask)
        cidr_matches: Dict[str, str] = {}
        for m in _PRIV_CIDR_RE.finditer(processed):
            ip_part = m.group(1)
            suffix = m.group(0)[len(ip_part):]  # e.g. "/24"
            full = ip_part + suffix
            if full not in cidr_matches:
                tok = self._get_or_create_token(full, "NET", session_id)
                cidr_matches[full] = tok

        for orig in sorted(cidr_matches, key=len, reverse=True):
            processed = processed.replace(orig, cidr_matches[orig], 1)
            used_tokens.append((orig, cidr_matches[orig]))

        # 3. Bare private IPs (skip if already inside a SAFE_NET token)
        bare_matches: Dict[str, str] = {}
        for m in _PRIV_BARE_RE.finditer(processed):
            ip_str = m.group(1)
            if ip_str not in bare_matches:
                start, end = m.span()
                surrounding = processed[max(0, start - 20): end + 20]
                if "<SAFE_" in surrounding and ">" in surrounding:
                    continue
                tok = self._get_or_create_token(ip_str, "IP", session_id)
                bare_matches[ip_str] = tok

        for orig in sorted(bare_matches, key=len, reverse=True):
            processed = processed.replace(orig, bare_matches[orig], 1)
            used_tokens.append((orig, bare_matches[orig]))

        # 4. Public / all remaining CIDRs (skip those already tokenised as private)
        all_cidr_matches: Dict[str, str] = {}
        for m in _ALL_CIDR_RE.finditer(processed):
            full = m.group(0)
            if full not in all_cidr_matches:
                tok = self._get_or_create_token(full, "NET", session_id)
                all_cidr_matches[full] = tok
        for orig in sorted(all_cidr_matches, key=len, reverse=True):
            processed = processed.replace(orig, all_cidr_matches[orig], 1)
            used_tokens.append((orig, all_cidr_matches[orig]))

        # 5. Public / all remaining bare IPs (skip if inside a token already)
        all_bare_matches: Dict[str, str] = {}
        for m in _ALL_BARE_RE.finditer(processed):
            ip_str = m.group(0)
            if ip_str not in all_bare_matches:
                start, end = m.span()
                surrounding = processed[max(0, start - 20): end + 20]
                if "<SAFE_" in surrounding and ">" in surrounding:
                    continue
                tok = self._get_or_create_token(ip_str, "IP", session_id)
                all_bare_matches[ip_str] = tok
        for orig in sorted(all_bare_matches, key=len, reverse=True):
            processed = processed.replace(orig, all_bare_matches[orig], 1)
            used_tokens.append((orig, all_bare_matches[orig]))

        # 6. Internal domain / FQDN suffixes
        domain_re = self._build_domain_regex()
        domain_matches: Dict[str, str] = {}
        for m in domain_re.findall(processed):
            if m not in domain_matches:
                tok = self._get_or_create_token(m, "DOMAIN", session_id)
                domain_matches[m] = tok
        for orig in sorted(domain_matches, key=len, reverse=True):
            processed = processed.replace(orig, domain_matches[orig], 1)
            used_tokens.append((orig, domain_matches[orig]))

        # 7. Hardware serial numbers (Meraki-style and Juniper/Generic)
        serial_matches: Dict[str, str] = {}
        for m in _SERIAL_RE.finditer(processed):
            s = m.group(0)
            if s not in serial_matches:
                tok = self._get_or_create_token(s, "SERIAL", session_id)
                serial_matches[s] = tok
        for orig in sorted(serial_matches, key=len, reverse=True):
            processed = processed.replace(orig, serial_matches[orig], 1)
            used_tokens.append((orig, serial_matches[orig]))

        # 8. MAC addresses — longest matches first to avoid partial replacements
        mac_matches: Dict[str, str] = {}
        for m in _MAC_COLON_RE.finditer(processed):
            mac = m.group(0)
            if mac not in mac_matches:
                tok = self._get_or_create_token(mac, "MAC", session_id)
                mac_matches[mac] = tok
        for m in _MAC_HYPHEN_RE.finditer(processed):
            mac = m.group(0)
            if mac not in mac_matches:
                tok = self._get_or_create_token(mac, "MAC", session_id)
                mac_matches[mac] = tok
        for m in _MAC_BARE_RE.finditer(processed):
            mac = m.group(0)
            # Distinguish from serials and IP-like strings
            start, end = m.span()
            surrounding = processed[max(0, start - 5): end + 5]
            if "<SAFE_" in surrounding:
                continue
            if mac not in mac_matches:
                tok = self._get_or_create_token(mac, "MAC", session_id)
                mac_matches[mac] = tok
        for orig in sorted(mac_matches, key=len, reverse=True):
            processed = processed.replace(orig, mac_matches[orig], 1)
            used_tokens.append((orig, mac_matches[orig]))

        # 9. Known hostnames — longest first to avoid partial overlap.
        #    Use re.sub (global) so every occurrence of each hostname is tokenised,
        #    not just the first one.
        if known_hostnames:
            hostname_matches: Dict[str, str] = {}
            for name in sorted(known_hostnames, key=len, reverse=True):
                if not name or name in hostname_matches:
                    continue
                tok = self._get_or_create_token(name, "HOST", session_id)
                hostname_matches[name] = tok
            for orig in sorted(hostname_matches, key=len, reverse=True):
                pat = re.compile(re.escape(orig), re.IGNORECASE)
                processed = pat.sub(hostname_matches[orig], processed)
                used_tokens.append((orig, hostname_matches[orig]))

        # Deterministic session token from all tokens consumed in this pass.
        # Uses SHA-256 (stable across processes) instead of Python's randomised hash().
        if session_id is None:
            token_names = tuple(sorted(t[1] for t in used_tokens))
            session_id = (
                f"sess_{hashlib.sha256(str(token_names).encode()).hexdigest()[:12]}"
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

        # Undo common LLM Markdown/HTML escaping artifacts so tokens remain matchable.
        text = text.replace("\\_", "_")
        text = text.replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&#x27;", "'").replace("&#x2F;", "/")

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
            serial_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM vault_mappings WHERE category = 'SERIAL'"
            ).fetchone()
            mac_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM vault_mappings WHERE category = 'MAC'"
            ).fetchone()
            host_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM vault_mappings WHERE category = 'HOST'"
            ).fetchone()
        return {
            "total": total_row["cnt"],
            "ips": ip_row["cnt"],
            "nets": net_row["cnt"],
            "domains": domain_row["cnt"],
            "serials": serial_row["cnt"],
            "macs": mac_row["cnt"],
            "hosts": host_row["cnt"],
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
