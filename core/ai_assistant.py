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

from config.settings import OPENROUTER_BASE_URL, OPENROUTER_API_KEY, AVAILABLE_MODELS

logger = logging.getLogger(__name__)

_VISION_SYSTEM_PROMPT = """\
You are an expert VMware ESXi networking engineer. You will be given screenshots of a \
vSphere/ESXi "Virtual switches" networking topology screen. Extract, from ALL provided \
images combined, the following items per virtual switch:

1. Physical Uplinks (vmnic adapters) — include the adapter name (e.g. vmnic0), the \
vSwitch it is attached to, the link speed (e.g. 10 Gbps / 1 Gbps) if visible, and \
infer an Active/Standby teaming status (higher/dual-speed adapters are Active; clearly \
redundant slower or backup links are Standby).
2. Port Groups — include the port group name and the vSwitch it belongs to, plus which \
physical uplinks are the Active and Standby teaming members if shown.
3. VMkernel adapters — include the vmk name (e.g. vmk0), the IP address / subnet if \
visible, the enabled service (Management, vMotion, vSAN, iSCSI, FT, etc.) and the \
vSwitch / port group it binds to.

Return ONLY a JSON array. Each element is an object with exactly these keys:
{"type": "Uplink"|"PortGroup"|"VMkernel", "name": "...", "vswitch": "...", \
"detail": "speed / IP / service / members", "description": "the final NetBox interface \
description string"}

For "description", produce a clean, standards-aligned NetBox description, e.g.:
- Uplink: "vmnic0 - vSwitch0 10 Gbps (Active)"
- PortGroup: "VM Network [vmnic0 Active / vmnic1 Standby]"
- VMkernel: "Management Network - vSwitch0 (vmk0, 192.168.1.1/24, vMotion)"

Do not invent values that are not visible in the screenshots. Use an empty string "" for \
any field you cannot determine. Do NOT wrap the array in markdown fences — output raw JSON.\
"""


def _build_vision_payload(images: list, naming_rules: dict) -> dict:
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
        "model": AVAILABLE_MODELS[0] if AVAILABLE_MODELS else "google/gemini-2.0-flash",
        "messages": [
            {"role": "system", "content": _VISION_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
    }


def analyze_esxi_topology_screenshot(images, naming_rules: dict) -> list:
    """Run AI Vision over the provided screenshot file objects and return dataframe rows.

    ``images`` may be a single upload or a list of ``UploadedFile`` objects. Returns a
    list of dicts suitable for ``st.dataframe``:
    ``{"Type", "Name", "vSwitch", "Details", "NetBox Description"}``.
    """
    if images is None:
        return []
    if not isinstance(images, list):
        images = [images]
    images = [im for im in images if im is not None]
    if not images:
        return []

    endpoint = f"{OPENROUTER_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = _build_vision_payload(images, naming_rules)

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
    itype = str(item.get("type") or "").strip() or "Uplink"
    return {
        "Type": itype,
        "Name": str(item.get("name") or "").strip(),
        "vSwitch": str(item.get("vswitch") or "").strip(),
        "Details": str(item.get("detail") or "").strip(),
        "NetBox Description": str(item.get("description") or "").strip(),
    }
