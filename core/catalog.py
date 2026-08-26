import os
import glob
import subprocess
from typing import Dict, List, Any, Optional

CATALOG_REPO_URL = "https://github.com/netbox-community/devicetype-library.git"
CATALOG_PATH = os.path.join("data", "devicetype-library")

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
                    rel_path = os.path.relpath(os.path.join(root, file), repo_dir)
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
                    rel_path = os.path.relpath(os.path.join(root, file), repo_dir)
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
        "module_types": module_files
    }