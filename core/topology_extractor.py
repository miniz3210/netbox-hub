"""
Backward-compatibility shim: re-exports the ESXi extractor as the legacy module path.

Preserves `from core.topology_extractor import extract_topology_from_ocr_boxes`
callers without requiring changes at the import site.  The canonical implementation
now lives under `core/extractors/esxi.py` as part of the Strategy/Adapter registry.
"""
from .extractors.esxi import EsxiTopologyExtractor, apply_normalizations  # noqa: F401

# Aliases used by the legacy naming convention in ui/tabs/naming_tab.py
extract_topology_from_ocr_boxes = EsxiTopologyExtractor().extract  # type: ignore[misc]
