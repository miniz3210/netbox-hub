import re
import fnmatch
import difflib
import json
import os
import requests
import streamlit as st
from typing import Dict, List, Optional
from config.constants import GITHUB_REPO, BRANCH, LOCAL_CACHE_DIR
from core.exceptions import GitHubCatalogError

MANUFACTURER_ALIASES = {
    "hp": "hpe",
    "hewlett packard": "hpe",
    "hewlett-packard": "hpe",
    "hewlett packard enterprise": "hpe",
    "cisco systems": "cisco",
    "palo alto": "paloaltonetworks",
    "palo alto networks": "paloaltonetworks",
    "paloalto": "paloaltonetworks",
    "forti": "fortinet",
    "juniper networks": "juniper",
    "arista networks": "arista",
    "dell emc": "dell",
    "dell technologies": "dell",
    "supermicro": "supermicro",
    "extreme networks": "extremenetworks"
}

MODEL_NORMALIZATION_PATTERNS = [
    (r"dl(\d+)", r"proliant-dl\1"),
    (r"gen(\d+)", r"gen\1"),
    (r"r(\d+)", r"r\1"),
    (r"switch", r"switch"),
    (r"firewall", r"firewall"),
    (r"access\s*point", r"access-point"),
]


def _get_cache_path() -> str:
    """Get the local cache file path for the catalog."""
    os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)
    return os.path.join(LOCAL_CACHE_DIR, "catalog_cache.json")


def _load_catalog_from_cache() -> Optional[Dict[str, List[str]]]:
    """Load catalog data from local cache file."""
    cache_path = _get_cache_path()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            if cached_data and isinstance(cached_data, dict):
                if "device_types" in cached_data and cached_data["device_types"]:
                    return cached_data
        except (json.JSONDecodeError, IOError, OSError):
            pass
    return None


def _save_catalog_to_cache(catalog: Dict[str, List[str]]) -> None:
    """Save catalog data to local cache file."""
    cache_path = _get_cache_path()
    try:
        os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2)
    except (IOError, OSError) as e:
        pass


def _fetch_github_file_tree() -> Optional[Dict[str, List[str]]]:
    """Fetch the complete file tree from GitHub API."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{BRANCH}?recursive=1"
    catalog = {
        "device_types": [],
        "module_types": [],
        "rack_types": [],
        "elevation_images": [],
        "module_images": [],
        "manufacturers": []
    }
    try:
        res = requests.get(url, timeout=30)
        if res.status_code == 200:
            for item in res.json().get("tree", []):
                path = item["path"]
                parts = path.split("/")
                if len(parts) >= 3 and parts[0] in ["device-types", "module-types", "rack-types"]:
                    if parts[1] not in catalog["manufacturers"]:
                        catalog["manufacturers"].append(parts[1])

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
            
            catalog["manufacturers"] = sorted(catalog["manufacturers"])
            return catalog
        else:
            raise GitHubCatalogError(f"GitHub API Error: HTTP {res.status_code}")
    except Exception as e:
        raise GitHubCatalogError(str(e))


@st.cache_data(ttl=3600, show_spinner="Indexing NetBox devicetype-library from GitHub...")
def get_repo_catalog() -> Dict[str, List[str]]:
    """
    Get the repository catalog. First checks local cache, then falls back to GitHub API.
    Ensures the local cache is populated for offline use.
    """
    cached_catalog = _load_catalog_from_cache()
    if cached_catalog and cached_catalog.get("device_types"):
        return cached_catalog
    
    github_catalog = _fetch_github_file_tree()
    
    if github_catalog:
        _save_catalog_to_cache(github_catalog)
        return github_catalog
    
    if cached_catalog:
        return cached_catalog
    
    raise GitHubCatalogError("Failed to load catalog from both GitHub and local cache")


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
    
    if len(clean_p) >= 3 and clean_p in clean_t:
        return True

    words = re.findall(r"[a-zA-Z0-9]+", t)
    initials = "".join(w[0] for w in words).lower()
    return bool(clean_p and (clean_p == initials or clean_p in initials))


def _normalize_model_query(query: str) -> List[str]:
    """
    Generate normalized variations of a model query for tolerant matching.
    E.g., 'dl380' matches 'proliant-dl380-gen10', 'dl380gen10', 'proliant-dl380'.
    """
    variations = [query.lower()]
    
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", query.lower())
    variations.append(cleaned)
    
    dl_match = re.match(r"dl(\d+)", cleaned)
    if dl_match:
        variations.append(f"proliant-dl{dl_match.group(1)}")
        variations.append(f"proliant-dl{dl_match.group(1)}-gen")
    
    gen_match = re.search(r"gen(\d+)", cleaned)
    if gen_match:
        variations.append(f"gen{gen_match.group(1)}")
    
    r_match = re.match(r"r(\d+)", cleaned)
    if r_match:
        variations.append(f"r{r_match.group(1)}")
    
    return list(set(variations))


def get_canonical_manufacturer(user_input: str, mfg_list: List[str]) -> str:
    cleaned = user_input.strip().lower()
    if not cleaned:
        return user_input

    if cleaned in MANUFACTURER_ALIASES:
        target_alias = MANUFACTURER_ALIASES[cleaned]
        for mfg in mfg_list:
            if mfg.lower() == target_alias:
                return mfg

    for mfg in mfg_list:
        if cleaned == mfg.lower():
            return mfg

    for mfg in mfg_list:
        if mfg.lower().startswith(cleaned):
            return mfg

    for mfg in mfg_list:
        if wildcard_match(cleaned, mfg):
            return mfg

    close = difflib.get_close_matches(cleaned, [m.lower() for m in mfg_list], n=1, cutoff=0.6)
    if close:
        for mfg in mfg_list:
            if mfg.lower() == close[0]:
                return mfg

    return user_input


def _model_matches_variations(path_model: str, query: str) -> bool:
    """
    Check if a model query matches a path's model component using tolerant matching.
    Supports manufacturer aliases and partial model number matching.
    """
    query_lower = query.strip().lower()
    path_model_lower = path_model.lower()
    
    query_cleaned = re.sub(r"[^a-zA-Z0-9]", "", query_lower)
    path_cleaned = re.sub(r"[^a-zA-Z0-9]", "", path_model_lower)
    
    if query_cleaned in path_cleaned:
        return True
    
    model_variations = _normalize_model_query(query_lower)
    for var in model_variations:
        var_cleaned = re.sub(r"[^a-zA-Z0-9]", "", var)
        if var_cleaned in path_cleaned:
            return True
        
        if wildcard_match(var, path_model_lower):
            return True
    
    return False


def search_catalog_wildcard(file_list: List[str], manufacturer_query: str, model_query: str) -> List[str]:
    mfg_q = manufacturer_query.strip().lower()
    model_q = model_query.strip().lower()

    if mfg_q in MANUFACTURER_ALIASES:
        mfg_q = MANUFACTURER_ALIASES[mfg_q]

    primary, secondary = [], []

    for path in file_list:
        parts = path.split("/")
        r_mfg = parts[1].lower() if len(parts) >= 3 else ""
        r_file = re.sub(r"\.(yaml|yml|png|svg|jpg)$", "", parts[-1].lower())
        
        mfg_hit = wildcard_match(mfg_q, r_mfg) if mfg_q else True
        
        if model_q:
            model_hit = _model_matches_variations(r_file, model_q)
        else:
            model_hit = True

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
