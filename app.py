import os
import re
import io
import sys
import json
import fnmatch
import difflib
import logging
import zipfile
import requests
import streamlit as st
import pandas as pd
from typing import Optional, Dict, List, Tuple

APP_VERSION = "v1.7.3"

# --- Logging Configuration ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logger = logging.getLogger("netbox-hub")

# --- Page Configuration ---
st.set_page_config(
    page_title="NetBox Universal Library Hub",
    page_icon="⚡",
    layout="wide"
)

GITHUB_REPO = "netbox-community/devicetype-library"
BRANCH = "master"
RULES_FILE = "naming_rules.json"

# --- Provider Keys & Dynamic Model Loading from .env ---
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
groq_models_env = os.getenv("GROQ_MODELS", os.getenv("GROQ_MODEL", "openai/gpt-oss-120b, qwen/qwen3.6-27b"))
GROQ_MODELS = [m.strip() for m in groq_models_env.split(",") if m.strip()]

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
openrouter_models_env = os.getenv("OPENROUTER_MODELS", os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"))
OPENROUTER_MODELS = [m.strip() for m in openrouter_models_env.split(",") if m.strip()]

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()

AVAILABLE_PROVIDERS = []
if GROQ_KEY:
    for model in GROQ_MODELS:
        AVAILABLE_PROVIDERS.append(f"Groq ({model})")

if OPENROUTER_KEY:
    for model in OPENROUTER_MODELS:
        AVAILABLE_PROVIDERS.append(f"OpenRouter ({model})")

if OLLAMA_URL:
    AVAILABLE_PROVIDERS.append(f"Local Ollama ({OLLAMA_MODEL})")

if GEMINI_KEY:
    AVAILABLE_PROVIDERS.append(f"Google Gemini ({GEMINI_MODEL})")

# --- Smart Site Code Generator ---
def compute_suggested_site_code(location_name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z\s]", "", location_name).strip()
    if not cleaned:
        return "SITE"
    words = cleaned.split()
    if len(words) >= 2:
        part1 = words[0][:2]
        part2 = words[1][:2]
        return (part1 + part2).upper()
    elif len(words) == 1:
        w = words[0]
        return w[:4].upper() if len(w) >= 4 else w.upper()
    return "SITE"

# --- Interface Name Normalizer ---
def normalize_port_shortname(port_name: str) -> str:
    p = port_name.strip()
    replacements = [
        (r"^TenGigabitEthernet", "Te"),
        (r"^TenGigE", "Te"),
        (r"^TenG", "Te"),
        (r"^GigabitEthernet", "Gi"),
        (r"^GigE", "Gi"),
        (r"^FastEthernet", "Fa"),
        (r"^TwentyFiveGigE", "Twe"),
        (r"^TwentyFiveGigabitEthernet", "Twe"),
        (r"^FortyGigabitEthernet", "Fo"),
        (r"^HundredGigE", "Hu"),
        (r"^HundredGigabitEthernet", "Hu"),
        (r"^Ethernet", "Eth"),
        (r"^Management", "Mgmt"),
    ]
    for pattern, replacement in replacements:
        if re.search(pattern, p, re.IGNORECASE):
            return re.sub(pattern, replacement, p, flags=re.IGNORECASE)
    return p

# --- Persistent Naming Rules Store ---
DEFAULT_RULES = {
    "branch_switch": "SW<Country><State><Site><Zone><Seq>-<StackID> (e.g. SWUKBRIS01-0, SWAUSAROFLBOT01-0)",
    "branch_ap": "WAP<Country><State><Site><Seq> (e.g. WAPUKBRIS01, WAPAUSARF01)",
    "branch_security": "FW<Country><State><Site><Vendor><Seq> / ION<Country><State><Site><Seq> (e.g. FWAUBERPA01, IONAUSABRS01)",
    "switch_uplink_desc_local": "to <Remote_Device>_<Remote_Port_Short> [<Role>]",
    "switch_uplink_desc_remote": "to <Local_Device>_<Local_Port_Short> [<Role>]",
    "switch_access_desc": "<VLAN_Name> - <Host/Device>_<Port>",
    "firewall_interface": "<Role/Zone>_<VLAN_ID>",
    "esxi_host": "<site_prefix>esx<number>.<domain> (e.g. pwsesx001.eswine.adds, ageotinfhost1.eswines.ot)",
    "vm_host": "<site_prefix><role><seq> (Roles: cvi=Core/Virt, afs=App/File, sani=Storage, vlab=Test)",
    "esxi_uplink": "<vmnicX> - <vSwitch> Active Uplink / Standby Uplink",
    "esxi_portgroup": "<vSwitch> (<vmnicX>, <vmnicY> Active [/ <vmnicZ> Standby])",
    "esxi_vmkernel": "<Purpose/Service> (<vSwitch>)",
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

1. Network Devices:
- Switch Hostname: {rules.get('branch_switch', '')}
- Wireless AP Hostname: {rules.get('branch_ap', '')}
- Firewall / Security Hostname: {rules.get('branch_security', '')}
- Switch Uplink Description (Local): {rules.get('switch_uplink_desc_local', '')}
- Switch Uplink Description (Remote): {rules.get('switch_uplink_desc_remote', '')}
- Switch Access Port Description: {rules.get('switch_access_desc', '')}
- Firewall Interface Description: {rules.get('firewall_interface', '')}

2. Hypervisors & Virtual Machines:
- ESXi Hostname: {rules.get('esxi_host', '')}
- VM Hostname: {rules.get('vm_host', '')}
- ESXi Physical Uplink Description: {rules.get('esxi_uplink', '')}
- ESXi Port Group Teaming Description: {rules.get('esxi_portgroup', '')}
- ESXi VMkernel Description: {rules.get('esxi_vmkernel', '')}

3. NetBox Hardware YAML Schema:
- {rules.get('netbox_server_yaml', '')}
"""

def get_active_naming_context() -> str:
    rules = load_naming_rules()
    return export_rules_as_prompt(rules)

# --- 1. Global GitHub Asset Indexer ---
@st.cache_data(ttl=3600, show_spinner="Indexing all repository asset types from GitHub...")
def get_repo_catalog() -> Dict[str, List[str]]:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{BRANCH}?recursive=1"
    catalog = {
        "device_types": [],
        "module_types": [],
        "rack_types": [],
        "elevation_images": [],
        "module_images": [],
        "manufacturers": set()
    }
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            for item in res.json().get("tree", []):
                path = item["path"]
                parts = path.split("/")
                if len(parts) >= 3 and parts[0] in ["device-types", "module-types", "rack-types"]:
                    catalog["manufacturers"].add(parts[1])

                if path.startswith("device-types/") and path.endswith((".yaml", ".yml")):
                    catalog["device_types"].append(path)
                elif path.startswith("module-types/") and path.endswith((".yaml", ".yml")):
                    catalog["module_types"].append(path)
                elif path.startswith("rack-types/") and path.endswith((".yaml", ".yml")):
                    catalog["rack_types"].append(path)
                elif path.startswith("elevation-images/") and path.lower().endswith((".png", ".svg", ".jpg")):
                    catalog["elevation_images"].append(path)
                elif path.startswith("module-images/") and path.lower().endswith((".png", ".svg", ".jpg")):
                    catalog["module_images"].append(path)
    except Exception as e:
        logger.error(f"Error fetching catalog from GitHub: {e}")
        st.error(f"Error fetching catalog from GitHub: {e}")
    
    catalog["manufacturers"] = sorted(list(catalog["manufacturers"]))
    return catalog

def wildcard_match(pattern: str, target: str) -> bool:
    p = pattern.strip().lower()
    t = target.strip().lower()
    if not p:
        return True

    glob_p = p if any(c in p for c in ["*", "?", "["]) else f"*{p}*"
    if fnmatch.fnmatch(t, glob_p):
        return True

    clean_p = re.sub(r"[^a-zA-Z0-9]", "", p)
    clean_t = re.sub(r"[^a-zA-Z0-9]", "", t)
    if clean_p and clean_p in clean_t:
        return True

    words = re.findall(r"[a-zA-Z0-9]+", t)
    initials = "".join(w[0] for w in words).lower()
    if clean_p and (clean_p == initials or clean_p in initials):
        return True

    return False

def get_canonical_manufacturer(user_input: str, mfg_list: List[str]) -> str:
    cleaned = user_input.strip()
    if not cleaned:
        return user_input

    for mfg in mfg_list:
        if wildcard_match(cleaned, mfg):
            return mfg

    close = difflib.get_close_matches(user_input, mfg_list, n=1, cutoff=0.5)
    if close:
        return close[0]

    return user_input

def search_catalog_wildcard(file_list: List[str], manufacturer_query: str, model_query: str) -> List[str]:
    mfg_q = manufacturer_query.strip().lower()
    model_q = model_query.strip().lower()

    primary_matches = []
    secondary_matches = []

    for path in file_list:
        parts = path.split("/")
        r_mfg = parts[1].lower() if len(parts) >= 3 else ""
        r_file = parts[-1].lower()
        r_file_noext = re.sub(r"\.(yaml|yml|png|svg|jpg)$", "", r_file)

        mfg_hit = wildcard_match(mfg_q, r_mfg) if mfg_q else True
        model_hit = wildcard_match(model_q, r_file_noext) if model_q else True

        if mfg_hit and model_hit:
            primary_matches.append(path)
        elif model_hit and len(model_q) >= 3:
            secondary_matches.append(path)

    combined = []
    for item in primary_matches + secondary_matches:
        if item not in combined:
            combined.append(item)

    return combined

def fetch_raw_content(path: str, binary: bool = False):
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{BRANCH}/{path}"
    res = requests.get(raw_url, timeout=10)
    if res.status_code == 200:
        return res.content if binary else res.text
    return None

def extract_reference_interface_pattern(content: Optional[str]) -> Optional[str]:
    if not content:
        return None
    match = re.search(r"-\s+name:\s*['\"]?([^'\"\n\r]+)['\"]?", content)
    if match:
        name_sample = match.group(1).strip()
        if "{module}" in name_sample:
            return name_sample
    return None

# --- 2. AI Multi-Engine Core Router ---
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
    mfg_idx = -1
    for idx, line in enumerate(lines):
        if re.match(r"^\s*manufacturer\s*:", line, flags=re.IGNORECASE):
            mfg_idx = idx
            break
            
    if mfg_idx != -1:
        if mfg_idx > 0 and lines[mfg_idx - 1].strip() == "---":
            lines = lines[mfg_idx - 1:]
        else:
            lines = ["---"] + lines[mfg_idx:]
        text = "\n".join(lines)
    else:
        if "---" in text:
            parts = text.split("---")
            for part in reversed(parts):
                if "model:" in part or "interfaces:" in part:
                    text = "---\n" + part.strip()
                    break

    cleaned_lines = []
    for line in text.splitlines():
        if re.match(r"^(Note:|Explanation:|Here is|Let me know|\*\*Note)", line.strip(), flags=re.IGNORECASE):
            break
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    text = re.sub(r"^```(?:ya?ml)?", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```$", "", text.strip())
    
    text = re.sub(r"type:\s*10gbase-x-sfp\b", "type: 10gbase-x-sfpp", text)
    text = re.sub(r"type:\s*1gbase-t\b", "type: 1000base-t", text)
    text = re.sub(r"type:\s*1gbase-x-sfp\b", "type: 1000base-x-sfp", text)
    return text.strip()

def extract_model_from_label(label: str) -> str:
    match = re.search(r"\((.+)\)", label)
    return match.group(1).strip() if match else label

def call_ai(prompt: str, selected_provider: str) -> str:
    naming_context = get_active_naming_context()
    system_msg = (
        "You are a strict NetBox hardware YAML specification generator and infrastructure architect. "
        "You MUST verify hardware specifications directly from official manufacturer datasheets. "
        f"Strictly align with these infrastructure conventions:\n{naming_context}\n"
        "Output ONLY valid, raw YAML starting with '---'. Use exact kebab-case hyphenated keys (never underscores). "
        "Do NOT output explanation, reasoning, or text outside the YAML block. "
        "Do NOT invent comments or URLs; omit 'comments' key entirely if no verified official datasheet URL is available."
    )

    try:
        if selected_provider.startswith("Groq"):
            from groq import Groq
            client = Groq(api_key=GROQ_KEY)
            target_model = extract_model_from_label(selected_provider)
            resp = client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            return clean_ai_yaml(resp.choices[0].message.content)

        elif selected_provider.startswith("Google Gemini"):
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=GEMINI_KEY)
            target_model = extract_model_from_label(selected_provider)
            resp = client.models.generate_content(
                model=target_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    tools=[{"google_search": {}}],
                    system_instruction=system_msg
                )
            )
            return clean_ai_yaml(resp.text)

        elif selected_provider.startswith("OpenRouter"):
            from openai import OpenAI
            client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY)
            target_model = extract_model_from_label(selected_provider)
            resp = client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            return clean_ai_yaml(resp.choices[0].message.content)

        elif selected_provider.startswith("Local Ollama"):
            from openai import OpenAI
            client = OpenAI(base_url=OLLAMA_URL, api_key="ollama")
            target_model = extract_model_from_label(selected_provider)
            resp = client.chat.completions.create(
                model=target_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            return clean_ai_yaml(resp.choices[0].message.content)

        else:
            st.error("No valid AI provider selected.")
            st.stop()

    except Exception as e:
        logger.error(f"Error from {selected_provider}: {str(e)}")
        st.error(f"❌ Generation Failed ({selected_provider}): {str(e)}")
        st.stop()

def parse_prompt_to_rules(prompt_text: str, provider: str) -> Dict[str, str]:
    extract_prompt = f"""
Analyze the following natural language infrastructure naming standards prompt and convert it into a JSON object matching this schema:
{{
  "branch_switch": "...",
  "branch_ap": "...",
  "branch_security": "...",
  "switch_uplink_desc_local": "...",
  "switch_uplink_desc_remote": "...",
  "switch_access_desc": "...",
  "firewall_interface": "...",
  "esxi_host": "...",
  "vm_host": "...",
  "esxi_uplink": "...",
  "esxi_portgroup": "...",
  "esxi_vmkernel": "...",
  "netbox_server_yaml": "..."
}}

Input Prompt:
{prompt_text}

Output ONLY the raw JSON block without markdown fences or conversational text.
"""
    raw_res = call_ai(extract_prompt, provider)
    clean_json = re.sub(r"^```(?:json)?|```$", "", raw_res.strip(), flags=re.IGNORECASE).strip()
    return json.loads(clean_json)

def generate_device_yaml(mfg: str, model: str, provider: str) -> str:
    prompt = f"""
Search official datasheets and generate a complete NetBox Device-Type YAML conforming to user infrastructure standards.
Manufacturer: {mfg}
Model: {model}

CRITICAL INFRASTRUCTURE RULES:
1. First line MUST be '---'
2. Top-level metadata keys:
   manufacturer: {mfg}
   model: <exact model name>
   slug: <lowercase-slug>
   part_number: <hardware part number if known, otherwise duplicate model>
   u_height: <rack units e.g. 1, 2, 4>
   is_full_depth: true
   airflow: <front-to-rear / rear-to-front / passive / side-to-rear>
   weight: <number e.g. 35.0>
   weight_unit: kg

3. Component Blocks (MANDATORY STANDARD):
   console-ports:
     - name: Serial
       type: de-9
   power-ports:
     - name: PSU1
       type: iec-60320-c14
     - name: PSU2
       type: iec-60320-c14
   module-bays:
     - name: PSU1
       position: 'PSU1'
     - name: PSU2
       position: 'PSU2'
     - name: OCP3
       position: 'OCP3'
     - name: PCIe1
       position: 'PCIe1'
     - name: PCIe2
       position: 'PCIe2'
     - name: PCIe3
       position: 'PCIe3'

4. Interfaces:
   - For standalone Server / Appliance Chassis: List ONLY the out-of-band management interface (e.g. iLO / IPMI / iDRAC) with `type: 1000base-t` and `mgmt_only: true`.
   - For Network Switches / Routers: List physical network interfaces.

Output ONLY valid, raw YAML starting with '---'.
"""
    return call_ai(prompt, provider)

def generate_module_yaml(mfg: str, model: str, part_num: str, provider: str, ref_pattern: Optional[str] = None) -> str:
    if ref_pattern:
        pattern_instruction = f"""
- Interface Naming Rule:
    - MUST follow this pattern: `{ref_pattern}`
    - Always keep '{{module}}' intact.
"""
    else:
        pattern_instruction = """
- Interface Naming Rule (Strict Standard):
    - MUST strictly use: `name: '{module}/Port1'`, `name: '{module}/Port2'`, `name: '{module}/Port3'`, etc.
    - NEVER replace '{module}' with vendor or model names.
"""

    prompt = f"""
Search official manufacturer datasheets and generate a NetBox Module-Type YAML.
Manufacturer: {mfg}
Model: {model}
Part Number: {part_num}

CRITICAL SCHEMA RULES:
- First line MUST be '---'
- Required Keys:
    manufacturer: {mfg}
    model: <exact clean SKU/model name>
    part_number: <exact part number>
    description: '<Clear hardware overview>'
- Do NOT include 'u_height' or 'is_full_depth'.
- Valid NetBox Interface Types: 10gbase-t, 10gbase-x-sfpp, 25gbase-x-sfp28, 1000base-t, 1000base-x-sfp
{pattern_instruction}

Output ONLY valid, raw YAML starting with '---'.
"""
    result = call_ai(prompt, provider)
    if not ref_pattern:
        result = re.sub(
            r"name:\s*['\"]?(?:.+?[/_-])?(?:Port|eth|LAN|mgmt|Ethernet)?\s*(\d+)['\"]?",
            r"name: '{module}/Port\1'",
            result,
            flags=re.IGNORECASE
        )
    return result

def generate_rack_yaml(mfg: str, model: str, provider: str) -> str:
    prompt = f"""
Search official specifications and generate a NetBox Rack-Type YAML.
Manufacturer: {mfg}
Model: {model}

Schema Rules:
- First line MUST be '---'
- Keys: manufacturer, model, slug, width (19 or 23), u_height, form_factor, starting_unit (default 1)
Output ONLY raw YAML.
"""
    return call_ai(prompt, provider)

def generate_placeholder_svg(mfg: str, model: str, u_height: int = 1, view: str = "front") -> str:
    height_px = max(40, u_height * 40)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 {height_px}" width="440" height="{height_px}">
  <rect width="440" height="{height_px}" fill="#1e293b" stroke="#475569" stroke-width="2" rx="4"/>
  <rect x="10" y="5" width="420" height="{height_px - 10}" fill="#0f172a" rx="2"/>
  <text x="220" y="{height_px / 2 + 4}" fill="#94a3b8" font-family="sans-serif" font-size="12" text-anchor="middle">
    [{mfg}] {model} ({view.upper()} - {u_height}U Generated)
  </text>
</svg>"""

# --- 3. UI Layout ---
st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Images | Excel Engine | Naming Standards")

catalog = get_repo_catalog()

with st.sidebar:
    st.header("⚙️ AI Engine Selection")
    if AVAILABLE_PROVIDERS:
        active_provider = st.selectbox("Active AI Model", AVAILABLE_PROVIDERS)
        st.success(f"Selected: `{active_provider}`")
    else:
        active_provider = None
        st.warning("No AI API keys configured. Only official GitHub library lookups are enabled.")

    st.markdown(
        f"""
        <style>
            .version-footer {{
                position: fixed;
                bottom: 15px;
                left: 15px;
                background-color: rgba(30, 41, 59, 0.85);
                color: #94a3b8;
                padding: 4px 10px;
                border-radius: 6px;
                font-size: 0.80rem;
                font-family: monospace;
                border: 1px solid rgba(71, 85, 105, 0.4);
                z-index: 999;
                letter-spacing: 0.5px;
            }}
        </style>
        <div class="version-footer">
            ⚡ NetBox Hub {APP_VERSION}
        </div>
        """,
        unsafe_allow_html=True
    )

t1, t2, t3, t4, t5, t6, t7 = st.tabs([
    "🖥️ Device Types", 
    "🧩 Module Types", 
    "🗄️ Rack Types", 
    "🖼️ Images (Elevation & Module)", 
    "📊 Batch Excel Engine",
    "🏷️ Naming Generator",
    "📖 Naming Standards Context"
])

# --- Tab 1: Device Types ---
with t1:
    col1, col2 = st.columns([1, 1])
    with col1:
        d_mfg_raw = st.text_input("Manufacturer", placeholder="e.g., HP, Cisco, Dell, Intel (Wildcards: *intel*, *cis*)", key="d_mfg")
        d_model = st.text_input("Device Model", placeholder="e.g., *dl360*, PowerEdge R750", key="d_mod")
        d_mfg = get_canonical_manufacturer(d_mfg_raw, catalog["manufacturers"]) if d_mfg_raw else ""

        selected_dev_choice = None
        if d_mfg_raw or d_model:
            similar_devs = search_catalog_wildcard(catalog["device_types"], d_mfg_raw, d_model)
            cross_mods = search_catalog_wildcard(catalog["module_types"], d_mfg_raw, d_model)
            all_official_matches = similar_devs + cross_mods

            if d_mfg and d_mfg != d_mfg_raw.strip():
                st.caption(f"ℹ️ Matched manufacturer: `{d_mfg_raw}` ➔ **`{d_mfg}`**")

            if cross_mods and not similar_devs:
                st.info(f"💡 `{d_model}` was matched in official **Module Types** (`{cross_mods[0]}`). Available to load below:")

            if all_official_matches:
                st.success(f"🔍 Found {len(all_official_matches)} matching definition(s) in Official Library:")
                options = all_official_matches + ["✨ Generate Fresh with AI (Auto-Researched)"]
                selected_dev_choice = st.selectbox(
                    "Select library definition or generate with AI:",
                    options,
                    index=0,
                    key="dev_select_box"
                )
            else:
                st.warning("No match found in library. Click below to generate fresh with AI.")
                selected_dev_choice = "✨ Generate Fresh with AI (Auto-Researched)"

        d_search = st.button("Load / Generate Device Type", type="primary", key="btn_dev")

    if d_search and (d_mfg or d_mfg_raw or selected_dev_choice) and d_model and selected_dev_choice:
        if selected_dev_choice and not selected_dev_choice.startswith("✨"):
            parts = selected_dev_choice.split("/")
            effective_mfg = parts[1] if len(parts) >= 3 else (d_mfg if d_mfg else d_mfg_raw)
        else:
            effective_mfg = d_mfg if d_mfg else d_mfg_raw

        with st.spinner("Processing..."):
            if selected_dev_choice.startswith("✨"):
                if not active_provider:
                    st.error("Configure an AI API key in `.env` to enable AI generation.")
                    st.stop()
                content = generate_device_yaml(effective_mfg, d_model, active_provider)
                src = f"🤖 AI Generated ({active_provider})"
            else:
                content = fetch_raw_content(selected_dev_choice, binary=False)
                src = f"✅ Official Repository (`{selected_dev_choice}`)"
        with col2:
            st.markdown(f"**Source:** {src}")
            st.code(content, language="yaml", line_numbers=True)
            st.download_button("📥 Download YAML", content, f"{effective_mfg}_{d_model}.yaml", "text/yaml")

# --- Tab 2: Module Types ---
with t2:
    col1, col2 = st.columns([1, 1])
    with col1:
        m_mfg_raw = st.text_input("Manufacturer", placeholder="e.g., Broadcom, Mellanox, Synology (*broad*, *syno*)", key="m_mfg")
        m_model = st.text_input("Module Name / Part #", placeholder="e.g., *57416*, ConnectX-4", key="m_mod")
        m_mfg = get_canonical_manufacturer(m_mfg_raw, catalog["manufacturers"]) if m_mfg_raw else ""

        selected_mod_choice = None
        discovered_pattern = None

        if m_mfg_raw or m_model:
            similar_mods = search_catalog_wildcard(catalog["module_types"], m_mfg_raw, m_model)
            cross_devs = search_catalog_wildcard(catalog["device_types"], m_mfg_raw, m_model)
            all_mod_matches = similar_mods + cross_devs

            if m_mfg and m_mfg != m_mfg_raw.strip():
                st.caption(f"ℹ️ Matched manufacturer: `{m_mfg_raw}` ➔ **`{m_mfg}`**")

            if cross_devs and not similar_mods:
                st.info(f"💡 `{m_model}` was matched in official **Device Types** (`{cross_devs[0]}`). Available to load below:")

            if all_mod_matches:
                st.success(f"🔍 Found {len(all_mod_matches)} matching module(s) in Official Library:")
                options = all_mod_matches + ["✨ Generate Fresh with AI (Auto-Researched)"]
                selected_mod_choice = st.selectbox(
                    "Select library definition or generate with AI:",
                    options,
                    index=0,
                    key="mod_select_box"
                )
                top_sample = fetch_raw_content(all_mod_matches[0], binary=False)
                discovered_pattern = extract_reference_interface_pattern(top_sample)
            else:
                st.warning("No match found in library. Interface naming will default to {module}/Port1, {module}/Port2.")
                selected_mod_choice = "✨ Generate Fresh with AI (Auto-Researched)"
                discovered_pattern = None

        m_search = st.button("Load / Generate Module Type", type="primary", key="btn_mod")

    if m_search and (m_mfg or m_mfg_raw or selected_mod_choice) and m_model and selected_mod_choice:
        if selected_mod_choice and not selected_mod_choice.startswith("✨"):
            parts = selected_mod_choice.split("/")
            effective_mfg = parts[1] if len(parts) >= 3 else (m_mfg if m_mfg else m_mfg_raw)
        else:
            effective_mfg = m_mfg if m_mfg else m_mfg_raw

        with st.spinner("Processing..."):
            if selected_mod_choice.startswith("✨"):
                if not active_provider:
                    st.error("Configure an AI API key in `.env` to enable AI generation.")
                    st.stop()
                content = generate_module_yaml(effective_mfg, m_model, m_model, active_provider, ref_pattern=discovered_pattern)
                src = f"🤖 AI Generated ({active_provider})"
            else:
                content = fetch_raw_content(selected_mod_choice, binary=False)
                src = f"✅ Official Repository (`{selected_mod_choice}`)"
        with col2:
            st.markdown(f"**Source:** {src}")
            st.code(content, language="yaml", line_numbers=True)
            st.download_button("📥 Download Module YAML", content, f"module_{effective_mfg}_{m_model}.yaml", "text/yaml")

# --- Tab 3: Rack Types ---
with t3:
    col1, col2 = st.columns([1, 1])
    with col1:
        r_mfg_raw = st.text_input("Rack Manufacturer", placeholder="e.g., APC, Eaton, Rittal", key="r_mfg")
        r_model = st.text_input("Rack Model", placeholder="e.g., *NetShelter*", key="r_mod")
        r_mfg = get_canonical_manufacturer(r_mfg_raw, catalog["manufacturers"]) if r_mfg_raw else ""

        selected_rack_choice = None
        if r_mfg_raw or r_model:
            similar_racks = search_catalog_wildcard(catalog["rack_types"], r_mfg_raw, r_model)

            if r_mfg and r_mfg != r_mfg_raw.strip():
                st.caption(f"ℹ️ Matched manufacturer: `{r_mfg_raw}` ➔ **`{r_mfg}`**")

            if similar_racks:
                st.success(f"🔍 Found {len(similar_racks)} matching rack(s) in Official Library:")
                options = similar_racks + ["✨ Generate Fresh with AI (Auto-Researched)"]
                selected_rack_choice = st.selectbox(
                    "Select library definition or generate with AI:",
                    options,
                    index=0,
                    key="rack_select_box"
                )
            else:
                st.warning("No exact match found in library. Click below to generate fresh with AI.")
                selected_rack_choice = "✨ Generate Fresh with AI (Auto-Researched)"

        r_search = st.button("Load / Generate Rack Type", type="primary", key="btn_rack")

    if r_search and (r_mfg or r_mfg_raw or selected_rack_choice) and r_model and selected_rack_choice:
        if selected_rack_choice and not selected_rack_choice.startswith("✨"):
            parts = selected_rack_choice.split("/")
            effective_mfg = parts[1] if len(parts) >= 3 else (r_mfg if r_mfg else r_mfg_raw)
        else:
            effective_mfg = r_mfg if r_mfg else r_mfg_raw

        with st.spinner("Processing..."):
            if selected_rack_choice.startswith("✨"):
                if not active_provider:
                    st.error("Configure an AI API key in `.env` to enable AI generation.")
                    st.stop()
                content = generate_rack_yaml(effective_mfg, r_model, active_provider)
                src = f"🤖 AI Generated ({active_provider})"
            else:
                content = fetch_raw_content(selected_rack_choice, binary=False)
                src = f"✅ Official Repository (`{selected_rack_choice}`)"
        with col2:
            st.markdown(f"**Source:** {src}")
            st.code(content, language="yaml", line_numbers=True)
            st.download_button("📥 Download Rack YAML", content, f"rack_{effective_mfg}_{r_model}.yaml", "text/yaml")

# --- Tab 4: Visual Images (Elevation & Module) ---
with t4:
    col1, col2 = st.columns([1, 2])
    with col1:
        img_cat = st.selectbox("Image Target", ["Elevation Images (Rack Face)", "Module Images"])
        i_mfg_raw = st.text_input("Manufacturer", placeholder="e.g., HP, Cisco, Dell", key="i_mfg")
        i_model = st.text_input("Model Name", placeholder="e.g., *DL360*", key="i_mod")
        i_mfg = get_canonical_manufacturer(i_mfg_raw, catalog["manufacturers"]) if i_mfg_raw else ""
        i_search = st.button("Find / Render Image", type="primary", key="btn_img")

    with col2:
        if i_search and (i_mfg or i_mfg_raw) and i_model:
            target_list = catalog["elevation_images"] if "Elevation" in img_cat else catalog["module_images"]
            matched_images = search_catalog_wildcard(target_list, i_mfg_raw, i_model)
            effective_mfg = i_mfg if i_mfg else i_mfg_raw

            if matched_images:
                st.success(f"Found {len(matched_images)} matching image(s) in Official Library:")
                for img_path in matched_images:
                    raw_data = fetch_raw_content(img_path, binary=True)
                    st.write(f"**Path:** `{img_path}`")
                    st.image(raw_data, caption=img_path.split("/")[-1], use_container_width=True)
                    st.download_button(f"📥 Download {img_path.split('/')[-1]}", raw_data, img_path.split('/')[-1])
            else:
                st.warning("No official image found. Generating a standard vector SVG template:")
                svg_front = generate_placeholder_svg(effective_mfg, i_model, u_height=2, view="front")
                st.image(svg_front, caption="Auto-Generated Front SVG", use_container_width=True)
                st.download_button("📥 Download Vector (.svg)", svg_front, f"{effective_mfg}_{i_model}.front.svg", "image/svg+xml")

# --- Tab 5: Universal Batch Excel Processing ---
with t5:
    st.write("Upload an Excel file with `Category` (`device`, `module`, or `rack`), `Manufacturer`, and `Model`.")
    sample_df = pd.DataFrame([
        {"Category": "device", "Manufacturer": "HP", "Model": "DL360 Gen10"},
        {"Category": "module", "Manufacturer": "Synology", "Model": "E10G21-F2"},
        {"Category": "rack", "Manufacturer": "APC", "Model": "NetShelter SX 42U"}
    ])
    st.download_button("📄 Download Sample Template (Excel)", sample_df.to_csv(index=False).encode('utf-8'), "template.csv", "text/csv")

    batch_file = st.file_uploader("Upload Batch File (.xlsx, .csv)", type=["xlsx", "csv"])
    if batch_file:
        df = pd.read_csv(batch_file) if batch_file.name.endswith(".csv") else pd.read_excel(batch_file)
        st.dataframe(df.head(), use_container_width=True)
        
        if st.button("Start Universal Batch Processing", type="primary"):
            if not active_provider:
                st.error("Please configure at least one API key in `.env` to run batch generation.")
                st.stop()

            pbar = st.progress(0)
            zip_buf = io.BytesIO()
            results = []

            with zipfile.ZipFile(zip_buf, "w") as zf:
                for idx, row in df.iterrows():
                    cat_input = str(row.get("Category", "device")).lower().strip()
                    mfg_raw = str(row.get("Manufacturer", "")).strip()
                    model = str(row.get("Model", "")).strip()
                    if not mfg_raw or not model or mfg_raw == "nan":
                        continue

                    mfg = get_canonical_manufacturer(mfg_raw, catalog["manufacturers"])

                    if cat_input == "module":
                        f_list, gen_fn, prefix = catalog["module_types"], generate_module_yaml, "module-types"
                    elif cat_input == "rack":
                        f_list, gen_fn, prefix = catalog["rack_types"], generate_rack_yaml, "rack-types"
                    else:
                        f_list, gen_fn, prefix = catalog["device_types"], generate_device_yaml, "device-types"

                    matches = search_catalog_wildcard(f_list, mfg_raw, model)
                    if matches:
                        content = fetch_raw_content(matches[0], binary=False)
                        src = f"Official Repository ({matches[0]})"
                    else:
                        if cat_input == "module":
                            content = gen_fn(mfg, model, model, active_provider, ref_pattern=None)
                        else:
                            content = gen_fn(mfg, model, active_provider)
                        src = f"AI Generated ({active_provider})"

                    clean_fname = f"{prefix}/{mfg}/{model}.yaml".replace(" ", "_")
                    zf.writestr(clean_fname, content)
                    results.append({"Category": cat_input, "Manufacturer": mfg, "Model": model, "Source": src})
                    pbar.progress((idx + 1) / len(df))

            st.success("Batch processing completed!")
            st.dataframe(pd.DataFrame(results), use_container_width=True)
            st.download_button("📦 Download All NetBox Assets (.zip)", zip_buf.getvalue(), "netbox_all_assets.zip", "application/zip")

# --- Tab 6: Infrastructure & ESXi Naming Generator ---
with t6:
    st.subheader("🏷️ Standardized Infrastructure Naming Generator")
    naming_cat = st.radio("Select Asset Class", [
        "1. Network Devices (Switches, APs & Firewalls)",
        "2. Hosts & Virtual Machines (ESXi & VMs)",
        "3. ESXi Network Descriptions (vmnic, PortGroup, VMkernel)"
    ], horizontal=True)

    st.markdown("---")

    # 1. Network Devices
    if "1. Network" in naming_cat:
        st.markdown("##### 📍 Location & Site Code Assistant")
        loc_col1, loc_col2 = st.columns([2, 1])
        with loc_col1:
            input_location = st.text_input("Enter Location / City / Facility Name", value="Bristol", key="loc_input_help")
        with loc_col2:
            auto_code = compute_suggested_site_code(input_location)
            st.info(f"Suggested Site Code: **`{auto_code}`**")

        st.markdown("---")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**Switch Hostname Generator**")
            dev_sw_type = st.selectbox("Switch Type", ["SW (Standalone Switch)", "VS (Virtual Chassis / Stack)", "OTSW (OT Field Switch)"])
            prefix_sw = dev_sw_type.split()[0]
            
            s_ctry = st.text_input("Country Code (2-letter)", value="UK", key="sw_ctry_g")
            s_state = st.text_input("State / Region (e.g. SA, VIC, NSW or empty)", value="", key="sw_st_g")
            s_site = st.text_input("Site Code", value=auto_code, key="sw_site_g")
            s_zone = st.text_input("Building / Zone / Role (Optional)", value="", help="e.g. COR, ACC, BOT, WH1, VTI, ADM")
            s_seq = st.text_input("Sequence Number", value="01", key="sw_seq_g")
            s_stack = st.text_input("Stack / Member ID (Optional)", value="0", key="sw_stk_g")
            
            clean_zone = s_zone.strip().upper()
            state_token = s_state.strip().upper()
            base_sw = f"{prefix_sw}{s_ctry.upper()}{state_token}{s_site.upper()}{clean_zone}{s_seq}"
            current_sw_name = f"{base_sw}-{s_stack.strip()}" if s_stack.strip() else base_sw
            
            st.caption("Generated Switch Hostname:")
            st.code(current_sw_name, language="text")

            st.markdown("💡 **Live Switch Reference Examples:**")
            st.code(
                "SWUKBRIS01-0      (Bristol Stack Switch 01, Member 0)\n"
                "SWUKBRIS01-1      (Bristol Stack Switch 01, Member 1)\n"
                "SWUKWEYCORE-0     (Weybridge Core Switch, Member 0)\n"
                "SWAUSAROFLWH1-0   (Rowland Flat WH1 Switch, Member 0)\n"
                "VSAUSAROFLCCORE-0 (Rowland Flat Core Virtual Chassis)\n"
                "SWAUSABRS01       (Banrock Station Standalone Switch)",
                language="text"
            )

            st.markdown("---")
            st.markdown("**Switch Port Description Formatter (Automation Standard)**")
            p_type = st.radio("Port Type", ["Uplink (Inter-Switch)", "Access (Host/Endpoint)"], horizontal=True)
            
            if "Uplink" in p_type:
                l_port_raw = st.text_input("Local Port (Raw)", value="ge-0/0/47")
                r_dev = st.text_input("Remote Device Hostname", value="SWUKBRIS01-0")
                r_port_raw = st.text_input("Remote Port (Raw)", value="ge-0/0/1")
                link_role = st.text_input("Link Purpose / Role", value="Core Uplink")

                l_port_short = normalize_port_shortname(l_port_raw)
                r_port_short = normalize_port_shortname(r_port_raw)

                st.caption(f"On Local Device (`{current_sw_name}`):")
                st.code(f"to {r_dev}_{r_port_short} [{link_role}]", language="text")

                st.caption(f"On Remote Device (`{r_dev}`):")
                st.code(f"to {current_sw_name}_{l_port_short} [{link_role}]", language="text")
            else:
                vlan_name = st.text_input("VLAN Name / Purpose", value="VLAN10_Management")
                host_port = st.text_input("Connected Host / Port", value="roflesx01_vmnic0")
                st.caption("Access Port Description:")
                st.code(f"{vlan_name} - {host_port}", language="text")

        with col_b:
            st.markdown("**Wireless AP Naming**")
            ap_ctry = st.text_input("Country Code", value="UK", key="ap_c")
            ap_state = st.text_input("State Code (e.g. SA, NSW or empty)", value="", key="ap_st")
            ap_site = st.text_input("Site Code", value=auto_code, key="ap_s")
            ap_seq = st.text_input("Sequence (2 digits)", value="01", key="ap_seq")
            
            st.caption("Generated AP Hostname:")
            st.code(f"WAP{ap_ctry.upper()}{ap_state.upper()}{ap_site.upper()}{ap_seq}", language="text")

            st.markdown("---")
            st.markdown("💡 **Live AP Reference Examples:**")
            st.code(
                "WAPUKBRIS01   (Bristol Access Point 01)\n"
                "WAPAUSAROFL01 (Rowland Flat Access Point 01)\n"
                "WAPAUSAHUG02  (St. Hugo Access Point 02)\n"
                "WAPAUSABER01  (Berri Estates Access Point 01)",
                language="text"
            )

        with col_c:
            st.markdown("**Firewall & Security Appliances**")
            fw_archetype = st.selectbox("Firewall Category", [
                "Prisma SD-WAN (ION<Country><State><Site><Seq>)",
                "Palo Alto / Fortinet Firewall (FW<Country><State><Site><Vendor><Seq>)",
                "Virtual Appliance Panorama (VA<Country><State><Site>PANORAMA<Seq>)"
            ])
            
            fw_ctry = st.text_input("Country Code", value="UK", key="fw_c_gen")
            fw_state = st.text_input("State Code (e.g. SA, NSW or empty)", value="", key="fw_st_gen")
            fw_site = st.text_input("Site Code", value=auto_code, key="fw_s_gen")
            
            if "Palo Alto" in fw_archetype:
                fw_vendor_role = st.text_input("Vendor / Role Identifier", value="PA", key="fw_vrole")
                fw_seq = st.text_input("Sequence Number", value="01", key="fw_seq_pa")
                st.caption("Generated Firewall Hostname:")
                st.code(f"FW{fw_ctry.upper()}{fw_state.upper()}{fw_site.upper()}{fw_vendor_role.upper()}{fw_seq}", language="text")
            elif "Prisma" in fw_archetype:
                fw_seq = st.text_input("Sequence Number", value="01", key="fw_seq_ion2")
                clean_ion = f"ION{fw_ctry.upper()}{fw_state.upper()}{fw_site.upper()}{fw_seq}"
                st.caption("Generated Security Hostname:")
                st.code(clean_ion, language="text")
            else:
                va_role = st.text_input("Virtual Appliance Role", value="PANORAMA", key="va_r")
                va_seq = st.text_input("Sequence Number", value="01", key="va_sq")
                st.caption("Generated Appliance Hostname:")
                st.code(f"VA{fw_ctry.upper()}{fw_state.upper()}{fw_site.upper()}{va_role.upper()}{va_seq}", language="text")

            st.markdown("💡 **Live Security Reference Examples:**")
            st.code(
                "IONUKBRIS01       (Bristol Prisma SD-WAN 01)\n"
                "IONAUSABRS01      (Banrock Station Prisma SD-WAN)\n"
                "IONAUNSWSYD01     (Sydney Prisma SD-WAN 01)\n"
                "FWAUBERPA01       (Berri Estates Palo Alto FW 01)\n"
                "VAAUDCPANORAMA01  (Australia DC Panorama Virtual App)",
                language="text"
            )

            st.markdown("---")
            st.markdown("**Firewall Interface Description**")
            fw_role = st.text_input("Role / Security Zone", value="DMZ")
            fw_vlan = st.text_input("VLAN ID", value="100")
            st.caption("Generated Interface Name:")
            st.code(f"{fw_role}_{fw_vlan}", language="text")

    # 2. Hosts & VMs
    elif "2. Hosts" in naming_cat:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**ESXi Hypervisor Hostname**")
            h_env = st.radio("Environment Profile", ["IT / Corporate (.eswine.adds)", "OT / Industrial (.eswines.ot)", "Branch / Local (.corp.local)"], horizontal=True)
            
            if "Corporate" in h_env:
                h_site = st.text_input("Site Prefix (3 letters)", value="pws", key="esx_site", help="e.g. pws (Campo Viejo), prw (San Sebastian), age (AGE)")
                h_num = st.text_input("Host Number (3 digits)", value="001", key="esx_num")
                h_dom = "eswine.adds"
                gen_esx = f"{h_site.lower()}esx{h_num}.{h_dom}"
            elif "Industrial" in h_env:
                h_site = st.text_input("Site Prefix (3 letters)", value="age", key="esx_site", help="e.g. age, cam")
                h_role = st.selectbox("OT Node Type", ["infhost (Infrastructure Host)", "infmgmt (OT Management Appliance)"])
                h_role_code = h_role.split()[0]
                h_num = st.text_input("Host Number (1 digit)", value="1", key="esx_num") if "infhost" in h_role_code else ""
                h_dom = "eswines.ot"
                gen_esx = f"{h_site.lower()}ot{h_role_code}{h_num}.{h_dom}"
            else:
                h_site = st.text_input("Site Prefix", value="rofl", key="esx_site")
                h_num = st.text_input("Host Number", value="01", key="esx_num")
                h_dom = st.text_input("Domain Name", value="corp.local", key="esx_dom")
                gen_esx = f"{h_site.lower()}esx{h_num}.{h_dom.lower()}"
            
            st.caption("Generated ESXi Hostname:")
            st.code(gen_esx, language="text")

            st.markdown("---")
            st.markdown("💡 **Live NetBox Reference Examples:**")
            st.code(
                "pwsesx001.eswine.adds     (Campo Viejo IT ESXi Host 001)\n"
                "prwesx002.eswine.adds     (San Sebastian IT ESXi Host 002)\n"
                "esagex10.eswine.adds      (AGE IT Lenovo ThinkSystem SR650)\n"
                "ageotinfhost1.eswines.ot  (AGE Industrial OT Cluster Node 1)\n"
                "camotinfmgmt.eswines.ot   (Campo Viejo Industrial OT Mgmt)\n"
                "ntnx01.eswine.adds        (Madrid Datacentre Nutanix Node 01)",
                language="text"
            )

        with col_b:
            st.markdown("**Virtual Machine (VM) Hostname**")
            vm_site = st.text_input("Site Prefix", value="rofl", key="vm_site")
            vm_role = st.text_input("Role Code (Manual Input)", value="cvi", help="Standard codes: cvi=Core/Virt, afs=App/File, sani=Storage, vlab=Test")
            vm_seq = st.text_input("Sequence Number", value="01", key="vm_seq")
            
            st.caption("Generated VM Name:")
            st.code(f"{vm_site.lower()}{vm_role.strip().lower()}{vm_seq}", language="text")

            st.markdown("---")
            st.markdown("💡 **Live Reference Examples:**")
            st.code(
                "roflcvi01  (Rowland Flat Core Virtualization 01)\n"
                "roflafs01  (Rowland Flat App/File Server 01)\n"
                "roflsani01 (Rowland Flat SAN/Storage Service 01)\n"
                "roflvlab01 (Rowland Flat Test Validation Lab 01)",
                language="text"
            )

    # 3. ESXi Network Descriptions (Multi-Uplink & Comma Teaming Engine)
    else:
        st.markdown("**ESXi NetBox Interface Standard Descriptions**")
        col_a, col_b, col_c = st.columns(3)

        with col_a:
            st.markdown("**1. Physical Uplink (`vmnic`)**")
            vmnic = st.text_input("vmnic Identifier", value="vmnic0")
            vsw = st.text_input("Target vSwitch", value="vSwitch0", key="vsw1")
            nic_status = st.radio("Uplink Status", ["Active Uplink", "Standby Uplink"], horizontal=True)
            
            st.caption("Generated Physical Uplink:")
            st.code(f"{vmnic} - {vsw} {nic_status}", language="text")

            st.markdown("---")
            st.markdown("💡 **Live Reference Examples:**")
            st.code(
                "vmnic0 - vSwitch0 Active Uplink\n"
                "vmnic1 - vSwitch0 Active Uplink\n"
                "vmnic2 - vSwitch0 Standby Uplink",
                language="text"
            )

        with col_b:
            st.markdown("**2. Port Group Teaming (`PG`)**")
            vsw_pg = st.text_input("vSwitch Name", value="vSwitch0", key="vsw2")
            act_nics = st.text_input("Active vmnics (comma separated)", value="vmnic0, vmnic1")
            stb_nics = st.text_input("Standby vmnics (comma separated / optional)", value="")
            uns_nics = st.text_input("Unused vmnics (optional)", value="")

            # Comma grouping string builder
            team_parts = []
            if act_nics.strip():
                team_parts.append(f"{act_nics.strip()} Active")
            if stb_nics.strip():
                team_parts.append(f"{stb_nics.strip()} Standby")
            if uns_nics.strip():
                team_parts.append(f"{uns_nics.strip()} Unused")

            joined_team = " / ".join(team_parts)
            st.caption("Generated Port Group Teaming:")
            st.code(f"{vsw_pg} ({joined_team})", language="text")

            st.markdown("---")
            st.markdown("💡 **Live Reference Examples:**")
            st.code(
                "vSwitch0 (vmnic0, vmnic1 Active)\n"
                "vSwitch0 (vmnic0 Active / vmnic1 Standby)\n"
                "vSwitch0 (vmnic0, vmnic1 Active / vmnic2 Standby)",
                language="text"
            )

        with col_c:
            st.markdown("**3. VMkernel Adapter (`vmk`)**")
            vmk_purp = st.text_input("Purpose / Service", value="Management Network")
            vsw_vmk = st.text_input("vSwitch Name", value="vSwitch0", key="vsw3")
            
            st.caption("Generated VMkernel Adapter:")
            st.code(f"{vmk_purp} ({vsw_vmk})", language="text")

            st.markdown("---")
            st.markdown("💡 **Live Reference Examples:**")
            st.code(
                "Management Network (vSwitch0)\n"
                "vMotion (vSwitch1)\n"
                "vSAN Network (vSwitch0)",
                language="text"
            )

# --- Tab 7: Naming Standards Context (Prompt-Driven) ---
with t7:
    st.subheader("📖 Infrastructure Naming Standards (Natural Language Prompt Engine)")
    st.info("💡 You can export, modify, or import complete infrastructure naming guidelines directly in human-readable prompt format.")

    current_rules = load_naming_rules()
    prompt_representation = export_rules_as_prompt(current_rules)

    p_col1, p_col2 = st.columns([1, 1])

    with p_col1:
        st.markdown("#### 📝 Active Infrastructure Guidelines Prompt")
        st.caption("This exact prompt text is actively injected into AI generation requests:")
        st.text_area("Current System Prompt Context", value=prompt_representation, height=380, disabled=True)
        
        st.download_button(
            "📥 Download Guidelines Prompt (.txt)",
            prompt_representation,
            "naming_standards_prompt.txt",
            "text/plain"
        )

    with p_col2:
        st.markdown("#### 📥 Import / Update from Prompt")
        st.caption("Paste any updated natural language naming rules here. AI will extract and apply them:")
        imported_prompt_text = st.text_area(
            "Paste Updated Prompt Text", 
            placeholder="e.g. Switch naming should be SW<Country><State><Site><Zone><Seq>-<StackID>, Firewalls are FW<Country><State><Site><Vendor><Seq>...", 
            height=260
        )

        if st.button("🔄 Parse & Apply Prompt to System Standards", type="primary"):
            if not imported_prompt_text.strip():
                st.warning("Please paste valid prompt text to parse.")
            elif not active_provider:
                st.error("Please configure an active AI provider to parse natural language prompts.")
            else:
                with st.spinner(f"Parsing prompt rules using {active_provider}..."):
                    try:
                        extracted_rules = parse_prompt_to_rules(imported_prompt_text, active_provider)
                        save_naming_rules(extracted_rules)
                        st.success("✅ Guidelines successfully parsed and saved into system memory!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to parse prompt: {str(e)}")

    st.markdown("---")
    with st.expander("🛠️ Advanced: Fine-Tune Individual Rule Fields Manually"):
        col_rule_1, col_rule_2 = st.columns(2)
        with col_rule_1:
            r_sw = st.text_input("Switch Pattern", value=current_rules.get("branch_switch", ""))
            r_ap = st.text_input("Wireless AP Pattern", value=current_rules.get("branch_ap", ""))
            r_sec = st.text_input("Security / Firewall Pattern", value=current_rules.get("branch_security", ""))
            r_up_loc = st.text_input("Switch Uplink (Local)", value=current_rules.get("switch_uplink_desc_local", ""))
            r_up_rem = st.text_input("Switch Uplink (Remote)", value=current_rules.get("switch_uplink_desc_remote", ""))
            r_acc = st.text_input("Switch Access Port Description", value=current_rules.get("switch_access_desc", ""))
            r_fw_if = st.text_input("Firewall Interface Description", value=current_rules.get("firewall_interface", ""))

        with col_rule_2:
            r_esx = st.text_input("ESXi Hostname Pattern", value=current_rules.get("esxi_host", ""))
            r_vm = st.text_input("VM Hostname Pattern", value=current_rules.get("vm_host", ""))
            r_esx_up = st.text_input("ESXi Physical Uplink Description", value=current_rules.get("esxi_uplink", ""))
            r_esx_pg = st.text_input("ESXi Port Group Description", value=current_rules.get("esxi_portgroup", ""))
            r_esx_vmk = st.text_input("ESXi VMkernel Description", value=current_rules.get("esxi_vmkernel", ""))
            r_srv_yaml = st.text_area("NetBox Server YAML Requirements", value=current_rules.get("netbox_server_yaml", ""), height=100)

        if st.button("💾 Save Field Updates", key="save_manual_fields_btn"):
            updated = {
                "branch_switch": r_sw,
                "branch_ap": r_ap,
                "branch_security": r_sec,
                "switch_uplink_desc_local": r_up_loc,
                "switch_uplink_desc_remote": r_up_rem,
                "switch_access_desc": r_acc,
                "firewall_interface": r_fw_if,
                "esxi_host": r_esx,
                "vm_host": r_vm,
                "esxi_uplink": r_esx_up,
                "esxi_portgroup": r_esx_pg,
                "esxi_vmkernel": r_esx_vmk,
                "netbox_server_yaml": r_srv_yaml
            }
            save_naming_rules(updated)
            st.success("✅ Standards updated successfully!")
            st.rerun()