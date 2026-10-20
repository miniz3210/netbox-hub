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
RULES_HISTORY_FILE = os.getenv("HISTORY_FILE", "data/naming_rules_history.json")
MAX_HISTORY_ENTRIES = int(os.getenv("MAX_HISTORY_ENTRIES", "50"))
HISTORY_FILE = os.getenv("HISTORY_FILE", "data/history.json")
BASELINE_FILE = os.getenv("BASELINE_FILE", "data/baseline_rules.yaml")
CACHE_DIR = Path("data/cache")

DEFAULT_VLAN_NAME_PATTERN = "<role>"
DEFAULT_PREFIX_PATTERN = "<site> <role> -- VLAN <vid>"
