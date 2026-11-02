import re
from core.extractors.esxi import EsxiTopologyExtractor

RAW = """[55, 97] Distributed Switch:dvSwitcha
[468, 98] MANAGE PHYSICALADAPTERS
[465, 222] ManagementNetwork
[1190, 224] dvSwitchO-DVUplinks-47
[461, 265] VLAN ID: 400
[1168, 263] uplink1 (1 NIC Adapters)
[464, 297] VMkernel Ports (1)
[1190, 299] vmnic0 ageotinfhost3.eswines.ot
[489, 339] vmk0 : 192.16B .40.23
[1168, 337] uplink2 (1 NIC Adapters)
[488, 372] Virtual Machines (O)
[1192, 375] vmnic1 ageotinfhost3.eswines.ot
[464, 460] OTRoutable
[461, 501] VLAN ID: 603
[466, 536] > Virtual Machines (2)
[462, 621] OT_Routable2-Vlan_604
[462, 666] VLAN ID: 604
[467, 700] > Virtual Machines (1)
[465, 789] vMotionNetwork
[461, 830] VLAN ID: 401
[464, 866] VMkernel Ports (1)
[489, 904] vmk1 : 192.16B.40.136
[488, 936] Virtual Machines (O)
[463, 1023] vSAN Network
[462, 1067] VLAN ID: 402
[465, 1104] VMkernel Ports (1)
[490, 1141] vmk2 : 192.16B.40.16B
[488, 1175] Virtual Machines (C)"""


def _tokens():
    out = []
    for ln in RAW.splitlines():
        m = re.match(r"\[(\d+), (\d+)\] (.*)", ln)
        out.append({"text": m.group(3), "box": [int(m.group(1)), int(m.group(2)), len(m.group(3)) * 11.0, 22.0]})
    return out


def test_ocr_path_carries_vlan_from_the_vlan_id_row():
    recs = EsxiTopologyExtractor().extract(_tokens(), 1920, 1200, {})
    got = {r["purpose"]: r["vlan"] for r in recs if r["type"] != "Uplink"}
    assert got == {"ManagementNetwork": "400", "vMotionNetwork": "401", "vSAN Network": "402",
                   "OTRoutable": "603", "OT_Routable2-Vlan_604": "604"}
