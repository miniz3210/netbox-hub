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
    text = re.sub(r"type:\s*10gbase-x-sfp\b", "type: 10gbase-x-sfpp", text)
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
    
    # Ensure u_height is float format
    text = re.sub(r"^u_height:\s*(\d+)$", r"u_height: \1.0", text, flags=re.MULTILINE)
    
    # Ensure YAML starts with --- (NetBox requirement)
    text = text.strip()
    if not text.startswith("---"):
        text = "---\n" + text
    
    return text

def generate_device_yaml(mfg: str, model: str, model_name: str) -> str:
    prompt = f"""
Search official manufacturer specifications and generate a NetBox Device-Type YAML definition.
Manufacturer: {mfg}
Model: {model}

STRICT SPECIFICATION RULES:
1. First line MUST be '---'.
2. Metadata keys:
   manufacturer: {mfg}
   model: <exact model name>
   slug: <mandatory lowercase slug with manufacturer prefix, e.g. {mfg.lower()}-<model-slug>>
   part_number: <hardware part number or clean model SKU>
   u_height: <rack units: 0 for desktop/compact; 1 for standard 1U switch/server>
   is_full_depth: <false for compact devices; true only for deep 19" rack chassis>
   airflow: <front-to-rear / passive / rear-to-front / left-to-right>
   weight: <accurate numeric weight in kg>
   weight_unit: kg

3. DEVICE TYPE DETECTION - Identify the device category first:

   A. NETWORK SWITCHES (e.g., Cisco Catalyst, Huawei S-series, Arista, Juniper EX):
      - Decode model number for port configuration (e.g., "S1720X-32XWR" = 32 ports, X=10G):
        * Look for port count in model name (24, 48, 32, 16, 8)
        * "X" or "XG" prefix typically means 10GbE
        * "G" prefix typically means 1GbE
        * Check for SFP/SFP+/QSFP indicators in model name
      - console_ports: Typically one RJ-45 console port (name: 'console', type: rj-45)
      - power_ports: Based on form factor (desktop switches: 1 PSU; enterprise: 1-2 PSUs)
      - interfaces: 
        * Use exact port count from datasheet
        * Naming convention: GigabitEthernet0/0/N for 1G, XGigabitEthernet0/0/N for 10G (Huawei)
        * Naming convention: GigabitEthernetN for 1G, TenGigabitEthernetN for 10G (Cisco)
        * Port types: 1000base-t (copper 1G), 1000base-x-sfp (SFP 1G), 10gbase-t (copper 10G), 10gbase-x-sfpp (SFP+ 10G), 25gbase-x-sfp28 (SFP28 25G)
        * Include management interface if separate (name: 'MGMT' or 'Management1', type: 1000base-t, mgmt_only: true)

   B. SERVERS (e.g., HP DL360, Dell PowerEdge, Supermicro):
      - power_ports:
        * Compact/MicroServer/Tower: Single PSU (e.g. 150W or 200W). Name: 'PSU1', type: 'iec-60320-c14'.
        * Enterprise Rack Servers (1U/2U): Dual redundant PSUs (e.g. PSU1, PSU2).
      - console_ports:
        * Include ONLY if the physical chassis has a dedicated external Serial/RS-232 (de-9 or RJ-45) management port.
      - interfaces:
        * Count onboard physical NICs accurately from datasheet (e.g. MicroServer Gen8 has EXACTLY 2 NICs).
        * Include dedicated Out-Of-Band Management (e.g. name: 'iLO' / 'iDRAC', type: 1000base-t, mgmt_only: true).
      - module-bays:
        * Include only real expansion slots present (e.g. PCIe1 for low-profile slots).

4. CRITICAL: Research the EXACT specifications from official datasheets. Do NOT guess port counts or types.

5. Output Restrictions:
   - DO NOT invent URLs or include a 'comments' block.
   - Output ONLY raw valid YAML starting with '---'.
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