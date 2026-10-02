"""
AI-powered screenshot analysis for ESXi network topology (Vision).

Uses the system's configured LLM client (OpenRouter/OmniRoute-compatible API,
e.g. a Gemini vision model) to parse topology screenshots and emit NetBox-ready
interface descriptions for physical uplinks, port groups and VMkernel adapters.
"""

import base64
import json
import logging
import re
import requests

from config.settings import OPENROUTER_BASE_URL, OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

_VISION_SYSTEM_PROMPT = """\
You are an expert VMware ESXi networking engineer. You will be given screenshots of a \
vSphere/ESXi "Virtual switches" networking topology screen. Extract, from ALL provided \
images combined, the following items per virtual switch:

1. Physical Uplinks (vmnic adapters) — include the adapter name (e.g. vmnic0), the \
vSwitch it is attached to, the link speed (e.g. 10 Gbps / 1 Gbps) if visible, and \
the link purpose / service.
2. Port Groups — include the port group name and the vSwitch it belongs to, plus which \
physical uplinks are the Active and Standby teaming members if shown.
3. VMkernel adapters — include the vmk name (e.g. vmk0), the IP address / subnet if \
visible, the enabled service (Management, vMotion, vSAN, iSCSI, FT, etc.) and the \
vSwitch / port group it binds to.

Apply the NetBox standard naming rules below strictly:

1. Identify each `vSwitch` in the topology.
2. Group and order the output per vSwitch as follows:
   1. Physical Uplinks (`vmnicX`)
   2. Port Groups
   3. VMkernel adapters (`vmkX`)
3. Port Group names must follow the standard format configured in the naming rule presets below (e.g. a `PG-` prefix when the configured pattern requires it).
4. Always pair each uplink with its respective vSwitch before listing that vSwitch's Port Groups and VMkernel adapters.

When analyzing 'Physical adapters' properties or screenshots:
 1. Locate the 'Location' field (e.g. 'PCI 0000:08:00.1', 'PCI 0000:5b:00.0').
 2. Extract the PCI BDF address (e.g. '08:00.1', '5b:00.0') as the raw hardware location.
 3. If a friendly PCIe/Card label is visible (e.g. 'PCIe 1 / Port 1', 'Slot 2'), normalize to 'PCIeX/PortY' or 'CardX/PortY'. Otherwise, store the normalized PCI identifier: 'PCI:08:00.1' or '08:00.1'.
 4. Cross-reference every vmnic/physical adapter item with its slot value and ALWAYS populate the 'Slot' key in the output JSON dictionary (e.g. Slot: 'PCI:08:00.1'). Never leave 'Slot' blank when a Location or PCI address is visible.

Naming rules per type (these apply to the **Description** field ONLY):

- Physical Uplink Description:
  `<vmnicX> - <vSwitch> <Purpose> Active Uplink`
  or
  `<vmnicX> - <vSwitch> <Purpose> Standby Uplink`
- Port Group Description:
  `<vSwitch> (<vmnicX> Active / <vmnicY> Standby)`
- VMkernel Description:
  `<Purpose> (<vSwitch>)`

Each Uplink description MUST infer its primary purpose from the PortGroups / VMkernels connected to the same vSwitch (e.g. Management, iSCSI, vMotion, VM Traffic). The Description format MUST strictly be: '<vmnic> - <vSwitch> <Purpose> Active Uplink' (or Standby Uplink).

**CRITICAL: Interface vs Description separation**
The "Interface" field MUST contain ONLY the bare, pure identifier — NEVER the full generated description string:
- For Uplinks (`Type: "Uplink"`): Interface MUST be the bare adapter name only (e.g., "vmnic0", "vmnic4"). Never include the switch name, purpose, or role in the Interface field.
- For PortGroups (`Type: "PortGroup"`): Interface MUST be the Port Group name only (e.g., "Management Network", "VM Network", "iSCSI01").
- For VMkernels (`Type: "VMkernel"`): Interface MUST be the kernel adapter name only (e.g., "vmk0", "vmk1").
- The generated naming standard description string belongs ONLY in the "Description" field.

Return ONLY a raw JSON array (no markdown fences). Each element is an object with exactly \
these keys:
{"Interface": "...", "Type": "Uplink"|"PortGroup"|"VMkernel", \
"Description": "the final NetBox interface description string", "IP Address": "...", \
"Slot": "normalized hardware slot like PCI:08:00.1 or PCIe1/Port1 or empty string"}

Populate "IP Address" only for VMkernel adapters where visible, otherwise "".

Do not invent values that are not visible in the screenshots. Use an empty string "" for \
any field you cannot determine.\
"""


def _build_vision_payload(images: list, naming_rules: dict, model: str) -> dict:
    context = ""
    preset_rules = ""
    if naming_rules:
        try:
            presets = naming_rules.get("esxi_network_presets") or []
            if presets:
                lines = []
                for p in presets:
                    code = p.get("code", "")
                    pat = p.get("pattern", "") or ""
                    lines.append(f"{code}: {pat}")
                context = "Configured ESXi description patterns to honor:\n" + "\n".join(lines)
            preset_rules = "\n".join([
                f"- Type: {p.get('label', p.get('code'))} | Pattern: '{p.get('pattern_template') or p.get('pattern', '')}'"
                for p in presets if isinstance(p, dict)
            ])
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not build naming context: %s", e)

    dynamic_rules = (
        "\n\nConfigured naming rule presets (govern prefixes, templates and formatting "
        "strictly; never invent prefixes beyond these):\n" + preset_rules
    ) if preset_rules else ""

    system_prompt = _VISION_SYSTEM_PROMPT + dynamic_rules

    user_text = (
        "Analyze the ESXi virtual switch topology screenshots below and extract the "
        "physical uplinks, port groups and VMkernel adapters.\n\n" + context
    )

    content = [{"type": "text", "text": user_text}]
    for img in images:
        raw = img.getvalue()
        b64 = base64.b64encode(raw).decode("ascii")
        mime = getattr(img, "type", None) or "image/png"
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )

    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
    }


def _resolve_active_model(model: str = "") -> str:
    """Resolve the active AI Assistant model from an explicit value, session state or the
    first configured preset, so the vision call never hardcodes a vendor model."""
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
    return "google/gemini-2.0-flash"


def analyze_hypervisor_topology_screenshot(images, naming_rules: dict, active_model: str = "") -> list:
    """Run AI Vision over the provided screenshot file objects and return dataframe rows.

    ``images`` may be a single upload or a list of ``UploadedFile`` objects. ``active_model``
    optionally names the configured AI model; when omitted it is resolved from session state or the
    first configured preset. Returns a list of dicts suitable for ``st.dataframe``:
    ``{"Interface", "Type", "Description", "IP Address"}``.
    """
    if images is None:
        return []
    if not isinstance(images, list):
        images = [images]
    images = [im for im in images if im is not None]
    if not images:
        return []

    model = _resolve_active_model(active_model)
    endpoint = f"{OPENROUTER_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = _build_vision_payload(images, naming_rules, model)

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Vision API request failed: %s", e, exc_info=True)
        raise RuntimeError(f"Vision API request failed: {e}") from e

    try:
        body = resp.json()
    except ValueError as e:
        raise RuntimeError(f"Vision API returned invalid JSON: {resp.text[:200]}") from e

    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("Vision API returned no choices.")
    content = choices[0].get("message", {}).get("content") or ""

    parsed = _parse_content(content)
    sanitized = [_sanitize_row(r) for r in (_row(x) for x in parsed) if r is not None]
    return _deduplicate_rows(sanitized)


# Backward compatibility alias
analyze_esxi_topology_screenshot = analyze_hypervisor_topology_screenshot


def _parse_content(content: str) -> list:
    text = (content or "").strip()
    if not text:
        raise RuntimeError("Vision API returned an empty response.")

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
            raise RuntimeError("Vision response was not valid JSON: " + text[:300])
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Could not parse Vision JSON response: {e}") from e

    if isinstance(data, dict):
        data = data.get("items") or data.get("descriptions") or data.get("rows") or []
    if not isinstance(data, list):
        data = []
    return [d for d in data if isinstance(d, dict)]


def _row(item: dict) -> dict:
    itype = str(item.get("type") or item.get("Type") or "").strip() or "Uplink"
    if "Interface" in item or "IP Address" in item:
        iface = str(item.get("Interface") or item.get("name") or "").strip()
        desc = str(
            item.get("Description") or item.get("description") or ""
        ).strip()
        if iface in ["None", "none", "", "null"]:
            return None
        if desc.endswith("None Uplink"):
            return None
        if itype == "Uplink" and iface == "":
            return None
        return {
            "Interface": iface,
            "Type": itype,
            "Description": desc,
            "IP Address": str(item.get("IP Address") or item.get("detail") or "").strip(),
            "Slot": str(item.get("Slot") or item.get("slot") or "").strip(),
        }
    return {
        "Type": itype,
        "Name": str(item.get("name") or "").strip(),
        "parent": str(item.get("vswitch") or "").strip(),
        "Details": str(item.get("detail") or "").strip(),
        "NetBox Description": str(item.get("description") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Sanitization & deduplication
# ---------------------------------------------------------------------------

# Token patterns that signal a "pure" Interface value for each Type.
_IFACE_TOKEN_RE = re.compile(
    r"\b(vmnic\d+|eno\w+|ens\w+|enp\w+|eth\d+|vmk\d+|vSwitch\w*|DSwitch\w*)\b",
    re.IGNORECASE,
)


def _sanitize_row(row: dict) -> dict:
    """Extract a bare identifier from the Interface field and clean surrounding noise."""
    iface = str(row.get("Interface") or "").strip()
    itype = str(row.get("Type") or "").strip()

    # If the Interface already looks clean (single token, no dashes/spaces beyond the token), keep it.
    # Otherwise try to extract the leading token from a polluted description string.
    m = _IFACE_TOKEN_RE.match(iface)
    if m and not any(c in iface for c in ["-", " ", "/", "("]):
        row["Interface"] = m.group(1)
    elif m:
        row["Interface"] = m.group(1)

    # Trim trailing parenthesized clauses that leaked from descriptions.
    row["Interface"] = re.sub(r"\s*\([^)]*\)\s*$", "", row["Interface"]).strip()
    row["Description"] = str(row.get("Description") or "").strip()
    row["IP Address"] = str(row.get("IP Address") or "").strip()
    row["Slot"] = str(row.get("Slot") or "").strip()
    return row


def _deduplicate_rows(rows: list) -> list:
    """Keep only the first occurrence of each (Interface, Type) pair."""
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
