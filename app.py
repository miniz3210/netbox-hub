import os
import re
import io
import sys
import logging
import zipfile
import requests
import streamlit as st
import pandas as pd
from typing import Optional, Dict, List

# --- Logging Configuration ---
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logger = logging.getLogger("netbox-hub")

# --- Page Configuration ---
st.set_page_config(
    page_title="NetBox All-in-One Asset Hub",
    page_icon="⚡",
    layout="wide"
)

GITHUB_REPO = "netbox-community/devicetype-library"
BRANCH = "master"
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Display warning banner if API key is not supplied
if not API_KEY:
    st.error("⚠️ **GEMINI_API_KEY is not configured!** Official GitHub searching works, but AI auto-generation for unlisted models is disabled. Add it to your `.env` file.")
    logger.warning("Application initialized without GEMINI_API_KEY.")

# --- 1. Global GitHub Asset Indexer ---
@st.cache_data(ttl=3600, show_spinner="Indexing all repository asset types from GitHub...")
def get_repo_catalog() -> Dict[str, List[str]]:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{BRANCH}?recursive=1"
    catalog = {
        "device_types": [],
        "module_types": [],
        "rack_types": [],
        "elevation_images": [],
        "module_images": []
    }
    try:
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            for item in res.json().get("tree", []):
                path = item["path"]
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
    return catalog

def search_catalog(file_list: List[str], manufacturer: str, query: str) -> Optional[str]:
    c_query = re.sub(r"[^a-zA-Z0-9]", "", query).lower()
    c_mfg = re.sub(r"[^a-zA-Z0-9]", "", manufacturer).lower()

    # Priority 1: Search in matching manufacturer directory
    for path in file_list:
        parts = path.split("/")
        if len(parts) >= 3:
            r_mfg = re.sub(r"[^a-zA-Z0-9]", "", parts[1]).lower()
            r_file = re.sub(r"[^a-zA-Z0-9]", "", parts[-1]).lower()
            if (c_mfg in r_mfg or r_mfg in c_mfg) and c_query in r_file:
                return path

    # Priority 2: Global search fallback
    for path in file_list:
        r_file = re.sub(r"[^a-zA-Z0-9]", "", path.split("/")[-1]).lower()
        if c_query in r_file:
            return path
    return None

def fetch_raw_content(path: str, binary: bool = False):
    raw_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{BRANCH}/{path}"
    res = requests.get(raw_url, timeout=10)
    if res.status_code == 200:
        return res.content if binary else res.text
    return None

# --- 2. AI Spec Research & Schema Generators ---
def call_gemini(prompt: str) -> str:
    if not API_KEY:
        error_msg = "GEMINI_API_KEY is not configured in the container environment."
        logger.error(error_msg)
        st.error(f"❌ {error_msg}")
        st.stop()

    from google import genai
    try:
        logger.info(f"Querying Gemini API using model '{GEMINI_MODEL}'...")
        client = genai.Client(api_key=API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
        return text.strip()
    except Exception as e:
        logger.error(f"Gemini API error: {str(e)}")
        st.error(f"❌ AI Generation Failed: {str(e)}")
        st.stop()

def generate_device_yaml(mfg: str, model: str) -> str:
    prompt = f"""
Search specifications and generate a NetBox Device-Type YAML.
Manufacturer: {mfg}, Model: {model}
Schema Rules:
- First line '---'
- Keys: manufacturer, model, slug, u_height, is_full_depth
- Interfaces: If switch, define physical ports (e.g. 1G/10G/40G). If server, define ONLY OOB management interface (mgmt_only: true).
- Module bays: Include PSU bays and expansion slots (e.g., PCIe, OCP).
- Power ports: IEC standard plugs (C14/C20).
- Console ports: Serial RJ-45 or DE-9.
Output ONLY raw YAML.
"""
    return call_gemini(prompt)

def generate_module_yaml(mfg: str, model: str, part_num: str = "") -> str:
    prompt = f"""
Search specifications and generate a NetBox Module-Type YAML.
Manufacturer: {mfg}, Model/Part Number: {model} {part_num}
Schema Rules:
- First line '---'
- Keys: manufacturer, model, part_number
- Include card-specific interfaces (e.g., SFP28 ports), console ports, or power outputs.
- Do NOT include u_height or is_full_depth.
Output ONLY raw YAML.
"""
    return call_gemini(prompt)

def generate_rack_yaml(mfg: str, model: str) -> str:
    prompt = f"""
Search specifications and generate a NetBox Rack-Type YAML.
Manufacturer: {mfg}, Model: {model}
Schema Rules:
- First line '---'
- Keys: manufacturer, model, slug, width (19 or 23), u_height (e.g. 42, 48), form_factor (4-post-cabinet/4-post-frame/2-post-frame), starting_unit (default 1)
- Optional dimensions (if known): outer_width, outer_depth, outer_unit (mm/in), mounting_depth_min, mounting_depth_max
Output ONLY raw YAML.
"""
    return call_gemini(prompt)

def generate_placeholder_svg(mfg: str, model: str, u_height: int = 1, view: str = "front") -> str:
    height_px = max(40, u_height * 40)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 {height_px}" width="440" height="{height_px}">
  <rect width="440" height="{height_px}" fill="#1e293b" stroke="#475569" stroke-width="2" rx="4"/>
  <rect x="10" y="5" width="420" height="{height_px - 10}" fill="#0f172a" rx="2"/>
  <text x="220" y="{height_px / 2 + 4}" fill="#94a3b8" font-family="sans-serif" font-size="12" text-anchor="middle">
    [{mfg}] {model} ({view.upper()} - {u_height}U Generated)
  </text>
</svg>"""

# --- 3. UI Implementation ---
st.title("⚡ NetBox Universal Library Hub")
st.caption("Device Types | Module Types | Rack Types | Elevation & Module Images")

catalog = get_repo_catalog()

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
        d_mfg = st.text_input("Manufacturer", placeholder="e.g., Cisco, Dell, Nutanix", key="d_mfg")
        d_model = st.text_input("Device Model", placeholder="e.g., Catalyst 9300-48P, PowerEdge R750", key="d_mod")
        d_search = st.button("Find / Generate Device Type", type="primary", key="btn_dev")

    if d_search and d_mfg and d_model:
        with st.spinner("Processing..."):
            matched = search_catalog(catalog["device_types"], d_mfg, d_model)
            if matched:
                content = fetch_raw_content(matched, binary=False)
                src = f"✅ Official Repository (`{matched}`)"
            else:
                content = generate_device_yaml(d_mfg, d_model)
                src = "🤖 AI Generated (Auto-Researched)"
        with col2:
            st.markdown(f"**Source:** {src}")
            st.code(content, language="yaml", line_numbers=True)
            st.download_button("📥 Download Device YAML", content, f"{d_mfg}_{d_model}.yaml", "text/yaml")

# --- Tab 2: Module Types ---
with t2:
    col1, col2 = st.columns([1, 1])
    with col1:
        m_mfg = st.text_input("Manufacturer", placeholder="e.g., Dell, Intel, Cisco", key="m_mfg")
        m_model = st.text_input("Module Name / Part #", placeholder="e.g., Broadcom 57414, C9300-NM-8X", key="m_mod")
        m_search = st.button("Find / Generate Module Type", type="primary", key="btn_mod")

    if m_search and m_mfg and m_model:
        with st.spinner("Processing..."):
            matched = search_catalog(catalog["module_types"], m_mfg, m_model)
            if matched:
                content = fetch_raw_content(matched, binary=False)
                src = f"✅ Official Repository (`{matched}`)"
            else:
                content = generate_module_yaml(m_mfg, m_model)
                src = "🤖 AI Generated (Auto-Researched)"
        with col2:
            st.markdown(f"**Source:** {src}")
            st.code(content, language="yaml", line_numbers=True)
            st.download_button("📥 Download Module YAML", content, f"module_{m_mfg}_{m_model}.yaml", "text/yaml")

# --- Tab 3: Rack Types ---
with t3:
    col1, col2 = st.columns([1, 1])
    with col1:
        r_mfg = st.text_input("Rack Manufacturer", placeholder="e.g., APC, Eaton, Rittal", key="r_mfg")
        r_model = st.text_input("Rack Model", placeholder="e.g., NetShelter SX 42U", key="r_mod")
        r_search = st.button("Find / Generate Rack Type", type="primary", key="btn_rack")

    if r_search and r_mfg and r_model:
        with st.spinner("Processing..."):
            matched = search_catalog(catalog["rack_types"], r_mfg, r_model)
            if matched:
                content = fetch_raw_content(matched, binary=False)
                src = f"✅ Official Repository (`{matched}`)"
            else:
                content = generate_rack_yaml(r_mfg, r_model)
                src = "🤖 AI Generated (Auto-Researched)"
        with col2:
            st.markdown(f"**Source:** {src}")
            st.code(content, language="yaml", line_numbers=True)
            st.download_button("📥 Download Rack YAML", content, f"rack_{r_mfg}_{r_model}.yaml", "text/yaml")

# --- Tab 4: Visual Images (Elevation & Module) ---
with t4:
    col1, col2 = st.columns([1, 2])
    with col1:
        img_cat = st.selectbox("Image Target", ["Elevation Images (Rack Face)", "Module Images"])
        i_mfg = st.text_input("Manufacturer", placeholder="e.g., Cisco, Dell", key="i_mfg")
        i_model = st.text_input("Model Name", placeholder="e.g., PowerEdge R740, c9300l-24p-4x", key="i_mod")
        i_search = st.button("Find / Render Image", type="primary", key="btn_img")

    with col2:
        if i_search and i_mfg and i_model:
            target_list = catalog["elevation_images"] if "Elevation" in img_cat else catalog["module_images"]
            
            clean_f = re.sub(r"[^a-zA-Z0-9]", "", i_mfg).lower()
            clean_m = re.sub(r"[^a-zA-Z0-9]", "", i_model).lower() 

            matched_images = []
            for path in target_list:
                if clean_f in path.lower():
                    r_file_literal = path.split("/")[-1].lower()
                    r_file_clean = re.sub(r"[^a-zA-Z0-9]", "", path.split("/")[-1]).lower()

                    if i_model.lower() in r_file_literal or clean_m in r_file_clean:
                        matched_images.append(path)

            if matched_images:
                st.success(f"Found {len(matched_images)} matching image(s) in Official Library:")
                for img_path in matched_images:
                    raw_data = fetch_raw_content(img_path, binary=True)
                    st.write(f"**Path:** `{img_path}`")
                    st.image(raw_data, caption=img_path.split("/")[-1], use_container_width=True)
                    st.download_button(
                        f"📥 Download {img_path.split('/')[-1]}", 
                        raw_data, 
                        img_path.split('/')[-1]
                    )
            else:
                st.warning("No official image found. Generating a standardized NetBox vector rack SVG template:")
                svg_front = generate_placeholder_svg(i_mfg, i_model, u_height=2, view="front")
                st.image(svg_front, caption="Auto-Generated Front SVG", use_container_width=True)
                st.download_button("📥 Download Vector (.svg)", svg_front, f"{i_mfg}_{i_model}.front.svg", "image/svg+xml")

# --- Tab 5: Universal Batch Excel Processing ---
with t5:
    st.write("Upload an Excel file with `Category` (`device`, `module`, or `rack`), `Manufacturer`, and `Model`.")
    sample_df = pd.DataFrame([
        {"Category": "device", "Manufacturer": "Cisco", "Model": "C9300-48P"},
        {"Category": "module", "Manufacturer": "Dell", "Model": "Broadcom 57414"},
        {"Category": "rack", "Manufacturer": "APC", "Model": "NetShelter SX 42U"}
    ])
    st.download_button(
        "📄 Download Sample Template (Excel)", 
        sample_df.to_csv(index=False).encode('utf-8'), 
        "template.csv", 
        "text/csv"
    )

    batch_file = st.file_uploader("Upload Batch File (.xlsx, .csv)", type=["xlsx", "csv"])
    if batch_file:
        df = pd.read_csv(batch_file) if batch_file.name.endswith(".csv") else pd.read_excel(batch_file)
        st.dataframe(df.head(), use_container_width=True)
        
        if st.button("Start Universal Batch Processing", type="primary"):
            pbar = st.progress(0)
            zip_buf = io.BytesIO()
            results = []

            with zipfile.ZipFile(zip_buf, "w") as zf:
                for idx, row in df.iterrows():
                    cat = str(row.get("Category", "device")).lower().strip()
                    mfg = str(row.get("Manufacturer", "")).strip()
                    model = str(row.get("Model", "")).strip()
                    if not mfg or not model or mfg == "nan":
                        continue

                    # Route category
                    if cat == "module":
                        f_list, gen_fn, prefix = catalog["module_types"], generate_module_yaml, "module-types"
                    elif cat == "rack":
                        f_list, gen_fn, prefix = catalog["rack_types"], generate_rack_yaml, "rack-types"
                    else:
                        f_list, gen_fn, prefix = catalog["device_types"], generate_device_yaml, "device-types"

                    matched = search_catalog(f_list, mfg, model)
                    if matched:
                        content = fetch_raw_content(matched, binary=False)
                        src = "Official Repository"
                    else:
                        content = gen_fn(mfg, model)
                        src = "AI Generated"

                    clean_fname = f"{prefix}/{mfg}/{model}.yaml".replace(" ", "_")
                    zf.writestr(clean_fname, content)
                    results.append({"Category": cat, "Manufacturer": mfg, "Model": model, "Source": src})
                    pbar.progress((idx + 1) / len(df))

            st.success("Batch processing completed!")
            st.dataframe(pd.DataFrame(results), use_container_width=True)
            st.download_button(
                "📦 Download All NetBox Assets (.zip)",
                zip_buf.getvalue(),
                "netbox_all_assets.zip",
                "application/zip"
            )