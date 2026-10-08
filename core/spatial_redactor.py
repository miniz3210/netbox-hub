"""
Spatial Redactor: Card-Centric Upward Binding Pipeline.

Processes OCR token lists (with bounding boxes) into hierarchical structured text
grouped by virtual switch containers, using card-centric upward binding that is
immune to DPI / resolution scaling and sidebar state. Simultaneously performs
deterministic in-memory redaction of sensitive tokens (IPv4, internal domains,
MACs) before the payload reaches any external LLM.

Token format expected:
    list[dict]  [{"text": str, "box": [x, y, w, h]}, ...]
    box = [x, y, width, height]  (top-left origin, pixel coordinates)

Config keys (spatial_anchors_and_redaction block):
    enabled: bool
    anchors: list[dict]           # Each dict has "role" and "patterns" (list of strings)
        role: "left_boundary" | "container_header" | "adapter_column"
        patterns: list[str]       # Multi-pattern support per role
    redact_ipv4: bool
    redact_domains: bool
    redact_mac: bool
    domain_patterns: list[str]    # e.g. ["\\.adds$", "\\.local$", ...]
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)

_MAC_COLON_RE = re.compile(
    r"\b(?:(?:[0-9A-Fa-f]{2}:){5})[0-9A-Fa-f]{2}\b"
)
_MAC_HYPHEN_RE = re.compile(
    r"\b(?:(?:[0-9A-Fa-f]{2}-){5})[0-9A-Fa-f]{2}\b"
)
_MAC_BARE_RE = re.compile(r"\b[0-9A-Fa-f]{12}\b")
_MAC_RE = re.compile(
    rf"(?:{_MAC_COLON_RE.pattern}|{_MAC_HYPHEN_RE.pattern}|{_MAC_BARE_RE.pattern})"
)

_COLLAPSED_PREFIX_RE = re.compile(r"^[\s>›»▶]+")

# Normalized container patterns — lowercase, no colons/spaces.
_CONTAINER_NORM_PATTERNS = ["standardswitch", "dvswitch", "vmbr"]

# Switch-name extraction regex (preserves legacy behaviour).
_SWITCH_NAME_RE = re.compile(
    r'(?:[vV\s]*)?'
    r'Standard\s*Switch:\s*'
    r'([^\n\r]+?)'
    r'(?:\s+ADD\s+NETWORKING|\s+EDIT|\s+MANAGE\s+(?:PHYSICAL\s+ADAPTERS|ADAPTERS)?|\s+MANAGE|\s*$)',
    re.IGNORECASE,
)


def _build_domain_re(patterns: List[str]) -> Optional[re.Pattern]:
    """Build a combined regex from user-provided domain suffix patterns."""
    if not patterns:
        return None
    fragments: List[str] = []
    for p in patterns:
        if not p:
            continue
        pat = p.rstrip("$").rstrip("\\") if p.endswith("$") else p
        try:
            compiled = re.compile(pat, re.IGNORECASE)
            fragments.append(compiled.pattern)
        except re.error as exc:
            logger.warning("Invalid domain pattern: %s — %s", pat, exc)
    if not fragments:
        return None
    label = r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?"
    dot_label = rf"\.{label}"
    inner = "|".join(fragments)
    combined = (
        rf"\b{label}(?:{dot_label})*"
        rf"(?:{inner})"
        rf"(?:{dot_label})*"
    )
    try:
        return re.compile(combined, re.IGNORECASE)
    except re.error as exc:
        logger.warning("Invalid combined domain pattern: %s — %s", combined, exc)
        return None


def _resolve_anchor_patterns(config: Dict, role: str) -> List[str]:
    """Extract pattern list for a given anchor role from config."""
    anchors = config.get("anchors")
    if isinstance(anchors, list):
        for anchor_entry in anchors:
            if not isinstance(anchor_entry, dict):
                continue
            if anchor_entry.get("role") == role:
                patterns = anchor_entry.get("patterns")
                if isinstance(patterns, list) and patterns:
                    return patterns
                return []

    legacy_map = {
        "left_boundary": "left_boundary_anchor",
        "container_header": "container_header",
        "adapter_column": "adapter_column_anchor",
    }
    key = legacy_map.get(role)
    if key and key in config:
        val = config[key]
        if isinstance(val, list):
            return val
        if isinstance(val, str) and val.strip():
            return [val.strip()]
    return []


def _token_center(box: List[float]) -> Tuple[float, float]:
    """Return (center_x, center_y) from a [x, y, w, h] box."""
    x, y, w, h = box
    return (x + w / 2.0, y + h / 2.0)


def _is_collapsed_header(text: str) -> bool:
    """Return True if the token text or its leading symbols indicate a collapsed section."""
    stripped = text.strip()
    if not stripped:
        return False
    prefix_match = _COLLAPSED_PREFIX_RE.match(stripped)
    if prefix_match:
        prefix = prefix_match.group(0)
        if any(c in prefix for c in ('>', '›', '»', '▶')):
            return True
    return False


def _normalize_for_match(text: str) -> str:
    """Lowercase, strip all whitespace and colons for pattern matching."""
    return re.sub(r'[\s:]', '', text.lower())


def _extract_switch_name(raw_text: str) -> str:
    """Extract the switch name from a header token's raw text.

    Uses the dedicated regex first, then falls back to colon-split + UI-artifact
    stripping per the card-centric spec.
    """
    text = str(raw_text).strip()

    m = _SWITCH_NAME_RE.match(text)
    if m:
        name = m.group(1).strip()
        if name:
            return name

    # Fallback: split on last colon, then strip UI control artifacts.
    parts = text.split(':')
    if len(parts) > 1:
        remaining = parts[-1].strip()
    else:
        remaining = text

    clean_name = re.split(
        r'\s*(?:ADD|EDIT|MANAGE|\.\.\.)',
        remaining,
        flags=re.IGNORECASE,
    )[0].strip(' :->»▶')

    return clean_name if clean_name else text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_spatial_topology(
    ocr_tokens: List[Dict],
    config: Dict,
) -> Tuple[str, Dict[str, str]]:
    """
    Process OCR tokens through card-centric upward binding + deterministic redaction.

    Args:
        ocr_tokens: list of {"text": str, "box": [x, y, w, h]} dicts.
        config: spatial_anchors_and_redaction dict from the active platform preset.

    Returns:
        (structured_text, redaction_map)
    """
    enabled = bool(config.get("enabled", False))
    if not enabled:
        lines = [t.get("text", "") for t in ocr_tokens if t.get("text")]
        return "\n".join(lines), {}

    left_boundary_patterns = _resolve_anchor_patterns(config, "left_boundary")
    container_header_patterns = _resolve_anchor_patterns(config, "container_header")
    adapter_patterns = _resolve_anchor_patterns(config, "adapter_column")

    redact_ipv4 = bool(config.get("redact_ipv4", True))
    redact_domains = bool(config.get("redact_domains", True))
    redact_mac = bool(config.get("redact_mac", True))
    domain_patterns: List[str] = config.get("domain_patterns", [])
    if not isinstance(domain_patterns, list):
        domain_patterns = []

    redaction_map: Dict[str, str] = {}

    # Step A: Dynamic left boundary filter
    filtered_tokens = _step_a_filter(ocr_tokens, left_boundary_patterns)
    print(f"[SPATIAL-FILTER] Input tokens: {len(ocr_tokens)}, Kept tokens: {len(filtered_tokens)}")

    # Step B: Card-centric upward binding
    containers = _step_b_card_centric_bind(filtered_tokens, container_header_patterns)

    # Step C + D + E: For each container, partition by column and redact
    output_lines: List[str] = []
    total_redacted = 0

    for container_name, pg_tokens, ul_tokens in containers:
        output_lines.append(f"=== CONTAINER: {container_name} ===")

        # Port Groups & VMkernel section
        pg_text, n_pg = _redact_and_emit(
            pg_tokens, redaction_map,
            redact_ipv4, redact_domains, redact_mac, domain_patterns,
        )
        total_redacted += n_pg
        if pg_text.strip():
            output_lines.append("[Port Groups & VMkernel]")
            output_lines.extend(pg_text.strip().splitlines())

        # Physical Uplinks section
        ul_text, n_ul = _redact_and_emit(
            ul_tokens, redaction_map,
            redact_ipv4, redact_domains, redact_mac, domain_patterns,
        )
        total_redacted += n_ul
        if ul_text.strip():
            output_lines.append("[Physical Adapters]")
            output_lines.extend(ul_text.strip().splitlines())

        output_lines.append("")

    structured_text = "\n".join(output_lines).strip()

    # Failsafe: if no valid switch containers were found or all containers are empty,
    # emit cleaned flat text of tokens.
    if not containers or all(
        not pg_tokens and not ul_tokens for _, pg_tokens, ul_tokens in containers
    ):
        logger.warning(
            "[SpatialEngine] No valid switch containers found. Falling back to clean flat text."
        )
        flat_lines = [
            str(t.get("text", "")).strip()
            for t in filtered_tokens
            if str(t.get("text", "")).strip()
        ]
        if flat_lines:
            structured_text, _ = _redact_flat_text(
                "\n".join(flat_lines), redaction_map,
                redact_ipv4, redact_domains, redact_mac, domain_patterns,
            )
    elif not structured_text:
        logger.warning(
            "[SpatialEngine] Fallback triggered: empty spatial clustering. "
            "Falling back to cleaned flat text."
        )
        flat_lines = [
            str(t.get("text", "")).strip()
            for t in ocr_tokens
            if str(t.get("text", "")).strip()
        ]
        if flat_lines:
            structured_text = "\n".join(flat_lines)

    logger.info(
        "Card-centric spatial grouping applied. Redacted %d sensitive tokens locally.",
        total_redacted,
    )
    return structured_text, redaction_map


# ---------------------------------------------------------------------------
# Step A: Left-boundary filter
# ---------------------------------------------------------------------------

def _step_a_filter(
    tokens: List[Dict],
    anchor_patterns: List[str],
) -> List[Dict]:
    if not anchor_patterns or not tokens:
        return list(tokens)

    matches = [
        t for t in tokens
        if any(
            pat.lower() in str(t.get("text", "")).lower()
            for pat in anchor_patterns
        )
        and t.get("box") and len(t["box"]) >= 2
    ]
    if not matches:
        return list(tokens)

    anchor_match = min(matches, key=lambda t: float(t["box"][0]))
    anchor_x = float(anchor_match["box"][0])

    if anchor_x < 180:
        return list(tokens)

    left_cutoff_x = max(0.0, anchor_x - 15.0)
    kept = [
        t for t in tokens
        if t.get("box") and len(t["box"]) >= 1
        and float(t["box"][0]) >= left_cutoff_x
    ]

    if len(tokens) > 5 and len(kept) < (len(tokens) * 0.6):
        return list(tokens)

    return kept


# ---------------------------------------------------------------------------
# Step B: Card-centric upward binding
# ---------------------------------------------------------------------------

def _extract_switch_headers(
    tokens: List[Dict],
    header_patterns: List[str],
) -> List[Tuple[float, str]]:
    """Scan tokens and identify active (non-collapsed) switch headers.

    Returns list of (header_y_top, switch_name) sorted by header_y ascending.
    """
    active_switches: List[Tuple[float, str]] = []

    # Combine user config patterns with default universal patterns
    all_patterns = list(header_patterns) + _CONTAINER_NORM_PATTERNS
    norm_containers = [re.sub(r'[\s:]', '', p).lower() for p in all_patterns if p]

    for t in tokens:
        text = str(t.get("text", "")).strip()
        if _is_collapsed_header(text):
            continue

        norm_text = _normalize_for_match(text)
        # Strip leading expanded arrow 'v' or 'v-'
        if norm_text.startswith(('v', 'v-')) and not any(np.startswith(('v', 'v-')) for np in norm_containers):
            norm_text = norm_text.lstrip('v-')

        is_container = any(npat in norm_text for npat in norm_containers)
        if not is_container:
            continue

        # Extract clean switch name:
        # Split by ':' first, then split by '|' or UI buttons (ADD, EDIT, MANAGE)
        parts = text.split(':')
        raw_name = parts[-1] if len(parts) > 1 else text
        clean_name = re.split(r'[\s|]*(?:ADD|EDIT|MANAGE|\.\.\.)', raw_name, flags=re.IGNORECASE)[0].strip(' :->»▶|')
        if clean_name:
            y_top = float(t.get("box", [0, 0, 0, 0])[1])
            active_switches.append((y_top, clean_name))

    # Sort by Y-top ascending.
    active_switches.sort(key=lambda sw: sw[0])
    return active_switches


def _is_port_group_card(text: str) -> bool:
    """Return True if token text indicates a Port Group / VMkernel card."""
    low = text.lower()
    indicators = [
        'vlan id', 'vmkernel', 'virtual machines', 'port group',
        'vmkernel ports', 'vmk',
    ]
    return any(ind in low for ind in indicators)


def _is_uplink_card(text: str) -> bool:
    """Return True if token text indicates an Uplink / physical adapter card."""
    low = text.lower()
    if 'vmnic' in low:
        return True
    if 'no physical network adapters' in low:
        return True
    if 'full' in low and re.search(r'\d+\s*full', low):
        return True
    return False


def _step_b_card_centric_bind(
    tokens: List[Dict],
    header_patterns: List[str],
) -> List[Tuple[str, List[Dict], List[Dict]]]:
    """Group tokens into (switch_name, port_group_tokens, uplink_tokens).

    Each card/token block is assigned to the nearest ACTIVE switch header ABOVE it.
    Tokens with no active switch above are discarded.
    """
    active_switches = _extract_switch_headers(tokens, header_patterns)

    if not active_switches:
        logger.warning(
            "[SpatialEngine] No active containers identified via dynamic patterns. "
            "Signalling fallback to process_spatial_topology."
        )
        return []

    # Classify each token as a card and bind to parent switch.
    card_assignments: Dict[str, List[Dict]] = {sw[1]: [] for sw in active_switches}
    discarded_count = 0

    for t in tokens:
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        # Discard collapsed headers — they are not cards, only section markers.
        if _is_collapsed_header(text):
            discarded_count += 1
            continue

        box = t.get("box", [0, 0, 0, 0])
        card_y = float(box[1])

        # Find nearest active switch ABOVE this card.
        parent_switch = None
        for sw_y, sw_name in active_switches:
            if sw_y <= card_y:
                parent_switch = sw_name
            else:
                break

        if parent_switch is None:
            # No active switch above — discard (eliminates breadcrumbs, top-bar controls).
            discarded_count += 1
            continue

        card_assignments[parent_switch].append(t)

    if discarded_count > 0:
        logger.debug(
            "[SpatialEngine] Discarded %d tokens with no active switch above them.",
            discarded_count,
        )

    # Within each container, split into port-group bucket and uplink bucket.
    result: List[Tuple[str, List[Dict], List[Dict]]] = []
    for sw_name in [sw[1] for sw in active_switches]:
        sw_tokens = card_assignments[sw_name]
        pg_tokens, ul_tokens = _step_c_column_split_card_centric(sw_tokens)
        result.append((sw_name, pg_tokens, ul_tokens))

    return result


def _resolve_anchor_patterns_raw(role: str) -> List[str]:
    """Placeholder — callers should pass patterns explicitly."""
    return []


# ---------------------------------------------------------------------------
# Step C: Column partitioning (PortGroups vs Uplinks) — card-centric
# ---------------------------------------------------------------------------

def _step_c_column_split_card_centric(
    tokens: List[Dict],
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split tokens into (port_groups, uplinks) based on card content patterns.

    - Uplink bucket: tokens containing 'vmnic', 'Full' (speed), or
      'No physical network adapters'.
    - Port Group bucket: all remaining tokens (port group titles, VLAN IDs,
      VMkernel interfaces, IP addresses, etc.).

    If 'No physical network adapters' is detected, uplink bucket is explicitly
    marked EMPTY (no inheritance from other switches).
    """
    if not tokens:
        return [], []

    combined_text = " ".join(str(t.get("text", "")).lower() for t in tokens)

    # Empty-state check: no physical adapters present.
    if "no physical network adapters" in combined_text:
        return list(tokens), []

    port_groups: List[Dict] = []
    uplinks: List[Dict] = []

    for t in tokens:
        text = str(t.get("text", "")).strip()
        if _is_uplink_card(text):
            uplinks.append(t)
        else:
            port_groups.append(t)

    return port_groups, uplinks


# Legacy column split preserved for backward compatibility with tests.
def _step_c_column_split(
    tokens: List[Dict],
    adapter_patterns: List[str],
) -> Tuple[List[Dict], List[Dict]]:
    """Legacy column split using adapter anchor position."""
    if not adapter_patterns:
        return list(tokens), []

    combined_text = " ".join(str(t.get("text", "")).lower() for t in tokens)
    if "no physical network adapters" in combined_text or "no associated port groups" in combined_text:
        return list(tokens), []

    split_x: Optional[float] = None
    for t in tokens:
        text = str(t.get("text", "")).strip()
        if any(pat.lower() in text.lower() for pat in adapter_patterns):
            box = t.get("box")
            if box and len(box) >= 1:
                split_x = float(box[0]) - 15.0
                break

    port_groups: List[Dict] = []
    uplinks: List[Dict] = []
    for t in tokens:
        bx = float(t.get("box", [0, 0, 0, 0])[0])
        if split_x is not None and bx >= split_x:
            uplinks.append(t)
        else:
            port_groups.append(t)

    return port_groups, uplinks


# ---------------------------------------------------------------------------
# Step D + E: Deterministic redaction & markdown emission
# ---------------------------------------------------------------------------

def _redact_and_emit(
    tokens: List[Dict],
    redaction_map: Dict[str, str],
    redact_ipv4: bool,
    redact_domains: bool,
    redact_mac: bool,
    domain_patterns: List[str],
) -> Tuple[str, int]:
    """Redact sensitive data in-token and emit clean markdown lines."""
    domain_re = _build_domain_re(domain_patterns) if redact_domains else None
    counters = {"ip": 0, "domain": 0, "mac": 0}
    redacted_count = 0
    lines: List[str] = []

    for t in tokens:
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        cleaned = text
        if redact_ipv4:
            cleaned, n = _redact_ipv4(cleaned, counters, redaction_map)
            redacted_count += n
        if redact_domains and domain_re:
            cleaned, n = _redact_domains(cleaned, counters, redaction_map, domain_re)
            redacted_count += n
        if redact_mac:
            cleaned, n = _redact_mac(cleaned, counters, redaction_map)
            redacted_count += n
        lines.append(f"- {cleaned}")

    return "\n".join(lines), redacted_count


def _redact_ipv4(
    text: str,
    counters: Dict,
    redaction_map: Dict[str, str],
) -> Tuple[str, int]:
    count = 0
    def _repl(m: re.Match) -> str:
        nonlocal count
        ip = m.group(0)
        if ip in redaction_map:
            return redaction_map[ip]
        counters["ip"] += 1
        token = f"<IP_{counters['ip']}>"
        redaction_map[token] = ip
        count += 1
        return token
    result = _IPV4_RE.sub(_repl, text)
    return result, count


def _redact_domains(
    text: str,
    counters: Dict,
    redaction_map: Dict[str, str],
    domain_re: re.Pattern,
) -> Tuple[str, int]:
    count = 0
    def _repl(m: re.Match) -> str:
        nonlocal count
        domain = m.group(0)
        if domain in redaction_map:
            return redaction_map[domain]
        counters["domain"] += 1
        token = f"<DOMAIN_{counters['domain']}>"
        redaction_map[token] = domain
        count += 1
        return token
    result = domain_re.sub(_repl, text)
    return result, count


def _redact_mac(
    text: str,
    counters: Dict,
    redaction_map: Dict[str, str],
) -> Tuple[str, int]:
    count = 0
    def _repl(m: re.Match) -> str:
        nonlocal count
        mac = m.group(0)
        if mac in redaction_map:
            return redaction_map[mac]
        counters["mac"] += 1
        token = f"<MAC_{counters['mac']}>"
        redaction_map[token] = mac
        count += 1
        return token
    result = _MAC_RE.sub(_repl, text)
    return result, count


def _redact_flat_text(
    text: str,
    redaction_map: Dict[str, str],
    redact_ipv4: bool,
    redact_domains: bool,
    redact_mac: bool,
    domain_patterns: List[str],
) -> Tuple[str, int]:
    """Redact sensitive data in plain text (no card structure)."""
    domain_re = _build_domain_re(domain_patterns) if redact_domains else None
    counters = {"ip": 0, "domain": 0, "mac": 0}
    redacted_count = 0

    cleaned = text
    if redact_ipv4:
        cleaned, n = _redact_ipv4(cleaned, counters, redaction_map)
        redacted_count += n
    if redact_domains and domain_re:
        cleaned, n = _redact_domains(cleaned, counters, redaction_map, domain_re)
        redacted_count += n
    if redact_mac:
        cleaned, n = _redact_mac(cleaned, counters, redaction_map)
        redacted_count += n

    return cleaned, redacted_count


# ---------------------------------------------------------------------------
# Legacy helpers preserved for test compatibility
# ---------------------------------------------------------------------------

def _step_b_partition(
    tokens: List[Dict],
    header_patterns: List[str],
) -> List[Tuple[str, List[Dict]]]:
    """Legacy Y-interval partitioning — preserved for backward compat with tests."""
    if not header_patterns:
        return [("__global__", list(tokens))]

    norm_patterns = []
    for pat in header_patterns:
        norm = re.sub(r'[\s:]', '', pat).lower()
        if norm:
            norm_patterns.append(norm)

    if not norm_patterns:
        return [("__global__", list(tokens))]

    header_tokens: List[Dict] = []
    for t in tokens:
        text = str(t.get("text", "")).strip()
        if _is_collapsed_header(text):
            continue
        norm_text = re.sub(r'[\s]', '', text).lower()
        if norm_text.startswith(('v', 'v-')) and not any(
            np.startswith(('v', 'v-')) for np in norm_patterns
        ):
            norm_text = norm_text[1:]
        for npat in norm_patterns:
            if norm_text.startswith(npat):
                header_tokens.append(t)
                break

    if not header_tokens:
        return [("__global__", list(tokens))]

    header_tokens.sort(key=lambda t: float(t.get("box", [0, 0, 0, 0])[1]))

    containers: List[Tuple[str, List[Dict]]] = []
    for idx, ht in enumerate(header_tokens):
        name = _extract_container_name(ht.get("text", ""), header_patterns)
        y_top = float(ht["box"][1])
        y_bottom = (
            float(header_tokens[idx + 1]["box"][1])
            if idx + 1 < len(header_tokens)
            else float(ht["box"][1]) + float(ht["box"][3]) + 500
        )
        assigned: List[Dict] = []
        for t in tokens:
            cy = _token_center(t.get("box", [0, 0, 0, 0]))[1]
            if y_top <= cy < y_bottom:
                assigned.append(t)
        containers.append((name, assigned))

    return containers


def _extract_container_name(raw_text: str, header_patterns: List[str]) -> str:
    """Pull the container label out of a header token's text (legacy)."""
    text = str(raw_text).strip()

    m = _SWITCH_NAME_RE.match(text)
    if m:
        name = m.group(1).strip()
        if name:
            return name

    norm_pat_stripped = re.sub(r'[\s:]', '', text).lower()
    if norm_pat_stripped.startswith(('v', 'v-')):
        norm_pat_stripped = norm_pat_stripped[1:]

    for pat in header_patterns:
        npat = re.sub(r'[\s:]', '', pat).lower()
        if not npat:
            continue
        orig_norm = ""
        match_end_orig = -1
        for i, ch in enumerate(text):
            if ch == ' ' or ch == '\t' or ch == '\n' or ch == '\r':
                continue
            orig_norm += ch.lower()
            if len(orig_norm) == len(npat):
                if orig_norm == npat:
                    match_end_orig = i + 1
                    break
                elif not orig_norm.startswith(npat):
                    break
        if match_end_orig < 0:
            continue

        remaining = text[match_end_orig:].lstrip(':').strip()

        if ':' in text:
            colon_idx = text.index(':')
            remaining = text[colon_idx + 1:].strip()

        clean_name = re.split(
            r'\s*(?:ADD|EDIT|MANAGE|\.\.\.)',
            remaining,
            flags=re.IGNORECASE,
        )[0].strip(' :->»▶')

        if clean_name:
            return clean_name

    return text
