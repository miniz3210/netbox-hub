import re
from typing import Optional
from core.ai_client import call_ai

def clean_ai_yaml(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    
    code_blocks = re.findall(r"```(?:ya?ml)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if code_blocks:
        for block in reversed(code_blocks):
            if "manufacturer:" in block or "model:" in block or "interfaces:" in block:
                text = block
                break
        else:
            text = code_blocks[-1]
            
    lines = text.strip().splitlines()
    mfg_idx = next((i for i, l in enumerate(lines) if re.match(r"^\s*manufacturer\s*:", l, re.I)), -1)
    if mfg_idx != -1:
        lines = lines[mfg_idx:] if mfg_idx > 0 and lines[mfg_idx - 1].strip() == "---" else ["---"] + lines[mfg_idx:]
        text = "\n".join(lines)
    elif "---" in text:
        parts = text.split("---")
        for part in reversed(parts):
            if "model:" in part or "interfaces:" in part:
                text = "---\n" + part.strip()
                break

    # Strip hallucinated comment URLs, reasoning artifacts, and non-YAML lines
    cleaned_lines = []
    for line in text.splitlines():
        # Preserve the --- header
        if line.strip() == "---":
            cleaned_lines.append(line)
            continue
        if re.match(r"^\s*comments\s*:", line, re.I) or "http://" in line or "https://" in line:
            continue
        if re.match(r"^(Note:|Explanation:|Here is|\*\*Note)", line.strip(), re.I):
            break
        cleaned_lines.append(line)
        
    text = "\n".join(cleaned_lines)
    text = re.sub(r"^```(?:ya?ml)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text.strip())
    
    # NetBox interface type standardization
    text = re.sub(r"type:\s*10gbase-x-sfp\+?\b", "type: 10gbase-x-sfpp", text)
    text = re.sub(r"type:\s*1gbase-t\b", "type: 1000base-t", text)
    text = re.sub(r"type:\s*1gbase-x-sfp\b", "type: 1000base-x-sfp", text)
    
    # Fix hyphenated field names to use underscores (NetBox standard)
    text = re.sub(r"^console-ports:", "console_ports:", text, flags=re.MULTILINE)
    text = re.sub(r"^power-ports:", "power_ports:", text, flags=re.MULTILINE)
    text = re.sub(r"^module-bays:", "module_bays:", text, flags=re.MULTILINE)
    text = re.sub(r"^device-bays:", "device_bays:", text, flags=re.MULTILINE)
    text = re.sub(r"^inventory-items:", "inventory_items:", text, flags=re.MULTILINE)
    text = re.sub(r"^front-ports:", "front_ports:", text, flags=re.MULTILINE)
    text = re.sub(r"^rear-ports:", "rear_ports:", text, flags=re.MULTILINE)
    text = re.sub(r"^mgmt-interfaces:", "interfaces:", text, flags=re.MULTILINE)
    
    # Remove empty arrays/lists that add no value
    text = re.sub(r"^module_bays:\s*\[\]\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^device_bays:\s*\[\]\s*$", "", text, flags=re.MULTILINE)
    
    # Normalize u_height to always have .0 decimal
    text = re.sub(r"^u_height:\s*(\d+)$", r"u_height: \1.0", text, flags=re.MULTILINE)
    text = re.sub(r"^u_height:\s*(\d+)\.0+(\d+)", r"u_height: \1.\2", text, flags=re.MULTILINE)  # Fix 0.00 -> 0.0
    
    # Normalize weight to 2 decimal places if it has decimals, or add .0 if integer
    def normalize_weight(match):
        val = match.group(1)
        if '.' in val:
            # Round to 2 decimal places
            return f"weight: {float(val):.2f}"
        else:
            # Integer weight, add .0
            return f"weight: {val}.0"
    text = re.sub(r"^weight:\s*(\d+(?:\.\d+)?)", normalize_weight, text, flags=re.MULTILINE)
    
    # Normalize boolean values to lowercase
    text = re.sub(r":\s*True\b", ": true", text)
    text = re.sub(r":\s*False\b", ": false", text)
    
    # Standardize console port naming - use 'console' not 'Console' or 'CONSOLE'
    text = re.sub(r"(console_ports:.*?name:\s*)Console\b", r"\1console", text, flags=re.IGNORECASE | re.DOTALL)
    
    # Fix 'DC Power' to 'power' for consistency
    text = re.sub(r"(power_ports:.*?name:\s*)DC Power", r"\1power", text, flags=re.DOTALL)
    text = re.sub(r"(power_ports:.*?name:\s*)Power", r"\1power", text, flags=re.DOTALL)
    
    # Standardize power port types
    text = re.sub(r"type:\s*dc-plug\b", "type: dc-terminal", text)
    
    # Remove duplicate interface sections (mgmt-interfaces that were converted)
    lines = text.split('\n')
    filtered_lines = []
    skip_until_next_section = False
    for i, line in enumerate(lines):
        # If we see a duplicate interfaces: section after already having one, skip it
        if skip_until_next_section:
            # Check if this is a new top-level section (no indentation)
            if line and not line[0].isspace() and ':' in line and line.strip() != '---':
                skip_until_next_section = False
            else:
                continue
        
        filtered_lines.append(line)
        
        # Track if we've already added an interfaces section
        if line.strip().startswith('interfaces:') and i > 0:
            # Check if there's already an interfaces: earlier in the file
            earlier_content = '\n'.join(lines[:i])
            if 'interfaces:' in earlier_content:
                skip_until_next_section = True
                filtered_lines.pop()  # Remove the duplicate interfaces: line
    
    text = '\n'.join(filtered_lines)
    
    # Ensure YAML starts with --- (NetBox requirement)
    text = text.strip()
    if not text.startswith("---"):
        text = "---\n" + text
    
    # Clean up multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    
    return text

def generate_device_yaml(mfg: str, model: str, model_name: str) -> str:
    prompt = f"""
CRITICAL INSTRUCTION: You MUST search for and verify the actual technical specifications from official manufacturer datasheets, spec sheets, or documentation for this specific device model before generating the YAML. Do NOT guess or assume specifications.

Generate a NetBox Device-Type YAML definition for:
Manufacturer: {mfg}
Model: {model}

RESEARCH REQUIREMENTS:
1. Find the official datasheet or product specifications
2. Verify the EXACT wireless standards supported (802.11b/g/n/ac/ax/be)
3. Confirm frequency bands (2.4GHz only, 5GHz only, or dual-band)
4. Check for Bluetooth/BLE capability
5. Verify number of ethernet ports and their speeds
6. Confirm PoE standard (802.3af/at/bt or none)
7. Get accurate weight from specifications
8. Verify physical dimensions and rack units

MANDATORY FIELDS - Include ALL of these fields in this exact order:
---
manufacturer: {mfg}
model: {model}
slug: <lowercase-with-hyphens, format: {mfg.lower()}-<model-slug>>
part_number: {model}
u_height: <number with .0 decimal, e.g., 0.0 for access points/compact, 1.0 for 1U rack>
is_full_depth: <true or false - false for compact/desktop/AP, true for deep rack chassis>
airflow: passive
weight: <numeric value with 2 decimal places, e.g., 1.04>
weight_unit: kg

CONDITIONAL FIELDS - Include only if applicable:
console_ports:
  - name: console
    type: <rj-45 OR de-9 - use rj-45 for modern equipment, de-9 for legacy serial>

power_ports:
  - name: <PSU1 for single, PSU1/PSU2 for dual, or 'power' for generic>
    type: <iec-60320-c14 for servers, dc-terminal for PoE devices, iec-60320-c8 for small devices>
    maximum_draw: <watts as integer, only if PoE-powered or known power draw>
    allocated_draw: <watts as integer, same as maximum_draw>

interfaces:
  - name: <GigabitEthernet0 OR eth0 - use vendor-standard naming>
    type: <1000base-t for RJ-45 gigabit, 10gbase-t for 10G copper, 1000base-x-sfp for SFP>
    poe_mode: <pd for powered devices receiving PoE, pse for switches providing PoE - OMIT if not applicable>
    poe_type: <type1-ieee802.3af OR type2-ieee802.3at OR type3-ieee802.3bt - ONLY if poe_mode is set>
    mgmt_only: <true - ONLY for dedicated management ports, OMIT for regular data ports>

DEVICE-SPECIFIC RULES:

ACCESS POINTS AND WIRELESS DEVICES (e.g., Cisco AIR-, Meraki MR-, Aruba AP-, Ubiquiti UAP-):
- u_height: 0.0 (always zero for APs)
- is_full_depth: false
- airflow: passive
- console_ports: Include ONLY if it has a physical console port (most APs use rj-45)
- power_ports: Include if it has DC jack or can use power adapter. Type: dc-terminal OR iec-60320-c8
  * Add maximum_draw and allocated_draw in watts if PoE-powered (e.g., 13W for 802.3af)
- interfaces - CRITICAL: Research and include ONLY the actual interfaces from the datasheet:
  * Primary data port: name: main OR eth0 OR GigabitEthernet0, type: 1000base-t OR 100base-tx (verify from specs)
  * If PoE-powered, add: poe_mode: pd, poe_type: <verify exact 802.3af/at/bt standard from datasheet>
  * WIRELESS RADIOS - MANDATORY: Verify from datasheet and include based on ACTUAL capabilities:
    
    SINGLE-BAND 2.4GHz ONLY devices (802.11b/g/n Wi-Fi 4):
    - name: wlan0
      type: ieee802.11n
    
    DUAL-BAND devices (2.4GHz + 5GHz):
    - name: wlan0
      type: ieee802.11n (for 2.4GHz radio with 802.11b/g/n)
    - name: wlan1
      type: ieee802.11ac (for 5GHz 802.11ac/Wave2) OR ieee802.11ax (for WiFi 6)
    
    TRI-BAND / WiFi 6E devices (2.4GHz + 5GHz + 6GHz):
    - name: wlan0
      type: ieee802.11ax (2.4GHz WiFi 6)
    - name: wlan1
      type: ieee802.11ax (5GHz WiFi 6)
    - name: wlan2
      type: ieee802.11ax (6GHz WiFi 6E)
    
    BLUETOOTH/BLE - Include ONLY if datasheet confirms BLE capability:
    - name: wlan2 (or wlan3 for tri-band)
      type: ieee802.15.1
      description: Bluetooth Scanning and Beaconing
    
  * DO NOT assume Bluetooth exists - verify from specs
  * DO NOT add 5GHz radio if device is 2.4GHz-only
  * For dual-ethernet APs, include both ports (main, eth1) with correct speeds
  * If separate management port exists, add mgmt_only: true

SWITCHES:
- RESEARCH FIRST: Verify from datasheet the EXACT number and type of ports
- u_height: <verify from specs: 0.0 for desktop, 1.0+ for rack-mount>
- is_full_depth: <verify chassis depth - true for deep switches, false for shallow/desktop>
- weight: <get EXACT weight from datasheet in kg>
- console_ports: Verify if console port exists and type (rj-45 for modern, de-9 for legacy)
- power_ports: 
  * Verify PSU configuration (single PSU, dual redundant PSU1/PSU2, or AC/DC)
  * Type: iec-60320-c14 (standard), iec-60320-c20 (high power), dc-terminal (DC powered)
  * Include maximum_draw if specified in datasheet
- interfaces: 
  * Count EXACT number of ports from datasheet (e.g., 24-port, 48-port)
  * Verify port types: 1000base-t (RJ-45 gigabit), 10gbase-t (10G copper), 1000base-x-sfp (SFP), 10gbase-x-sfpp (SFP+), 25gbase-x-sfp28, 40gbase-x-qsfpp, 100gbase-x-qsfp28
  * Use correct naming: GigabitEthernet1/0/1 format for Cisco, eth0-eth47 for generic
  * For PoE switches: Add poe_mode: pse, poe_type: <type2-ieee802.3at or type3-ieee802.3bt>
  * Verify if management port exists separately (mgmt_only: true)
  * Include all uplink ports with correct type (SFP+, QSFP+, etc.)
  * DO NOT guess port count - verify from actual specs

ROUTERS:
- RESEARCH FIRST: Verify exact interface configuration from datasheet
- u_height: <verify from specs: varies by model>
- weight: <get EXACT weight from datasheet>
- console_ports: Usually rj-45 console port
- power_ports: Verify PSU type and redundancy from specs
- interfaces:
  * Verify EXACT number and type of WAN/LAN ports
  * Common types: 1000base-t, 10gbase-t, 1000base-x-sfp, 10gbase-x-sfpp
  * For routers with SFP slots: Include all SFP/SFP+ ports
  * Verify if separate management port exists
  * DO NOT assume standard configuration - check actual model specs

SERVERS (e.g., HP ProLiant DL380, Dell PowerEdge):
- RESEARCH FIRST: Search for official QuickSpecs or datasheet for the SPECIFIC generation (e.g., DL380 Gen10 vs Gen11)
- u_height: <verify EXACT rack units from specs: DL380 is typically 2.0, DL360 is 1.0>
- is_full_depth: <servers are typically true for full-depth chassis>
- weight: <get EXACT weight from QuickSpecs - servers vary significantly>
- console_ports: Usually NO external serial console on modern servers (post-2010)
- power_ports:
  * Verify PSU configuration: single or redundant (PSU1, PSU2)
  * Type: Typically iec-60320-c14 (standard server), iec-60320-c20 (high-power), or iec-60320-c13
  * Verify maximum_draw from power supply specs (e.g., 500W, 800W)
- interfaces:
  * Onboard NICs: Verify EXACT count from datasheet (typically 2 or 4 onboard ports)
  * Port naming: Use vendor naming (e.g., NIC1, NIC2, NIC3, NIC4)
  * Types: 1000base-t (most common), 10gbase-t (higher-end), 25gbase-x-sfp28, etc.
  * OOB Management: ALWAYS include separate management port:
    - name: iLO (HP/HPE), iDRAC (Dell), IMM/XCC (Lenovo), IPMI (generic)
    - type: 1000base-t
    - mgmt_only: true
  * FlexibleLOM/Mezz slots: If datasheet shows expansion slots, include module_bays
  * DO NOT guess NIC count - verify exact configuration from model specs

STRICT FORMATTING RULES:
1. Use underscore for field names: console_ports, power_ports, module_bays, NOT hyphens
2. Use lowercase true/false for booleans, not True/False
3. Numeric fields: Use .0 decimal for u_height (1.0 not 1), 2 decimals for weight (1.04 not 1)
4. Type values: Must be exact NetBox types (1000base-t NOT 1gbase-t, 10gbase-x-sfpp NOT 10gbase-x-sfp+)
5. Slug format: All lowercase, hyphens only, must start with manufacturer prefix
6. DO NOT include comments, URLs, or explanatory text
7. DO NOT include empty arrays like module_bays: [] unless there are actual bays

CONSISTENCY REQUIREMENTS:
- Always include weight as a decimal number (e.g., 1.04, not 1.0 or 1)
- Console port type: Use rj-45 for modern equipment (post-2005), de-9 for older legacy
- Management interfaces: ONLY mark as mgmt_only: true if it's a separate OOB management port
- PoE fields: Include poe_mode and poe_type ONLY for devices that receive or provide PoE

Example output for Cisco Meraki MR33 (Dual-band WiFi 5 AP with Bluetooth):
---
manufacturer: Cisco
model: Meraki MR33
slug: cisco-meraki-mr33
part_number: MR33-HW
u_height: 0.0
is_full_depth: false
airflow: passive
weight: 0.37
weight_unit: kg
interfaces:
  - name: main
    type: 1000base-t
    poe_mode: pd
    poe_type: type1-ieee802.3af
  - name: wlan0
    type: ieee802.11n
  - name: wlan1
    type: ieee802.11ac
  - name: wlan2
    type: ieee802.15.1
    description: Bluetooth Scanning and Beaconing

Example output for HPE ProLiant DL380 Gen10 (2U Server):
---
manufacturer: HPE
model: ProLiant DL380 Gen10
slug: hpe-proliant-dl380-gen10
part_number: DL380-GEN10
u_height: 2.0
is_full_depth: true
airflow: front-to-rear
weight: 17.20
weight_unit: kg
console_ports:
  - name: console
    type: rj-45
power_ports:
  - name: PSU1
    type: iec-60320-c14
    maximum_draw: 800
    allocated_draw: 800
  - name: PSU2
    type: iec-60320-c14
    maximum_draw: 800
    allocated_draw: 800
interfaces:
  - name: NIC1
    type: 1000base-t
  - name: NIC2
    type: 1000base-t
  - name: NIC3
    type: 1000base-t
  - name: NIC4
    type: 1000base-t
  - name: iLO
    type: 1000base-t
    mgmt_only: true

Example output for Cisco Catalyst 2960X-48FPS-L (48-port PoE+ Switch):
---
manufacturer: Cisco
model: Catalyst 2960X-48FPS-L
slug: cisco-catalyst-2960x-48fps-l
part_number: WS-C2960X-48FPS-L
u_height: 1.0
is_full_depth: false
airflow: front-to-rear
weight: 5.90
weight_unit: kg
console_ports:
  - name: console
    type: rj-45
power_ports:
  - name: PSU1
    type: iec-60320-c14
    maximum_draw: 740
    allocated_draw: 740
interfaces:
  - name: GigabitEthernet1/0/1
    type: 1000base-t
    poe_mode: pse
    poe_type: type2-ieee802.3at
  - name: GigabitEthernet1/0/2
    type: 1000base-t
    poe_mode: pse
    poe_type: type2-ieee802.3at
  - name: GigabitEthernet1/0/3
    type: 1000base-t
    poe_mode: pse
    poe_type: type2-ieee802.3at
  - name: GigabitEthernet1/0/4
    type: 1000base-t
    poe_mode: pse
    poe_type: type2-ieee802.3at

Output ONLY the YAML. Start with --- and include NO explanations or markdown.
"""
    return clean_ai_yaml(call_ai(prompt, model_name))

def generate_module_yaml(mfg: str, model: str, part_num: str, model_name: str, ref_pattern: Optional[str] = None) -> str:
    pattern_rule = f"MUST strictly use: `name: '{ref_pattern}'`" if ref_pattern else "MUST strictly use: `name: '{module}/Port1'`, `name: '{module}/Port2'`, etc. NEVER omit the literal '{module}' token."
    prompt = f"""
Search official datasheets and generate a NetBox Module-Type YAML definition.
Manufacturer: {mfg}
Model: {model}
Part Number: {part_num}

CRITICAL RULES:
1. First line MUST be '---'
2. Keys: manufacturer, model, part_number, description
3. Do NOT include u_height or is_full_depth.
4. Exact NetBox Interface Types:
   - RJ-45 10GbE -> `10gbase-t`
   - SFP+ 10GbE -> `10gbase-x-sfpp`
   - SFP28 25GbE -> `25gbase-x-sfp28`
   - 1GbE RJ-45 / SFP -> `1000base-t` / `1000base-x-sfp`
5. Interface Naming: {pattern_rule}
6. DO NOT invent URLs. DO NOT output 'comments'. Output ONLY raw valid YAML.
"""
    result = clean_ai_yaml(call_ai(prompt, model_name))
    if "{module}" not in result:
        result = re.sub(
            r"name:\s*['\"]?(?:(?:Ethernet|Port|eth|GigabitEthernet|Te|Gi)[/_ -]*)?(?:\d+/)?(\d+)['\"]?",
            r"name: '{module}/Port\1'",
            result,
            flags=re.IGNORECASE
        )
    return result

def generate_rack_yaml(mfg: str, model: str, model_name: str) -> str:
    prompt = f"""
Search official specifications and generate a NetBox Rack-Type YAML.
Manufacturer: {mfg}
Model: {model}

CRITICAL RULES:
1. First line MUST be '---'
2. Keys: manufacturer, model, slug, width (19 or 23), u_height, form_factor, starting_unit (default 1)
3. DO NOT invent URLs. DO NOT output 'comments'. Output ONLY raw valid YAML.
"""
    return clean_ai_yaml(call_ai(prompt, model_name))

def generate_placeholder_svg(mfg: str, model: str, u_height: int = 1, view: str = "front") -> str:
    height_px = max(40, u_height * 40)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 {height_px}" width="440" height="{height_px}">
  <rect width="440" height="{height_px}" fill="#1e293b" stroke="#475569" stroke-width="2" rx="4"/>
  <rect x="10" y="5" width="420" height="{height_px - 10}" fill="#0f172a" rx="2"/>
  <text x="220" y="{height_px / 2 + 4}" fill="#94a3b8" font-family="sans-serif" font-size="12" text-anchor="middle">
    [{mfg}] {model} ({view.upper()} - {u_height}U Generated)
  </text>
</svg>"""