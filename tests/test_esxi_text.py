import pytest
from core.extractors.esxi_text import parse_vsphere_text
from core.topology_reducer import reduce_topology_pipeline

NTNX01_FIXTURE = """
Skip to main content
vSphere Client
ntnx01.eswine.adds
ACTIONS
Summary
Configure
Networking
Virtual switches
VMkernel adapters
Physical adapters
Firewall
Services
Hardware
Virtual switches
ADD NETWORKING...
REFRESH
Standard Switch: vSwitch0
ADD NETWORKING
EDIT
MANAGE PHYSICAL ADAPTERS
Backplane Network
VLAN ID: --
Virtual Machines (1)
Management Network PRW
VLAN ID: --
VMkernel Ports (1)
vmk0 : 10.27.179.65
VM Net DMZ
VLAN ID: 11
Virtual Machines (1)
VM Net Externa
VLAN ID: 10
Virtual Machines (1)
VM Network
VLAN ID: --
Virtual Machines (14)
iSCSI Veeam
VLAN ID: 31
Virtual Machines (1)
Physical Adapters
vmnic0 10000 Full
vmnic1 10000 Full
vmnic2 1000 Full
vmnic3 1000 Full
Standard Switch: vSwitchNutanix
ADD NETWORKING
EDIT
MANAGE PHYSICAL ADAPTERS
ntnx-internal-pg
VLAN ID: --
Virtual Machines (1)
ntnx-internal-vmk
VLAN ID: --
VMkernel Ports (1)
vmk1 : 192.168.5.1
Physical Adapters
No physical network adapters
Standard Switch: vSwitch1
ADD NETWORKING
EDIT
MANAGE PHYSICAL ADAPTERS
OnbaseBubbleNet
VLAN ID: 22
Virtual Machines (0)
Physical Adapters
No physical network adapters
Splitter collapsed
Recent Tasks
Alarms
"""

COLLAPSED_2OTVS_FIXTURE = """
Virtual switches
ADD NETWORKING...
REFRESH
Distributed Switch: dvSwitch0
MANAGE PHYSICAL ADAPTERS
Management Network
VLAN ID: 400
VMkernel Ports (1)
vmk0 : 192.168.40.21
OT Routable
VLAN ID: 603
OT_Routable2-Vlan_604
VLAN ID: 604
vMotion Network
VLAN ID: 401
VMkernel Ports (1)
vmk1 : 192.168.40.134
vSAN Network
VLAN ID: 402
VMkernel Ports (1)
vmk2 : 192.168.40.166
dvSwitch0-DVUplinks-47
uplink1 (1 NIC Adapters)
uplink2 (1 NIC Adapters)
uplink3 (0 NIC Adapters)
uplink4 (0 NIC Adapters)
Distributed Switch: iDMZ-vdSwitch02
MANAGE PHYSICAL ADAPTERS
IDMZ-VLAN600
VLAN ID: 600
uplink1 (1 NIC Adapters)
uplink2 (1 NIC Adapters)
Standard Switch: vSwitch0
Standard Switch: vSwitch1
Recent Tasks
"""

def test_ntnx01_parsing():
    records, warns = parse_vsphere_text(NTNX01_FIXTURE)
    assert len(warns) == 0
    assert len(records) == 15

    # Target tuple: (switch, type, interface, purpose, ip, vlan, speed)
    tuples = {(r["switch"], r["type"], r["interface"], r["purpose"], r["ip"], r["vlan"], r["speed"]) for r in records}

    assert ("vSwitch0", "VMkernel", "vmk0", "Management Network PRW", "10.27.179.65", "", "") in tuples
    assert ("vSwitch0", "Port Group", "", "VM Net DMZ", "", "11", "") in tuples
    assert ("vSwitch0", "Port Group", "", "VM Net Externa", "", "10", "") in tuples
    assert ("vSwitch0", "Port Group", "", "iSCSI Veeam", "", "31", "") in tuples
    assert ("vSwitch0", "Uplink", "vmnic2", "", "", "", "1000 Full") in tuples
    assert ("vSwitchNutanix", "VMkernel", "vmk1", "ntnx-internal-vmk", "192.168.5.1", "", "") in tuples
    assert ("vSwitchNutanix", "Uplink", "No Uplink", "", "", "", "") in tuples
    assert ("vSwitch1", "Uplink", "No Uplink", "", "", "", "") in tuples

    # Negative assertions
    purposes = {r["purpose"] for r in records}
    for forbidden in ["Splitter collapsed", "Firewall", "Services", "Virtual Machines (1)", "Virtual switches"]:
        assert forbidden not in purposes

def test_collapsed_2otvs_parsing():
    records, warns = parse_vsphere_text(COLLAPSED_2OTVS_FIXTURE)
    # 6 port groups across dvSwitch0 and iDMZ-vdSwitch02
    pg_recs = [r for r in records if r["type"] in ("Port Group", "VMkernel")]
    assert len(pg_recs) == 6
    vlans = {r["vlan"] for r in pg_recs}
    assert {"400", "603", "604", "401", "402", "600"}.issubset(vlans)

    # 4 warnings: 2 for uplink slots with 0 vmnics, 2 for empty switches
    assert len(warns) == 4
    assert any("uplink slots list" in w for w in warns)
    assert any("collapsed or empty" in w for w in warns)

def test_reducer_integration():
    records, _ = parse_vsphere_text(NTNX01_FIXTURE)
    reduced = reduce_topology_pipeline(records, {}, {})
    for r in reduced:
        assert "vlan" in r

    # Real port groups survive reducer
    purposes = {r["purpose"] for r in reduced if r.get("purpose")}
    assert "VM Net DMZ" in purposes
    assert "iSCSI Veeam" in purposes
    assert "OnbaseBubbleNet" in purposes
