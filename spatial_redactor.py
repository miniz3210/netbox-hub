"""
Spatial Redactor: Resolution-Agnostic Relative Geometry & Zero-Leakage Local Redaction.

Processes OCR token lists (with bounding boxes) into hierarchical structured text
grouped by virtual switch containers, using dynamic relative spatial anchors
that are immune to DPI / resolution scaling. Simultaneously performs deterministic
in-memory redaction of sensitive tokens (IPv4, internal domains, MACs) before
the payload reaches any external LLM.

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

_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")

_MAC_COLON_RE = re.compile(r"\b(?:(?:[0-9A-Fa-f]{2}:){5})[0-9A-Fa-f]{2}\b")
_MAC_HYPHEN_RE = re.compile(r"\b(?:(?:[0-9A-Fa-f]{2}-){5})[0-9A-Fa-f]{2}\b")
_MAC_BARE_RE = re.compile(r"\b[0-9A-Fa-f]{12}\b")
_MAC_RE = re.compile(rf"(?:{_MAC_COLON_RE.pattern}|{_MAC_HYPHEN_RE.pattern}|{_MAC_BARE_RE.pattern})")

_COLLAPSED_PREFIX_RE = re.compile(r"^[\s>›»▶]+")

_SWITCH_NAME_RE = re.compile(
    r'(?:[vV\s]*)?'
    r'Standard\s*Switch:\s*'
    r'([^\n\r]+?)'
    r'(?:\s+ADD\s+NETWORKING|\s+EDIT|\s+MANAGE\s+(?:PHYSICAL\s+ADAPTERS|ADAPTERS)?|\s+MANAGE|\s*$)',
    re.IGNORECASE,
)


def _build_domain_re(patterns: List[str]) -> Optional[re.Pattern]:
    """Build a combined regex from user-provided domain suffix patterns.

    Patterns like ``\\.adds$`` are treated as regex fragments; the trailing
    ``$`` is stripped so the fragment can match anywhere within an FQDN token.
    The resulting regex matches sequences of dot-separated labels that contain
    at least one of the provided fragments.
    """
    if not patterns:
        return None
    fragments: List[str] = []
    for p in patterns:
        if not p:
            continue
        # Strip trailing $ anchor so the fragment matches mid-FQDN.
        pat = p.rstrip("$").rstrip("\\") if p.endswith("$") else p
        try:
            compiled = re.compile(pat, re.IGNORECASE)
            fragments.append(compiled.pattern)
        except re.error as exc:
            logger.warning("Invalid domain pattern: %s — %s", pat, exc)
    if not fragments:
        return None
    # Match a complete FQDN token (dot-separated labels) that contains any fragment.
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
    """Extract pattern list for a given anchor role from config.

    Supports both legacy scalar fields and the new anchors list format.
    Legacy scalar keys:
        left_boundary_anchor, container_header, adapter_column_anchor
    New anchors list:
        anchors: [{role, patterns: [...]}]
    """
    # New format: anchors list
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

    # Legacy scalar format → wrap into single-element list
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
    """Return True if the token text or its leading symbols indicate a collapsed section.

    Collapsed ESXi tree nodes are prefixed with '>', '›', '»', or '▶'
    (e.g. '> Standard Switch: vSwitchNutanix').
    These should NOT define active container boundaries.
    """
    stripped = text.strip()
    if not stripped:
        return False
    prefix_match = _COLLAPSED_PREFIX_RE.match(stripped)
    if prefix_match:
        prefix = prefix_match.group(0)
        if any(c in prefix for c in ('>', '›', '»', '▶')):
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def process_spatial_topology(
    ocr_tokens: List[Dict],
    config: Dict,
) -> Tuple[str, Dict[str, str]]:
    """
    Process OCR tokens through relative spatial grouping + deterministic redaction.

    Args:
        ocr_tokens: list of {"text": str, "box": [x, y, w, h]} dicts.
        config: spatial_anchors_and_redaction dict from the active platform preset.

    Returns:
        (structured_text, redaction_map)
        structured_text  — hierarchical markdown grouped by switch container.
        redaction_map    — { "<IP_N>": "real_ip", "<DOMAIN_N>": "real_domain", ... }
    """
    enabled = bool(config.get("enabled", False))
    if not enabled:
        # Fallback: concatenate raw tokens line-by-line, no redaction.
        lines = [t.get("text", "") for t in ocr_tokens if t.get("text")]
        return "\n".join(lines), {}

    # Extract multi-pattern lists for each anchor role
    left_boundary_patterns = _resolve_anchor_patterns(config, "left_boundary")
    container_patterns = _resolve_anchor_patterns(config, "container_header")
    adapter_patterns = _resolve_anchor_patterns(config, "adapter_column")

    redact_ipv4 = bool(config.get("redact_ipv4", True))
    redact_domains = bool(config.get("redact_domains", True))
    redact_mac = bool(config.get("redact_mac", True))
    domain_patterns: List[str] = config.get("domain_patterns", [])
    if not isinstance(domain_patterns, list):
        domain_patterns = []

    redaction_map: Dict[str, str] = {}

    # Step A: Dynamic left boundary filter (multi-pattern)
    filtered_tokens = _step_a_filter(ocr_tokens, left_boundary_patterns)

    # Step B: Y-interval partitioning into switch containers (multi-pattern, exclude collapsed)
    containers = _step_b_partition(filtered_tokens, container_patterns)

    # Step C + D + E: For each container, partition by column and redact
    output_lines: List[str] = []
    total_redacted = 0

    for container_name, tokens_in_container in containers:
        # Step C: Column split (multi-pattern)
        port_group_tokens, uplink_tokens = _step_c_column_split(
            tokens_in_container, adapter_patterns
        )

        output_lines.append(f"=== CONTAINER: {container_name} ===")

        # Step D/E: Redact and emit Port Groups & VMkernel section
        pg_text, n_pg = _redact_and_emit(
            port_group_tokens, redaction_map,
            redact_ipv4, redact_domains, redact_mac, domain_patterns,
        )
        total_redacted += n_pg
        if pg_text.strip():
            output_lines.append("[Port Groups & VMkernel]")
            output_lines.extend(pg_text.strip().splitlines())

        # Step D/E: Redact and emit Physical Uplinks section
        ul_text, n_ul = _redact_and_emit(
            uplink_tokens, redaction_map,
            redact_ipv4, redact_domains, redact_mac, domain_patterns,
        )
        total_redacted += n_ul
        if ul_text.strip():
            output_lines.append("[Physical Uplinks]")
            output_lines.extend(ul_text.strip().splitlines())

        output_lines.append("")  # blank separator

    structured_text = "\n".join(output_lines).strip()

    # Failsafe: if structured clustering produced empty output, fall back to
    # flat concatenated text so the LLM always receives a parseable payload.
    if not structured_text or len(containers) == 0:
        logger.warning(
            "[SpatialEngine] Fallback triggered: empty spatial clustering. "
            "Falling back to cleaned flat text."
        )
        flat_lines = []
        for t in ocr_tokens:
            txt = str(t.get("text", "")).strip()
            if txt:
                flat_lines.append(txt)
        if flat_lines:
            structured_text = "\n".join(flat_lines)

    logger.info(
        "Spatial grouping applied. Redacted %d sensitive tokens locally.",
        total_redacted,
    )
    return structured_text, redaction_map


# ---------------------------------------------------------------------------
# Step A: Left-boundary filter (multi-pattern)
# ---------------------------------------------------------------------------

def _step_a_filter(
    tokens: List[Dict],
    anchor_patterns: List[str],
) -> List[Dict]:
    """Discard tokens that lie to the left of the sidebar boundary anchor.

    Adaptive sidebar cutoff: if the anchor token is at x < 150px (or < 12% of
    canvas width), the sidebar is collapsed/absent and we keep ALL tokens.
    Otherwise, discard tokens strictly to the left of the anchor title.
    """
    if not anchor_patterns:
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
        # Anchor not found (e.g. scrolled past) — retain all tokens.
        return list(tokens)

    anchor_match = max(matches, key=lambda t: float(t["box"][0]))
    anchor_x = float(anchor_match["box"][0])
    canvas_width = max(
        (float(t["box"][0]) + float(t["box"][2]))
        for t in tokens
        if t.get("box") and len(t["box"]) >= 3
    ) if tokens else 0
    canvas_width = max(canvas_width, 1920.0)  # safety floor

    if anchor_x < 150 or anchor_x < canvas_width * 0.12:
        # Sidebar is collapsed or absent — keep every token.
        return list(tokens)

    # Sidebar exists. Discard tokens strictly to the left of the anchor title.
    left_cutoff_x = max(0.0, anchor_x - 15.0)
    kept = [
        t for t in tokens
        if t.get("box") and len(t["box"]) >= 1
        and float(t["box"][0]) >= left_cutoff_x
    ]
    return kept


# ---------------------------------------------------------------------------
# Step B: Y-interval partitioning (multi-pattern, exclude collapsed headers)
# ---------------------------------------------------------------------------

def _step_b_partition(
    tokens: List[Dict],
    header_patterns: List[str],
) -> List[Tuple[str, List[Dict]]]:
    """
    Group tokens into containers based on vertical intervals delimited by
    container-header tokens (e.g. "Standard Switch: vSwitch0").

    Collapsed headers (prefixed with '>', '›', '»', or '▶') are EXCLUDED — they
    only mark the end of a previous switch, not the start of a new active
    container.  Headers starting with 'v' or 'V' (expanded downward arrow) or
    without symbols are treated as active containers.

    Returns list of (container_name, [tokens]) sorted top-to-bottom.
    """
    if not header_patterns:
        return [("__global__", list(tokens))]

    # Find all container header tokens, excluding collapsed entries.
    # Only accept tokens that match a header pattern AND are not collapsed.
    header_tokens: List[Dict] = []
    for t in tokens:
        text = str(t.get("text", "")).strip()
        if _is_collapsed_header(text):
            continue
        if any(pat.lower() in text.lower() for pat in header_patterns):
            header_tokens.append(t)

    if not header_tokens:
        return [("__global__", list(tokens))]

    # Sort by global cumulative Y-coordinate ascending.
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
    """Pull the container label out of a header token's text.

    Uses a dedicated regex that strips trailing action verbs
    (ADD NETWORKING, EDIT, MANAGE PHYSICAL ADAPTERS) and supports
    names with spaces, hyphens, and 'v'/'V' prefixes.
    """
    text = str(raw_text).strip()

    # Try the dedicated switch-name regex first.
    m = _SWITCH_NAME_RE.match(text)
    if m:
        name = m.group(1).strip()
        if name:
            return name

    # Fallback: pattern-position split.
    for pat in header_patterns:
        pat_clean = pat.rstrip(":").strip()
        if pat_clean and pat_clean.lower() in text.lower():
            parts = text.split(pat_clean, 1)
            label = parts[-1].strip().lstrip(":").strip()
            if label:
                return label
    return text


# ---------------------------------------------------------------------------
# Step C: Column partitioning (PortGroups vs Uplinks) — multi-pattern
# ---------------------------------------------------------------------------

def _step_c_column_split(
    tokens: List[Dict],
    adapter_patterns: List[str],
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split *tokens* into (port_groups, uplinks) using a vertical split at the
    x-position of the adapter-column anchor token.

    If the container text contains "No physical network adapters" or
    "No associated port groups", returns all tokens as port_groups (no uplinks).
    """
    if not adapter_patterns:
        return list(tokens), []

    # Check for empty-state markers first.
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
    """
    Redact sensitive data in-token and emit clean markdown lines.
    Returns (formatted_text, count_of_redacted_tokens).
    """
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
