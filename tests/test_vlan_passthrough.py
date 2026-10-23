import pytest
import pandas as pd
from core.topology_reducer import reduce_topology_pipeline
from core.extractors.esxi_text import parse_vsphere_text
from core.extractors.esxi import heal_vlan_id
from tests.test_esxi_text import COLLAPSED_2OTVS_FIXTURE

def test_vlan_pipeline_preservation():
    raw_records = [
        {"switch": "dvSwitch0", "type": "VMkernel", "interface": "vmk0", "purpose": "Management Network", "ip": "192.168.40.23", "role": "None", "speed": "", "vlan": "400"},
        {"switch": "dvSwitch0", "type": "Port Group", "interface": "", "purpose": "OT Routable", "ip": "", "role": "None", "speed": "", "vlan": "603"},
        {"switch": "dvSwitch0", "type": "Port Group", "interface": "", "purpose": "OT_Routable2-Vlan_604", "ip": "", "role": "None", "speed": "", "vlan": "604"},
        {"switch": "dvSwitch0", "type": "VMkernel", "interface": "vmk1", "purpose": "vMotion Network", "ip": "192.168.40.136", "role": "None", "speed": "", "vlan": "401"},
        {"switch": "dvSwitch0", "type": "VMkernel", "interface": "vmk2", "purpose": "vSAN Network", "ip": "192.168.40.168", "role": "None", "speed": "", "vlan": "402"},
        {"switch": "iDMZ-vdSwitch02", "type": "Port Group", "interface": "", "purpose": "IDMZ-VLAN600", "ip": "", "role": "None", "speed": "", "vlan": "600"},
        {"switch": "vSwitch0", "type": "Port Group", "interface": "", "purpose": "TEMP-OT-Routable603", "ip": "", "role": "None", "speed": "", "vlan": "603"},
        {"switch": "vSwitch1", "type": "Port Group", "interface": "", "purpose": "iDMZ Servers", "ip": "", "role": "None", "speed": "", "vlan": "1"},
        {"switch": "vSwitch0", "type": "Port Group", "interface": "", "purpose": "Backplane Network", "ip": "", "role": "None", "speed": "", "vlan": ""},
    ]

    reduced = reduce_topology_pipeline(raw_records, {}, {})
    for r in reduced:
        assert "vlan" in r

    vlan_map = {r["purpose"]: r["vlan"] for r in reduced if r.get("purpose")}
    assert vlan_map["Management Network"] == "400"
    assert vlan_map["OT Routable"] == "603"
    assert vlan_map["OT_Routable2-Vlan_604"] == "604"
    assert vlan_map["vMotion Network"] == "401"
    assert vlan_map["vSAN Network"] == "402"
    assert vlan_map["IDMZ-VLAN600"] == "600"
    assert vlan_map["TEMP-OT-Routable603"] == "603"
    assert vlan_map["iDMZ Servers"] == "1"
    assert vlan_map["Backplane Network"] == ""

def test_vlan_merge_duplicate_backfill():
    dup1 = {"switch": "dvSwitch0", "type": "VMkernel", "interface": "vmk0", "purpose": "Management Network", "ip": "192.168.40.23", "role": "None", "speed": "", "vlan": ""}
    dup2 = {"switch": "dvSwitch0", "type": "VMkernel", "interface": "vmk0", "purpose": "Management Network", "ip": "192.168.40.23", "role": "None", "speed": "", "vlan": "400"}

    reduced = reduce_topology_pipeline([dup1, dup2], {}, {})
    assert len(reduced) == 1
    assert reduced[0]["vlan"] == "400"

def test_full_chain_from_text_with_vlan():
    records, warns = parse_vsphere_text(COLLAPSED_2OTVS_FIXTURE)
    reduced = reduce_topology_pipeline(records, {}, {})
    
    vlans = {r["vlan"] for r in reduced if r.get("vlan")}
    assert {"400", "603", "604", "401", "402", "600"}.issubset(vlans)

def test_grid_dataframe_vlan_dtype():
    records = [{"switch": "vSwitch0", "interface": "", "type": "Port Group", "ip": "", "purpose": "VM Network", "vlan": "100", "role": "None", "speed": ""}]
    df = pd.DataFrame(records, columns=["switch", "interface", "type", "ip", "purpose", "vlan", "role", "speed"])
    for col in df.columns:
        df[col] = df[col].fillna("").astype(str)
    
    assert str(df["vlan"].dtype) in ("object", "str", "string")
    assert not df["vlan"].isnull().any()
    assert df.loc[0, "vlan"] == "100"

def test_ocr_vlan_digit_repair():
    assert heal_vlan_id("VLAN ID: 4OO") == "400"
    assert heal_vlan_id("VLAN ID: --") == ""
    assert heal_vlan_id("VLAN ID: 6O5") == "605"
    assert heal_vlan_id("VLAN ID: 3l") == "31"
