import os
import glob
import subprocess
from typing import Dict, List, Any, Optional

CATALOG_DIR = "data/devicetype-library"
REPO_URL = "https://github.com/netbox-community/devicetype-library.git"

MFG_ALIASES = {
    "hp": "hpe",
    "hewlett packard": "hpe",
    "hewlett packard enterprise": "hpe",
    "paloalto": "palo-alto",
    "palo alto": "palo-alto",
    "palo alto networks": "palo-alto",
    "cisco systems": "cisco",
    "dell emc": "dell",
    "dell inc": "dell",
    "aruba networks": "aruba",
    "forti": "fortinet",
    "f5 networks": "f5",
    "lenovo enterprise": "lenovo"
}

def normalize_mfg(mfg_str: str) -> str:
    clean = str(mfg_str).lower().strip()
    return MFG_ALIASES.get(clean, clean)

def sync_github_repo() -> str:
    """Clones or pulls the catalog repo using the system git binary."""
    os.makedirs("data", exist_ok=True)
    if not os.path.exists(CATALOG_DIR):
        try:
            subprocess.run(["git", "clone", "--depth", "1", REPO_URL, CATALOG_DIR], check=True, capture_output=True)
        except Exception:
            pass
    else:
        try:
            subprocess.run(["git", "-C", CATALOG_DIR, "pull"], check=False, capture_output=True)
        except Exception:
            pass
    return CATALOG_DIR

def load_catalog() -> Dict[str, Any]:
    """Indexes all manufacturers and device-type YAML files without external libraries."""
    repo_path = sync_github_repo()
    dev_types_dir = os.path.join(repo_path, "device-types")
    
    manufacturers = set()
    devices = []

    if os.path.exists(dev_types_dir):
        for mfg_folder in os.listdir(dev_types_dir):
            full_mfg_path = os.path.join(dev_types_dir, mfg_folder)
            if os.path.isdir(full_mfg_path):
                manufacturers.add(mfg_folder)
                for ext in ("*.yaml", "*.yml"):
                    for yml_file in glob.glob(os.path.join(full_mfg_path, ext)):
                        slug = os.path.splitext(os.path.basename(yml_file))[0]
                        devices.append({
                            "manufacturer": mfg_folder,
                            "slug": slug,
                            "file_path": yml_file
                        })

    return {
        "manufacturers": sorted(list(manufacturers)),
        "devices": devices,
        "repo_path": repo_path
    }

def search_library(catalog: Dict[str, Any], mfg_input: str, model_input: str) -> Optional[Dict[str, Any]]:
    """Searches catalog using exact match, partial match, and cross-manufacturer fallbacks."""
    if not catalog or not catalog.get("devices"):
        return None

    clean_mfg = normalize_mfg(mfg_input)
    clean_model = model_input.lower().strip().replace(" ", "-").replace("_", "-")
    model_words = [w for w in clean_model.split("-") if w]

    # 1. Exact match within specified manufacturer
    for dev in catalog["devices"]:
        if normalize_mfg(dev["manufacturer"]) == clean_mfg:
            if dev["slug"].lower() == clean_model:
                return dev

    # 2. Substring / multi-word match within specified manufacturer
    for dev in catalog["devices"]:
        if normalize_mfg(dev["manufacturer"]) == clean_mfg:
            if clean_model in dev["slug"].lower() or all(w in dev["slug"].lower() for w in model_words):
                return dev

    # 3. Global search if manufacturer was mistyped (e.g. searched 'dell' for 'dl360')
    if clean_model:
        for dev in catalog["devices"]:
            if all(w in dev["slug"].lower() for w in model_words):
                return dev

    return None

def read_yaml_content(file_path: str) -> str:
    """Reads raw YAML content as string without requiring yaml parser packages."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"# Error reading file: {e}"