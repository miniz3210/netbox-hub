import json
import os
from datetime import datetime
from config.constants import RULES_FILE
from core.session_manager import SessionStateManager as SSM


BASELINE_RULES_FILE = "data/naming_rules_baseline.json"
BASELINE_META_FILE = "data/baseline_meta.json"


DEFAULT_PARSING_PRESETS = [
    {
        "id": "general_default",
        "name": "General (Default)",
        "instructions": "",
    },
    {
        "id": "vmware_esxi",
        "name": "VMware ESXi",
        "instructions": "[PLATFORM ARCHITECTURE: VMWARE ESXI]\n1. HIERARCHY & CONTAINER ROLES:\n   - Root Container (parent): Any entity labeled 'vSwitchX', 'StandardSwitch:X', or 'dvSwitchX'.\n   - Sub-Network Profile (purpose): Functional labels (e.g., 'Management Network', 'vMotion', 'VM Network', 'iSCSI*') are Port Groups/Purposes, NEVER the parent container.\n   - Endpoints (interface): Physical network adapters ('vmnic*') and VMkernel ports ('vmk*').\n\n2. PORTGROUP & PURPOSE ENFORCEMENT (TOKEN BAG PURITY - ELIMINATE DOUBLE-PREFIX):\n   - Port Group / Service Purpose MUST be captured for every network entity and NEVER omitted.\n   - For Physical Uplinks (vmnic*):\n     * The 'purpose' field MUST contain ONLY: <PortGroup/Service> Active Uplink or <PortGroup/Service> Standby Uplink (NEVER 'Primary').\n     * Examples: 'Management Network Active Uplink', 'iSCSI01 Active Uplink', 'vMotion Active Uplink'.\n     * STRICT PROHIBITION: NEVER prefix 'purpose' with '{interface} - {parent}' or 'vmnicX - vSwitchY'. The downstream renderer automatically handles interface and parent prefixes; adding them into 'purpose' causes critical double-prefix duplication errors.\n   - For VMkernel ports (vmk*):\n     * The 'purpose' field MUST capture ONLY: <Purpose/Service> (<vSwitch>)\n     * Examples: 'Management Network (vSwitch0)', 'iSCSI01 (vSwitch01)', 'vMotion (vSwitch1)'.\n     * STRICT PROHIBITION: NEVER prefix with 'vmkX - vSwitchY'.\n\n3. MULTI-ADAPTER UPLINK AGGREGATION (GENERIC TOPOLOGY INHERITANCE):\n   - In VMware ESXi topology diagrams, a single vSwitch frequently binds multiple physical uplinks (e.g., active/standby teaming or multi-adapter trunks).\n   - ALL physical network adapters (vmnic*) visually contained within a switch's 'Physical Adapters' box or connected directly to that switch MUST strictly inherit that switch as their 'parent'.\n   - NEVER drop an adapter's parent or treat it as standalone if it is visually grouped under any vSwitch in the topology diagram.\n\n4. CONFLICT RESOLUTION: TOPOLOGY DIAGRAM OVERRIDES STANDALONE PROPERTIES:\n   - When cross-referencing between topology views ('Virtual switches') and individual adapter properties ('Physical network adapters'):\n     * Visual switch topology ALWAYS takes absolute precedence over isolated properties showing 'No networks', 'Unconnected', or blank switch columns.\n     * If an adapter is bound to a vSwitch in a topology diagram, it is strictly ACTIVE or STANDBY for that switch. NEVER mark its parent as empty and NEVER set purpose to 'Unassigned' based on an isolated property sheet.\n   - Cross-Screenshot Reconciliation: Match entries across screenshots by vmnic name, ensuring PCI slot, driver, speed, and parent vSwitch are fully populated.",
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
            "   - Normalize interface names to consistent lowercase identifiers (e.g., vmnic, eth, bond, vmk, eth0).\n"
            "   - Map functional roles/purposes (management, storage, vMotion, etc.) from descriptions.\n"
            "   - For multi-adapter setups, group all physical uplinks under their parent switch via topological inheritance.\n"
            "   - Prefer topology diagram views over isolated status sheets when conflicts arise.\n"
            "   - NEVER omit the 'purpose' field for any network entity. Every interface, adapter, or port MUST have a populated purpose.\n"
            "   - 'purpose' FIELD PURITY: The 'purpose' value MUST contain ONLY the functional role / port group name and state. "
            "NEVER self-format with '{interface} - {parent}' or similar prefixes inside the 'purpose' value itself — "
            "the output template handles those prefixes automatically; embedding them in 'purpose' causes double-prefix duplication.\n"
            "   - GENERIC TOPOLOGICAL PARENT BINDING: When an entity appears bound in a topology diagram, "
            "its 'parent' MUST reflect that binding even if isolated property sheets show 'No networks', 'Unconnected', or blank values. "
            "Never default to empty parent or 'Unassigned' when topology confirms a connection.\n"
        )
        notes_section = (
            f"\n3. ADDITIONAL NOTES / CUSTOM REQUIREMENTS:\n   {notes}\n"
            if notes.strip() else "\n3. ADDITIONAL NOTES / CUSTOM REQUIREMENTS:\n"
            "   (Minimal notes detected. Infer industry-standard networking architecture for this platform "
            "and generate comprehensive, production-grade parsing rules autonomously. "
            "Do not ask clarifying questions. Apply standard topological invariants and purpose-field purity rules "
            "appropriate to the platform's known networking stack.)\n"
        )
        output_section = (
            "\n4. OUTPUT FORMAT:\n"
            "   Return the complete platform-specific topology parsing ruleset as plain English instructions. "
            "Match the template structure shown below exactly:\n"
            "   [PLATFORM ARCHITECTURE: <PLATFORM>]\n"
            "   1. HIERARCHY & CONTAINER ROLES\n"
            "   2. PORTGROUP & PURPOSE ENFORCEMENT (TOKEN BAG PURITY)\n"
            "   3. MULTI-ADAPTER UPLINK AGGREGATION (TOPOLOGICAL INHERITANCE)\n"
            "   4. CONFLICT RESOLUTION: TOPOLOGY OVERRIDES STANDALONE\n\n"
            "   Output ONLY the raw ruleset text. Do not ask clarifying questions. "
            "Do not include conversational greetings, markdown framing, or explanatory prose outside the ruleset."
        )
        return f"{platform_section}{dtype_section}{instructions_section}{notes_section}{output_section}"

    def solidify_baseline(self) -> bool:
        try:
            if os.path.exists(RULES_FILE):
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    rules_data = json.load(f)
            else:
                rules_data = {}
            os.makedirs(os.path.dirname(BASELINE_RULES_FILE), exist_ok=True)
            with open(BASELINE_RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(rules_data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            meta = {"timestamp": datetime.now().isoformat()}
            with open(BASELINE_META_FILE, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            return True
        except Exception:
            return False

    def get_baseline_timestamp(self) -> str:
        try:
            if os.path.exists(BASELINE_META_FILE):
                with open(BASELINE_META_FILE, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                return meta.get("timestamp", "Factory Default")
        except Exception:
            pass
        return "Factory Default"

    def restore_from_baseline(self) -> bool:
        try:
            if os.path.exists(BASELINE_RULES_FILE):
                with open(BASELINE_RULES_FILE, "r", encoding="utf-8") as f:
                    baseline_data = json.load(f)
            else:
                baseline_data = {}
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(baseline_data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            SSM.set_naming_rules(baseline_data)
            SSM.refresh_naming_rules()
            return True
        except Exception:
            return False

    def compile_full_system_prompt(self) -> str:
        try:
            from config.naming_rules import export_rules_as_prompt
            if os.path.exists(RULES_FILE):
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    rules = json.load(f)
                return export_rules_as_prompt(rules)
        except Exception:
            pass
        return "# Error compiling system prompt"
