import json
import os
from config.constants import RULES_FILE
from core.session_manager import SessionStateManager as SSM


DEFAULT_PARSING_PRESETS = [
    {
        "id": "general_default",
        "name": "General (Default)",
        "instructions": "",
    },
    {
        "id": "vmware_esxi",
        "name": "VMware ESXi",
        "instructions": "[PLATFORM ARCHITECTURE: VMWARE ESXI]\n1. HIERARCHY & CONTAINER ROLES:\n   - Root Container (parent): Any entity labeled 'vSwitchX', 'StandardSwitch:X', or 'dvSwitchX'.\n   - Sub-Network Profile (purpose): Functional labels (e.g., 'ManagementNetwork', 'vMotion', 'VM Network', 'iSCSI*') are Port Groups/Purposes, NEVER the parent.\n   - Endpoints (interface): Physical adapters ('vmnic*') and VMkernel ports ('vmk*').\n\n2. MULTI-ADAPTER UPLINK AGGREGATION:\n   - A single vSwitch binds multiple physical uplinks.\n   - ALL physical adapters (vmnic*) listed under a vSwitch MUST inherit that vSwitch as 'parent'.\n\n3. CONFLICT OVERRIDE:\n   - Topology diagram view connections ALWAYS override isolated sheets showing 'No networks'.",
    },
]


class StandardsManager:
    def __init__(self, rules_file: str = RULES_FILE):
        self.rules_file = rules_file
        self.standards = self._load()

    def _load(self) -> dict:
        try:
            if os.path.exists(self.rules_file):
                with open(self.rules_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def save_standards(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.rules_file), exist_ok=True)
            with open(self.rules_file, "w", encoding="utf-8") as f:
                json.dump(self.standards, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            import streamlit as st
            if hasattr(st, "session_state"):
                st.session_state["_cached_naming_rules"] = self.standards
            SSM.set_naming_rules(self.standards.copy())
            return True
        except Exception:
            return False

    def get_parsing_presets(self) -> list:
        presets = self.standards.get("parsing_presets")
        if isinstance(presets, list) and presets:
            return presets
        return list(DEFAULT_PARSING_PRESETS)

    def save_parsing_presets(self, presets: list) -> bool:
        self.standards["parsing_presets"] = presets
        return self.save_standards()

    def get_meta_prompt_template(self, platform: str, data_type: str, notes: str) -> str:
        platform_section = f"[PLATFORM ARCHITECTURE: {platform.upper()}]"
        dtype_section = f"\n1. DATA SOURCE TYPE: {data_type}"
        instructions_section = (
            "\n\n2. TOPOLOGY PARSING RULES:\n"
            "   - Extract all network interfaces, adapters, port groups, and virtual switches.\n"
            "   - Identify parent-container relationships (which interfaces belong to which switch/fabric).\n"
            "   - Normalize interface names (e.g., vmnic, eth, bond, vmk) to consistent lowercase identifiers.\n"
            "   - Map functional roles/purposes (management, storage, vMotion, etc.) from descriptions.\n"
            "   - For multi-adapter setups, group physical uplinks under their parent switch.\n"
            "   - Prefer topology diagram views over isolated status sheets when conflicts arise.\n"
        )
        notes_section = (
            f"\n3. ADDITIONAL NOTES / CUSTOM REQUIREMENTS:\n   {notes}\n"
            if notes.strip() else "\n3. ADDITIONAL NOTES / CUSTOM REQUIREMENTS:\n   (none specified)\n"
        )
        output_section = (
            "\n4. OUTPUT FORMAT:\n"
            "   Return a flat JSON array where each object represents one extracted interface/endpoint with these keys:\n"
            "   - interface (str): the interface name (e.g. vmnic0, vmk0, eth0)\n"
            "   - parent (str): the container it belongs to (e.g. vSwitch0, vmbr0); empty if unassigned\n"
            "   - purpose (str): functional role/description\n"
            "   - speed (str): connection speed if visible\n"
            "   - slot (str): hardware slot if visible\n"
            "   - ip (str): IP/CIDR if assigned\n"
            "   - vlan (str): VLAN tag/ID if applicable\n"
            "   - remote_device (str): peer device name if known\n"
            "   - remote_port (str): peer port if known\n"
            "   Output ONLY the JSON array, no markdown fences or explanations."
        )
        return f"{platform_section}{dtype_section}{instructions_section}{notes_section}{output_section}"
