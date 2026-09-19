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
        lines = lines[mfg_idx:] if lines[mfg_idx - 1].strip() == "---" else ["---"] + lines[mfg_idx:]
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
    return text.strip()

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
   u_height: <rack units: 0 for MicroServer/tower/desktop; 1, 2, 4 for standard rack servers>
   is_full_depth: <false for MicroServer/tower/desktop/compact; true only for deep 19" rack chassis>
   airflow: <front-to-rear / passive / rear-to-front>
   weight: <accurate numeric weight in kg>
   weight_unit: kg

3. Hardware Component Accuracy:
   - power-ports:
     * Compact/MicroServer/Tower: Single PSU (e.g. 150W or 200W). Name: 'PSU1' or 'Power Port 1', type: 'iec-60320-c14'.
     * Enterprise Rack Servers (1U/2U): Dual redundant PSUs (e.g. PSU1, PSU2).
   - console-ports:
     * Include ONLY if the physical chassis has a dedicated external Serial/RS-232 (de-9 or RJ-45) management port. Do NOT include for towers/microservers that only have VGA/USB/iLO.
   - interfaces:
     * Count onboard physical NICs accurately from datasheet (e.g. MicroServer Gen8 has EXACTLY 2 physical NICs: GigabitEthernet1, GigabitEthernet2 or NIC1, NIC2 — DO NOT add 4 NICs).
     * Include dedicated Out-Of-Band Management (e.g. name: 'iLO' / 'iDRAC', type: 1000base-t, mgmt_only: true).
   - module-bays:
     * Include only real expansion slots present (e.g. PCIe1 for low-profile slots).

4. Output Restrictions:
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