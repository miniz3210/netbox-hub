#!/usr/bin/env bash
set -e

echo "=== 1. Update config/constants.py to v4.8.12 ==="
cat << 'EOF' > config/constants.py
"""
Application constants for NetBox Hub (v4.8.12)
"""
import os
from pathlib import Path

APP_NAME = "NetBox Hub"
APP_VERSION = "4.8.12"
PAGE_TITLE = "NetBox Hub"
PAGE_ICON = "📦"
DEFAULT_PLATFORM = "VMware ESXi"

GITHUB_REPO = "netbox-community/devicetype-library"
BRANCH = "master"
LOCAL_CACHE_DIR = os.getenv("LOCAL_CACHE_DIR", "data/devicetype-library")

DATA_DIR = Path("data")
RULES_FILE = os.getenv("RULES_FILE", "data/naming_rules.yaml")
HISTORY_FILE = os.getenv("HISTORY_FILE", "data/history.json")
BASELINE_FILE = os.getenv("BASELINE_FILE", "data/baseline_rules.yaml")
CACHE_DIR = Path("data/cache")

DEFAULT_VLAN_NAME_PATTERN = "<role>"
DEFAULT_PREFIX_PATTERN = "<site> <role> -- VLAN <vid>"
EOF

echo "=== 2. Clean up ui/tabs/standards_tab.py (Remove Aliases, Normalize 1 to 4) ==="
python3 -c '
import re

p = "ui/tabs/standards_tab.py"
with open(p, "r", encoding="utf-8") as f:
    c = f.read()

# 1. Remove Section 2: Platform Token Aliases expander entirely
pattern_alias = r"    # ──+\s+    # Section 2: Platform Token Aliases[\s\S]+?# ──+\s+    # Section 2: Token Normalizations"
if re.search(pattern_alias, c):
    c = re.sub(pattern_alias, "    # ──────────────────────────────────────────────────────────────────────\n    # Section 2: Token Normalizations", c)
    print("Successfully purged Section 2: Platform Token Aliases.")

# 2. Also ensure any stray Section 2.5 or 2.6 headers are cleaned
c = c.replace("2. Platform Token Aliases", "REMOVED")

with open(p, "w", encoding="utf-8") as f:
    f.write(c)
'

echo "=== 3. Purge aliases from data/naming_rules.yaml ==="
python3 -c '
import yaml

p = "data/naming_rules.yaml"
with open(p, "r", encoding="utf-8") as f:
    d = yaml.safe_load(f)

for root_k in ["standards", "topology_parsing_presets"]:
    if root_k in d and isinstance(d[root_k], dict):
        for pk, pv in d[root_k].items():
            if isinstance(pv, dict):
                pv.pop("aliases", None)

with open(p, "w", encoding="utf-8") as f:
    yaml.dump(d, f, allow_unicode=True, sort_keys=False)
print("Purged aliases key from naming_rules.yaml.")
'

echo "=== 4. Syntax verification ==="
python3 -m py_compile config/constants.py ui/tabs/standards_tab.py core/extractors/esxi.py
python3 -c "import app; print('✅ Successfully verified standards_tab.py and updated to v4.8.12!')"
