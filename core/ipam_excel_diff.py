"""
Master IPAM Excel Diff Engine — NetBox Hub v4.3

High-performance in-memory parser and audit engine for multi-sheet Excel/IPAM
inventories.  Dynamically discovers sheet names and column headers, normalises
records against local naming-standard rules, and classifies every row into one
of four buckets (matched / pending_add / conflicts / unmapped_sites) using
O(1) hash-set lookups against the local SQLite DB state.

Zero cloud / LLM leakage.  Pure local processing with openpyxl + sqlite3 +
ipaddress + pandas.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import ipaddress
import openpyxl
import pandas as pd

from config.constants import DATABASE_PATH
from config.naming_rules import (
    get_site_code_rules,
    get_ipam_role_mappings,
    get_vlan_description_mappings,
    load_naming_rules,
)
from core.ipam_engine import (
    slugify,
    normalize_role_name,
    resolve_vlan_description,
)
from core.db_manager import (
    get_existing_prefix_strings,
    get_all_site_names,
    init_db as db_init,
)

# ---------------------------------------------------------------------------
# Semantic column disambiguation helpers — NO hard-coded sheet / vendor / site
# names.  All matching is purely token-based against lower-cased column headers.
# ---------------------------------------------------------------------------

_NETWORK_ATTR_TOKENS = frozenset({
    "ip", "network", "subnet", "vlan", "prefix", "cidr",
})

_SUBNET_NEGATIVE_TOKENS = frozenset({
    "mask", "netmask", "length", "name", "desc", "description",
})

_MASK_POSITIVE_TOKENS = frozenset({"mask", "netmask"})

_IP_NEGATIVE_TOKENS = frozenset({
    "gateway", "vlan", "mask", "netmask", "subnet", "prefix", "cidr",
})

_SITE_POSITIVE_TOKENS = frozenset({"site", "location", "branch"})

_SITE_NEGATIVE_TOKENS = frozenset({
    "device", "host", "hostname", "server", "fqdn", "app", "vpn",
    "description", "desc", "name", "purpose",
    "palo", "fw", "as400", "ibm", "auth",
})

# ---------------------------------------------------------------------------
# Header-alias maps (lower-cased, no hard-coded column counts)
# ---------------------------------------------------------------------------

_HEADER_ALIASES: Dict[str, List[str]] = {
    "ip": ["ip", "address", "ipv4", "host"],
    "subnet": ["subnet", "network", "cidr", "prefix"],
    "vlan_id": ["vlan id", "vid", "vlan_id", "vlan"],
    "vlan_name": ["vlan name", "role", "name", "purpose", "description"],
    "site": ["site", "location", "branch", "site name", "site_name", "location name"],
}

_NORMALISE_RE = re.compile(r"[\s\-]+")


def _norm_key(h: str) -> str:
    return _NORMALISE_RE.sub("_", h.strip().lower()).strip("_")


def _col_norm(name: str) -> str:
    """Normalise a column header for semantic matching."""
    return _norm_key(name)


def _is_subnet_col(normed: str) -> bool:
    """True when *normed* is a subnet/network column per the spec."""
    has_network = any(tok in normed for tok in ("subnet", "network", "cidr", "prefix"))
    has_negative = any(tok in normed for tok in _SUBNET_NEGATIVE_TOKENS)
    return has_network and not has_negative


def _is_mask_col(normed: str) -> bool:
    """True when *normed* is a mask/netmask column per the spec."""
    return any(tok in normed for tok in _MASK_POSITIVE_TOKENS)


def _is_ip_col(normed: str) -> bool:
    """True when *normed* is an IP/host column per the spec."""
    has_ip = any(tok in normed for tok in ("ip", "address", "host"))
    has_negative = any(tok in normed for tok in _IP_NEGATIVE_TOKENS)
    return has_ip and not has_negative


def _is_site_col(normed: str) -> bool:
    """True when *normed* is a strict site/location/branch column per the spec.

    Only matches exact header tokens (no partial substring matches) to avoid
    capturing device/firewall tokens like 'sy3-palo-alto' or 'wips-as400-ibm' as
    site names in sheets without an explicit site column.
    """
    strict_matches = {"site", "location", "branch", "site_name", "location_name",
                      "site name", "location name"}
    if normed not in strict_matches:
        return False
    if any(tok in normed for tok in _SITE_NEGATIVE_TOKENS):
        return False
    return True

def _is_vlan_name_col(normed: str) -> bool:
    """True when *normed* is a vlan-name / role column per the spec."""
    if not normed:
        return False
    return (
        ("vlan" in normed and "name" in normed)
        or ("role" in normed and "vlan" not in normed)
        or normed in ("vlan_name", "vlanname", "name_", "purpose_", "description_")
    )


def _has_network_attr(normed: str) -> bool:
    return any(tok in normed for tok in _NETWORK_ATTR_TOKENS)


def _is_valid_ipv4(val: str) -> bool:
    """Return True when *val* is a dotted-decimal IPv4 address or CIDR."""
    if not val:
        return False
    try:
        ipaddress.IPv4Address(val.strip())
        return True
    except (ipaddress.AddressValueError, ValueError):
        pass
    try:
        ipaddress.IPv4Network(val.strip(), strict=False)
        return True
    except (ipaddress.AddressValueError, ValueError):
        return False


def _is_valid_mask(val: str) -> bool:
    """Return True when *val* is a valid dotted-decimal subnet mask
    (contiguous bit-string such as ``255.255.255.0``)."""
    if not val:
        return False
    s = val.strip()
    try:
        addr = ipaddress.IPv4Address(s)
        i = int(addr)
        # A valid mask has all leading 1-bits followed by all 0-bits.
        b = format(i, '032b')
        return b == '1' * b.count('1') + '0' * b.count('0')
    except (ipaddress.AddressValueError, ValueError):
        return False


def _combine_ip_and_mask(base_ip: str, mask_str: str) -> str:
    """Combine a base IPv4 address and a dotted-decimal netmask into a CIDR string.

    Returns the CIDR string (e.g. ``"10.113.252.0/23"``) or empty string on
    failure.  Never produces a bogus ``/32`` for a mask value.
    """
    if not base_ip or not mask_str:
        return ""
    try:
        network = ipaddress.IPv4Network(f"{base_ip.strip()}/{mask_str.strip()}", strict=False)
        return str(network)
    except (ipaddress.AddressValueError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# ExcelIPAMParser
# ---------------------------------------------------------------------------


class ExcelIPAMParser:
    """Stream multi-sheet Excel/CSV workbooks into normalised IPAM records."""

    def __init__(
        self,
        file_bytes: io.BytesIO,
        filename: str = "",
        rules: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.file_bytes = file_bytes
        self.filename = filename
        self.rules = rules or load_naming_rules()
        self._wb: Optional[openpyxl.Workbook] = None

    # -- private helpers -----------------------------------------------------

    @staticmethod
    def _find_header_row(
        rows_iter: Any, max_scan: int = 10
    ) -> Tuple[Optional[List[str]], int]:
        """Scan the first *max_scan* rows for a usable header row.

        Enterprise workbooks often place title banners in rows 1-3, so the scan
        window runs up to row 10 (0-based index 9).  A row qualifies as a header
        when it contains at least one network attribute token (``ip``,
        ``network``, ``subnet``, ``vlan``, ``prefix``, ``cidr``).  Returns the
        header values and the 0-based index of the header row within the scanned
        window.  Once a recognisable header is found the scan stops early so
        that subsequent data rows are not silently consumed by the discovery
        pass.
        """
        collected: List[Tuple[int, List[str]]] = []
        for i, row in enumerate(rows_iter):
            if i >= max_scan:
                break
            if row is None:
                continue
            vals = [str(c).strip() if c is not None else "" for c in row]
            if any(v for v in vals):
                collected.append((i, vals))
                # Stop as soon as we find a row containing a recognised
                # network attribute — don't burn through the scan window.
                normed = [_norm_key(v) for v in vals]
                if any(_has_network_attr(nk) for nk in normed):
                    return vals, i
        if not collected:
            return None, -1
        # Fallback: use the very first non-empty row.
        return collected[0][1], collected[0][0]

    def _build_header_map(self, headers: List[str]) -> Dict[str, int]:
        normed_headers = [_col_norm(h) for h in headers]
        mapping: Dict[str, int] = {}

        for idx, nh in enumerate(normed_headers):
            if not nh or nh in mapping.values() and nh:
                pass
            if _is_subnet_col(nh) and "subnet" not in mapping:
                mapping["subnet"] = idx
                continue
            if _is_mask_col(nh) and "mask" not in mapping:
                mapping["mask"] = idx
                continue
            if _is_ip_col(nh) and "ip" not in mapping:
                mapping["ip"] = idx
                continue
            if _is_site_col(nh) and "site" not in mapping:
                mapping["site"] = idx
                continue
            if _col_norm(nh) in ("vid", "vlan_id", "vlan_id_") or (
                "vlan" in nh and "name" not in nh and "id" in nh
            ):
                if "vlan_id" not in mapping:
                    mapping["vlan_id"] = idx
                    continue
            if _is_vlan_name_col(nh) and "vlan_name" not in mapping:
                mapping["vlan_name"] = idx

        # Alias fallback for canonical short headers that semantic tokens
        # may have skipped (e.g. a bare ``vlan`` column).
        norm_aliases = {
            _norm_key(a)
            for canon, aliases in _HEADER_ALIASES.items()
            for a in aliases
        }
        for idx, nh in enumerate(normed_headers):
            if nh in norm_aliases and "ip" not in mapping and _is_ip_col(nh):
                mapping["ip"] = idx
            elif nh in norm_aliases and "subnet" not in mapping and _is_subnet_col(nh):
                mapping["subnet"] = idx
            elif nh in norm_aliases and "site" not in mapping and _is_site_col(nh):
                mapping["site"] = idx
        return mapping

    @staticmethod
    def _normalise_ip(raw: str) -> str:
        if not raw or raw.lower() in ("nan", "none", "null", ""):
            return ""
        try:
            return str(ipaddress.IPv4Address(raw.strip()))
        except (ipaddress.AddressValueError, ValueError):
            return ""

    @staticmethod
    def _normalise_subnet(raw: str) -> str:
        if not raw or raw.lower() in ("nan", "none", "null", ""):
            return ""
        s = raw.strip()
        dot_m = re.match(r"^((?:\d{1,3}\.){3}\d{1,3})\.(\d{1,2})$", s)
        if dot_m:
            s = f"{dot_m.group(1)}/{dot_m.group(2)}"
        try:
            return str(ipaddress.IPv4Network(s, strict=False))
        except (ipaddress.AddressValueError, ValueError):
            return ""

    def _normalise_site(self, raw_site: str, default_site: str) -> str:
        site = (raw_site or default_site).strip()
        if not site or site.lower() in ("nan", "none", "null", ""):
            return default_site
        exact = get_site_code_rules(self.rules).get("exact_mappings", {})
        key = re.sub(r"[\s\-_]+", " ", site).strip().lower()
        for pat, code in exact.items():
            pat_str = str(pat).strip().lower()
            if key == pat_str or key.startswith(pat_str + " "):
                return str(code).upper()
        return slugify(site)

    def _normalise_role(self, raw_role: str) -> str:
        return normalize_role_name(raw_role, get_ipam_role_mappings(self.rules))

    def _make_record(
        self,
        ip_raw: str,
        subnet_raw: str,
        mask_raw: str,
        vlan_id_raw: str,
        vlan_name_raw: str,
        site_raw: str,
        default_site: str,
        source_sheet: str,
        extra_row: Optional[List[Any]] = None,
        header_map: Optional[Dict[str, int]] = None,
    ) -> Optional[Dict[str, Any]]:
        ip_clean = self._normalise_ip(ip_raw)

        # When a valid mask column exists, ALWAYS derive the subnet from
        # ip + mask rather than trusting the subnet column on its own.
        # This prevents a plain IP like 10.113.252.0 from becoming /32.
        if mask_raw and _is_valid_mask(mask_raw):
            base_ip = self._normalise_ip(ip_raw)
            if not base_ip and subnet_raw:
                base_ip = self._normalise_ip(subnet_raw)
            if base_ip:
                subnet_clean = _combine_ip_and_mask(base_ip, mask_raw)
            else:
                return None
            # No ip-only fallback when a mask column is present.
            if not subnet_clean:
                return None
        else:
            subnet_clean = self._normalise_subnet(subnet_raw)
            if not subnet_clean and not ip_clean:
                return None

        # Strict site vs description disambiguation: if the cell value in the
        # mapped "site" column contains device/VPN markers, route it to
        # description and fall back the site to the sheet name.
        device_markers = frozenset([
            "palo", "fw", "vpn", "as400", "ibm", "app", "auth",
            "server", "router", "switch", "firewall", "host",
        ])
        site_val_lower = site_raw.lower()
        has_device_marker = any(m in site_val_lower for m in device_markers)
        if has_device_marker and "site" in (header_map or {}):
            site_norm = self._normalise_site("", default_site)
            # Append the raw value as description context
            desc_extra = f"{site_raw} " if site_raw else ""
        else:
            site_norm = self._normalise_site(site_raw, default_site)
            desc_extra = ""

        role_norm = self._normalise_role(vlan_name_raw)
        vlan_desc = resolve_vlan_description(
            vlan_id_raw, role_norm,
            vlan_desc_mappings=get_vlan_description_mappings(self.rules),
        )
        description = f"{desc_extra}{vlan_desc}".strip() if desc_extra else vlan_desc

        return {
            "ip_address": ip_clean,
            "subnet": subnet_clean,
            "vlan_id": vlan_id_raw if vlan_id_raw not in ("", "nan", "NaN") else "",
            "vlan_name": role_norm or vlan_name_raw,
            "site": site_norm,
            "description": description,
            "source_sheet": source_sheet,
            "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    # -- public --------------------------------------------------------------

    @staticmethod
    def _detect_parallel_blocks(
        header_values: List[str],
        header_map: Dict[str, int],
        total_cols: int,
    ) -> List[Tuple[Dict[str, int], int, int]]:
        """Detect repeated header patterns indicating parallel tables.

        Returns a list of (sub_header_map, start_col, end_col) tuples.
        If no parallelism is detected, returns [(header_map, 0, total_cols)].
        """
        if total_cols < 4 or len(header_values) < 4:
            return [(header_map, 0, total_cols)]

        # Count occurrences of each canonical column type across columns
        col_types = []
        for idx in range(total_cols):
            normed = _col_norm(header_values[idx]) if idx < len(header_values) else ""
            types = []
            if _is_subnet_col(normed):
                types.append("subnet")
            if _is_mask_col(normed):
                types.append("mask")
            if _is_ip_col(normed):
                types.append("ip")
            if _is_site_col(normed):
                types.append("site")
            if normed in ("vid", "vlan_id", "vlan_id_") or (
                "vlan" in normed and "name" not in normed and "id" in normed
            ):
                types.append("vlan_id")
            if _is_vlan_name_col(normed):
                types.append("vlan_name")
            col_types.append(types)

        # Find repeating groups: a block boundary exists where a full subnet+ip
        # pair reappears after having appeared before.
        seen_pairs: set = set()
        boundaries: List[int] = []
        for idx in range(total_cols):
            pair = tuple(sorted(col_types[idx]))
            if pair in seen_pairs and len(seen_pairs) >= 2:
                boundaries.append(idx)
                break
            seen_pairs.add(pair)

        if not boundaries:
            return [(header_map, 0, total_cols)]

        # Split into two blocks
        split_at = boundaries[0]
        blocks = []
        for start, end in [(0, split_at), (split_at, total_cols)]:
            sub_headers = header_values[start:end]
            sub_map: Dict[str, int] = {}
            for k, v in header_map.items():
                if start <= v < end:
                    sub_map[k] = v - start
            if "ip" in sub_map or "subnet" in sub_map:
                blocks.append((sub_map, start, end))

        return blocks if blocks else [(header_map, 0, total_cols)]

    def parse(self) -> List[Dict[str, Any]]:
        """Open workbook (or CSV fallback) in streaming read-only mode and
        return a flat list of normalised IPAM records across all sheets."""
        self._wb = openpyxl.load_workbook(
            self.file_bytes, read_only=True, data_only=True
        )
        all_records: List[Dict[str, Any]] = []
        seen_keys: set = set()

        for sheet_name in self._wb.sheetnames:
            try:
                ws = self._wb[sheet_name]
            except KeyError:
                continue
            rows_iter = ws.iter_rows(values_only=True)

            header_values, header_row_idx = self._find_header_row(rows_iter)
            if header_values is None:
                continue

            header_map = self._build_header_map(header_values)
            if "ip" not in header_map and "subnet" not in header_map:
                continue

            default_site = sheet_name.strip()

            # Horizontal parallel table slicing
            total_cols = len(header_values)
            blocks = self._detect_parallel_blocks(header_values, header_map, total_cols)

            for block_map, block_start, block_end in blocks:
                for row in rows_iter:
                    if row is None:
                        continue
                    vals = list(row)
                    while len(vals) < block_end:
                        vals.append("")

                    # Translate block-relative column indices to absolute
                    abs_map = {k: v + block_start for k, v in block_map.items()}

                    ip_raw = str(vals[abs_map["ip"]]).strip() if "ip" in abs_map else ""
                    subnet_raw = str(vals[abs_map["subnet"]]).strip() if "subnet" in abs_map else ""
                    mask_raw = str(vals[abs_map["mask"]]).strip() if "mask" in abs_map else ""
                    vlan_id_raw = str(vals[abs_map["vlan_id"]]).strip() if "vlan_id" in abs_map else ""
                    vlan_name_raw = str(vals[abs_map["vlan_name"]]).strip() if "vlan_name" in abs_map else ""
                    site_raw = str(vals[abs_map["site"]]).strip() if "site" in abs_map else default_site

                    rec = self._make_record(
                        ip_raw, subnet_raw, mask_raw, vlan_id_raw, vlan_name_raw,
                        site_raw, default_site, sheet_name,
                        extra_row=list(row),
                        header_map=block_map,
                    )
                    if rec is None:
                        continue

                    key = (rec["subnet"], rec["ip_address"])
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    all_records.append(rec)

        if self._wb:
            self._wb.close()
            self._wb = None
        return all_records


# ---------------------------------------------------------------------------
# CSV / JSON fallback parsers
# ---------------------------------------------------------------------------


def _records_from_dataframe(
    df: pd.DataFrame,
    rules: Dict[str, Any],
    default_site: str,
    source: str,
) -> List[Dict[str, Any]]:
    """Map a flat DataFrame (CSV/JSON payload) onto normalised IPAM records
    using the same header-alias discovery as the Excel parser."""
    parser = ExcelIPAMParser(io.BytesIO(), filename=source, rules=rules)
    headers = [str(c) for c in df.columns]
    header_map = parser._build_header_map(headers)

    if "ip" not in header_map and "subnet" not in header_map:
        return []

    records: List[Dict[str, Any]] = []
    seen_keys: set = set()
    for _, row in df.iterrows():
        vals = []
        for h in headers:
            v = row.get(h)
            vals.append("" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))

        ip_raw = vals[header_map["ip"]].strip() if "ip" in header_map else ""
        subnet_raw = vals[header_map["subnet"]].strip() if "subnet" in header_map else ""
        mask_raw = vals[header_map["mask"]].strip() if "mask" in header_map else ""
        vlan_id_raw = vals[header_map["vlan_id"]].strip() if "vlan_id" in header_map else ""
        vlan_name_raw = vals[header_map["vlan_name"]].strip() if "vlan_name" in header_map else ""
        site_raw = vals[header_map["site"]].strip() if "site" in header_map else default_site

        rec = parser._make_record(
            ip_raw, subnet_raw, mask_raw, vlan_id_raw, vlan_name_raw,
            site_raw, default_site, source,
            extra_row=vals,
            header_map=header_map,
        )
        if rec is None:
            continue
        key = (rec["subnet"], rec["ip_address"])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        records.append(rec)
    return records


def _records_from_json(
    raw: Any,
    rules: Dict[str, Any],
    source: str,
) -> List[Dict[str, Any]]:
    """Extract records from a NetBox-style JSON payload.

    Accepts either a flat list of objects or a dict containing a ``prefixes``
    / ``ip-addresses`` / ``vlans`` array (NetBox backup export shape).
    """
    if isinstance(raw, dict):
        candidates = []
        for key in ("prefixes", "ip_addresses", "ip-addresses", "vlans", "records", "results"):
            val = raw.get(key)
            if isinstance(val, list):
                candidates.extend(val)
        if not candidates:
            candidates = [raw]
        raw = candidates

    if not isinstance(raw, list):
        return []

    rows: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rows.append({
            "address": item.get("address", item.get("ip_address", item.get("prefix", ""))),
            "subnet": item.get("prefix", item.get("subnet", item.get("network", ""))),
            "vlan_id": item.get("vid", item.get("vlan_id", item.get("vlan", ""))),
            "vlan_name": item.get("role", item.get("name", item.get("vlan_name", ""))),
            "site": item.get("site", item.get("location", "")),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    return _records_from_dataframe(df, rules, default_site=source, source=source)


def parse_file_bytes(
    file_bytes: io.BytesIO,
    filename: str = "",
    rules: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Dispatch to the right parser based on the uploaded file extension.

    Supported: ``.xlsx`` / ``.xls`` (Excel), ``.csv`` (pandas), ``.json``
    (NetBox backup).  Returns a flat list of normalised IPAM records.
    """
    rules = rules or load_naming_rules()
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        return ExcelIPAMParser(file_bytes, filename=filename, rules=rules).parse()
    if lower.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes.read()))
        return _records_from_dataframe(df, rules, default_site=filename, source=filename)
    if lower.endswith(".json"):
        raw = json.loads(file_bytes.read().decode("utf-8"))
        return _records_from_json(raw, rules, source=filename)
    raise ValueError(f"Unsupported file type: {filename}")


# ---------------------------------------------------------------------------
# In-Memory Audit Engine
# ---------------------------------------------------------------------------


def _build_db_index() -> Tuple[set, set, set, Dict[str, List[Dict[str, Any]]]]:
    """Query local DB once and build hash sets for O(1) lookups.

    Returns:
        (existing_prefixes, existing_ips, existing_sites, db_record_map)
    """
    db_init()
    prefixes = set(get_existing_prefix_strings())
    sites = set(str(s).strip().lower() for s in get_all_site_names() if s)
    ip_set: set = set()
    record_map: Dict[str, List[Dict[str, Any]]] = {}
    try:
        with sqlite3.connect(DATABASE_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM ipam_records WHERE prefix_or_subnet != ''")
            for row in cur.fetchall():
                prefix = str(row["prefix_or_subnet"]).strip()
                if not prefix:
                    continue
                ip_set.add(prefix)
                if "/" in prefix:
                    record_map.setdefault(prefix, []).append({
                        "site": str(row["site"] or "").strip(),
                        "vlan_id": str(row["vlan_id"] or "").strip(),
                        "vlan_name": str(row["vlan_name"] or "").strip(),
                        "role": str(row["role"] or "").strip(),
                        "description": str(row["description"] or "").strip(),
                        "record_type": str(row["record_type"] or "").strip(),
                    })
    except Exception:
        pass
    return prefixes, ip_set, sites, record_map


def _build_backup_index() -> Tuple[set, set, set, Dict[str, List[Dict[str, Any]]]]:
    """Build lookup sets from the loaded NetBox JSON backup (authoritative source).

    Falls back to empty sets when no backup is loaded.
    Returns (existing_prefixes, existing_ips, existing_sites, db_record_map).
    """
    from core.shared_backup_state import SharedBackupState

    prefixes: set = set()
    sites: set = set()
    ip_set: set = set()
    record_map: Dict[str, List[Dict[str, Any]]] = {}

    inspector = SharedBackupState.get_inspector()
    if inspector is None:
        return prefixes, ip_set, sites, record_map

    registry = inspector.inspect_all_objects()

    def _find_key(candidates: List[str]) -> Optional[str]:
        for c in candidates:
            if c in registry:
                return c
        return None

    # --- Prefixes ---
    prefix_key = _find_key(["ipam/prefixes", "ipam/prefix", "prefixes", "prefix"])
    if prefix_key:
        for obj in registry[prefix_key].get("data", []):
            cidr = obj.get("prefix", obj.get("address", ""))
            if cidr:
                try:
                    net = ipaddress.IPv4Network(str(cidr).strip(), strict=False)
                    prefixes.add(str(net))
                except (ValueError, ipaddress.AddressValueError):
                    prefixes.add(str(cidr).strip())
            site = obj.get("site", "")
            if site:
                sites.add(str(site).strip().lower())

    # --- VLANs / IPs ---
    vlan_key = _find_key(["ipam/vlans", "ipam/vlan", "vlans", "vlan"])
    if vlan_key:
        for obj in registry[vlan_key].get("data", []):
            sid = obj.get("site", "")
            if sid:
                sites.add(str(sid).strip().lower())

    # --- Sites ---
    site_key = _find_key(["dcim/sites", "dcim/site", "sites", "site"])
    if site_key:
        for obj in registry[site_key].get("data", []):
            sn = obj.get("slug", obj.get("name", ""))
            if sn:
                sites.add(str(sn).strip().lower())

    return prefixes, ip_set, sites, record_map


def _classify_from_backup(
    records: List[Dict[str, Any]],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, int]]:
    """Classify records using the authoritative backup index."""
    existing_prefixes, existing_ips, existing_sites, db_record_map = _build_backup_index()
    if not existing_prefixes and not existing_sites:
        return classify_records(records)
    return classify_records(
        records,
        existing_prefixes=existing_prefixes,
        existing_ips=existing_ips,
        existing_sites=existing_sites,
        db_record_map=db_record_map,
    )


def _index_is_provided(idx):
    """Return True when a non-empty hash index has been explicitly supplied."""
    return idx is not None and len(idx) > 0


def classify_records(
    records: List[Dict[str, Any]],
    existing_prefixes: Optional[set] = None,
    existing_ips: Optional[set] = None,
    existing_sites: Optional[set] = None,
    db_record_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, int]]:
    """Classify parsed IPAM records into matched / pending_add / conflicts /
    unmapped_sites using in-memory hash sets.

    Returns:
        (matched_df, pending_add_df, conflicts_df, metrics)
    """
    if existing_prefixes is None or existing_ips is None or existing_sites is None:
        existing_prefixes, existing_ips, existing_sites, db_record_map = _build_backup_index()
        if not existing_prefixes and not existing_sites:
            existing_prefixes, existing_ips, existing_sites, db_record_map = _build_db_index()

    matched_rows: List[Dict[str, Any]] = []
    pending_rows: List[Dict[str, Any]] = []
    conflict_rows: List[Dict[str, Any]] = []
    unmapped_sites: set = set()

    for rec in records:
        subnet = rec.get("subnet", "").strip()
        ip = rec.get("ip_address", "").strip()
        site = rec.get("site", "").strip()
        vlan_id = str(rec.get("vlan_id", "") or "").strip()

        # 1. Absent from DB → pending add (unmapped sites are ingestible here).
        has_prefix_idx = _index_is_provided(existing_prefixes)
        has_ip_idx = _index_is_provided(existing_ips)
        in_db = (
            (has_prefix_idx and subnet in existing_prefixes)
            or (has_ip_idx and ip and ip in existing_ips)
        )
        if not in_db:
            # 2. Unmapped site — non-blocking, placed in pending_add with tag.
            if _index_is_provided(existing_sites) and site.lower() not in existing_sites:
                unmapped_sites.add(site)
                tagged = {**rec, "category": "pending_add", "unmapped_site": True}
                pending_rows.append(tagged)
            else:
                pending_rows.append({**rec, "category": "pending_add"})
            continue

        # 3. Present in DB — verify site is mapped; unmapped sites stay in
        #    conflicts only when there is a genuine attribute inconsistency.
        if _index_is_provided(existing_sites) and site.lower() not in existing_sites:
            unmapped_sites.add(site)
            conflict_rows.append({
                **rec,
                "conflict_reason": f"Site unmapped: {site}",
                "category": "unmapped_site",
            })
            continue

        attrs = (db_record_map or {}).get(subnet, []) if subnet else []
        if not attrs:
            matched_rows.append({**rec, "category": "matched"})
            continue

        reason = _attribute_conflict_reason(rec, attrs)
        if reason:
            conflict_rows.append({
                **rec,
                "conflict_reason": reason,
                "category": "conflict",
            })
        else:
            matched_rows.append({**rec, "category": "matched"})

    matched_df = _df(matched_rows, ["ip_address", "subnet", "vlan_id", "vlan_name",
                                    "site", "description", "source_sheet",
                                    "ingested_at", "category"])
    pending_df = _df(pending_rows, ["ip_address", "subnet", "vlan_id", "vlan_name",
                                    "site", "description", "source_sheet",
                                    "ingested_at", "category", "unmapped_site"])
    conflicts_df = _df(conflict_rows, ["ip_address", "subnet", "vlan_id", "vlan_name",
                                       "site", "description", "source_sheet",
                                       "ingested_at", "category", "conflict_reason"])

    metrics = {
        "total_parsed": len(records),
        "matched": len(matched_rows),
        "pending_add": len(pending_rows),
        "conflicts": len(conflict_rows),
        "unmapped_sites": len(unmapped_sites),
        "unmapped_site_list": sorted(unmapped_sites),
    }
    # Mathematical partition invariant: Total == Matched + Pending + Conflicts
    metrics["partition_valid"] = (
        metrics["total_parsed"]
        == metrics["matched"] + metrics["pending_add"] + metrics["conflicts"]
    )
    return matched_df, pending_df, conflicts_df, metrics


def _df(rows: List[Dict[str, Any]], columns: List[str]) -> pd.DataFrame:
    if rows:
        return pd.DataFrame(rows)
    return pd.DataFrame(columns=columns)


def _attribute_conflict_reason(
    rec: Dict[str, Any], db_attrs: List[Dict[str, Any]]
) -> str:
    """Return a human-readable conflict reason when the parsed record's site or
    VLAN ID differs from every stored attribute set for the same prefix."""
    excel_site = str(rec.get("site", "")).strip().lower()
    excel_vlan = str(rec.get("vlan_id", "") or "").strip()

    reasons: List[str] = []
    if excel_site:
        site_mismatch = all(
            excel_site != str(a.get("site", "")).strip().lower()
            for a in db_attrs if a.get("site")
        )
        if site_mismatch:
            reasons.append("Site mismatch")
    if excel_vlan:
        vlan_mismatch = all(
            excel_vlan != str(a.get("vlan_id", "") or "").strip()
            for a in db_attrs if a.get("vlan_id")
        )
        if vlan_mismatch:
            reasons.append("VLAN ID mismatch")
    return "; ".join(reasons)


def export_pending_csvs(
    pending_df: pd.DataFrame,
) -> Tuple[io.BytesIO, io.BytesIO]:
    """Build NetBox-import-ready CSV buffers for pending-add records.

    Returns two ``BytesIO`` objects (prefixes, vlans) already seeked to 0 so
    callers can invoke ``.getvalue()`` or ``.read()`` without further state
    management.
    """
    prefixes_cols = ["subnet", "vlan_id", "vlan_name", "site", "description",
                     "ip_address"]
    vlans_cols = ["vlan_id", "vlan_name", "site", "description", "subnet"]

    prefix_buf = io.BytesIO()
    if not pending_df.empty:
        cols = [c for c in prefixes_cols if c in pending_df.columns]
        pending_df[cols].to_csv(prefix_buf, index=False)
    prefix_buf.seek(0)

    vlan_buf = io.BytesIO()
    if not pending_df.empty:
        cols = [c for c in vlans_cols if c in pending_df.columns]
        pending_df[cols].to_csv(vlan_buf, index=False)
    vlan_buf.seek(0)

    return prefix_buf, vlan_buf


def run_audit(
    file_bytes: io.BytesIO,
    filename: str = "",
    rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Top-level entry point: parse → classify → return structured result.

    Returns dict with keys:
        matched, pending_add, conflicts, metrics,
        prefixes_csv, vlans_csv, parsed_at
    """
    records = parse_file_bytes(file_bytes, filename=filename, rules=rules)
    matched, pending, conflicts, metrics = classify_records(records)
    prefixes_buf, vlans_buf = export_pending_csvs(pending)
    return {
        "matched": matched,
        "pending_add": pending,
        "conflicts": conflicts,
        "metrics": metrics,
        "prefixes_csv": prefixes_buf,
        "vlans_csv": vlans_buf,
        "parsed_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
