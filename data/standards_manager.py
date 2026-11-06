import json
import os
import copy
from datetime import datetime
from config.constants import RULES_FILE
from core.session_manager import SessionStateManager as SSM


BASELINE_RULES_FILE = "data/naming_rules_baseline.json"
BASELINE_META_FILE = "data/baseline_meta.json"


DEFAULT_PARSING_PRESETS = {
    "VMware ESXi": {
        "platform": "VMware ESXi",
        "instructions": """[PLATFORM ARCHITECTURE: VMWARE ESXI - PRODUCTION RECONCILIATION]

1. OCR TEXT & TYPO NORMALIZATION:
   - Normalize switch names: convert 'vSwitcho' or 'vSwitcho1' into 'vSwitch0' / 'vSwitch01' (letter 'o'/'O' to digit '0').
   - Strip generic prefixes: 'StandardSwitch:vSwitch0' -> 'vSwitch0', 'Standard Switch: vSwitch02' -> 'vSwitch02'.

2. VISUAL TOPOLOGY INHERITANCE (ABSOLUTE PRECEDENCE):
   - All physical adapters ('vmnic*') visually listed under a switch's 'Physical Adapters' box belong strictly to that switch as their 'parent'.
   - NEVER mark an adapter as unassigned or standalone if it appears connected to a switch in any screenshot.
   - Reconcile PCI slot, driver, speed, and CDP neighbor info from 'Physical adapters' screenshots into the respective vmnic records.

3. MANDATORY ATOMIC JSON ATTRIBUTES (EVERY RECORD MUST HAVE THESE):
   - "interface": Interface identifier (e.g. 'vmnic1', 'vmnic4', 'vmk0').
   - "parent": Connected vSwitch name (e.g. 'vSwitch0', 'vSwitch01', 'vSwitch02', 'vSwitch1'). Leave empty if unassigned.
   - "purpose": Pure functional/service name:
       * For VMkernels (vmk): The exact service name (e.g. 'Management Network', 'iSCSI01', 'iSCSI02', 'vMotion').
       * For Physical Adapters (vmnic):
         - If the vSwitch has a single dedicated purpose (e.g. iSCSI01, iSCSI02, vMotion), inherit that exact name.
         - If the vSwitch hosts multiple services (e.g. vSwitch0 hosts ManagementNetwork and VM Network), set purpose to the primary service or management network (e.g. 'Management Network').
         - Leave strictly empty if the adapter is unassigned / has no parent vSwitch.
         - STRICT PROHIBITION: NEVER append '(vSwitchX)', 'Active Uplink', or 'Standby Uplink' into 'purpose'.
   - "uplink_role": REQUIRED ONLY for physical adapters connected to a vSwitch:
       * Since ESXi topology diagrams do not explicitly print failover status, if an adapter is connected to a vSwitch, set "uplink_role" to "Active Uplink" by default (unless explicitly marked standby).
       * If an adapter is UNASSIGNED / has no parent vSwitch, "uplink_role" MUST be left strictly empty ("").
       * Leave strictly empty for VMkernels ('vmk*') and Port Groups.
   - "slot": Hardware PCIe slot (e.g. '0000:5b:00.0', '0000:08:00.1').
   - "speed": Link speed (e.g. '10 Gbit/s Full', '1 Gbit/s Full').
   - "ip": IPv4 address for vmk interfaces (e.g. '10.27.177.246').

4. ZERO CONCATENATION:
   Keep 'interface', 'parent', 'purpose', and 'uplink_role' strictly isolated into their atomic keys. Downstream templates handle all prefix/suffix formatting.""",
        "export_prompt": """You are an expert infrastructure network engineer specializing in VMware ESXi.
I have attached screenshots of VMware ESXi virtual switch topology and physical network adapter properties.
Please analyze the images and extract the network topology into the standard atomic JSON format matching these exact attributes:
- interface: 'vmnicX' or 'vmkX'
- parent: 'vSwitchX' (or null if unassigned)
- purpose: Pure service name ('Management Network', 'vMotion', 'iSCSI01') without appending switch names or roles
- uplink_role: 'Active Uplink' or 'Standby Uplink' for vmnics connected to a switch; null if unassigned or vmk
- slot: Hardware PCIe slot (e.g. '0000:5b:00.0')
- speed: Link speed (e.g. '10 Gbit/s Full')
- ip: IP address for vmk ports

Output ONLY a JSON object with key "interfaces": array of objects."""
    },
    "Proxmox VE": {
        "platform": "Proxmox VE",
        "instructions": """[PLATFORM ARCHITECTURE: PROXMOX VE]
1. HIERARCHY & BRIDGES:
   - Root bridges: 'vmbr0', 'vmbr1', etc.
   - Bonds: 'bond0', 'bond1', etc.
   - Physical slaves: 'eno1', 'ens18', etc.
2. ATOMIC ATTRIBUTES:
   - "interface": Interface or bridge name.
   - "parent": Connected bridge or bond master.
   - "purpose": Functional service name or VLAN purpose.
   - "ip": CIDR IP address assigned to bridge/interface.""",
        "export_prompt": """You are an expert infrastructure network engineer specializing in Proxmox VE.
I have attached screenshots of Proxmox VE network configuration (/etc/network/interfaces / GUI).
Please extract the Linux Bridge (vmbr), Bond, and physical interfaces into the standard atomic JSON format matching these exact attributes:
- interface: 'vmbrX', 'bondX', or physical NIC ('ensX', 'enoX')
- parent: Connected bridge or master
- purpose: Functional description or VLAN profile
- ip: CIDR IP address if assigned

Output ONLY a JSON object with key "interfaces": array of objects."""
    }
}


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

    def load_baseline(self) -> dict:
        """Return the Global Baseline as a dictionary.

        Reads the same Baseline file used by solidify/restore (BASELINE_RULES_FILE).
        Safely returns ``{}`` when missing or invalid. Does not mutate Current Rules.
        """
        try:
            if os.path.exists(BASELINE_RULES_FILE):
                with open(BASELINE_RULES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
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

    def get_parsing_presets(self):
        presets = self.standards.get("parsing_presets")
        if isinstance(presets, list) and presets:
            return presets
        return {}

    def get_parsing_preset_for_platform(self, platform: str) -> dict:
        """Return the parsing preset dict for a single platform from Current Rules."""
        presets = self.standards.get("topology_parsing_presets")
        if isinstance(presets, dict):
            return presets.get(platform, {})
        return {}

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

    def restore_from_baseline(self) -> dict:
        """Restore the complete Global Baseline into Current Rules.

        Loads through load_baseline(). Returns {} and leaves Current Rules
        unchanged when the Baseline is missing or invalid. Otherwise writes
        the exact Baseline to Current Rules and returns a deep copy of the
        restored Baseline dict. Does not merge DEFAULT_* content or reuse
        stale Current Rules.
        """
        baseline = self.load_baseline()
        if not baseline:
            return {}
        try:
            os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(baseline, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            return copy.deepcopy(baseline)
        except Exception:
            return {}

    def restore_section_from_baseline(self, section: str) -> bool:
        """Restore only ``section`` from Global Baseline into Current Rules.

        Preserves all unrelated Current Rules sections, persists Current Rules,
        and refreshes the session cache. Returns False (no change) when the
        Baseline or section is unavailable.
        """
        try:
            baseline = self.load_baseline()
            if not baseline or section not in baseline:
                return False
            current = {}
            if os.path.exists(RULES_FILE):
                try:
                    with open(RULES_FILE, "r", encoding="utf-8") as f:
                        current = json.load(f)
                except Exception:
                    current = {}
            if not isinstance(current, dict):
                current = {}
            current[section] = copy.deepcopy(baseline[section])
            os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            SSM.set_naming_rules(current)
            SSM.refresh_naming_rules()
            return True
        except Exception:
            return False

    def restore_path_from_baseline(self, path: str, wc_prefixes: tuple = ()) -> dict:
        """Restore a single Baseline path into Current Rules.

        ``path`` may be a top-level rules key (e.g. ``"device_presets"``) or a
        dotted nested path (e.g. ``"hardware_baseline_standards.server"``).

        Returns:
            {"ok": True, "value": <deepcopy of baseline value>}
                Path present in Baseline (even when the value is an empty
                list/dict). Current Rules updated, persisted, session cache
                refreshed, standards_nonce bumped, working copies cleared.
            {"ok": False, "missing": True}
                Path absent from Baseline. No change; no factory fallback.

        The caller is responsible for showing any warning/toast and for calling
        ``st.rerun()``.
        """
        from config.naming_rules import load_naming_rules, save_naming_rules
        import streamlit as st

        baseline = self.load_baseline()

        parts = path.split(".")
        cursor = baseline
        for part in parts:
            if not isinstance(cursor, dict) or part not in cursor:
                return {"ok": False, "missing": True}
            cursor = cursor[part]
        value = copy.deepcopy(cursor)

        current = {}
        if os.path.exists(RULES_FILE):
            try:
                with open(RULES_FILE, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = {}
        if not isinstance(current, dict):
            current = {}

        if len(parts) == 1:
            current[path] = value
        else:
            parent = current
            for part in parts[:-1]:
                parent = parent.setdefault(part, {})
            parent[parts[-1]] = value

        os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        SSM.set_naming_rules(current.copy())
        SSM.refresh_naming_rules()
        st.session_state["standards_nonce"] = st.session_state.get("standards_nonce", 0) + 1

        for prefix in wc_prefixes:
            st.session_state.pop(f"wc_{prefix}", None)
            for k in list(st.session_state.keys()):
                sk = str(k)
                if sk.startswith(f"wc_{prefix}_"):
                    st.session_state.pop(k, None)

        return {"ok": True, "value": value}

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
