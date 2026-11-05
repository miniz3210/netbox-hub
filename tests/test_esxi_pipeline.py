import copy
from core.extractors.esxi_text import parse_vsphere_text, parse_vsphere_text_ex
from core.extractors.esxi_pipeline import analyze_esxi, merge_ocr_into_text

SIDEBAR = "Skip to main content\nvSphere Client\nNetworking\nVirtual switches\nVMkernel adapters\nPhysical adapters\nFirewall\nServices\nHardware\n"
FOOT = "Recent Tasks\nAlarms\nTable\nName\n"

# REAL page text, dvSwitch host (all nodes expanded)
DVS = SIDEBAR + """Virtual switches
ADD NETWORKING...
REFRESH
 Distributed Switch: dvSwitch0
MANAGE PHYSICAL ADAPTERS
Management Network
VLAN ID: 400
VMkernel Ports (1)
vmk0 : 192.168.40.23
Virtual Machines (0)
OT Routable
VLAN ID: 603
Virtual Machines (2)
OT_Routable2-Vlan_604
VLAN ID: 604
Virtual Machines (1)
vMotion Network
VLAN ID: 401
VMkernel Ports (1)
vmk1 : 192.168.40.136
Virtual Machines (0)
vSAN Network
VLAN ID: 402
VMkernel Ports (1)
vmk2 : 192.168.40.168
Virtual Machines (0)
dvSwitch0-DVUplinks-47
uplink1 (1 NIC Adapters)
vmnic0 ageotinfhost3.eswines.ot
uplink2 (1 NIC Adapters)
vmnic1 ageotinfhost3.eswines.ot
uplink3 (0 NIC Adapters)
uplink4 (0 NIC Adapters)
 Distributed Switch: iDMZ-vdSwitch02
MANAGE PHYSICAL ADAPTERS
IDMZ-VLAN600
VLAN ID: 600
Virtual Machines (7)
iDMZ-vdSwitch02-DVUplinks...
Uplink 1 (1 NIC Adapters)
vmnic2 ageotinfhost3.eswines.ot
Uplink 2 (1 NIC Adapters)
vmnic3 ageotinfhost3.eswines.ot
Uplink 3 (0 NIC Adapters)
Uplink 4 (0 NIC Adapters)
 Standard Switch: vSwitch0
ADD NETWORKING
EDIT
MANAGE PHYSICAL ADAPTERS
TEMP-OT-Routable603
VLAN ID: 603
Virtual Machines (0)
Physical Adapters
No physical network adapters
 Standard Switch: vSwitch1
ADD NETWORKING
EDIT
MANAGE PHYSICAL ADAPTERS
iDMZ Servers
VLAN ID: 1
Virtual Machines (1)
Physical Adapters
No physical network adapters
""" + FOOT

# same host with the vmk lines and vmnic lines absent (nodes were collapsed when copied)
DVS_COLLAPSED = DVS.replace("vmk0 : 192.168.40.23\n", "").replace("vmk1 : 192.168.40.136\n", "").replace("vmk2 : 192.168.40.168\n", "") \
                .replace("vmnic0 ageotinfhost3.eswines.ot\n", "").replace("vmnic1 ageotinfhost3.eswines.ot\n", "") \
                .replace("vmnic2 ageotinfhost3.eswines.ot\n", "").replace("vmnic3 ageotinfhost3.eswines.ot\n", "")

def R(sw, iface, typ, ip="", purpose="", role="None", speed=""):
    return {"switch": sw, "interface": iface, "type": typ, "ip": ip, "purpose": purpose, "role": role, "speed": speed}

# OCR records as the extractor produced them from the user's real RapidOCR tokens (raw names: dvSwitcha / vdSwitchQ2 / vSwitcho)
OCR_DVS = [
    R("dvSwitcha", "vmnic0", "Uplink", role="Active Uplink"), R("dvSwitcha", "vmnic1", "Uplink", role="Active Uplink"),
    R("dvSwitcha", "vmk0", "VMkernel", "192.168.40.23", "Management Network"),
    R("dvSwitcha", "vmk1", "VMkernel", "192.168.40.136", "vMotion Network"),
    R("dvSwitcha", "vmk2", "VMkernel", "192.168.40.168", "vSAN Network"),
    R("dvSwitcha", "", "Port Group", purpose="OT Routable"), R("dvSwitcha", "", "Port Group", purpose="OT_Routable2-Vlan_604"),
    R("iDMZ-vdSwitchQ2", "vmnic2", "Uplink", role="Active Uplink"), R("iDMZ-vdSwitchQ2", "vmnic3", "Uplink", role="Active Uplink"),
    R("iDMZ-vdSwitchQ2", "", "Port Group", purpose="IDMZ-VLAN600"),
    R("vSwitcho", "", "Port Group", purpose="TEMP-OT-Routable603"), R("vSwitch1", "", "Port Group", purpose="iDMZ Servers"),
]

def sig(rs, vlan=False):
    return {(r["switch"], r["type"], r["interface"], r["purpose"], r["ip"]) + ((r.get("vlan", ""),) if vlan else ()) for r in rs}

# ---------------------------------------------------------------------------
# Parser on real text
# ---------------------------------------------------------------------------
def test_dvs_extracted_real_text_is_complete():
    p = parse_vsphere_text_ex(DVS)
    assert len(p["records"]) == 14 and p["gaps"] == []
    assert ("dvSwitch0", "VMkernel", "vmk2", "vSAN Network", "192.168.40.168", "402") in sig(p["records"], vlan=True)

def test_dvs_collapsed_real_text_reports_gaps_that_ocr_can_fill():
    p = parse_vsphere_text_ex(DVS_COLLAPSED)
    kinds = sorted((g["kind"], g["switch"]) for g in p["gaps"])
    assert kinds == [("nic_missing", "dvSwitch0"), ("nic_missing", "iDMZ-vdSwitch02"),
                     ("vmk_missing", "dvSwitch0"), ("vmk_missing", "dvSwitch0"), ("vmk_missing", "dvSwitch0")]
    assert all(g["ocr_can_help"] for g in p["gaps"])

def test_two_vmk_on_one_card_are_two_records():
    t = "Virtual switches\nADD NETWORKING...\n Standard Switch: vSwitch9 \nPG\nVLAN ID: 5\nVMkernel Ports (2)\nvmk4 : 10.0.0.4\nvmk5 : 10.0.0.5\nPhysical Adapters\nvmnic7 10000 Full\n"
    p = parse_vsphere_text_ex(t)
    assert sorted((r["interface"], r["ip"], r["purpose"], r["vlan"]) for r in p["records"] if r["type"] == "VMkernel") == \
        [("vmk4", "10.0.0.4", "PG", "5"), ("vmk5", "10.0.0.5", "PG", "5")] and p["gaps"] == []

def test_collapsed_switch_header_is_a_gap_that_ocr_cannot_fill():
    t = DVS.replace("Standard Switch: vSwitch1\nADD NETWORKING\nEDIT\nMANAGE PHYSICAL ADAPTERS\niDMZ Servers\nVLAN ID: 1\nVirtual Machines (1)\nPhysical Adapters\nNo physical network adapters\n",
                    "Standard Switch: vSwitch1\n")
    g = [x for x in parse_vsphere_text_ex(t)["gaps"] if x["kind"] == "collapsed"]
    assert len(g) == 1 and g[0]["switch"] == "vSwitch1" and g[0]["ocr_can_help"] is False

# ---------------------------------------------------------------------------
# Pipeline modes
# ---------------------------------------------------------------------------
def test_mode_text_only_does_not_run_ocr():
    called = []
    out = analyze_esxi(DVS, lambda: called.append(1) or OCR_DVS)
    assert out["source"] == "text" and called == [] and len(out["records"]) == 14

def test_mode_text_plus_ocr_fills_only_the_gaps_and_tags_them():
    out = analyze_esxi(DVS_COLLAPSED, lambda: OCR_DVS)
    assert out["source"] == "text+ocr"
    added = [r for r in out["records"] if r.get("source") == "ocr"]
    assert {(a["switch"], a["interface"], a["ip"]) for a in added} == {
        ("dvSwitch0", "vmk0", "192.168.40.23"), ("dvSwitch0", "vmk1", "192.168.40.136"), ("dvSwitch0", "vmk2", "192.168.40.168"),
        ("dvSwitch0", "vmnic0", ""), ("dvSwitch0", "vmnic1", ""), ("iDMZ-vdSwitch02", "vmnic2", ""), ("iDMZ-vdSwitch02", "vmnic3", "")}
    assert all(a["is_uncertain"] and a["note"] for a in added)
    vl = {(r["purpose"], r["vlan"]) for r in out["records"] if r["type"] == "VMkernel"}
    assert vl == {("Management Network", "400"), ("vMotion Network", "401"), ("vSAN Network", "402")}   # titles + VLAN come from TEXT
    assert len([r for r in out["records"] if r["purpose"] == "vSAN Network"]) == 1                    # no duplicate Port Group row
    assert sig(out["records"]) == sig(parse_vsphere_text_ex(DVS)["records"])                          # end state == the complete text
    assert out["report"]["unresolved"] == []

def test_mode_text_with_gaps_but_no_screenshots_asks_for_one():
    out = analyze_esxi(DVS_COLLAPSED, None)
    assert out["source"] == "text" and out["needs_screenshot"] is True and len(out["report"]["unresolved"]) == 5

def test_mode_ocr_only_when_no_text():
    out = analyze_esxi("", lambda: OCR_DVS)
    assert out["source"] == "ocr" and out["records"] == OCR_DVS
    assert analyze_esxi("   \n ", lambda: OCR_DVS)["source"] == "ocr"

def test_mode_unusable_text_falls_back_to_ocr():
    out = analyze_esxi("hello world\nnothing useful", lambda: OCR_DVS)
    assert out["source"] == "ocr" and any("no usable" in w.lower() for w in out["warnings"])

def test_mode_nothing_at_all():
    out = analyze_esxi("", None)
    assert out["source"] == "none" and out["records"] == []
    out2 = analyze_esxi("hello", None)
    assert out2["source"] == "none" and out2["needs_screenshot"] is True

def test_ocr_is_called_at_most_once():
    n = []
    analyze_esxi(DVS_COLLAPSED, lambda: n.append(1) or OCR_DVS)
    assert n == [1]

# ---------------------------------------------------------------------------
# Merge safety rules
# ---------------------------------------------------------------------------
def test_ocr_never_overrides_text_and_reports_conflict():
    p = parse_vsphere_text_ex(DVS)
    bad = copy.deepcopy(OCR_DVS); bad[2]["ip"] = "192.168.40.28"
    merged, rep = merge_ocr_into_text(p["records"], p["gaps"], bad)
    assert [m["ip"] for m in merged if m["interface"] == "vmk0"] == ["192.168.40.23"]
    assert len(rep["conflicts"]) == 1

def test_unfillable_gap_stays_unresolved():
    broken = DVS.replace("vmk2 : 192.168.40.168\n", "")
    p = parse_vsphere_text_ex(broken)
    merged, rep = merge_ocr_into_text(p["records"], p["gaps"], [o for o in OCR_DVS if o["interface"] != "vmk2"])
    assert not [m for m in merged if m.get("source") == "ocr"] and len(rep["unresolved"]) == 1

def test_ocr_only_port_group_is_suggested_not_added():
    p = parse_vsphere_text_ex(DVS)
    merged, rep = merge_ocr_into_text(p["records"], p["gaps"], OCR_DVS + [R("dvSwitcha", "", "Port Group", purpose="Backup Network")])
    assert sig(merged) == sig(p["records"]) and any("Backup Network" in s for s in rep["suggestions"])

def test_vswitch0_and_vswitch01_are_never_conflated():
    t = "Virtual switches\nADD NETWORKING...\n Standard Switch: vSwitch01 \nADD NETWORKING\niSCSI01\nVLAN ID: --\nVMkernel Ports (1)\nPhysical Adapters\nvmnic2 10000 Full\n"
    p = parse_vsphere_text_ex(t)
    merged, rep = merge_ocr_into_text(p["records"], p["gaps"], [R("vSwitch0", "vmk9", "VMkernel", "10.0.0.9", "iSCSI01")])
    assert not [m for m in merged if m.get("source") == "ocr"] and "vSwitch0" in rep["unmatched_switches"]

def test_backward_compatible_tuple_api():
    recs, warns = parse_vsphere_text(DVS)
    assert len(recs) == 14 and warns == []


def test_duplicate_ocr_from_overlapping_screenshots_still_fills_everything():
    out = analyze_esxi(DVS_COLLAPSED, lambda: OCR_DVS + copy.deepcopy(OCR_DVS) + copy.deepcopy(OCR_DVS))
    assert out["source"] == "text+ocr" and out["report"]["unresolved"] == []
    assert len([r for r in out["records"] if r.get("source") == "ocr"]) == 7
    assert sig(out["records"]) == sig(parse_vsphere_text_ex(DVS)["records"])


def test_screenshots_that_disagree_on_an_ip_are_not_used_to_fill():
    a, b = copy.deepcopy(OCR_DVS), copy.deepcopy(OCR_DVS)
    b[4]["ip"] = "192.168.40.188"                                 # second screenshot misread vmk2
    out = analyze_esxi(DVS_COLLAPSED, lambda: a + b)
    assert not [r for r in out["records"] if r.get("source") == "ocr" and r["interface"] == "vmk2"]
    assert any("vSAN Network" in s and "ambiguous" in s for s in out["report"]["unresolved"])

