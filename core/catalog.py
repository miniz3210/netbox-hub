import os
import re
import requests
from typing import List, Optional
from config.constants import GITHUB_REPO, BRANCH

def get_repo_catalog() -> List[str]:
    """Fetches the repository catalog tree from GitHub."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/git/trees/{BRANCH}?recursive=1"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            tree = resp.json().get("tree", [])
            return [item["path"] for item in tree if item["path"].endswith((".yaml", ".yml"))]
    except Exception:
        pass
    return []

# Alias for backward compatibility
fetch_device_catalog = get_repo_catalog

def get_canonical_manufacturer(name: str, catalog: Optional[List[str]] = None) -> str:
    """Finds canonical manufacturer name matching catalog folders or normalizes common aliases."""
    if not name:
        return ""
    m_clean = name.strip().lower()
    
    if catalog:
        mfg_folders = set()
        for p in catalog:
            parts = p.split("/")
            if len(parts) >= 2:
                mfg_folders.add(parts[1])
        for folder in mfg_folders:
            if folder.lower() == m_clean:
                return folder

    mapping = {
        "hp": "HPE",
        "hpe": "HPE",
        "hewlett packard": "HPE",
        "hewlett packard enterprise": "HPE",
        "cisco": "Cisco",
        "cisco systems": "Cisco",
        "dell": "Dell",
        "dell emc": "Dell",
        "palo alto": "Palo Alto Networks",
        "paloalto": "Palo Alto Networks",
        "pan": "Palo Alto Networks",
        "palo alto networks": "Palo Alto Networks",
        "fortinet": "Fortinet",
        "juniper": "Juniper",
        "juniper networks": "Juniper",
        "arista": "Arista",
        "arista networks": "Arista",
        "checkpoint": "Check Point",
        "check point": "Check Point",
        "ubiquiti": "Ubiquiti",
        "unifi": "Ubiquiti",
        "aruba": "Aruba",
        "mikrotik": "MikroTik",
        "f5": "F5",
        "f5 networks": "F5",
    }
    return mapping.get(m_clean, name.strip())

def search_catalog_wildcard(catalog: List[str], manufacturer: str, query: str, item_type: str = "device-types") -> List[str]:
    """Search catalog items matching manufacturer and query."""
    m_clean = manufacturer.lower().strip()
    q_clean = query.lower().strip().replace(" ", "").replace("-", "")
    
    results = []
    for path in catalog:
        if item_type and item_type not in path:
            continue
        path_lower = path.lower()
        if m_clean and m_clean in path_lower:
            norm_path = path_lower.replace(" ", "").replace("-", "")
            if not q_clean or q_clean in norm_path:
                results.append(path)
        elif not m_clean and q_clean:
            norm_path = path_lower.replace(" ", "").replace("-", "")
            if q_clean in norm_path:
                results.append(path)
    return results

def search_device_type(catalog: List[str], manufacturer: str, model: str) -> Optional[str]:
    """Finds single exact or best matching device-type path."""
    matches = search_catalog_wildcard(catalog, manufacturer, model, item_type="device-types")
    return matches[0] if matches else None

def get_raw_yaml_from_github(file_path: str) -> str:
    """Fetches raw YAML content from GitHub."""
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{BRANCH}/{file_path}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return f"# Error fetching {file_path} from GitHub repository."

# Aliases for tab compatibility
get_device_yaml_from_github = get_raw_yaml_from_github
get_module_yaml_from_github = get_raw_yaml_from_github