import re
import fnmatch
import difflib
import requests
import streamlit as st
from typing import Dict, List, Optional
from config.constants import GITHUB_REPO, BRANCH
from core.exceptions import GitHubCatalogError

@st.cache_data(ttl=3600, show_spinner="Indexing NetBox devicetype-library from GitHub...")
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
        else:
            raise GitHubCatalogError(f"GitHub API Error: HTTP {res.status_code}")
    except Exception as e:
        raise GitHubCatalogError(str(e))
    
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
    return bool(clean_p and (clean_p == initials or clean_p in initials))

def get_canonical_manufacturer(user_input: str, mfg_list: List[str]) -> str:
    cleaned = user_input.strip()
    if not cleaned:
        return user_input
    for mfg in mfg_list:
        if wildcard_match(cleaned, mfg):
            return mfg
    close = difflib.get_close_matches(user_input, mfg_list, n=1, cutoff=0.5)
    return close[0] if close else user_input

def search_catalog_wildcard(file_list: List[str], manufacturer_query: str, model_query: str) -> List[str]:
    mfg_q = manufacturer_query.strip().lower()
    model_q = model_query.strip().lower()
    primary, secondary = [], []

    for path in file_list:
        parts = path.split("/")
        r_mfg = parts[1].lower() if len(parts) >= 3 else ""
        r_file = re.sub(r"\.(yaml|yml|png|svg|jpg)$", "", parts[-1].lower())
        mfg_hit = wildcard_match(mfg_q, r_mfg) if mfg_q else True
        model_hit = wildcard_match(model_q, r_file) if model_q else True

        if mfg_hit and model_hit:
            primary.append(path)
        elif model_hit and len(model_q) >= 3:
            secondary.append(path)

    seen = set()
    return [x for x in primary + secondary if not (x in seen or seen.add(x))]

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
    if match and "{module}" in match.group(1):
        return match.group(1).strip()
    return None