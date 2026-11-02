"""Tests for ESXi screenshot extractor (OCR → topology records)."""
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.extractors.esxi import find_ipv4, repair_switch_name, EsxiTopologyExtractor
from core.topology_reducer import reduce_topology_pipeline


def make_tokens(coords_text):
    """Build tokens from list of (x, y, text) tuples."""
    toks = []
    for x, y, text in coords_text:
        toks.append({"text": text, "box": [x, y, len(text) * 11.0, 22.0]})
    return toks


TEST_NORMS = [
    {"pattern": r"(?<=Switch)[oO](?=\d*\b)", "replace": "0"},
    {"pattern": r"(?<=[a-z])Network\b", "replace": " Network"},
    {"pattern": r"\s+", "replace": " "},
]


# ── Unit tests for find_ipv4 ────────────────────────────────────────────────

def test_find_ipv4_basic():
    assert find_ipv4("192.16B .40.23") == "192.168.40.23"


def test_find_ipv4_double_repair():
    assert find_ipv4("192.16B.40.16B") == "192.168.40.168"


def test_find_ipv4_mixed():
    assert find_ipv4("10.27.l.5") == "10.27.1.5"


def test_find_ipv4_no_match():
    assert find_ipv4("vmk0 x") is None


# ── Unit tests for repair_switch_name ───────────────────────────────────────

def test_repair_switch_lowercase_a():
    assert repair_switch_name("dvSwitcha") == "dvSwitch0"


def test_repair_switch_lowercase_o():
    assert repair_switch_name("dvSwitcho") == "dvSwitch0"


def test_repair_switch_uppercase_O():
    assert repair_switch_name("dvSwitchO") == "dvSwitch0"


def test_repair_switch_q2():
    assert repair_switch_name("iDMZ-vdSwitchQ2") == "iDMZ-vdSwitch02"


def test_repair_switch_strip_dvuplinks():
    assert repair_switch_name("iDMZ-vdSwitchO2-DVUplinks_") == "iDMZ-vdSwitch02"


def test_repair_switch_numerics_unchanged():
    assert repair_switch_name("vSwitch1") == "vSwitch1"
    assert repair_switch_name("vSwitch01") == "vSwitch01"


def test_repair_switch_office_unchanged():
    assert repair_switch_name("vSwitchOffice") == "vSwitchOffice"


def test_repair_switch_oob_unchanged():
    assert repair_switch_name("vSwitchOOB") == "vSwitchOOB"


def test_repair_switch_prod_unchanged():
    assert repair_switch_name("dvSwitchProd") == "dvSwitchProd"


# ── Golden fixture integration test ─────────────────────────────────────────

SHOT1 = [
    (17, 38, "Virtual switches"),
    (1723, 41, "ADD NI"),
    (325, 97, "Distributed Switch:dvSwitcha"),
    (468, 98, "MANAGE PHYSICALADAPTERS"),
    (465, 222, "ManagementNetwork"),
    (3190, 224, "dvSwitchO-DVUplinks-47"),
    (461, 265, "VLAN ID: 400"),
    (1168, 263, "uplink1 (1 NIC Adapters)"),
    (464, 297, "VMkernel Ports (1)"),
    (1190, 299, "vmnic0 ageotinfhost3.eswines.ot"),
    (489, 339, "vmk0 : 192.16B .40.23"),
    (1168, 337, "uplink2 *1 NIC Ad*pters)"),
    (488, 372, "Virtual Machines (O)"),
    (1192, 375, "vmnic1 ageotinfhost3.eswines.ot"),
    (1193, 410, "uplink3 (O NIC Adapters}"),
    (1193, 447, "uplink4 (O NIC Adapters}"),
    (464, 460, "OT_Routable"),
    (461, 501, "VLAN ID: 604"),
    (466, 536, "> Virtual Machines (2)"),
    (462, 621, "OT_Routable2-Vlan_604"),
    (462, 666, "VLAN ID: 604"),
    (467, 710, "> Virtual Machines (1)"),
    (465, 780, "vMotionNetwork"),
    (461, 830, "VLAN ID: 401"),
    (464, 866, "VMkernel Ports (1)"),
    (489, 904, "vmk1 : 192.16B.40.l36"),
    (488, 936, "Virtual Machines (O)"),
    (463, 1023, "vSAN Network"),
    (462, 1067, "VLAN ID: 602"),
    (465, 1104, "VMkernel Ports (1)"),
    (490, 1141, "vmk2 : 192.16B.40.16B"),
    (488, 1175, "Virtual Machines (C)"),
]

SHOT2 = [
    (33, 12, "Virtual switches"),
    (1145, 17, "ADD"),
    (72, 71, "Distributed Switch: dvSwitcho"),
    (75, 143, "DistributedSwitch:iDMZ-vdSwitchQ2"),
    (568, 142, "MANAGEPHYSICALADAPTERS"),
    (482, 267, "iDMZ-VLAN600"),
    (1192, 270, "iDMZ-vdSwitchO2-DVUplinks_."),
    (479, 311, "VLAN ID: 600"),
    (1188, 309, "uplink 1 (1 NIC Adapters)"),
    (482, 348, "> Virtual Machines (7)"),
    (1210, 346, "vmnic2 ageotinfhost3.eswines.ot"),
    (1188, 382, "Uplink 2 (1 NICAdapters)"),
    (1211, 420, "vmnic3 ageotinfhost3.eswines.ot"),
    (1213, 457, "Uplink 3 (O NIC Adapters)"),
    (1214, 493, "Uplink 4 (O NIC Adapters)"),
    (75, 695, "StandardSwitch:vSwitcho"),
    (448, 695, "ADD NETWORKING"),
    (800, 693, "EDIT"),
    (807, 695, "MANAGEPHYSICALADAPTERS"),
    (520, 823, "TEMP-OT-Routable603"),
    (1212, 824, "Physical Adapters"),
    (479, 864, "VLAN ID: 603"),
    (1211, 862, "No physical network acapters"),
    (507, 896, "Virtual Machines (O)"),
]

SHOT3 = [
    (29, 47, "Virtual switches"),
    (71, 108, "DistributedSwitch:dvSwitcha"),
    (72, 180, "DistributedSwitch:iDMZ-vdSwitchQ2"),
    (72, 251, "StandardSwitch:vSwitcho"),
    (71, 322, "StandardSwitch:vSwitch1"),
    (448, 321, "ADD NETWORKING"),
    (708, 321, "EDIT"),
    (822, 325, "MANAGEPHYSICALADAPTERS"),
    (540, 454, "iDMZServers"),
    (1262, 457, "Physical Adapters"),
    (503, 498, "VLAN ID: 1"),
    (1261, 498, "No physical network aclapters"),
    (508, 533, "> Virtual Machines (1)"),
]


def test_golden_fixture():
    cfg = {"token_normalizations": TEST_NORMS, "noise_filters": []}
    extractor = EsxiTopologyExtractor()

    all_records = []
    for shot in (SHOT1, SHOT2, SHOT3):
        tokens = make_tokens(shot)
        recs = extractor.extract(tokens, 1920, 1200, cfg)
        all_records.extend(recs)

    reduced = reduce_topology_pipeline(all_records, cfg, {})

    # Basic sanity
    assert len(reduced) == 12, f"Expected 12 records, got {len(reduced)}: {reduced}"

    switches = [r["switch"] for r in reduced]
    # No phantom or duplicate switches
    assert "dvSwitchO" not in switches
    assert "dvSwitcha" not in switches
    assert "dvSwitchQ2" not in switches

    # No "No Uplink" rows
    for r in reduced:
        assert r["interface"] != "No Uplink", f"Found No Uplink: {r}"

    # Expected records (switch | type | interface | purpose | ip | role)
    expected = [
        ("dvSwitch0", "Uplink", "vmnic0", "", "", "Active Uplink"),
        ("dvSwitch0", "Uplink", "vmnic1", "", "", "Active Uplink"),
        ("dvSwitch0", "VMkernel", "vmk0", "Management Network", "192.168.40.23", "None"),
        ("dvSwitch0", "VMkernel", "vmk1", "vMotion Network", "192.168.40.136", "None"),
        ("dvSwitch0", "VMkernel", "vmk2", "vSAN Network", "192.168.40.168", "None"),
        ("dvSwitch0", "Port Group", "", "OT_Routable", "", "None"),
        ("dvSwitch0", "Port Group", "", "OT_Routable2-Vlan_604", "", "None"),
        ("iDMZ-vdSwitch02", "Uplink", "vmnic2", "", "", "Active Uplink"),
        ("iDMZ-vdSwitch02", "Uplink", "vmnic3", "", "", "Active Uplink"),
        ("iDMZ-vdSwitch02", "Port Group", "", "iDMZ-VLAN600", "", "None"),
        ("vSwitch0", "Port Group", "", "TEMP-OT-Routable603", "", "None"),
        ("vSwitch1", "Port Group", "", "iDMZServers", "", "None"),
    ]

    for i, exp in enumerate(expected):
        r = reduced[i]
        for j, key in enumerate(("switch", "type", "interface", "purpose", "ip", "role")):
            assert r[key] == exp[j], f"Record {i} key={key}: expected={exp[j]!r}, got={r[key]!r}"
