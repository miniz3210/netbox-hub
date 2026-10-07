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
    left_boundary_anchor: str          # e.g. "Virtual switches"
    container_header: str              # e.g. "Standard Switch:"
    adapter_column_anchor: str         # e.g. "Physical Adapters"
    redact_ipv4: bool
    redact_domains: bool
    redact_mac: bool
    domain_patterns: list[str]         # e.g. ["\\.adds$", "\\.local$", ...]
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
def _token_center(box: List[float]) -> Tuple[float, float]:
    """Return (center_x, center_y) from a [x, y, w, h] box."""
    x, y, w, h = box
    return (x + w / 2.0, y + h / 2.0)


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

    left_anchor = str(config.get("left_boundary_anchor", "")).strip()
    container_header = str(config.get("container_header", "")).strip()
    adapter_anchor = str(config.get("adapter_column_anchor", "")).strip()

    redact_ipv4 = bool(config.get("redact_ipv4", True))
    redact_domains = bool(config.get("redact_domains", True))
    redact_mac = bool(config.get("redact_mac", True))
    domain_patterns: List[str] = config.get("domain_patterns", [])
    if not isinstance(domain_patterns, list):
        domain_patterns = []

    redaction_map: Dict[str, str] = {}

    # Step A: Dynamic left boundary filter
    filtered_tokens = _step_a_filter(ocr_tokens, left_anchor)

    # Step B: Y-interval partitioning into switch containers
    containers = _step_b_partition(filtered_tokens, container_header)

    # Step C + D + E: For each container, partition by column and redact
    output_lines: List[str] = []
    total_redacted = 0

    for container_name, tokens_in_container in containers:
        # Step C: Column split
        port_group_tokens, uplink_tokens = _step_c_column_split(
            tokens_in_container, adapter_anchor
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

    logger.info(
        "Spatial grouping applied. Redacted %d sensitive tokens locally.",
        total_redacted,
    )
    return structured_text, redaction_map


# ---------------------------------------------------------------------------
# Step A: Left-boundary filter
# ---------------------------------------------------------------------------

def _step_a_filter(
    tokens: List[Dict],
    anchor_text: str,
) -> List[Dict]:
    """Discard tokens that lie to the left of the sidebar boundary anchor."""
    if not anchor_text:
        return list(tokens)

    # Find all matching tokens; pick the one with the largest x to target the
    # main-content anchor rather than a potential sidebar navigation item.
    matches = [
        t for t in tokens
        if anchor_text.lower() in str(t.get("text", "")).lower()
        and t.get("box") and len(t["box"]) >= 3
    ]
    if not matches:
        # Anchor not found (e.g. scrolled past) — retain all tokens.
        return list(tokens)

    anchor_match = max(matches, key=lambda t: float(t["box"][0]))
    box = anchor_match["box"]
    left_cutoff = box[0] - (box[2] * 0.15)

    kept = [
        t for t in tokens
        if (t.get("box") and len(t["box"]) >= 1
            and float(t["box"][0]) >= left_cutoff)
    ]
    return kept


# ---------------------------------------------------------------------------
# Step B: Y-interval partitioning
# ---------------------------------------------------------------------------

def _step_b_partition(
    tokens: List[Dict],
    header_pattern: str,
) -> List[Tuple[str, List[Dict]]]:
    """
    Group tokens into containers based on vertical intervals delimited by
    container-header tokens (e.g. "Standard Switch: vSwitch0").
    Returns list of (container_name, [tokens]) sorted top-to-bottom.
    """
    if not header_pattern:
        # No header anchor: treat all tokens as one global container.
        return [("__global__", list(tokens))]

    # Find all container header tokens.
    header_tokens: List[Dict] = []
    for t in tokens:
        text = str(t.get("text", "")).strip()
        if header_pattern.lower() in text.lower():
            header_tokens.append(t)

    if not header_tokens:
        # No headers found — everything falls into one global container.
        return [("__global__", list(tokens))]

    header_tokens.sort(key=lambda t: float(t.get("box", [0, 0, 0, 0])[1]))

    containers: List[Tuple[str, List[Dict]]] = []
    for idx, ht in enumerate(header_tokens):
        name = _extract_container_name(ht.get("text", ""), header_pattern)
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


def _extract_container_name(raw_text: str, header_pattern: str) -> str:
    """Pull the container label out of a header token's text."""
    text = str(raw_text).strip()
    pat = header_pattern.rstrip(":").strip()
    if pat and pat.lower() in text.lower():
        parts = text.split(pat, 1)
        label = parts[-1].strip().lstrip(":").strip()
        return label or text
    return text


# ---------------------------------------------------------------------------
# Step C: Column partitioning (PortGroups vs Uplinks)
# ---------------------------------------------------------------------------

def _step_c_column_split(
    tokens: List[Dict],
    adapter_anchor: str,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split *tokens* into (port_groups, uplinks) using a vertical split at the
    x-position of the adapter-column anchor token.
    """
    if not adapter_anchor:
        return list(tokens), []

    split_x: Optional[float] = None
    for t in tokens:
        text = str(t.get("text", "")).strip()
        if adapter_anchor.lower() in text.lower():
            box = t.get("box")
            if box and len(box) >= 1:
                split_x = float(box[0]) - 10.0
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
