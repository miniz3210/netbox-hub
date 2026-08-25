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
            return [item["path"] for item in tree if item["path"].endswith(".yaml") and "device-types" in item["path"]]
    except Exception:
        pass
    return []

# Alias for backward compatibility
fetch_device_catalog = get_repo_catalog

def search_device_type(catalog: List[str], manufacturer: str, model: str) -> Optional[str]:
    m_clean = manufacturer.lower().strip()
    mod_clean = model.lower().strip().replace(" ", "")
    
    for path in catalog:
        path_lower = path.lower()
        if m_clean in path_lower and mod_clean in path_lower.replace(" ", ""):
            return path
    
    for path in catalog:
        if m_clean in path.lower():
            return path
            
    return catalog[0] if catalog else None

def get_device_yaml_from_github(file_path: str) -> str:
    url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{BRANCH}/{file_path}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return "# Error fetching YAML from GitHub repository."