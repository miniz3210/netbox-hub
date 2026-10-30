from core.extractors.esxi_text import parse_vsphere_text_ex
from core.topology_reducer import reduce_topology_pipeline

TEXT = """Virtual switches
ADD NETWORKING...
 Standard Switch: vSwitch0
ADD NETWORKING
Services
VLAN ID: 100
Virtual Machines (0)
Firewall
VLAN ID: 200
Virtual Machines (0)
Physical Adapters
vmnic0 1000 Full
Recent Tasks
"""


def test_text_path_keeps_port_groups_named_like_ui_menu_items():
    recs = parse_vsphere_text_ex(TEXT)["records"]
    reduced = reduce_topology_pipeline(recs, {"token_normalizations": [], "noise_filters": []}, {})
    assert {"Services", "Firewall"} <= {r["purpose"] for r in reduced}


def test_reducer_preserves_extra_fields():
    rec = {"switch": "dvSwitch0", "interface": "vmk2", "type": "VMkernel", "ip": "192.168.40.168", "purpose": "vSAN Network",
           "role": "None", "speed": "", "vlan": "402", "source": "ocr", "is_uncertain": True, "note": "x"}
    reduced = reduce_topology_pipeline([rec], {"token_normalizations": [], "noise_filters": []}, {})
    assert len(reduced) == 1
    assert (reduced[0]["source"], reduced[0]["is_uncertain"], reduced[0]["note"], reduced[0]["vlan"]) == ("ocr", True, "x", "402")
