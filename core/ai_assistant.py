"""
AI-powered screenshot analysis for ESXi network topology (Local OCR + Text LLM).

Topologies are processed through a local OCR pipeline (Grayscale, CLAHE,
bicubic upscaling) to extract text from screenshots. The consolidated OCR text
is then dispatched to the configured text LLM via ``core.ai_client.call_ai``.
Instructions and prompt rules are loaded dynamically from
``topology_parsing_presets`` in naming rules so no platform-specific hardcoded
prompts live in this module.

The result is an atomic JSON interfaces array returned by the LLM, which this
module normalises into standard DataFrame rows
(``Interface``, ``Type``, ``Description``, ``IP Address``, ``Slot``).
"""

import json
import logging

from config.naming_rules import load_naming_rules
from core.ai_client import call_ai
from core.ocr_engine import run_local_ocr_pipeline

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "google/gemini-2.0-flash"


def _resolve_active_model(model: str) -> str:
    if model:
        return model
    try:
        import streamlit as st
        for key in ("active_model", "approved_model"):
            val = st.session_state.get(key)
            if val:
                return val
    except Exception:
        pass
    from config.settings import AVAILABLE_MODELS
    if AVAILABLE_MODELS:
        return AVAILABLE_MODELS[0]
    return _DEFAULT_MODEL


def _resolve_platform_preset(platform_name: str) -> dict:
    """Return the topology-parsing preset dict for *platform_name*."""
    rules = load_naming_rules()
    presets = rules.get("topology_parsing_presets") or {}
    if platform_name and platform_name in presets:
        return presets[platform_name]
    # Fallback: pick the first available preset
    if presets:
        return next(iter(presets.values()))
    return {}


def _build_prompt_text(screenshots: list, platform_preset: dict) -> str:
    """Build the text prompt for the LLM from consolidated OCR output."""
    instructions = platform_preset.get("instructions", "")
    export_prompt = platform_preset.get("export_prompt", "")

    prompt_parts = []
    if instructions:
        prompt_parts.append(instructions)
    if export_prompt:
        prompt_parts.append(export_prompt)
    if not prompt_parts:
        prompt_parts.append(
            "You are an expert VMware ESXi networking engineer. "
            "Extract network topology information from the OCR text below."
        )

    system_text = "\n\n".join(prompt_parts)

    screenshot_lines = []
    for idx, name in enumerate(screenshots):
        screenshot_lines.append(f"--- Screenshot {idx + 1}: {name} ---")
    ocr_text = "\n".join(screenshot_lines)
    user_text = (
        "Analyze the following OCR-extracted text from topology screenshots and "
        "return the network interface data.\n\n" + ocr_text
    )

    return system_text, user_text


def _resolve_platform_name(images, naming_rules: dict) -> str:
    """Try to detect the platform name from naming_rules or default to ESXi."""
    if not isinstance(naming_rules, dict):
        return "VMware ESXi"
    # Check active_platform or similar session keys
    try:
        import streamlit as st
        active = st.session_state.get("active_platform")
        if active:
            return str(active)
    except Exception:
        pass
    return "VMware ESXi"


def _parse_interfaces_response(content: str) -> list:
    """Parse the atomic JSON array returned by the LLM."""
    text = (content or "").strip()
    if not text:
        raise RuntimeError("LLM returned an empty response.")

    # Strip optional markdown code fences.
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            raise RuntimeError("LLM response was not valid JSON: " + text[:300])
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Could not parse LLM JSON response: {e}") from e

    if isinstance(data, dict):
        data = data.get("interfaces") or data.get("items") or data.get("descriptions") or []
    if not isinstance(data, list):
        data = []
    return [d for d in data if isinstance(d, dict)]


def _normalize_interface_type(item: dict) -> str:
    iface = str(item.get("interface") or item.get("Interface") or "").strip()
    if iface.startswith("vmnic"):
        return "Uplink"
    if iface.startswith("vmk"):
        return "VMkernel"
    if "port_group" in str(item).lower() or iface.lower().startswith("pg-"):
        return "PortGroup"
    # Infer from context
    purpose = str(item.get("purpose") or "").strip()
    if purpose.lower() in ("", "management", "vmotion", "iscsi", "vSAN", "ft", "vflash"):
        return "Uplink"
    return "PortGroup"


def _row(item: dict) -> dict:
    iface_raw = str(item.get("interface") or item.get("Interface") or "").strip()
    itype = str(item.get("Type") or item.get("type") or "").strip() or _normalize_interface_type(item)
    desc_raw = str(item.get("Description") or item.get("description") or "").strip()
    ip_raw = str(item.get("IP Address") or item.get("ip") or "").strip()
    slot_raw = str(item.get("Slot") or item.get("slot") or "").strip()

    if not iface_raw or iface_raw in ("None", "none", "", "null"):
        return None

    # Clean description: prefer explicit Description; otherwise build one
    if not desc_raw:
        purpose = str(item.get("purpose") or "").strip()
        parent = str(item.get("parent") or item.get("vswitch") or "").strip()
        role = str(item.get("uplink_role") or "").strip()
        if itype == "Uplink":
            desc_raw = f"{iface_raw} - {parent or 'Unknown'} {purpose} {role}" if purpose or role else f"{iface_raw} - {parent or 'Unknown'}"
        elif itype == "PortGroup":
            active = str(item.get("active_vmnics") or item.get("active") or "").strip()
            standby = str(item.get("standby_vmnics") or item.get("standby") or "").strip()
            if active or standby:
                desc_raw = f"{parent or ''} ({active} Active / {standby} Standby)"
            else:
                desc_raw = f"{parent or iface_raw}"
        elif itype == "VMkernel":
            desc_raw = f"{purpose or 'Unknown'} ({parent or 'Unknown'})"
        else:
            desc_raw = iface_raw

    return {
        "Interface": iface_raw,
        "Type": itype,
        "Description": desc_raw.strip(),
        "IP Address": ip_raw,
        "Slot": slot_raw,
    }


def _sanitize_row(row: dict) -> dict:
    """Trim trailing parenthesized noise from Interface and clean fields."""
    iface = str(row.get("Interface") or "").strip()
    row["Interface"] = iface
    row["Description"] = str(row.get("Description") or "").strip()
    row["IP Address"] = str(row.get("IP Address") or "").strip()
    row["Slot"] = str(row.get("Slot") or "").strip()
    return row


def _deduplicate_rows(rows: list) -> list:
    seen = set()
    out = []
    for r in rows:
        iface = str(r.get("Interface") or "").strip().lower()
        itype = str(r.get("Type") or "").strip()
        key = (iface, itype)
        if key not in seen and iface:
            seen.add(key)
            out.append(r)
    return out


def analyze_hypervisor_topology_screenshot(images, naming_rules: dict, platform_name: str = "", active_model: str = "") -> list:
    """Run local OCR over screenshots, dispatch text to LLM, return dataframe rows.

    ``images`` may be a single upload or a list of file objects with a
    ``getvalue()`` method. ``active_model`` optionally names the configured AI
    model; when omitted it is resolved from session state or the first configured
    preset. Returns a list of dicts suitable for ``st.dataframe``:
    ``{"Interface", "Type", "Description", "IP Address", "Slot"}``.
    """
    if images is None:
        return []
    if not isinstance(images, list):
        images = [images]
    images = [im for im in images if im is not None]
    if not images:
        return []

    # Capture names (filenames or labels) from uploaded files
    screenshot_names = []
    image_refs = []
    for im in images:
        name = getattr(im, "name", None) or getattr(im, "filename", None) or ""
        screenshot_names.append(str(name))
        image_refs.append(im)

    model = _resolve_active_model(active_model)
    preset = _resolve_platform_preset(platform_name)
    system_text, user_text = _build_prompt_text(screenshot_names, preset)

    # Run local OCR pipeline on all uploaded screenshots
    ocr_result = run_local_ocr_pipeline(image_refs)
    ocr_text = ocr_result.get("combined_text", "")
    if not ocr_text:
        logger.warning("OCR produced no text from %d image(s)", len(images))
        return []

    # Append OCR text to the user prompt so the LLM has something to work with
    full_prompt = user_text + "\n\n--- Extracted OCR Text ---\n" + ocr_text

    try:
        content = call_ai(full_prompt, model, custom_system_msg=system_text, timeout=120)
    except Exception as e:
        logger.error("LLM call failed: %s", e, exc_info=True)
        raise RuntimeError(f"LLM API request failed: {e}") from e

    parsed = _parse_interfaces_response(content)
    sanitized = [_sanitize_row(r) for r in (_row(x) for x in parsed) if r is not None]
    return _deduplicate_rows(sanitized)


# Backward compatibility alias
analyze_esxi_topology_screenshot = analyze_hypervisor_topology_screenshot
