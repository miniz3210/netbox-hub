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

    cleaned_lines = [l for l in text.splitlines() if not re.match(r"^(Note:|Explanation:|Here is|\*\*Note)", l.strip(), re.I)]
    text = "\n".join(cleaned_lines)
    text = re.sub(r"^```(?:ya?ml)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text.strip())
    
    text = re.sub(r"type:\s*10gbase-x-sfp\b", "type: 10gbase-x-sfpp", text)
    text = re.sub(r"type:\s*1gbase-t\b", "type: 1000base-t", text)
    text = re.sub(r"type:\s*1gbase-x-sfp\b", "type: 1000base-x-sfp", text)
    return text.strip()

def generate_device_yaml(mfg: str, model: str, model_name: str) -> str:
    prompt = f"""
Search official datasheets and generate a complete NetBox Device-Type YAML conforming to standards.
Manufacturer: {mfg}
Model: {model}

CRITICAL RULES:
1. First line MUST be '---'
2. Keys: manufacturer, model, slug, part_number, u_height, is_full_depth, airflow, weight, weight_unit: kg
3. Components: console-ports (Serial/de-9), power-ports (PSU1, PSU2), module-bays (PSU1, PSU2, OCP3, PCIe1, PCIe2, PCIe3)
4. Interfaces: OOB management ONLY (1000base-t, mgmt_only: true) for servers; physical interfaces for switches.
Output ONLY raw YAML.
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
   - 1GbE -> `1000base-t` / `1000base-x-sfp`
5. Interface Naming: {pattern_rule}
Output ONLY raw YAML.
"""
    result = clean_ai_yaml(call_ai(prompt, model_name))
    if "{module}" not in result:
        result = re.sub(r"name:\s*['\"]?(?:(?:Ethernet|Port|eth|GigabitEthernet|Te|Gi)[/_ -]*)?(?:\d+/)?(\d+)['\"]?", r"name: '{module}/Port\1'", result, flags=re.IGNORECASE)
    return result

def generate_rack_yaml(mfg: str, model: str, model_name: str) -> str:
    prompt = f"""
Search official specifications and generate a NetBox Rack-Type YAML.
Manufacturer: {mfg}
Model: {model}
Keys: manufacturer, model, slug, width (19 or 23), u_height, form_factor, starting_unit (default 1).
Output ONLY raw YAML.
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