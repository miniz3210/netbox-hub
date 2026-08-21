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

# Identify available engines
AVAILABLE_PROVIDERS = []
if GROQ_KEY:
    AVAILABLE_PROVIDERS.append("Groq (High Rate Limit)")
if GEMINI_KEY:
    AVAILABLE_PROVIDERS.append("Google Gemini (Search Grounded)")
if OPENROUTER_KEY:
    AVAILABLE_PROVIDERS.append("OpenRouter")
if OLLAMA_URL:
    AVAILABLE_PROVIDERS.append("Local Ollama")

# --- 1. Global GitHub Asset Indexer ---
@st.cache_data(ttl=3600, show_spinner="Indexing all repository asset types from GitHub...")
def get_repo_catalog() -> Dict[str, List[str]]:
    url = f"[https://api.github.com/repos/](https://api.github.com/repos/){GITHUB_REPO}/git/trees/{BRANCH}?recursive=1"
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

    # Priority 2: Global fallback
    for path in file_list:
        r_file = re.sub(r"[^a-zA-Z0-9]", "", path.split("/")[-1]).lower()
        if c_query in r_file:
            return path
    return None

def fetch_raw_content(path: str, binary: bool = False):
    raw_url = f"[https://raw.githubusercontent.com/](https://raw.githubusercontent.com/){GITHUB_REPO}/{BRANCH}/{path}"
    res = requests.get(raw_url, timeout=10)
    if res.status_code == 200:
        return res.content if binary else res.text
    return None

# --- 2. AI Multi-Engine Core Router ---
def clean_ai_yaml(text: str) -> str:
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().endswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()

def call_ai(prompt: str, selected_provider: str) -> str:
    system_msg = (
        "You are a strict NetBox hardware YAML specification generator. "
        "You MUST verify hardware specifications directly from official manufacturer datasheets. "
        "Output ONLY valid, raw YAML starting with '---'. Use exact kebab-case hyphenated keys (never underscores)."
    )

    try:
        # 1. Groq Engine
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

        # 2. Google Gemini Engine (with Grounding)
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

        # 3. OpenRouter Engine
        elif selected_provider.startswith("OpenRouter"):
            from openai import OpenAI
            client = OpenAI(base_url="[https://openrouter.ai/api/v1](https://openrouter.ai/api/v1)", api_key=OPENROUTER_KEY)
            resp = client.chat.completions.create(
                model=OPENROUTER_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1
            )
            return clean_ai_yaml(resp.choices[0].message.content)

        # 4. Local Ollama Engine
        elif selected_provider.startswith("Local Ollama"):