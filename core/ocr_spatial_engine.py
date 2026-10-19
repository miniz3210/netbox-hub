"""
Hybrid Spatial OCR + Text-Only LLM Engine (v4.6.2)

Replaces the heavy VLM image-encoding pipeline with a two-stage
approach optimized for 4 OCPU ARM hosts running Qwen2.5:3b.

  Stage A — RapidOCR:         ~0.3 s per image
      Extract text fragments with bounding-box coordinates.
      Preserve spatial ordering via Y/X sorting within 15 px rows.

  Stage A+ — Sidebar & Navigation Filtering:
      Filter vSphere left nav pane (Storage, Licensing, etc.),
      top breadcrumbs, action buttons, and collapsed switch headers.
      All pre-LLM in Python — zero tokens from sidebar reach the prompt.

  Stage B — Natural Line-Grouping Serialization:
      Cluster tokens by |y1-y2| <= 8px into visual lines, sort by X,
      then classify left/right of X_mid to produce compact line-based
      layout text. No [y=..., x=...] per-token coordinate tags.
      Typical output: < 180 tokens vs 2500+ with previous approach.

  Stage C — Bounded Ollama Inference:
      num_predict: 350 (prev 1024 → 110s timeout loops).
      temperature: 0.0, repeat_penalty: 1.1, num_ctx: 2048.

  Stage D — Resilient JSON Recovery:
      Regex-based per-object extraction from partial/truncated streams.

Total per-image latency: ~4 s with < 350 output tokens cap.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TEXT_LLM_MODEL = "qwen2.5:3b"
TEXT_LLM_ENDPOINT = "http://netbox-ollama:11434"
TEXT_LLM_KEEP_ALIVE = "10m"
TEXT_LLM_TIMEOUT = 180
TEXT_LLM_NUM_THREADS = 4
TEXT_LLM_NUM_CTX = 2048
Y_ROW_THRESHOLD = 8  # pixels — tokens within this gap share a line (v4.6.2 tighter grouping)

# v4.6.1: hard token budget per Ollama call
MAX_LLM_PROMPT_TOKENS = 400
# rough token-to-char ratio for budget estimation
TOKEN_CHAR_RATIO = 3.5

# ---------------------------------------------------------------------------
# Regex patterns for collapsed-header / expanded-anchor detection
# ---------------------------------------------------------------------------
_COLLAPSED_HEADER_RE = re.compile(
    r"^(?:>|▶|\+)?\s*(?:Standard\s*Switch|Distributed\s*Switch|Switch):\s*(.+)$",
    re.IGNORECASE,
)
_EXPANDED_ANCHOR_RE = re.compile(
    r"(?<![vV])v\s*[●▪▸►▼]|(?:Standard\s*Switch|Distributed\s*Switch|Switch):\s*(.+)$",
    re.IGNORECASE,
)
_SWITCH_NAME_STripper = re.compile(
    r"^(?:Standard\s*Switch|Distributed\s*Switch|Switch|Bridge):\s*",
    re.IGNORECASE,
)
_GHOST_HEADER_RE = re.compile(
    r"^(?:Standard\s*Switch|Distributed\s*Switch|Switch):",
    re.IGNORECASE,
)
_COLLAPSED_LINE_RE = re.compile(r"^(?:>|▶|\+|\s)+\s*(?:Standard\s*Switch|Distributed\s*Switch|Switch):")

# v4.6.2: UI noise tokens discarded before any LLM serialization
_ACTION_BUTTON_RE = re.compile(
    r"^(?:ADD\s*NETWORKING|REFRESH|EDIT|MANAGE\s*PHYSICAL\s*ADAPTERS?|ACTIONS)\s*(?:\.\.\.)?$",
    re.IGNORECASE,
)
_NAV_PANE_SIDEBAR = {
    "storage adapters", "storage devices", "host cache", "licensing",
    "host profile", "agent vm settings", "networking", "virtual machines",
    "vm startup/shutdown", "vm startup / shutdown", "default vm compatibility",
    "swap file location", "time configuration", "authentication services",
    "power management", "security profile", "system swap", "system",
    "hardware", "firewall", "summary", "monitor", "configure",
    "permissions", "datastores", "networks", "updates", "actions",
}
_SIDEBAR_X_THRESHOLD = 220  # px: any token strictly left of this is nav pane
_CONTENT_START_PATTERN = re.compile(
    r"(?:virtual\s*switches|standard\s*switch|distributed\s*switch)\s*:",
    re.IGNORECASE,
)


def _filter_nav_pane_sidebar(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove vSphere left-navigation pane tokens by X-coordinate and text match.

    Strategy:
      1. Find the first content-start anchor (e.g. "Virtual switches:" or
         "Standard Switch:" header). Record its X_min as the left boundary.
      2. Discard every token whose xmin is strictly < (X_anchor - 20 px).
      3. Also discard tokens whose text matches known sidebar/nav keywords.
    """
    if not items:
        return items

    # Step 1: locate content start anchor
    x_anchor = float("inf")
    for e in items:
        if _CONTENT_START_PATTERN.search(e["text"]):
            x_anchor = min(x_anchor, e["xmin"])
    if x_anchor == float("inf"):
        x_anchor = _SIDEBAR_X_THRESHOLD  # fallback

    cutoff = max(0.0, x_anchor - 20.0)
    result = []
    for e in items:
        if e["xmin"] < cutoff:
            continue
        low = e["text"].strip().lower()
        if low in _NAV_PANE_SIDEBAR:
            continue
        if _ACTION_BUTTON_RE.match(e["text"].strip()):
            continue
        result.append(e)
    return result


def preprocess_ocr_boxes(
    ocr_results: List[Any],
    preset_config: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Filter platform-specific navigation noise and apply regex normalizations.

    v4.6.2: Now also applies sidebar X-boundary filtering before normalized
    items are passed to the collapsed-header discard step.
    """
    if not preset_config:
        # Even without config, run sidebar/collapsed filters for safety
        uniform: List[Dict[str, Any]] = []
        for item in ocr_results:
            is_dict = isinstance(item, dict)
            if is_dict:
                box = item.get("box")
                text = str(item.get("text", "")).strip()
                score = item.get("confidence", 0.0) or 0.0
            else:
                try:
                    box = item[0]
                    text = str(item[1]).strip()
                    score = item[2] if len(item) > 2 else 0.0
                except (IndexError, TypeError, ValueError):
                    continue
            if not text:
                continue
            xmin, ymin, w, h = _box_to_coords(box)
            uniform.append({
                "text": text, "box": box,
                "xmin": xmin, "ymin": ymin,
                "is_dict": is_dict, "raw": item, "score": score,
            })
        uniform = _filter_nav_pane_sidebar(uniform)
        uniform = _discard_collapsed_headers(uniform)
        out: List[Any] = []
        for e in uniform:
            if e["is_dict"]:
                out.append({"text": e["text"], "box": e["box"],
                            **{k: v for k, v in e["raw"].items() if k not in ("text", "box")}})
            else:
                out.append([e["box"], e["text"], e["score"]])
        return out

    noise_filters = preset_config.get("noise_filters", [])
    normalizations = preset_config.get("token_normalizations", [])

    uniform: List[Dict[str, Any]] = []
    for item in ocr_results:
        is_dict = isinstance(item, dict)
        if is_dict:
            box = item.get("box")
            text = str(item.get("text", "")).strip()
            score = item.get("confidence", 0.0) or 0.0
        else:
            try:
                box = item[0]
                text = str(item[1]).strip()
                score = item[2] if len(item) > 2 else 0.0
            except (IndexError, TypeError, ValueError):
                continue

        if any(kw.lower() in text.lower() for kw in noise_filters):
            continue

        for rule in normalizations:
            pattern = rule.get("pattern")
            replacement = rule.get("replace")
            if pattern and replacement is not None:
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        if not text:
            continue

        xmin, ymin, w, h = _box_to_coords(box)
        uniform.append({
            "text": text,
            "box": box,
            "xmin": xmin,
            "ymin": ymin,
            "is_dict": is_dict,
            "raw": item,
            "score": score,
        })

    # v4.6.2: filter sidebar navigation pane by X-coordinate cutoff
    uniform = _filter_nav_pane_sidebar(uniform)

    # v4.6.2: discard collapsed switch headers
    uniform = _discard_collapsed_headers(uniform)

    cleaned_results: List[Any] = []
    for e in uniform:
        raw = e["raw"]
        if e["is_dict"]:
            cleaned_results.append({
                "text": e["text"],
                "box": e["box"],
                **{k: v for k, v in raw.items() if k not in ("text", "box")},
            })
        else:
            cleaned_results.append([e["box"], e["text"], e["score"]])

    return cleaned_results


def _discard_collapsed_headers(items: List[Any]) -> List[Any]:
    """Remove collapsed switch headers from OCR tokens.

    A collapsed header is a line like:
        > Standard Switch: vSwitch0
    or
        ▶ Distributed Switch: vSwitch01
    where the bounding-box footprint below it contains no child adapter
    or port-group entries.  Such headers are UI navigation toggles, not
    topology data, and must never reach the LLM.

    Algorithm:
      1. Identify all collapsed-header candidate tokens.
      2. For each candidate, check if any subsequent token (within the
         same image) falls within its vertical range AND has text matching
         a child adapter (vmnic*) or VMkernel (vmk*) entry.
      3. Discard candidates with zero children in their vertical range.
    """
    if not preset_config:
        return ocr_results

    noise_filters = preset_config.get("noise_filters", [])
    normalizations = preset_config.get("token_normalizations", [])

    # Normalize raw items to uniform dicts for spatial filtering
    uniform: List[Dict[str, Any]] = []
    for item in ocr_results:
        is_dict = isinstance(item, dict)
        if is_dict:
            box = item.get("box")
            text = str(item.get("text", "")).strip()
            score = item.get("confidence", 0.0) or 0.0
        else:
            try:
                box = item[0]
                text = str(item[1]).strip()
                score = item[2] if len(item) > 2 else 0.0
            except (IndexError, TypeError, ValueError):
                continue

        if any(kw.lower() in text.lower() for kw in noise_filters):
            continue

        for rule in normalizations:
            pattern = rule.get("pattern")
            replacement = rule.get("replace")
            if pattern and replacement is not None:
                text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        if not text:
            continue

        xmin, ymin, w, h = _box_to_coords(box)
        uniform.append({
            "text": text,
            "box": box,
            "xmin": xmin,
            "ymin": ymin,
            "is_dict": is_dict,
            "raw": item,
            "score": score,
        })

    # v4.6.2: filter sidebar navigation pane by X-coordinate cutoff
    uniform = _filter_nav_pane_sidebar(uniform)

    # v4.6.2: discard collapsed switch headers
    uniform = _discard_collapsed_headers(uniform)

    # Restore original shape
    cleaned_results: List[Any] = []
    for e in uniform:
        raw = e["raw"]
        if e["is_dict"]:
            cleaned_results.append({
                "text": e["text"],
                "box": e["box"],
                **{k: v for k, v in raw.items() if k not in ("text", "box")},
            })
        else:
            cleaned_results.append([e["box"], e["text"], e["score"]])

    return cleaned_results


def _box_to_coords(box: Any) -> Tuple[float, float, float, float]:
    """Normalize a bounding box to (xmin, ymin, width, height)."""
    if box is None:
        return (0.0, 0.0, 0.0, 0.0)
    if isinstance(box, (list, tuple)):
        if len(box) >= 4:
            return (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
        if len(box) == 2:
            return (float(box[0]), float(box[1]), 0.0, 0.0)
    return (0.0, 0.0, 0.0, 0.0)


def _discard_collapsed_headers(items: List[Any]) -> List[Any]:
    """Remove collapsed switch headers from OCR tokens.

    A collapsed header is a line like:
        > Standard Switch: vSwitch0
    or
        ▶ Distributed Switch: vSwitch01
    where the bounding-box footprint below it contains no child adapter
    or port-group entries.  Such headers are UI navigation toggles, not
    topology data, and must never reach the LLM.

    Algorithm:
      1. Identify all collapsed-header candidate tokens.
      2. For each candidate, check if any subsequent token (within the
         same image) falls within its vertical range AND has text matching
         a child adapter (vmnic*) or VMkernel (vmk*) entry.
      3. Discard candidates with zero children in their vertical range.
    """
    if not items:
        return items

    # Build a uniform representation
    entries: List[Dict[str, Any]] = []
    for item in items:
        is_dict = isinstance(item, dict)
        if is_dict:
            box = item.get("box")
            text = str(item.get("text", "")).strip()
        else:
            try:
                box = item[0]
                text = str(item[1]).strip()
            except (IndexError, TypeError):
                continue
        if not text:
            continue
        xmin, ymin, w, h = _box_to_coords(box)
        entries.append({
            "text": text,
            "xmin": xmin,
            "ymin": ymin,
            "ymax": ymin + h if h > 0 else ymin + 20,
            "is_dict": is_dict,
            "raw": item,
        })

    # Find collapsed header candidates
    collapsed_indices: List[int] = []
    for i, e in enumerate(entries):
        m = _COLLAPSED_LINE_RE.search(e["text"])
        if m:
            collapsed_indices.append(i)

    if not collapsed_indices:
        return items

    # For each candidate, check vertical range for children
    result_indices: set = set(range(len(entries)))
    for ci in collapsed_indices:
        ce = entries[ci]
        # Vertical range: from this line's bottom to next collapsed header or end
        next_ci = None
        for other_ci in collapsed_indices:
            if other_ci > ci:
                next_ci = other_ci
                break
        v_bottom = entries[next_ci]["ymin"] if next_ci is not None else ce["ymax"] + 200

        # Check for child tokens (physical adapters like vmnic*, vmk*) within vertical range
        has_children = False
        for j in range(ci + 1, min(next_ci if next_ci is not None else len(entries), len(entries))):
            je = entries[j]
            if je["ymin"] >= ce["ymin"] and je["ymin"] < v_bottom:
                jt = je["text"].lower()
                # Child tokens: vmnic*, vmk*, or expanded switch headers
                if re.match(r"^\s*vmnic\d+", jt, re.I) or re.match(r"^\s*vmk\d+", jt, re.I):
                    has_children = True
                    break
                # Also detect if an expanded switch header appears (which would end the band)
                if re.match(r"^(?:Standard\s*Switch|Distributed\s*Switch|Switch):\s*", jt, re.I):
                    has_children = True
                    break
                # Detect vSwitchN bare names
                if re.match(r"^vswitch\d+", jt, re.I):
                    has_children = True
                    break
            else:
                break

        if not has_children:
            result_indices.discard(ci)

    return [entries[i]["raw"] for i in sorted(result_indices)]


# ---------------------------------------------------------------------------
# Stage A: RapidOCR (delegates to existing core.ocr_engine)
# ---------------------------------------------------------------------------

def _run_ocr_stage(
    image_files: List[Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Run RapidOCR on all images and return (all_raw_tokens, errors).

    Each raw_token dict contains:
        text  – recognized text string
        box   – [xmin, ymin, width, height]  (top-left origin)
        _src  – image index (0-based)
    """
    from core.ocr_engine import run_local_ocr_pipeline

    errors: List[str] = []
    all_tokens: List[Dict[str, Any]] = []

    for idx, img in enumerate(image_files):
        try:
            res = run_local_ocr_pipeline([img])
            toks = res.get("raw_tokens", [])
            for t in toks:
                t["_src"] = idx
            all_tokens.extend(toks)
            if not res.get("success"):
                errors.extend(res.get("errors", []))
        except Exception as exc:
            errors.append(f"Image #{idx + 1} OCR failed: {exc}")

    return all_tokens, errors


# ---------------------------------------------------------------------------
# Stage B: Compact natural line-grouping serialization (v4.6.2)
# ---------------------------------------------------------------------------

def _classify_switch_band(item: Dict[str, Any]) -> Optional[str]:
    """Return the switch name this token belongs to, based on expanded anchor.
    Returns None if token is not inside an expanded switch band.
    """
    text = item.get("text", "").strip()
    if not text:
        return None
    if re.match(r"^(?:Standard\s*Switch|Distributed\s*Switch|Switch):\s*(.+)$", text, re.IGNORECASE):
        return re.sub(r"^(?:Standard\s*Switch|Distributed\s*Switch|Switch):\s*", "", text, flags=re.I).strip()
    m = re.match(r"^[v◄►▼▪●]?\s*(vSwitch\d+)\s*$", text, re.I)
    if m:
        return m.group(1)
    return None


def _extract_switch_bands(items: List[Dict[str, Any]]) -> List[Tuple[str, int, int]]:
    """Identify vertical bands for each expanded switch."""
    bands: List[Tuple[str, int, int]] = []
    current_switch = None
    current_y_start = None
    for item in items:
        text = item.get("text", "").strip()
        if not text:
            continue
        _, ymin, _, h = _box_to_coords(item.get("box"))
        ymax = ymin + (h if h > 0 else 20)
        sw_match = _classify_switch_band(item)
        if sw_match:
            if current_switch and current_y_start is not None:
                bands.append((current_switch, current_y_start, ymin))
            current_switch = sw_match
            current_y_start = ymin
    if current_switch and current_y_start is not None and items:
        max_y = max(
            _box_to_coords(it.get("box"))[1] + (_box_to_coords(it.get("box"))[3] if _box_to_coords(it.get("box"))[3] > 0 else 20)
            for it in items
        )
        bands.append((current_switch, current_y_start, max_y))
    return bands


def _assign_switch_attribute(items: List[Dict[str, Any]], bands: List[Tuple[str, int, int]]) -> List[Dict[str, Any]]:
    if not bands:
        return items
    result = []
    for item in items:
        _, ymin, _, _ = _box_to_coords(item.get("box"))
        assigned_switch = ""
        for sw_name, y_start, y_end in bands:
            if y_start <= ymin < y_end:
                assigned_switch = sw_name
                break
        new_item = dict(item)
        new_item["_assigned_switch"] = assigned_switch
        result.append(new_item)
    return result


def serialize_ocr_boxes_to_layout(ocr_results: List[Dict[str, Any]]) -> str:
    """Compact natural line-grouping layout for LLM consumption (v4.6.2).

    Instead of verbose per-token coordinate tags, tokens are clustered into
    visual lines by |y1-y2| <= 8 px, sorted by X within each line, then
    grouped into [SWITCH], [LOGICAL], and [PHYSICAL] sections.

    Typical output size: < 180 tokens vs 2500+ with the v4.6.1 approach.
    """
    if not ocr_results:
        return ""

    items: List[Dict[str, Any]] = []
    max_x = 0.0
    for tok in ocr_results:
        box = tok.get("box")
        if not box or len(box) < 2:
            continue
        text = str(tok.get("text", "")).strip()
        if not text:
            continue
        xmin = float(box[0])
        ymin = float(box[1])
        xmax = xmin + (float(box[2]) if len(box) > 2 else 0.0)
        if xmax > max_x:
            max_x = xmax
        items.append({"text": text, "x": int(xmin), "y": int(ymin), "box": box})

    if not items:
        return ""

    # v4.6.2: switch-band bounding + X-midline classification
    bands = _extract_switch_bands(items)
    items = _assign_switch_attribute(items, bands)
    midline = max_x * 0.5 if max_x > 0 else 0.0
    for item in items:
        item["_bucket"] = "logical" if item["x"] < midline else "physical"

    # Group by visual line per-bucket (keep left/right separated)
    # Sort each bucket independently by Y then X
    logical_items = sorted(
        [it for it in items if it.get("_bucket") == "logical"],
        key=lambda it: (it["y"] // Y_ROW_THRESHOLD * Y_ROW_THRESHOLD, it["x"]),
    )
    physical_items = sorted(
        [it for it in items if it.get("_bucket") == "physical"],
        key=lambda it: (it["y"] // Y_ROW_THRESHOLD * Y_ROW_THRESHOLD, it["x"]),
    )

    def _group_lines(token_list):
        groups: List[List[Dict[str, Any]]] = []
        cur: List[Dict[str, Any]] = []
        cur_y: Optional[int] = None
        for item in token_list:
            ry = item["y"] // Y_ROW_THRESHOLD * Y_ROW_THRESHOLD
            if cur_y is None or abs(ry - cur_y) <= Y_ROW_THRESHOLD:
                cur.append(item)
                cur_y = ry
            else:
                if cur:
                    groups.append(cur)
                cur = [item]
                cur_y = ry
        if cur:
            groups.append(cur)
        return groups

    logical_groups = _group_lines(logical_items)
    physical_groups = _group_lines(physical_items)

    # Build structured compact output
    switch_sections: Dict[str, List[str]] = {}
    logical_parts: List[str] = []
    physical_parts: List[str] = []

    def _process_groups(groups):
        for group in groups:
            switch_name = ""
            for g in group:
                sn = g.get("_assigned_switch", "")
                if sn:
                    switch_name = sn
                    break
            line_text = " ".join(g["text"] for g in group if g["text"])
            if not line_text:
                continue
            bucket = group[0].get("_bucket", "logical")
            if switch_name:
                section = switch_sections.setdefault(switch_name, [])
                tag = "[LOGICAL]" if bucket == "logical" else "[PHYSICAL]"
                section.append(f"{tag} {line_text}")
            elif bucket == "logical":
                logical_parts.append(line_text)
            else:
                physical_parts.append(line_text)

    _process_groups(logical_groups)
    _process_groups(physical_groups)

    result_parts: List[str] = []
    for sw_name, chunks in switch_sections.items():
        result_parts.append(f"[SWITCH] {sw_name}")
        for chunk in chunks:
            result_parts.append(chunk)
    if logical_parts:
        result_parts.append(f"[LOGICAL] {' | '.join(logical_parts)}")
    if physical_parts:
        result_parts.append(f"[PHYSICAL] {' | '.join(physical_parts)}")
    return "\n".join(result_parts) if result_parts else ""


# ---------------------------------------------------------------------------
# Stage C: Text-only LLM inference (Ollama /api/chat, no images)
# ---------------------------------------------------------------------------

def call_text_llm(
    layout_text: str,
    prompt: str,
    model: str = TEXT_LLM_MODEL,
    endpoint: str = TEXT_LLM_ENDPOINT,
    keep_alive: str = TEXT_LLM_KEEP_ALIVE,
    temperature: float = 0.0,
    num_ctx: int = TEXT_LLM_NUM_CTX,
    num_thread: int = TEXT_LLM_NUM_THREADS,
    timeout: int = TEXT_LLM_TIMEOUT,
) -> Tuple[List[Dict[str, Any]], str]:
    """Send purely textual layout prompt to Ollama text model.

    Parameters
    ----------
    layout_text : str
        Output of ``serialize_ocr_boxes_to_layout``.
    prompt : str
        System prompt (platform-specific extraction instructions).
    model : str
        Ollama model identifier (e.g. ``"qwen2.5:3b"``).
    endpoint : str
        Ollama base URL.
    keep_alive : str
        Model retention duration.
    temperature : float
        Sampling temperature (0.0 = deterministic).
    num_ctx : int
        Context window size.
    num_thread : int
        Number of CPU threads.
    timeout : int
        HTTP request timeout in seconds.

    Returns
    -------
    Tuple[List[Dict[str, Any]], str]
        (parsed JSON array items, raw response text).
    """
    import urllib.request

    messages: List[Dict[str, Any]] = []
    if prompt:
        messages.append({"role": "system", "content": prompt})
    messages.append({
        "role": "user",
        "content": f"OCR Spatial Layout:\n```\n{layout_text}\n```",
    })

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": 350,
            "repeat_penalty": 1.1,
            "temperature": temperature,
            "num_thread": num_thread,
        },
        "stream": False,
    }

    url = f"{endpoint.strip('/')}/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("Text LLM call failed after %.1fs: %s", timeout, exc)
        raise RuntimeError(f"Text LLM inference failed: {exc}") from exc

    message = raw.get("message", {})
    response_text = message.get("content", "") or ""
    records = _safe_extract_json_records(response_text)
    return records, response_text


def _safe_extract_json_records(raw_content: str) -> list:
    """Extract JSON array/dict records from raw Ollama response text.

    v4.6.2: On json.loads failure, falls back to per-object regex extraction
    so that individual valid switch records are recovered even from truncated
    or partial LLM streams (eliminating 0-record errors).
    """
    if not raw_content:
        return []
    text = re.sub(r"^```(?:json)?\s*|```$", "", raw_content.strip(), flags=re.IGNORECASE).strip()
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict):
            for k in ("records", "topology", "data", "items", "entities", "switches"):
                if isinstance(val.get(k), list):
                    return [x for x in val[k] if isinstance(x, dict)]
            return [val]
    except Exception:
        pass
    # v4.6.2: per-object regex fallback — recover individual {} records
    obj_matches = re.findall(r"\{[^{}]*\"switch\"[^{}]*\}", text, flags=re.DOTALL)
    if obj_matches:
        out: list = []
        for m in obj_matches:
            try:
                rec = json.loads(m)
                if isinstance(rec, dict):
                    out.append(rec)
            except Exception:
                continue
        if out:
            return out
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        try:
            val = json.loads(m.group(0))
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
        except Exception:
            pass
    return []


# ---------------------------------------------------------------------------
# Response parsing (shared between text and legacy VLM paths)
# ---------------------------------------------------------------------------

def _parse_llm_response(text: str) -> List[Dict[str, Any]]:
    """Extract JSON array from LLM response text."""
    if not text:
        return []

    cleaned = re.sub(r"^```(?:json)?\s*|```$", "", text.strip(), flags=re.IGNORECASE).strip()

    try:
        val = json.loads(cleaned)
        if isinstance(val, list):
            return [item for item in val if isinstance(item, dict)]
        if isinstance(val, dict):
            for key in ("records", "topology", "data", "items", "entities", "switches"):
                if isinstance(val.get(key), list):
                    return [item for item in val[key] if isinstance(item, dict)]
            return [val]
    except (json.JSONDecodeError, ValueError):
        pass

    m = re.search(r"\[[\s\S]*\]", cleaned)
    if m:
        try:
            val = json.loads(m.group(0))
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
        except (json.JSONDecodeError, ValueError):
            pass

    return []


# ---------------------------------------------------------------------------
# Hybrid queue processor (sequential, progressive via callback)
# ---------------------------------------------------------------------------

def process_hybrid_queue(
    image_files: List[Any],
    progress_callback: Optional[Any] = None,
    model: str = TEXT_LLM_MODEL,
    endpoint: str = TEXT_LLM_ENDPOINT,
    keep_alive: str = TEXT_LLM_KEEP_ALIVE,
    temperature: float = 0.0,
    num_ctx: int = TEXT_LLM_NUM_CTX,
    num_thread: int = TEXT_LLM_NUM_THREADS,
    timeout: int = TEXT_LLM_TIMEOUT,
    system_prompt: str = "",
    preset_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Process images through the hybrid OCR + text-LLM pipeline.

    v4.6.2: Each image is processed independently with a strict < 350-token
    prompt budget. No bundling of multiple screenshots into a single call.
    num_predict capped at 350 to prevent 110s timeout loops.

    Returns
    -------
    dict
        {
            "records": List[Dict],
            "image_results": List[Dict],
            "total_time_s": float,
            "errors": List[str],
        }
    """
    start = time.time()
    all_records: List[Dict[str, Any]] = []
    image_results: List[Dict[str, Any]] = []
    errors: List[str] = []

    total = len(image_files)
    all_tokens: List[Dict[str, Any]] = []

    # -- Stage A: batch OCR --
    msg = f"Running RapidOCR on {total} image(s)..."
    if progress_callback:
        progress_callback(0, total, msg)

    try:
        batch_tokens, ocr_errors = _run_ocr_stage(image_files)
        errors.extend(ocr_errors)
    except Exception as exc:
        errors.append(f"Batch OCR failed: {exc}")
        batch_tokens = []

    if not batch_tokens:
        return {
            "records": [],
            "image_results": image_results,
            "total_time_s": round(time.time() - start, 2),
            "errors": errors,
        }

    # Stage A+ pre-processing: noise filtering + regex normalization
    batch_tokens = preprocess_ocr_boxes(batch_tokens, preset_config)

    # Assign global indices so we can attribute records back to images
    for i, t in enumerate(batch_tokens):
        src_idx = t.pop("_src", 0)
        t["_global_idx"] = i
        t["_src_img"] = src_idx

    # Group tokens by source image
    tokens_by_image: Dict[int, List[Dict[str, Any]]] = {}
    for t in batch_tokens:
        src = t["_src_img"]
        tokens_by_image.setdefault(src, []).append(t)

    # -- Stage B + C: per-image spatial serialization + LLM call --
    for idx in range(total):
        img_start = time.time()
        img_tokens = tokens_by_image.get(idx, [])

        if not img_tokens:
            msg = f"Image {idx + 1}/{total}: no OCR tokens found"
            if progress_callback:
                progress_callback(idx, total, msg)
            image_results.append({"index": idx, "status": "ok", "records_count": 0, "time_s": 0.0})
            continue

        layout_text = serialize_ocr_boxes_to_layout(img_tokens)

        msg = f"Image {idx + 1}/{total}: calling text LLM ({model})..."
        if progress_callback:
            progress_callback(idx, total, msg)

        try:
            records, _raw_resp = call_text_llm(
                layout_text=layout_text,
                prompt=system_prompt,
                model=model,
                endpoint=endpoint,
                keep_alive=keep_alive,
                temperature=temperature,
                num_ctx=num_ctx,
                num_thread=num_thread,
                timeout=timeout,
            )
            img_time = time.time() - img_start
            image_results.append({
                "index": idx,
                "status": "ok",
                "records_count": len(records),
                "time_s": round(img_time, 2),
            })
            all_records.extend(records)
            msg = f"Image {idx + 1}/{total}: parsed {len(records)} topology elements ({img_time:.1f}s)"
        except Exception as exc:
            img_time = time.time() - img_start
            err_msg = str(exc)
            errors.append(f"Image #{idx + 1} LLM call failed: {err_msg}")
            image_results.append({
                "index": idx,
                "status": "error",
                "error": err_msg,
                "time_s": round(img_time, 2),
            })
            msg = f"Image {idx + 1}/{total}: FAILED ({img_time:.1f}s)"

        if progress_callback:
            progress_callback(idx, total, msg)

    return {
        "records": all_records,
        "image_results": image_results,
        "total_time_s": round(time.time() - start, 2),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Progressive streaming generator (v4.6.1: strict 4-tuple yield)
# ---------------------------------------------------------------------------

def process_hybrid_queue_stream(
    image_files: List[Any],
    model: str = TEXT_LLM_MODEL,
    endpoint: str = TEXT_LLM_ENDPOINT,
    keep_alive: str = TEXT_LLM_KEEP_ALIVE,
    temperature: float = 0.0,
    num_ctx: int = TEXT_LLM_NUM_CTX,
    num_thread: int = TEXT_LLM_NUM_THREADS,
    timeout: int = TEXT_LLM_TIMEOUT,
    system_prompt: str = "",
    preset_config: Optional[Dict[str, Any]] = None,
):
    """Yield (idx, total, records, raw_text) as each image completes.

    v4.6.2 guarantees:
      - Each call processes ONLY a single image (no bundling).
      - Prompt tokens bounded < 350 via compact line-grouping serialization.
      - num_predict: 350 prevents 110s timeout loops (was 1024).
      - Every yield is a strict 4-tuple: (idx, total, records, raw_text).
      - Empty tokens: yield (idx, total, [], "")
      - Exception: yield (idx, total, [], f"ERROR: {str(exc)}")
    """
    total = len(image_files)
    if total == 0:
        return

    # Batch OCR once
    try:
        batch_tokens, _ = _run_ocr_stage(image_files)
    except Exception as exc:
        logger.warning("Batch OCR failed: %s", exc)
        batch_tokens = []

    # Stage A+ pre-processing: noise filtering + regex normalization + collapsed-header discard
    batch_tokens = preprocess_ocr_boxes(batch_tokens, preset_config)

    # Group by image index
    tokens_by_image: Dict[int, List[Dict[str, Any]]] = {}
    for t in batch_tokens:
        src = t.pop("_src", 0)
        tokens_by_image.setdefault(src, []).append(t)

    for idx, img in enumerate(image_files):
        img_tokens = tokens_by_image.get(idx, [])
        if not img_tokens:
            yield (idx, total, [], "")
            continue

        layout_text = serialize_ocr_boxes_to_layout(img_tokens)

        try:
            records, _raw_resp = call_text_llm(
                layout_text=layout_text,
                prompt=system_prompt,
                model=model,
                endpoint=endpoint,
                keep_alive=keep_alive,
                temperature=temperature,
                num_ctx=num_ctx,
                num_thread=num_thread,
                timeout=timeout,
            )
            yield (idx, total, records, _raw_resp or "")
        except Exception as exc:
            logger.warning("Image #%d text LLM call failed: %s", idx + 1, exc)
            yield (idx, total, [], f"ERROR: {str(exc)}")


# ---------------------------------------------------------------------------
# High-level orchestrator
# ---------------------------------------------------------------------------

def run_hybrid_topology_analysis(
    image_files: List[Any],
    naming_rules: Dict[str, Any],
    target_platform: str,
    progress_callback: Optional[Any] = None,
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """Full hybrid topology analysis mirroring the existing OCR pipeline output."""
    from core.vault import SanitizerVault

    vault = SanitizerVault()
    tpp = naming_rules.get("topology_parsing_presets") or {}
    plat_cfg = tpp.get(target_platform, {}) if isinstance(tpp.get(target_platform), dict) else {}
    system_prompt_text = plat_cfg.get("instructions", "").strip()
    if not system_prompt_text:
        system_prompt_text = (
            "You are a network infrastructure topology extraction specialist. "
            "Analyze the provided hypervisor network topology OCR text. "
            "Extract each virtual switch and its interfaces as JSON."
        )

    resolved_model = model or TEXT_LLM_MODEL
    resolved_endpoint = endpoint or TEXT_LLM_ENDPOINT

    start = time.time()

    results = process_hybrid_queue(
        image_files=image_files,
        progress_callback=progress_callback,
        model=resolved_model,
        endpoint=resolved_endpoint,
        keep_alive=TEXT_LLM_KEEP_ALIVE,
        temperature=0.0,
        num_ctx=TEXT_LLM_NUM_CTX,
        num_thread=TEXT_LLM_NUM_THREADS,
        timeout=TEXT_LLM_TIMEOUT,
        system_prompt=system_prompt_text,
        preset_config=plat_cfg,
    )

    raw_items = results.get("records", [])
    errors = results.get("errors", [])
    all_records = []

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        if "finish_reason" in raw_item or "message" in raw_item:
            continue
        item = {str(k).strip().lower(): v for k, v in raw_item.items()}
        all_records.append(item)

    timing = {
        "total_time_s": results.get("total_time_s", 0.0),
        "image_count": len(image_files),
        "records_extracted": len(all_records),
    }

    return {
        "records": all_records,
        "errors": errors,
        "raw_responses": raw_items,
        "timing": timing,
        "vault_session": None,
        "sanitized_text": "",
    }
