import os
import json
from typing import Dict, List
from datetime import datetime
from config.constants import RULES_FILE, RULES_HISTORY_FILE, MAX_HISTORY_ENTRIES

DEFAULT_RULES = {
    "branch_switch": "SW<Country><State><Site><Zone><Seq>-<StackID> / VS<Country><State><Site><Seq>-<StackID>",
    "branch_ap": "WAP<Country><State><Site><Seq>",
    "branch_security": "FW<Country><State><Site><Vendor><Seq> / ION<Country><State><Site><Seq>",
    "switch_uplink_desc": "Uplink_to_<Remote_Device>_<Remote_Port_Short>",
    "switch_lag_member": "LACP_to_<Remote_Device>_<Remote_Port_Short>",
    "switch_port_channel": "<Local_Po_ID>_to_<Remote_Device>",
    "switch_access_desc": "<VLAN_Name> - <Device>_<Port>",
    "firewall_interface": "<Role_Zone>_<VLAN_ID>",
    "esxi_host": "<site><role/esx><seq>.<domain> (Valid domains: .eswine.adds for IT/Corp, .eswines.ot or .eswine.ot for OT/Industrial, .corp.local for Branch/Local, or shortname without domain)",
    "vm_host": "<Country><Site><Role><Seq> (e.g. AURFLWOTAPP01, AUGLOSFS01) or <Site><Role><Seq> (e.g. PWSAFS001, ESCPDC01, NYCCVI01)",
    "esxi_uplink": "<vmnic> - <vSwitch> <Purpose> <Status>",
    "esxi_portgroup": "<PortGroup> [<Active_vmnics> Active / <Standby_vmnics> Standby]",
    "esxi_vmkernel": "<Purpose> Network - <vSwitch> (<Active_vmnics> Active / <Standby_vmnics> Standby)",
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

def save_naming_rules(rules: Dict[str, str], source: str = "Manual Edit"):
    """Save naming rules and add to history."""
    # Ensure data directory exists
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    
    # Save current rules to history before overwriting
    if os.path.exists(RULES_FILE):
        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                old_rules = json.load(f)
            add_to_history(old_rules, source)
        except Exception:
            pass
    
    # Save new rules with explicit flush
    with open(RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())

def add_to_history(rules: Dict[str, str], source: str = "Manual Edit"):
    """Add rules to history with timestamp."""
    history = load_history()
    
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "rules": rules
    }
    
    # Add to beginning of list
    history.insert(0, entry)
    
    # Keep only last MAX_HISTORY_ENTRIES
    history = history[:MAX_HISTORY_ENTRIES]
    
    # Save history
    os.makedirs(os.path.dirname(RULES_HISTORY_FILE), exist_ok=True)
    with open(RULES_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)

def load_history() -> List[Dict]:
    """Load history of naming rules changes."""
    if os.path.exists(RULES_HISTORY_FILE):
        try:
            with open(RULES_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def restore_from_history(index: int) -> Dict[str, str]:
    """Restore naming rules from history by index."""
    history = load_history()
    if 0 <= index < len(history):
        return history[index]["rules"]
    return DEFAULT_RULES.copy()

def clear_history():
    """Clear all history entries."""
    if os.path.exists(RULES_HISTORY_FILE):
        os.remove(RULES_HISTORY_FILE)

def export_rules_as_prompt(rules: Dict[str, str]) -> str:
    return f"""# INFRASTRUCTURE & NAMING CONVENTIONS STANDARD (AUTOMATION GRADE)

1. Network & Security Devices:
- Switch Hostname: {rules.get('branch_switch', '')}
- Wireless AP Hostname: {rules.get('branch_ap', '')}
- Firewall / Security Hostname: {rules.get('branch_security', '')}

2. Switch & Firewall Interface Descriptions:
- Switch Uplink Description: {rules.get('switch_uplink_desc', '')}
- Switch LAG Member Description: {rules.get('switch_lag_member', '')}
- Switch Port Channel Description: {rules.get('switch_port_channel', '')}
- Switch Access Port Description: {rules.get('switch_access_desc', '')}
- Firewall Interface Description: {rules.get('firewall_interface', '')}

3. Hypervisors & Virtual Machines:
- ESXi Hypervisor Hostname: {rules.get('esxi_host', '')}
  * IT / Corporate ESXi Domain: .eswine.adds (e.g. pwsesx001.eswine.adds, ageesx01.eswine.adds, esagex10.eswine.adds)
  * OT / Industrial Cluster Domain: .eswines.ot / .eswine.ot (e.g. ageotinfhost1.eswines.ot, camotinfmgmt.eswines.ot)
  * Branch / Standalone: .corp.local or shortname (no FQDN)
- Virtual Machine (VM) Hostname: {rules.get('vm_host', '')}

4. ESXi Network Descriptions:
- ESXi Physical Uplink Description: {rules.get('esxi_uplink', '')}
- ESXi Port Group Teaming Description: {rules.get('esxi_portgroup', '')}
- ESXi VMkernel Description: {rules.get('esxi_vmkernel', '')}

5. NetBox Hardware YAML Schema:
- {rules.get('netbox_server_yaml', '')}
"""