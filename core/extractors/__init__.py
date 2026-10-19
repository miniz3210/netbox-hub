from typing import Dict
from .base import BaseTopologyExtractor
from .esxi import EsxiTopologyExtractor
from .proxmox import ProxmoxTopologyExtractor

EXTRACTOR_REGISTRY: Dict[str, BaseTopologyExtractor] = {
    "VMware ESXi": EsxiTopologyExtractor(),
    "Proxmox VE": ProxmoxTopologyExtractor(),
}

def get_topology_extractor(platform_name: str) -> BaseTopologyExtractor:
    """Return the dedicated extractor adapter for the target platform."""
    return EXTRACTOR_REGISTRY.get(platform_name, EsxiTopologyExtractor())
