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
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

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

    # Priority 2: Global fallback
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

# --- 2. AI Multi-Engine Core Router ---
def clean_ai_yaml(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n