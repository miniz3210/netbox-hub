import os
import glob
import subprocess
import requests
import re
from typing import Dict, List, Any, Optional

CATALOG_REPO_URL = "https://github.com/netbox-community/devicetype-library.git"
REPO_URL = CATALOG_REPO_URL
CATALOG_PATH = os.path.join("data", "devicetype-library")
CATALOG_DIR = CATALOG_PATH

MANUFACTURER_ALIASES = {
    "hp": "HPE",
    "hewlett packard": "HPE",
    "hewlett-packard": "HPE",
    "hewlett packard enterprise": "HPE",
    "hpe": "HPE",
    "palo alto": "Palo Alto",
    "paloalto": "Palo Alto",
    "palo alto networks": "Palo Alto",
    "cisco": "Cisco",
    "cisco systems": "Cisco",
    "dell": "Dell",
    "dell emc": "Dell",
    "dell inc": "Dell",
    "forti": "Fortinet",
    "fortinet": "Fortinet",
    "aruba": "Aruba",
    "aruba networks": "Aruba",
    "intel": "Intel",
    "lenovo": "Lenovo",
    "juniper": "Juniper",
    "juniper networks": "Juniper",
    "extreme": "Extreme Networks",
    "extreme networks": "Extreme Networks",
    "arista": "Arista",
    "arista networks": "Arista",
    "supermicro": "Supermicro"
}

MFG_ALIASES = MANUFACTURER_ALIASES
MFG_MAP = MANUFACTURER_ALIASES

def normalize_mfg(mfg_str: str) -> str:
    clean = str(mfg_str).lower().strip()
    return MANUFACTURER_ALIASES.get(clean, clean)

def sync_repository() -> str:
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(CATALOG_PATH):
        try:
            subprocess.run(["git", "clone", "--depth", "1", CATALOG_REPO_URL, CATALOG_PATH], check=True, capture_output=True)
        except Exception:
            pass
    else:
        try:
            subprocess.run(["git", "-C", CATALOG_PATH, "pull"], check=False, capture_output=True)
        except Exception:
            pass
    return CATALOG_PATH

def sync_github_repo() -> str:
    return sync_repository()

def load_catalog() -> Dict[str, Any]:
    repo_dir = sync_repository()
    
    dev_types_dir = os.path.join(repo_dir, "device-types")
    mod_types_dir = os.path.join(repo_dir, "module-types")
    
    mfg_set = set()
    device_files = []
    module_files = []
    
    if os.path.exists(dev_types_dir):
        for root, dirs, files in os.walk(dev_types_dir):
            for file in files:
                if file.endswith((".yaml", ".yml")):
                    rel_path = os.path.relpath(os.path.join(root, file), repo_dir).replace("\\", "/")
                    mfg = os.path.basename(root)
                    mfg_set.add(mfg)
                    device_files.append({
                        "manufacturer": mfg,
                        "filename": file,
                        "slug": os.path.splitext(file)[0],
                        "rel_path": rel_path,
                        "full_path": os.path.join(root, file)
                    })

    if os.path.exists(mod_types_dir):
        for root, dirs, files in os.walk(mod_types_dir):
            for file in files:
                if file.endswith((".yaml", ".yml")):
                    rel_path = os.path.relpath(os.path.join(root, file), repo_dir).replace("\\", "/")
                    mfg = os.path.basename(root)
                    mfg_set.add(mfg)
                    module_files.append({
                        "manufacturer": mfg,
                        "filename": file,
                        "slug": os.path.splitext(file)[0],
                        "rel_path": rel_path,
                        "full_path": os.path.join(root, file)
                    })

    return {
        "repo_path": repo_dir,
        "manufacturers": sorted(list(mfg_set)),
        "device_types": device_files,
        "module_types": module_files,
        "devices": device_files
    }

def load_github_catalog() -> Dict[str, Any]:
    return load_catalog()

def get_catalog() -> Dict[str, Any]:
    return load_catalog()

def get_canonical_manufacturer(mfg_query: str, available_mfgs: Optional[List[str]] = None) -> str:
    if not mfg_query:
        return ""
    clean = mfg_query.strip().lower()
    
    target_canonical = MANUFACTURER_ALIASES.get(clean, mfg_query.strip())
    
    if available_mfgs:
        for m in available_mfgs:
            if m.lower() == target_canonical.lower():
                return m
        for m in available_mfgs:
            if m.lower() == clean:
                return m
        for m in available_mfgs:
            if clean in m.lower():
                return m
                
    return target_canonical

def search_catalog_wildcard(catalog: Dict[str, Any], manufacturer: str, query: str, category: str = "device-types") -> List[Dict[str, Any]]:
    if not catalog:
        return []

    if category in ("module-types", "modules", "module_types"):
        items = catalog.get("module_types", [])
    else:
        items = catalog.get("device_types", [])

    mfg_clean = manufacturer.strip().lower() if manufacturer else ""
    query_clean = query.strip().lower().replace(" ", "-").replace("_", "-") if query else ""
    tokens = [t for t in query_clean.split("-") if t]

    results = []
    for item in items:
        item_mfg = item.get("manufacturer", "").lower()
        slug = item.get("slug", "").lower().replace("_", "-")
        filename = item.get("filename", "").lower()

        mfg_match = True
        if mfg_clean:
            mfg_match = (mfg_clean == item_mfg) or (mfg_clean in item_mfg) or (item_mfg in mfg_clean)

        if mfg_match:
            if not query_clean:
                results.append(item)
            elif query_clean in slug or query_clean in filename or (tokens and all(t in slug or t in filename for t in tokens)):
                results.append(item)

    if not results and tokens:
        for item in items:
            slug = item.get("slug", "").lower().replace("_", "-")
            filename = item.get("filename", "").lower()
            if all(t in slug or t in filename for t in tokens):
                results.append(item)

    return results

def search_library(catalog: Dict[str, Any], mfg_input: str, model_input: str) -> Optional[Dict[str, Any]]:
    res = search_catalog_wildcard(catalog, mfg_input, model_input, category="device-types")
    return res[0] if res else None

def search_device_types(catalog: Dict[str, Any], manufacturer: str, query: str) -> List[Dict[str, Any]]:
    return search_catalog_wildcard(catalog, manufacturer, query, category="device-types")

def search_module_types(catalog: Dict[str, Any], manufacturer: str, query: str) -> List[Dict[str, Any]]:
    return search_catalog_wildcard(catalog, manufacturer, query, category="module-types")

def fetch_raw_content(path_or_url: str) -> str:
    """Reads file content safely from relative path, absolute path, or URL."""
    if not path_or_url:
        return ""
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        try:
            resp = requests.get(path_or_url, timeout=10)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            return ""
    
    candidates = [
        path_or_url,
        os.path.join(CATALOG_PATH, path_or_url),
        os.path.join("data", "devicetype-library", path_or_url),
        os.path.join(os.getcwd(), path_or_url)
    ]
    for p in candidates:
        if os.path.exists(p) and os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    return ""

def read_yaml_content(file_path: str) -> str:
    return fetch_raw_content(file_path)

def extract_reference_interface_pattern(content: str) -> str:
    """Extracts interface naming style/pattern from a reference YAML file content."""
    if not content:
        return ""
    patterns = []
    for line in content.splitlines():
        line_s = line.strip()
        if line_s.startswith("- name:") or line_s.startswith("name:"):
            val = line_s.split("name:", 1)[1].strip().strip('"').strip("'")
            if val and "{" in val:
                return val
            elif val:
                patterns.append(val)
    if patterns:
        return patterns[0]
    return ""