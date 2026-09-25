"""
AI-powered screenshot analysis for ESXi network topology (Vision).

Uses the system's configured LLM client (OpenRouter/OmniRoute-compatible API,
e.g. a Gemini vision model) to parse topology screenshots and emit NetBox-ready
interface descriptions for physical uplinks, port groups and VMkernel adapters.
"""

import base64
import json
import logging
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
3. Port Group names must follow the standard format, prefixed exactly as `PG-<Name>` (e.g. `PG-Management Network`, `PG-VM Network`).
4. Always pair each uplink with its respective vSwitch before listing that vSwitch's Port Groups and VMkernel adapters.

Naming rules per type:

- Physical Uplink:
  `<vmnicX> - <vSwitch> <Purpose> Active Uplink`
  or
  `<vmnicX> - <vSwitch> <Purpose> Standby Uplink`
- Port Group:
  `<vSwitch> (<vmnicX> Active / <vmnicY> Standby)`
- VMkernel:
  `<Purpose> Network (<vSwitch>)`

Internal / Isolated vSwitches (vSwitches with NO physical network adapters, e.g. \
"PR Spain Fuenmayor VLab"):
- Uplink: `None`
- Port Group description: `<vSwitch> (Internal Only / No Uplink)`
- Do NOT output empty parentheses like `( / )`.

Determine whether a link is "Active" or "Standby" by detecting its speed: 10 Gbps links \
are Active, 1 Gbps (or otherwise slower/redundant) links are Standby.

Return ONLY a raw JSON array (no markdown fences). Each element is an object with exactly \
these keys:
{"Interface": "...", "Type": "Uplink"|"PortGroup"|"VMkernel", \
"Description": "the final NetBox interface description string", "IP Address": "..."}

Populate "IP Address" only for VMkernel adapters where visible, otherwise "".

Do not invent values that are not visible in the screenshots. Use an empty string "" for \
any field you cannot determine.\
"""


def _build_vision_payload(images: list, naming_rules: dict, model: str) -> dict:
    context = ""
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
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Could not build naming context: %s", e)

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
            {"role": "system", "content": _VISION_SYSTEM_PROMPT},
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


def analyze_esxi_topology_screenshot(images, naming_rules: dict, active_model: str = "") -> list:
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
    return [_row(r) for r in parsed]


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
        return {
            "Interface": str(item.get("Interface") or item.get("name") or "").strip(),
            "Type": itype,
            "Description": str(
                item.get("Description") or item.get("description") or ""
            ).strip(),
            "IP Address": str(item.get("IP Address") or item.get("detail") or "").strip(),
        }
    return {
        "Type": itype,
        "Name": str(item.get("name") or "").strip(),
        "vSwitch": str(item.get("vswitch") or "").strip(),
        "Details": str(item.get("detail") or "").strip(),
        "NetBox Description": str(item.get("description") or "").strip(),
    }
