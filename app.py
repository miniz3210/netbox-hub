import os
import re
import io
import sys
import fnmatch
import difflib
import logging
import zipfile
import requests
import streamlit as st
import pandas as pd
from typing import Optional, Dict, List, Tuple

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

# --- Provider Environment Keys & Models ---
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free").strip()

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b").strip()

AVAILABLE_PROVIDERS = []
if GROQ_KEY:
    AVAILABLE_PROVIDERS.append("Groq (High Rate Limit - 1000/day)")
if OPENROUTER_KEY:
    AVAILABLE_PROVIDERS.append("OpenRouter (Free Models)")
if OLLAMA_URL:
    AVAILABLE_PROVIDERS.append("Local Ollama (Unlimited)")
if GEMINI_KEY:
    AVAILABLE_PROVIDERS.append("Google Gemini (Search Grounded - 20/day)")

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
    """Matches exact, wildcard (*, ?), substring, or word initials (e.g. hp -> Hewlett Packard)."""
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
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().endswith("```"):
        lines = lines[:-1]
    cleaned = "\n".join(lines).strip()
    
    cleaned = re.sub(r"type:\s*10gbase-x-sfp\b", "type: 10gbase-x-sfpp", cleaned)
    cleaned = re.sub(r"type:\s*1gbase-t\b", "type: 1000base-t", cleaned)
    cleaned = re.sub(r"type:\s*1gbase-x-sfp\b", "type: 1000base-x-sfp", cleaned)
    return cleaned

def call_ai(prompt: str, selected_provider: str) -> str:
    system_msg = (
        "You are a strict NetBox hardware YAML specification generator. "
        "You MUST verify hardware specifications directly from official manufacturer datasheets. "
        "Output ONLY valid, raw YAML starting with '---'. Use exact kebab-case hyphenated keys (never underscores). "
        "Do NOT invent comments or URLs; omit 'comments' key entirely if no verified official datasheet URL is available."
    )

    try:
        if selected_provider.startswith("Groq"):
            from groq import Groq
            client = Groq(api_key=GROQ_KEY)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1
            )
            return clean_ai_yaml(resp.choices[0].message.content)

        elif selected_provider.startswith("Google Gemini"):
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=GEMINI_KEY)
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    tools=[{"google_search": {}}],
                    system_instruction=system_msg
                )
            )
            return clean_ai_yaml(resp.text)

        elif selected_provider.startswith("OpenRouter"):
            from openai import OpenAI
            client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY)
            resp = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1
            )
            return clean_ai_yaml(resp.choices[0].message.content)

        elif selected_provider.startswith("Local Ollama"):
            from openai import OpenAI
            client = OpenAI(base_url=OLLAMA_URL, api_key="ollama")
            resp = client.chat.completions.create(
                model=OLLAMA_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1
            )
            return clean_ai_yaml(resp.choices[0].message.content)

        else:
            st.error("No valid AI provider selected.")
            st.stop()

    except Exception as e:
        logger.error(f"Error from {selected_provider}: {str(e)}")
        st.error(f"❌ Generation Failed ({selected_provider}): {str(e)}")
        st.stop()

def generate_device_yaml(mfg: str, model: str, provider: str) -> str:
    prompt = f"""
Search official datasheets and generate a complete, production-ready NetBox Device-Type YAML.
Manufacturer: {mfg}
Model: {model}

CRITICAL SCHEMA RULES (Use exact kebab-case hyphenated keys, NEVER underscores):
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
   comments: "<Brief hardware description or omit if unknown>"

3. Valid NetBox Interface Types:
   - 1000base-t (1GE Copper)
   - 1000base-x-sfp (1GE SFP)
   - 10gbase-t (10GE Copper)
   - 10gbase-x-sfpp (10GE SFP+ -- MUST use 'sfpp', NOT 'sfp')
   - 25gbase-x-sfp28 (25GE SFP28)
   - 40gbase-x-qsfpp (40GE QSFP+)
   - 100gbase-x-qsfp28 (100GE QSFP28)

4. Component Blocks:
   - console-ports:
       - name: Serial
         type: de-9
   - power-ports:
       - name: PSU1
         type: iec-60320-c14
       - name: PSU2
         type: iec-60320-c14
   - module-bays:
       - name: PSU1
         position: 'PSU1'
       - name: PSU2
         position: 'PSU2'
       - name: PCIe1
         position: 'PCIe1'
       - name: PCIe2
         position: 'PCIe2'
       - name: PCIe3
         position: 'PCIe3'
   - interfaces:
       - If switch/router: list physical interfaces.
       - If server/appliance/chassis: list ONLY the out-of-band management interface (e.g. IPMI/iDRAC/iLO) with `mgmt_only: true` and `type: 1000base-t`.

Output ONLY valid, raw YAML.
"""
    return call_ai(prompt, provider)

def generate_module_yaml(mfg: str, model: str, part_num: str, provider: str, ref_pattern: Optional[str] = None) -> str:
    if ref_pattern:
        pattern_instruction = f"""
- Interface Naming Rule (Matched pattern from similar existing library items):
    - MUST follow this exact style: `{ref_pattern}` (e.g. replacing index with 1, 2, etc.)
    - Always include literal '{{module}}' intact without replacing it.
"""
    else:
        pattern_instruction = """
- Interface Naming Rule (Strict Default when no match found):
    - MUST strictly use: `name: '{module}/Port1'`, `name: '{module}/Port2'`, `name: '{module}/Port3'`, etc.
    - DO NOT prepend 'Ethernet/' or manufacturer/model names.
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
    model: <exact model or part number>
    part_number: <exact part number>
    description: '<Clear hardware overview>'
- Note on comments: DO NOT include 'comments' key unless verified official datasheet link is known.
- DO NOT include 'u_height' or 'is_full_depth'.
- Valid NetBox Interface Types:
    - 10gbase-t (10G RJ-45 Copper)
    - 10gbase-x-sfpp (10G SFP+ optical/DAC -- MUST use 'sfpp', NOT 'sfp')
    - 25gbase-x-sfp28 (25G SFP28)
    - 1000base-t (1G RJ-45)
    - 1000base-x-sfp (1G SFP)
{pattern_instruction}

Output ONLY valid, raw YAML.
"""
    result = call_ai(prompt, provider)
    
    if not ref_pattern:
        result = re.sub(
            r"name:\s*['\"]?(?:[a-zA-Z0-9_\-]+/)?(?:Ethernet/\{module\}/|Port\s*|eth|mgmt|Ethernet)(\d+)['\"]?",
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
- Keys: manufacturer, model, slug, width (19 or 23), u_height (e.g. 42, 48), form_factor (4-post-cabinet/4-post-frame/2-post-frame), starting_unit (default 1)
- Optional dimensions (if known): outer_width, outer_depth, outer_unit (mm/in), mounting_depth_min, mounting_depth_max
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
st.caption("Device Types | Module Types | Rack Types | Elevation & Module Images")

catalog = get_repo_catalog()

with st.sidebar:
    st.header("⚙️ AI Engine Status")
    if AVAILABLE_PROVIDERS:
        active_provider = st.selectbox("Active AI Engine", AVAILABLE_PROVIDERS)
        st.success(f"Connected: `{active_provider}`")
    else:
        active_provider = None
        st.warning("No AI API keys configured. Only official GitHub library lookups are enabled.")

t1, t2, t3, t4, t5 = st.tabs([
    "🖥️ Device Types", 
    "🧩 Module Types", 
    "🗄️ Rack Types", 
    "🖼️ Images (Elevation & Module)", 
    "📊 Batch Excel Engine"
])

# --- Tab 1: Device Types ---
with t1:
    col1, col2 = st.columns([1, 1])
    with col1:
        d_mfg_raw = st.text_input("Manufacturer", placeholder="e.g., HP, Cisco, Dell, Nutanix (Wildcards supported: *hp*, *cis*)", key="d_mfg")
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

    if d_search and (d_mfg or d_mfg_raw) and d_model and selected_dev_choice:
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

    if m_search and (m_mfg or m_mfg_raw) and m_model and selected_mod_choice:
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

    if r_search and (r_mfg or r_mfg_raw) and r_model and selected_rack_choice:
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
                    cat = str(row.get("Category", "device")).lower().strip()
                    mfg_raw = str(row.get("Manufacturer", "")).strip()
                    model = str(row.get("Model", "")).strip()
                    if not mfg_raw or not model or mfg_raw == "nan":
                        continue

                    mfg = get_canonical_manufacturer(mfg_raw, catalog["manufacturers"])

                    if cat == "module":
                        f_list, gen_fn, prefix = catalog["module_types"], generate_module_yaml, "module-types"
                    elif cat == "rack":
                        f_list, gen_fn, prefix = catalog["rack_types"], generate_rack_yaml, "rack-types"
                    else:
                        f_list, gen_fn, prefix = catalog["device_types"], generate_device_yaml, "device-types"

                    matches = search_catalog_wildcard(f_list, mfg_raw, model)
                    if matches:
                        content = fetch_raw_content(matches[0], binary=False)
                        src = f"Official Repository ({matches[0]})"
                    else:
                        if cat == "module":
                            content = gen_fn(mfg, model, model, active_provider, ref_pattern=None)
                        else:
                            content = gen_fn(mfg, model, active_provider)
                        src = f"AI Generated ({active_provider})"

                    clean_fname = f"{prefix}/{mfg}/{model}.yaml".replace(" ", "_")
                    zf.writestr(clean_fname, content)
                    results.append({"Category": cat, "Manufacturer": mfg, "Model": model, "Source": src})
                    pbar.progress((idx + 1) / len(df))

            st.success("Batch processing completed!")
            st.dataframe(pd.DataFrame(results), use_container_width=True)
            st.download_button("📦 Download All NetBox Assets (.zip)", zip_buf.getvalue(), "netbox_all_assets.zip", "application/zip")