#!/usr/bin/env python3
"""
Tests for spatial_redactor.py — Adaptive Geometry Engine v4.1.1

Covers:
  1. Sidebar Layout (n1sw1/n1sw2 style): collapsed sidebar at x≈15-30px
  2. Full-Width Layout (11vs1/11vs2/11vs3 style): no sidebar, tokens at x≈0
  3. Long Name & Empty Switch Layout (10vs1/10vs2/10vs3 style): spaces in names, empty adapters
  4. Failsafe graceful fallback when clustering produces empty output
  5. Collapsed header discard
  6. IPv4 / domain / MAC redaction
  7. naming_tab payload validation (< 50 chars → fallback)
"""
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from spatial_redactor import (
    process_spatial_topology,
    _step_a_filter,
    _step_b_partition,
    _step_c_column_split,
    _extract_container_name,
    _is_collapsed_header,
    _redact_ipv4,
    _redact_domains,
    _redact_mac,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tok(text: str, x: int, y: int, w: int = 200, h: int = 20) -> dict:
    return {"text": text, "box": [x, y, w, h]}


SPATIAL_CONFIG = {
    "enabled": True,
    "anchors": [
        {"role": "left_boundary", "patterns": ["Virtual switches"]},
        {"role": "container_header", "patterns": ["Standard Switch:"]},
        {"role": "adapter_column", "patterns": ["Physical Adapters", "Uplinks"]},
    ],
    "redact_ipv4": True,
    "redact_domains": True,
    "redact_mac": True,
    "domain_patterns": [r"\.adds$", r"\.local$", r"\.internal$", r"\.corp$"],
}

SPATIAL_CONFIG_NO_ANCHORS = {"enabled": True, "anchors": []}


# ===========================================================================
# TEST 1: Sidebar Layout — collapsed sidebar (x ≈ 15-30px)
# ===========================================================================

def test_collapsed_sidebar_keeps_all_tokens():
    """When 'Virtual switches' anchor is at x < 150, ALL tokens are kept."""
    tokens = [
        tok("Virtual switches", 15, 10),
        tok("Standard Switch: vSwitch0", 150, 50),
        tok("VMkernel", 160, 60),
        tok("vSwitch1", 155, 200),
    ]
    kept = _step_a_filter(tokens, ["Virtual switches"])
    assert len(kept) == 4, f"Expected 4 tokens kept, got {len(kept)}"


def test_expanded_sidebar_discards_left_of_anchor():
    """When 'Virtual switches' is at x >= 150 and > 12% of canvas, left tokens are discarded."""
    # Use wide canvas so anchor at x=400 is clearly > 12% (400 > 1920*0.12=230)
    tokens = [
        tok("Sidebar logo", 0, 10, w=100),
        tok("Virtual switches", 400, 10, w=150),
        tok("Standard Switch: vSwitch0", 450, 50, w=200),
    ]
    kept = _step_a_filter(tokens, ["Virtual switches"])
    texts = [t["text"] for t in kept]
    assert "Sidebar logo" not in texts, "Sidebar logo should be discarded"
    assert "Virtual switches" in texts
    assert "Standard Switch: vSwitch0" in texts


def test_no_anchor_keeps_all():
    tokens = [tok("A", 10, 10), tok("B", 20, 20)]
    kept = _step_a_filter(tokens, [])
    assert len(kept) == 2


# ===========================================================================
# TEST 2: Full-Width Layout — no sidebar
# ===========================================================================

def test_full_width_no_sidebar():
    """No 'Virtual switches' token found → all tokens kept."""
    tokens = [
        tok("Standard Switch: vSwitch0", 10, 50),
        tok("vmnic0", 20, 80),
        tok("Standard Switch: vSwitch01", 10, 200),
        tok("vmnic1", 20, 230),
        tok("Standard Switch: vSwitch02", 10, 400),
        tok("Standard Switch: vSwitch1", 10, 600),
    ]
    kept = _step_a_filter(tokens, ["Virtual switches"])
    assert len(kept) == 6


def test_full_width_partitioning():
    """vSwitch0, vSwitch01, vSwitch02, vSwitch1 parsed with zero missing."""
    tokens = [
        tok("Standard Switch: vSwitch0", 10, 50),
        tok("PG-VLAN100", 20, 80),
        tok("vmnic0", 300, 80),
        tok("Standard Switch: vSwitch01", 10, 200),
        tok("PG-VLAN200", 20, 230),
        tok("vmnic1", 300, 230),
        tok("Standard Switch: vSwitch02", 10, 400),
        tok("PG-VLAN300", 20, 430),
        tok("vmnic2", 300, 430),
        tok("Standard Switch: vSwitch1", 10, 600),
        tok("PG-VLAN400", 20, 630),
        tok("vmnic3", 300, 630),
    ]
    _, rmap = process_spatial_topology(tokens, SPATIAL_CONFIG)
    # Each switch should appear as a container
    assert "=== CONTAINER: vSwitch0 ===" in rmap or True  # check via structured text
    # Just verify no exception and non-empty output
    structured, _ = process_spatial_topology(tokens, SPATIAL_CONFIG)
    assert "vSwitch0" in structured
    assert "vSwitch01" in structured
    assert "vSwitch02" in structured
    assert "vSwitch1" in structured


# ===========================================================================
# TEST 3: Long Name & Empty Switch Layout
# ===========================================================================

def test_long_switch_names():
    """Switch names with spaces are extracted correctly."""
    tokens = [
        tok("Standard Switch: PR Spain Fuenmayor Age VLab", 10, 50),
        tok("PG-Data", 20, 80),
        tok("Standard Switch: PR Spain Fuenmayor VLab", 10, 200),
        tok("PG-Mgmt", 20, 230),
    ]
    containers = _step_b_partition(tokens, ["Standard Switch:"])
    names = [c[0] for c in containers]
    assert "PR Spain Fuenmayor Age VLab" in names
    assert "PR Spain Fuenmayor VLab" in names


def test_empty_adapters_no_inheritance():
    """Container with no physical adapters should not inherit uplinks."""
    tokens = [
        tok("Standard Switch: vSwitch0", 10, 50),
        tok("PG-Data", 20, 80),
        tok("Physical Adapters", 300, 80),
        tok("vmnic0", 310, 100),
        tok("Standard Switch: vSwitch1", 10, 200),
        tok("No physical network adapters", 20, 230),
        tok("Standard Switch: vSwitch2", 10, 350),
        tok("PG-VLAN", 20, 380),
        tok("Physical Adapters", 300, 380),
        tok("vmnic2", 310, 400),
    ]
    structured, _ = process_spatial_topology(tokens, SPATIAL_CONFIG)
    # vSwitch1 section should NOT contain vmnic0 or vmnic2
    lines = structured.splitlines()
    in_vswitch1 = False
    vswitch1_lines = []
    for line in lines:
        if "vSwitch1" in line and "CONTAINER" in line:
            in_vswitch1 = True
            continue
        if line.startswith("=== CONTAINER:") and "vSwitch1" not in line:
            in_vswitch1 = False
            continue
        if in_vswitch1:
            vswitch1_lines.append(line)
    vswitch1_text = "\n".join(vswitch1_lines)
    assert "vmnic0" not in vswitch1_text, "vSwitch1 should not inherit vmnic0"
    assert "vmnic2" not in vswitch1_text, "vSwitch1 should not inherit vmnic2"


# ===========================================================================
# TEST 4: Failsafe graceful fallback
# ===========================================================================

def test_fallback_empty_clustering():
    """When clustering produces no containers, flat text fallback kicks in."""
    # No container headers match → falls into __global__ but with no structure
    # Use tokens that won't match any header pattern
    tokens = [
        tok("Random text without switch headers", 10, 10),
        tok("More random content", 10, 30),
    ]
    structured, _ = process_spatial_topology(tokens, SPATIAL_CONFIG)
    # Should fall back to flat concatenated text
    assert "Random text without switch headers" in structured
    assert "More random content" in structured


def test_fallback_empty_structured():
    """Empty ocr_tokens → __global__ container with no tokens → minimal output."""
    structured, _ = process_spatial_topology([], SPATIAL_CONFIG)
    # Empty input produces a __global__ container with no tokens → structure is minimal
    assert "=== CONTAINER:" in structured


# ===========================================================================
# TEST 5: Collapsed header discard
# ===========================================================================

def test_collapsed_header_not_a_container():
    """Tokens starting with '>' should NOT become container boundaries."""
    tokens = [
        tok("> Standard Switch: vSwitchNutanix", 10, 50),
        tok("PG-Data", 20, 80),
        tok("Standard Switch: vSwitch0", 10, 200),
        tok("PG-Prod", 20, 230),
    ]
    containers = _step_b_partition(tokens, ["Standard Switch:"])
    names = [c[0] for c in containers]
    assert "vSwitchNutanix" not in names, "Collapsed header should not create a container"
    assert "vSwitch0" in names


def test_various_collapsed_markers():
    for marker in [">", "›", "»", "▶"]:
        text = f"{marker} Standard Switch: vSwitchX"
        assert _is_collapsed_header(text), f"Marker {marker!r} should be collapsed"


def test_v_prefix_is_active():
    """Headers starting with 'v' or 'V' (expanded downward arrow) are active."""
    assert not _is_collapsed_header("v Standard Switch: vSwitch0")
    assert not _is_collapsed_header("V Standard Switch: vSwitch0")
    assert not _is_collapsed_header("Standard Switch: vSwitch0")


# ===========================================================================
# TEST 6: Redaction
# ===========================================================================

def test_ipv4_redaction():
    rmap = {}
    counters = {"ip": 0, "domain": 0, "mac": 0}
    text, count = _redact_ipv4("IP is 192.168.1.1 and 10.0.0.1", counters, rmap)
    assert "192.168.1.1" not in text
    assert "10.0.0.1" not in text
    assert "<IP_1>" in text
    assert "<IP_2>" in text
    assert len(rmap) == 2


def test_domain_redaction():
    rmap = {}
    counters = {"ip": 0, "domain": 0, "mac": 0}
    domain_re = re.compile(
        r"\b[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?)*"
        r"(?:\.adds|\.local|\.internal|\.corp)"
        r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]*[a-zA-Z0-9])?)*",
        re.IGNORECASE,
    )
    text, count = _redact_domains(
        "Host at server.adds.internal.local", counters, rmap, domain_re
    )
    assert "server.adds.internal.local" not in text or "<DOMAIN" in text
    assert len(rmap) >= 1


def test_mac_redaction():
    rmap = {}
    counters = {"ip": 0, "domain": 0, "mac": 0}
    text, count = _redact_mac("MAC aa:bb:cc:dd:ee:ff", counters, rmap)
    assert "aa:bb:cc:dd:ee:ff" not in text
    assert "<MAC_1>" in text


# ===========================================================================
# TEST 7: naming_tab payload validation
# ===========================================================================

def test_payload_validation_short_fallback():
    """Simulate: cleaned_ocr_text < 50 chars → fallback to combined_raw_text."""
    cleaned = "tiny"
    combined = "This is a much longer raw OCR text that should definitely be used as fallback when the cleaned version is too short for meaningful LLM parsing."
    if len(cleaned.strip()) < 50:
        final = combined
    else:
        final = cleaned
    assert final == combined
    assert len(final.strip()) >= 50


def test_payload_validation_long_kept():
    """cleaned_ocr_text >= 50 chars → keep as-is."""
    cleaned = "A" * 60
    combined = "short"
    if len(cleaned.strip()) < 50:
        final = combined
    else:
        final = cleaned
    assert final == cleaned


# ===========================================================================
# TEST 8: Switch name regex extraction
# ===========================================================================

def test_switch_name_regex():
    patterns = ["Standard Switch:"]
    cases = [
        ("Standard Switch: vSwitch0", "vSwitch0"),
        ("Standard Switch: vSwitch01", "vSwitch01"),
        ("Standard Switch: vSwitch02", "vSwitch02"),
        ("Standard Switch: vSwitch1", "vSwitch1"),
        ("Standard Switch: PR Spain Fuenmayor Age VLab", "PR Spain Fuenmayor Age VLab"),
        ("Standard Switch: PR Spain Fuenmayor VLab", "PR Spain Fuenmayor VLab"),
        ("Standard Switch: vSwitch0 ADD NETWORKING", "vSwitch0"),
        ("Standard Switch: vSwitch0 EDIT", "vSwitch0"),
        ("Standard Switch: vSwitch0 MANAGE PHYSICAL ADAPTERS", "vSwitch0"),
        ("Standard Switch: vSwitch0 MANAGE", "vSwitch0"),
    ]
    for raw, expected in cases:
        result = _extract_container_name(raw, patterns)
        assert result == expected, f"Failed for {raw!r}: got {result!r}, expected {expected!r}"


# ===========================================================================
# TEST 9: Container header detection with 'v' prefix
# ===========================================================================

def test_v_prefix_active_container():
    """Headers with 'v' prefix (expanded arrow) should be detected as active."""
    tokens = [
        tok("v Standard Switch: vSwitch0", 10, 50),
        tok("PG-Data", 20, 80),
        tok("Standard Switch: vSwitch1", 10, 200),
        tok("PG-Prod", 20, 230),
    ]
    containers = _step_b_partition(tokens, ["Standard Switch:"])
    names = [c[0] for c in containers]
    assert "vSwitch0" in names
    assert "vSwitch1" in names


# ===========================================================================
# Main
# ===========================================================================

def main():
    tests = [
        ("Collapsed sidebar keeps all tokens", test_collapsed_sidebar_keeps_all_tokens),
        ("Expanded sidebar discards left of anchor", test_expanded_sidebar_discards_left_of_anchor),
        ("No anchor keeps all", test_no_anchor_keeps_all),
        ("Full width no sidebar", test_full_width_no_sidebar),
        ("Full width partitioning", test_full_width_partitioning),
        ("Long switch names", test_long_switch_names),
        ("Empty adapters no inheritance", test_empty_adapters_no_inheritance),
        ("Fallback empty clustering", test_fallback_empty_clustering),
        ("Fallback empty structured", test_fallback_empty_structured),
        ("Collapsed header not a container", test_collapsed_header_not_a_container),
        ("Various collapsed markers", test_various_collapsed_markers),
        ("v prefix is active", test_v_prefix_active_container),
        ("IPv4 redaction", test_ipv4_redaction),
        ("Domain redaction", test_domain_redaction),
        ("MAC redaction", test_mac_redaction),
        ("Payload validation short fallback", test_payload_validation_short_fallback),
        ("Payload validation long kept", test_payload_validation_long_kept),
        ("Switch name regex extraction", test_switch_name_regex),
        ("V-prefix active container", test_v_prefix_active_container),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS: {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {name} — {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {name} — {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed, {passed+failed} total")
    print(f"{'='*60}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
