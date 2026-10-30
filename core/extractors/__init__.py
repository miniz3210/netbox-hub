from typing import Callable, Dict, List, Optional

from .base import BaseTopologyExtractor
from .esxi import EsxiTopologyExtractor
from .esxi_pipeline import analyze_esxi
from .esxi_text import parse_vsphere_text_ex
from .proxmox import ProxmoxTopologyExtractor

# ── Legacy registries (preserved for external callers; not used by generic routing) ──

TEXT_PARSERS: Dict[str, Dict[str, Callable]] = {
    "vsphere_text": {
        "pipeline": analyze_esxi,
        "preview": parse_vsphere_text_ex,
        "metadata": {
            "display_name": "vSphere Text Parser",
            "input_kind": "text",
            "paste_label": "vSphere Page Text",
            "paste_help": "Expand every switch, VMkernel Ports and uplink first, run this one-liner in your browser Console on the vSphere tab, then paste here:",
            "screenshot_guidance_title": "Screenshot Guidelines (Virtual Switches Topology)",
            "screenshot_guidance": """**Recommended Capture Location:**

1. **Access**: vCenter or ESXi Host Client.
2. **Navigate**: `Host` ➔ `Configure` ➔ `Networking` ➔ `Virtual switches`.
3. **Expand**: Open target switches.
4. **Capture**: Ensure all three sections are visible:
    - **Left**: Port Groups & VMkernel ports
    - **Middle**: Virtual Switch diagram
    - **Right**: Physical Adapters (with link speeds)

> 💡 **Pro-Tip (Complete Data Discovery)**: In addition to **Virtual switches** topology, also upload/paste screenshots of **Networking ➜ Physical adapters** (click `>>` to expand adapters). This provides exact **MAC addresses**, **PCI slot mappings (PCIeX/PortX)**, and **CDP/LLDP Switch Ports (Cable Connections)** with zero manual guesswork!

> ⚠️ **Manual Verification Required:** ESXi topology views do not explicitly display Active vs. Standby status. Please verify in ESXi: click **EDIT** beside the switch ➜ go to **Teaming and failover** ➜ check **Failover order** (Active vs Standby adapters) before committing to NetBox.""",
            "export_instructions": "copy([document,...[...document.querySelectorAll('iframe')].map(f=>{try{return f.contentDocument}catch(e){return null}}).filter(Boolean)].map(d=>d.body.innerText).join('\\n'))",
        },
    },
}

EXTRACTORS: Dict[str, BaseTopologyExtractor] = {
    "esxi": EsxiTopologyExtractor(),
    "proxmox": ProxmoxTopologyExtractor(),
}


def preview_topology_text(preview_id, text):
    if preview_id not in TEXT_PARSERS:
        return None
    text = (text or "").strip()
    if not text:
        return None
    out = TEXT_PARSERS[preview_id]["preview"](text)
    sw_map: Dict[str, Dict[str, int]] = {}
    for r in out["records"]:
        sw = r.get("switch")
        if sw is None:
            continue
        if sw not in sw_map:
            sw_map[sw] = {"uplinks": 0, "services": 0}
        t = r.get("type", "")
        if t == "Uplink":
            sw_map[sw]["uplinks"] += 1
        elif t in ("Port Group", "VMkernel"):
            sw_map[sw]["services"] += 1
    gaps_set = {g["switch"] for g in out["gaps"] if g.get("switch")}
    switches = []
    for sw_name, info in sw_map.items():
        has_gaps = sw_name in gaps_set
        if info["uplinks"] == 0 and info["services"] == 0:
            status = "empty"
        elif has_gaps:
            status = "partial"
        else:
            status = "ok"
        switches.append({"name": sw_name, "uplinks": info["uplinks"], "services": info["services"], "status": status})
    seen: set = set()
    warnings = []
    for w in out["warnings"]:
        if w not in seen:
            seen.add(w)
            warnings.append(w)
    return {"switches": switches, "warnings": warnings, "gaps": out["gaps"]}

def get_topology_extractor(extractor_id: str) -> Optional[BaseTopologyExtractor]:
    """Return the dedicated extractor adapter for the given extractor id, or None."""
    return EXTRACTORS.get(extractor_id)


# ── Generic Processing Modules Registry ──────────────────────────────────────

PROCESSING_MODULES: Dict[str, Dict[str, object]] = {
    "vsphere_text": {
        "id": "vsphere_text",
        "input_kind": "text",
        "display_name": "vSphere Text Parser",
        "handler": analyze_esxi,
        "preview": parse_vsphere_text_ex,
        "metadata": TEXT_PARSERS["vsphere_text"]["metadata"],
    },
    "esxi_ocr": {
        "id": "esxi_ocr",
        "input_kind": "image",
        "display_name": "ESXi Screenshot Extractor",
        "handler": EsxiTopologyExtractor(),
        "preview": None,
        "metadata": {},
    },
    "proxmox_ocr": {
        "id": "proxmox_ocr",
        "input_kind": "image",
        "display_name": "Proxmox Screenshot Extractor",
        "handler": ProxmoxTopologyExtractor(),
        "preview": None,
        "metadata": {},
    },
}


def validate_processing_modules(module_ids: List[str]) -> List[str]:
    """Validate a platform's configured module ID list.

    Returns a list of warning strings.  Empty list means valid.
    - Unknown module IDs are reported.
    - Module order is preserved in all outputs.
    - More than one configured module with the same input_kind is reported.
    """
    warnings: List[str] = []
    if not isinstance(module_ids, list):
        return ["modules must be a list of module IDs."]
    seen_ids: Dict[str, str] = {}
    seen_kinds: Dict[str, str] = {}
    for mid in module_ids:
        if mid not in PROCESSING_MODULES:
            warnings.append(f"Unknown processing module: {mid}")
            continue
        kind = PROCESSING_MODULES[mid]["input_kind"]
        if kind in seen_kinds:
            warnings.append(
                f"Duplicate input_kind '{kind}': both '{seen_kinds[kind]}' and '{mid}' are configured."
            )
        else:
            seen_kinds[kind] = mid
        if mid in seen_ids:
            warnings.append(f"Duplicate module ID: '{mid}' appears more than once.")
        else:
            seen_ids[mid] = mid
    return warnings


def run_topology_pipeline(preset: dict, text, get_ocr_records=None):
    configured_modules = preset.get("modules")
    if not isinstance(configured_modules, list):
        configured_modules = []

    validation_warnings = validate_processing_modules(configured_modules)

    text_module_id = next(
        (mid for mid in configured_modules
         if mid in PROCESSING_MODULES and PROCESSING_MODULES[mid]["input_kind"] == "text"),
        None,
    )
    image_module_id = next(
        (mid for mid in configured_modules
         if mid in PROCESSING_MODULES and PROCESSING_MODULES[mid]["input_kind"] == "image"),
        None,
    )

    text_input = (text or "").strip()
    has_images = bool(get_ocr_records is not None)

    empty_rep = {"filled": [], "unresolved": [], "conflicts": [], "suggestions": [], "unmatched_switches": []}

    if not configured_modules or (not text_module_id and not image_module_id):
        return {
            "source": "none",
            "records": [],
            "warnings": list(validation_warnings) + ["No processing module configured for this platform."],
            "gaps": [],
            "report": empty_rep,
            "needs_screenshot": False,
        }

    text_result = None
    text_warnings: List[str] = []
    ocr_result = None
    ocr_warnings: List[str] = []

    # Text module (first compatible)
    if text_module_id:
        if text_input:
            text_result = PROCESSING_MODULES[text_module_id]["handler"](text_input, get_ocr_records if has_images else None)
            if isinstance(text_result, dict):
                text_warnings = list(text_result.get("warnings", []))
        elif has_images:
            pass
        else:
            text_warnings.append("No text input provided for text module.")

    # Image module (first compatible only)
    if image_module_id and has_images:
        try:
            ocr_recs = get_ocr_records()
        except Exception:
            ocr_recs = []
        if ocr_recs:
            mod = PROCESSING_MODULES[image_module_id]
            handler = mod["handler"]
            if hasattr(handler, "extract"):
                recs = handler.extract(ocr_recs, plat_cfg=preset)
            else:
                recs = handler(ocr_recs, plat_cfg=preset) if callable(handler) else []
            if isinstance(recs, list):
                for r in recs:
                    r.setdefault("source", "ocr")
                ocr_result = {"records": recs, "warnings": list(ocr_warnings)}

    # Determine source and merge
    all_warnings = list(validation_warnings)

    if text_result and isinstance(text_result, dict) and text_result.get("records"):
        records = list(text_result.get("records", []))
        all_warnings.extend(text_result.get("warnings", []))
        if ocr_result and ocr_result.get("records"):
            records.extend(ocr_result["records"])
            all_warnings.extend(ocr_result.get("warnings", []))
            source = "text+ocr"
        else:
            source = "text"
        gaps = list(text_result.get("gaps", []))
        report = text_result.get("report", empty_rep)
        needs_screenshot = text_result.get("needs_screenshot", False)
    elif ocr_result and ocr_result.get("records"):
        records = ocr_result["records"]
        all_warnings.extend(ocr_result.get("warnings", []))
        source = "ocr"
        gaps = []
        report = empty_rep
        needs_screenshot = False
    elif text_input and text_module_id is None:
        all_warnings.append("Pasted text is not supported for this platform - ignored.")
        source = "none"
        records = []
        gaps = []
        report = empty_rep
        needs_screenshot = False
    elif has_images and image_module_id is None:
        all_warnings.append("No image module configured for this platform.")
        source = "none"
        records = []
        gaps = []
        report = empty_rep
        needs_screenshot = False
    elif not text_input and not has_images:
        all_warnings.append("Nothing to analyse.")
        source = "none"
        records = []
        gaps = []
        report = empty_rep
        needs_screenshot = False
    else:
        all_warnings.append("No processing module configured for this platform.")
        source = "none"
        records = []
        gaps = []
        report = empty_rep
        needs_screenshot = False

    return {
        "source": source,
        "records": records,
        "warnings": all_warnings,
        "gaps": gaps,
        "report": report,
        "needs_screenshot": needs_screenshot,
    }
