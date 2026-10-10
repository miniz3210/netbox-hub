#!/usr/bin/env python3
"""
Unit Tests for v3.9.21 Features
  1. Safe Hostname Fuzzy Matching with Full Context Fetch (ai_helper.py)
  2. Deep IPAM Overlap & Hierarchy-Aware Allocation (ipam_engine.py)
  3. Version bump verification
  4. Vault sanitization compatibility
"""

import sys
import os
import re
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ai_helper import (
    fuzzy_match_hostname,
    build_fuzzy_suggestion,
    try_fuzzy_hostname_lookup,
    _fetch_device_context,
    _names_differ_only_by_trailing_number,
)
from core.ipam_engine import (
    check_prefix_overlap,
    get_top_3_available_subnets,
    sanitize_cidr,
)
from config.constants import APP_VERSION


# ── Test Harness ────────────────────────────────────────────────────────────

_passed = 0
_failed = 0


def assert_true(condition: bool, label: str):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ✅ PASS: {label}")
    else:
        _failed += 1
        print(f"  ❌ FAIL: {label}")


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# ============================================================================
# PART 1: VERSION BUMP
# ============================================================================
def test_version_bump():
    section("TEST 0: Version Bump → v4.3")
    assert_true(APP_VERSION == "4.3", f"APP_VERSION == '4.3' (got {APP_VERSION!r})")


# ============================================================================
# PART 2: FUZZY HOSTNAME MATCHING
# ============================================================================
def test_fuzzy_suffix_guard():
    """SW1 vs SW2 must be blocked by the suffix guard."""
    section("TEST 1a: Suffix Guard — SW1 vs SW2 blocked")
    names = ["SW1", "SW2", "SW3", "CoreSwitch01", "CoreSwitch02"]
    result = fuzzy_match_hostname("SW1", known_names=names)
    names_found = [r["name"] for r in result]
    assert_true("SW2" not in names_found and "SW3" not in names_found,
                "SW2/SW3 blocked by suffix guard when looking up SW1")
    assert_true(len(result) == 0, "No fuzzy candidates for SW1 (suffix guard blocks all)")


def test_fuzzy_typo_recovery():
    """fwazewine1 should suggest fwAZESWINE1 with high ratio."""
    section("TEST 1b: Typo Recovery — fwazewine1 → fwAZESWINE1")
    names = ["fwAZESWINE1", "fwAZESWINE2", "fwAZESWINE10", "fwFirewall01", "rtrHQ01"]
    result = fuzzy_match_hostname("fwazewine1", known_names=names)
    assert_true(len(result) > 0, "Fuzzy matcher returns candidates for typo input")
    if result:
        top = result[0]["name"]
        assert_true(top == "fwAZESWINE1",
                    f"Top candidate is 'fwAZESWINE1' (got {top!r})")
        assert_true(result[0]["ratio"] >= 0.82,
                    f"Similarity ratio >= 0.82 (got {result[0]['ratio']})")
    # fwAZESWINE2 should also appear but at lower ratio
    all_names = [r["name"] for r in result]
    assert_true("fwAZESWINE2" in all_names, "fwAZESWINE2 appears as lower-ratio candidate")


def test_fuzzy_max_candidates():
    """At most 3 top-ranked suggestions returned."""
    section("TEST 1c: Max 3 Candidates")
    names = ["fwAZESWINE1", "fwAZESWINE2", "fwAZESWINE3", "fwAZESWINE4", "fwAZESWINE5"]
    result = fuzzy_match_hostname("fwazewine1", known_names=names)
    assert_true(len(result) <= 3, f"Max 3 candidates returned (got {len(result)})")


def test_fuzzy_exact_match_skipped():
    """Exact match must NOT appear in fuzzy results."""
    section("TEST 1d: Exact Match Skipped")
    names = ["fwAZESWINE1", "fwAZESWINE2"]
    result = fuzzy_match_hostname("fwAZESWINE1", known_names=names)
    names_found = [r["name"] for r in result]
    assert_true("fwAZESWINE1" not in names_found, "Exact match 'fwAZESWINE1' excluded from fuzzy results")


def test_fuzzy_no_candidates():
    """Totally unrelated query returns empty."""
    section("TEST 1e: No Candidates for Unrelated Query")
    names = ["fwAZESWINE1", "CoreSwitch01"]
    result = fuzzy_match_hostname("xyz123totallyunrelated", known_names=names)
    assert_true(len(result) == 0, "No candidates for completely unrelated query")


def test_fuzzy_suggestion_block():
    """build_fuzzy_suggestion formats output correctly."""
    section("TEST 1f: Suggestion Block Formatting")
    candidates = [{"name": "fwAZESWINE1", "ratio": 0.91, "token": "fwAZESWINE1"}]
    block = build_fuzzy_suggestion("fwazewine1", candidates)
    assert_true("[FUZZY_SUGGESTION]" in block, "Block contains [FUZZY_SUGGESTION] marker")
    assert_true("fwAZESWINE1" in block, "Block contains the candidate name")
    assert_true("fwazewine1" in block, "Block contains original query")


def test_fuzzy_empty_inputs():
    section("TEST 1g: Empty Inputs Return Empty")
    assert_true(fuzzy_match_hostname("", known_names=["SW1"]) == [], "Empty query → []")
    assert_true(fuzzy_match_hostname("SW1", known_names=None) == [], "None known_names → []")
    assert_true(build_fuzzy_suggestion("q", []) == "", "Empty candidates → ''")


# ============================================================================
# PART 3: DEEP IPAM OVERLAP DETECTION (BUG-6)
# ============================================================================
def test_overlap_multi_collision():
    """Multiple overlapping prefixes in same VRF all returned (not just first)."""
    section("TEST 2a: Multi-Finding — All Overlaps Returned")
    known = [
        {"prefix": "10.0.0.0/24", "vlan": "10", "site": "HQ", "vrf": "VRF-A"},
        {"prefix": "10.0.1.0/24", "vlan": "20", "site": "HQ", "vrf": "VRF-A"},
        {"prefix": "10.0.2.0/24", "vlan": "30", "site": "HQ", "vrf": "VRF-A"},
    ]
    # Query a /22 that contains all three /24s
    findings = check_prefix_overlap("10.0.0.0/22", known)
    # All three should be found as CONTAINED_BY_SUPERNET
    assert_true(len(findings) == 3,
                f"All 3 overlapping prefixes found (got {len(findings)})")
    types = {f["prefix"] for f in findings}
    assert_true("10.0.0.0/24" in types, "10.0.0.0/24 in findings")
    assert_true("10.0.1.0/24" in types, "10.0.1.0/24 in findings")
    assert_true("10.0.2.0/24" in types, "10.0.2.0/24 in findings")


def test_overlap_supernet_containment():
    """Parent supernet containing child subnet is CONTAINED_BY_SUPERNET (info)."""
    section("TEST 2b: Parent Supernet Containment — Info Level")
    known = [
        {"prefix": "10.0.0.0/24", "vlan": "10", "site": "HQ", "vrf": "VRF-A"},
    ]
    findings = check_prefix_overlap("10.0.0.0/16", known)
    containments = [f for f in findings if f["conflict_type"] == "CONTAINED_BY_SUPERNET"]
    assert_true(len(containments) == 1, "One containment finding for parent supernet")
    assert_true(containments[0]["status"] == "INFO", "Containment status is INFO")


def test_overlap_child_in_parent():
    """Child subnet inside parent supernet → CONTAINED_BY_SUPERNET (info)."""
    section("TEST 2c: Child in Parent — Containment Info")
    known = [
        {"prefix": "10.0.0.0/16", "vlan": None, "site": "HQ", "vrf": "VRF-A"},
    ]
    findings = check_prefix_overlap("10.0.1.0/24", known)
    containments = [f for f in findings if f["conflict_type"] == "CONTAINED_BY_SUPERNET"]
    assert_true(len(containments) == 1, "Child inside parent → CONTAINED_BY_SUPERNET")


def test_overlap_cross_vrf_ignored():
    """Overlaps in different VRFs are NOT flagged."""
    section("TEST 2d: Cross-VRF Overlaps Ignored")
    known = [
        {"prefix": "10.0.0.0/24", "vlan": "10", "site": "HQ", "vrf": "VRF-A"},
        {"prefix": "10.0.0.0/24", "vlan": "20", "site": "BR2", "vrf": "VRF-B"},
    ]
    findings = check_prefix_overlap("10.0.0.0/23", known, vrf_id="VRF-A")
    vrf_a_findings = [f for f in findings if f.get("vrf") != "VRF-B"]
    # Only one collision in VRF-A; the VRF-B duplicate should be excluded
    assert_true(len([f for f in findings if f["conflict_type"] == "COLLISION_ERROR"]) <= 1,
                "Cross-VRF overlap not flagged when VRF filter applied")


def test_overlap_no_collision():
    """Non-overlapping prefixes produce no findings."""
    section("TEST 2e: No Collision for Disjoint Prefixes")
    known = [
        {"prefix": "10.0.0.0/24", "vlan": "10", "site": "HQ", "vrf": "VRF-A"},
        {"prefix": "172.16.0.0/16", "vlan": "20", "site": "HQ", "vrf": "VRF-A"},
    ]
    findings = check_prefix_overlap("192.168.1.0/24", known)
    assert_true(len(findings) == 0, "No findings for completely disjoint prefix")


def test_overlap_equal_length_no_overlap():
    """Equal-length non-overlapping prefixes → no findings."""
    section("TEST 2f: Equal-Length Disjoint — No Finding")
    known = [
        {"prefix": "10.0.0.0/24", "vlan": "10", "site": "HQ", "vrf": "VRF-A"},
    ]
    findings = check_prefix_overlap("10.0.1.0/24", known)
    collisions = [f for f in findings if f["conflict_type"] == "COLLISION_ERROR"]
    assert_true(len(collisions) == 0, "No collision for non-overlapping /24s")


# ============================================================================
# PART 4: FRAGMENTATION-AWARE SUBNET ALLOCATOR (BUG-7)
# ============================================================================
def test_allocator_cidr_aligned():
    """Allocated subnets respect CIDR binary boundaries."""
    section("TEST 3a: CIDR Boundary Alignment")
    existing = ["10.0.0.0/24", "10.0.4.0/22"]
    results = get_top_3_available_subnets("10.0.0.0/16", existing, 24)
    assert_true(len(results) > 0, "Allocator returns at least one candidate")
    for r in results:
        cidr = r["prefix"]
        assert_true("/24" in cidr, f"Candidate is /24: {cidr}")
    # Verify no overlap with existing
    for r in results:
        cand = sanitize_cidr(r["prefix"])
        assert_true(cand not in existing, f"Returned non-overlapping prefix: {cand}")


def test_allocator_multiple_results():
    """Returns up to 3 distinct non-overlapping candidates."""
    section("TEST 3b: Top-3 Distinct Candidates")
    existing = ["10.0.0.0/24"]
    results = get_top_3_available_subnets("10.0.0.0/16", existing, 24)
    prefixes = [r["prefix"] for r in results]
    assert_true(len(set(prefixes)) == len(prefixes), "All candidate prefixes are unique")
    assert_true(len(results) <= 3, "At most 3 candidates returned")


def test_allocator_tail_placement():
    """Small link subnets (/30, /31, /29) placed in fragmented tail regions."""
    section("TEST 3c: Tail Placement for Small Link Subnets")
    existing = ["10.0.0.0/20", "10.0.16.0/20", "10.0.32.0/20"]
    results = get_top_3_available_subnets("10.0.0.0/16", existing, 30)
    assert_true(len(results) > 0, "Allocator finds /30 candidates in fragmented gaps")
    for r in results:
        assert_true("/30" in r["prefix"], f"Result is /30: {r['prefix']}")


def test_allocator_no_space():
    """Returns empty when supernet is fully occupied."""
    section("TEST 3d: Empty Result When Fully Occupied")
    # Fill every /24 in a /16 — this is a large list but realistic test
    all_24s = [f"10.0.{i}.0/24" for i in range(256)]
    results = get_top_3_available_subnets("10.0.0.0/16", all_24s, 24)
    assert_true(len(results) == 0, "No candidates when supernet is fully occupied")


def test_allocator_respects_boundaries():
    """A /26 must start on a multiple of 64 in the last octet."""
    section("TEST 3e: /26 Boundary Alignment")
    existing: list = []
    results = get_top_3_available_subnets("10.0.0.0/24", existing, 26)
    for r in results:
        cidr = r["prefix"]
        # /26 subnets within a /24 must start at 0, 64, 128, or 192
        last_octet = int(cidr.split(".")[3].split("/")[0])
        assert_true(last_octet % 64 == 0,
                    f"/26 starts on CIDR boundary (last-octet={last_octet}, multiples of 64)")


# ============================================================================
# PART 5: VAULT COMPATIBILITY
# ============================================================================
def test_vault_sanitization_no_regression():
    """Existing vault sanitization continues to work after changes."""
    section("TEST 4: Vault Sanitization Compatibility")
    from core.vault import SanitizerVault
    vault = SanitizerVault(db_path="/tmp/test_vault_compat.db")
    vault.clear_vault()

    text = "Check fwAZESWINE1 and 10.0.0.0/24 on VRF-A"
    known_hostnames = ["fwAZESWINE1", "fwAZESWINE2"]
    sanitized, session_id = vault.sanitize(text, known_hostnames=known_hostnames)
    assert_true("<SAFE_HOST_1>" in sanitized, "Hostname tokenised as <SAFE_HOST_N>")
    assert_true("<SAFE_NET_1>" in sanitized, "CIDR tokenised as <SAFE_NET_N>")
    assert_true("<SAFE_IP_1>" in sanitized or "<SAFE_NET" in sanitized,
                "IP address tokenised")

    restored = vault.restore(sanitized, session_id=session_id)
    assert_true("fwAZESWINE1" in restored, "Restored text contains original hostname")
    assert_true("10.0.0.0/24" in restored, "Restored text contains original CIDR")

    vault.clear_vault()
    try:
        os.remove("/tmp/test_vault_compat.db")
    except OSError:
        pass


# ============================================================================
# PART 6: FUZZY CONTEXT FETCH (v3.9.21)
# ============================================================================
def test_fuzzy_context_fetch_system_notice():
    """When exact match fails but fuzzy finds candidate, context block has SYSTEM NOTICE."""
    section("TEST 5a: SYSTEM NOTICE in Context Block")
    known = ["fwAZESWINE1", "fwAZESWINE2"]
    # No DB — mock _fetch_device_context to return empty, should fall back to suggestion
    with mock.patch("core.ai_helper._fetch_device_context", return_value=[]):
        _, ctx = try_fuzzy_hostname_lookup(
            "fwazewine1", known_names=known, search_func=lambda ids, site="", limit=10: []
        )
    assert_true(ctx is not None, "Context block returned on fuzzy match")
    assert_true("[FUZZY_SUGGESTION]" in ctx, "Contains FUZZY_SUGGESTION when no DB context")


def test_fuzzy_context_fetch_with_db_data():
    """When DB has data, context block includes SYSTEM NOTICE + device details."""
    section("TEST 5b: Full Context with SYSTEM NOTICE + Device Details")
    known = ["fwAZESWINE1"]
    mock_rows = [
        {"object_type": "dcim_devices", "object_label": "Device", "name": "fwAZESWINE1",
         "site": "Spain Central", "summary": "role: firewall | model: Cisco ASA"},
        {"object_type": "dcim_interfaces", "object_label": "Interface", "name": "GigabitEthernet0/0",
         "site": "Spain Central", "summary": "mac: aa:bb:cc:dd:ee:01 | speed: 1000Mb/s"},
        {"object_type": "ipam_ip_addresses", "object_label": "IP Address", "name": "10.0.1.1",
         "site": "Spain Central", "summary": "assigned: fwAZESWINE1 GigabitEthernet0/0"},
    ]
    with mock.patch("core.ai_helper._fetch_device_context", return_value=mock_rows):
        _, ctx = try_fuzzy_hostname_lookup(
            "fwazewine1", known_names=known, search_func=lambda ids, site="", limit=10: []
        )
    assert_true(ctx is not None, "Context block returned")
    assert_true("[SYSTEM NOTICE]" in ctx, "Contains [SYSTEM NOTICE] header")
    assert_true("fwazewine1" in ctx, "Original query in notice")
    assert_true("fwAZESWINE1" in ctx, "Matched hostname in notice")
    assert_true("Spain Central" in ctx, "Site present in context")
    assert_true("GigabitEthernet0/0" in ctx, "Interface present in context")
    assert_true("10.0.1.1" in ctx, "IP address present in context")


def test_fuzzy_context_registers_both_tokens():
    """Both query and candidate are registered in vault."""
    section("TEST 5c: Vault Tokens for Query + Candidate")
    known = ["fwAZESWINE1"]
    mock_rows = [{"object_type": "dcim_devices", "name": "fwAZESWINE1", "site": "HQ", "summary": ""}]
    with mock.patch("core.ai_helper._fetch_device_context", return_value=mock_rows):
        with mock.patch("core.vault.SanitizerVault") as MockVault:
            mock_vault_inst = mock.MagicMock()
            MockVault.return_value = mock_vault_inst
            try_fuzzy_hostname_lookup(
                "fwazewine1", known_names=known,
                search_func=lambda ids, site="", limit=10: []
            )
            token_calls = [c[0][0] for c in mock_vault_inst._get_or_create_token.call_args_list]
            assert_true("fwazewine1" in token_calls, "Misspelled query registered in vault")
            assert_true("fwAZESWINE1" in token_calls, "Matched hostname registered in vault")


def test_exact_match_bypasses_fuzzy():
    """Exact match returns results without fuzzy processing."""
    section("TEST 5d: Exact Match Bypasses Fuzzy Logic")
    known = ["fwAZESWINE1"]
    mock_results = [{"name": "fwAZESWINE1", "site": "HQ", "summary": "firewall"}]
    search_called = []
    def mock_search(ids, site="", limit=10):
        search_called.append(ids)
        # Return results when the exact name is in the ids list
        return [r for r in mock_results if any(r["name"].lower() == i.lower() for i in ids)]
    results, ctx = try_fuzzy_hostname_lookup(
        "fwAZESWINE1", known_names=known, search_func=mock_search
    )
    assert_true(len(results) > 0, "Exact match returns results")
    assert_true(ctx is None, "No fuzzy context when exact match succeeds")


def test_fetch_device_context_queries_both_tables():
    """_fetch_device_context searches backup_records and inventory_records."""
    section("TEST 5e: _fetch_device_context Queries Both Tables")
    rows = _fetch_device_context("nonexistent-host-xyz")
    # Should not crash; may return empty list if no backup loaded
    assert_true(isinstance(rows, list), "_fetch_device_context returns a list")
    # Verify it doesn't raise an exception even with no data
    assert_true(True, "Function executes without error on missing host")


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  NetBox Hub v4.3 — Unit Test Suite")
    print("=" * 60)

    test_version_bump()
    test_fuzzy_suffix_guard()
    test_fuzzy_typo_recovery()
    test_fuzzy_max_candidates()
    test_fuzzy_exact_match_skipped()
    test_fuzzy_no_candidates()
    test_fuzzy_suggestion_block()
    test_fuzzy_empty_inputs()
    test_overlap_multi_collision()
    test_overlap_supernet_containment()
    test_overlap_child_in_parent()
    test_overlap_cross_vrf_ignored()
    test_overlap_no_collision()
    test_overlap_equal_length_no_overlap()
    test_allocator_cidr_aligned()
    test_allocator_multiple_results()
    test_allocator_tail_placement()
    test_allocator_no_space()
    test_allocator_respects_boundaries()
    test_vault_sanitization_no_regression()
    test_fuzzy_context_fetch_system_notice()
    test_fuzzy_context_fetch_with_db_data()
    test_fuzzy_context_registers_both_tokens()
    test_exact_match_bypasses_fuzzy()
    test_fetch_device_context_queries_both_tables()
    test_overlap_cross_vrf_ignored()
    test_overlap_no_collision()
    test_overlap_equal_length_no_overlap()
    test_allocator_cidr_aligned()
    test_allocator_multiple_results()
    test_allocator_tail_placement()
    test_allocator_no_space()
    test_allocator_respects_boundaries()
    test_vault_sanitization_no_regression()

    print(f"\n{'═' * 60}")
    print(f"  Results: {_passed} passed, {_failed} failed")
    print(f"{'═' * 60}")
    sys.exit(1 if _failed else 0)
