import os
import json
from typing import Dict
from config.constants import RULES_FILE

DEFAULT_RULES = {
    "branch_switch": "SW<Country><State><Site><Zone><Seq>-<StackID>",
    "branch_ap": "WAP<Country><State><Site><Seq>",
    "branch_security": "FW<Country><State><Site><Vendor><Seq> / ION<Country><State><Site><Seq>",
    "switch_uplink_desc_local": "to <Remote_Device>_<Remote_Port_Short> [<Role>]",
    "switch_uplink_desc_remote": "to <Local_Device>_<Local_Port_Short> [<Role>]",
    "switch_lag_member": "<Local_Port_Short> [<Local_Po>] -> <Remote_Device>_<Remote_Port_Short> [<Role>]",
    "switch_port_channel": "<Local_Po> -> <Remote_Device>_<Remote_Po> [<Trunk_Info>]",
    "switch_access_desc": "<VLAN_Name> - <Host/Device>_<Port>",
    "firewall_interface": "<Role/Zone>_<VLAN_ID>",
    "esxi_host": "<site_prefix>esx<number>.<domain>",
    "vm_host": "<Country><Site><Role><Seq> or <Site_Prefix><Role><Seq> (e.g. AURFLWOTAPP01, AUGLOSFS01, NYCCVI01, ROFLAFS01)",
    "esxi_uplink": "<vmnicX> - <vSwitch> Active Uplink / Standby Uplink",
    "esxi_portgroup": "<vSwitch> [<vmnicX>, <vmnicY> Active / <vmnicZ> Standby]",
    "esxi_vmkernel": "<Purpose/Service> [<vSwitch>]",
    "netbox_server_yaml": (
        "console-ports: Serial (de-9); "
        "module-bays: PSU1, PSU2, OCP3, PCIe1, PCIe2, PCIe3; "
        "interfaces: OOB Management ONLY (1000base-t, mgmt_only: true)"
    )
}

def load_naming_rules() -> Dict[str, str]:
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_RULES.copy()

def save_naming_rules(rules: Dict[str, str]):
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)

def export_rules_as_prompt(rules: Dict[str, str]) -> str:
    return f"""# INFRASTRUCTURE & NAMING CONVENTIONS STANDARD (AUTOMATION GRADE)

1. Network & Security Devices:
- Switch Hostname: {rules.get('branch_switch', '')}
- Wireless AP Hostname: {rules.get('branch_ap', '')}
- Firewall / Security Hostname: {rules.get('branch_security', '')}
- Switch Uplink Description (Local): {rules.get('switch_uplink_desc_local', '')}
- Switch Uplink Description (Remote): {rules.get('switch_uplink_desc_remote', '')}
- Switch LAG Member Description: {rules.get('switch_lag_member', '')}
- Switch Port Channel Description: {rules.get('switch_port_channel', '')}
- Switch Access Port Description: {rules.get('switch_access_desc', '')}
- Firewall Interface Description: {rules.get('firewall_interface', '')}

2. Hypervisors & Virtual Machines:
- ESXi Hostname: {rules.get('esxi_host', '')}
- Virtual Machine (VM) Hostname: {rules.get('vm_host', '')}
- ESXi Physical Uplink Description: {rules.get('esxi_uplink', '')}
- ESXi Port Group Teaming Description: {rules.get('esxi_portgroup', '')}
- ESXi VMkernel Description: {rules.get('esxi_vmkernel', '')}

3. NetBox Hardware YAML Schema:
- {rules.get('netbox_server_yaml', '')}
"""