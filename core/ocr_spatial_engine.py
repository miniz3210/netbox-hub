"""
Hybrid Spatial OCR + Text-Only LLM Engine (v4.5.18)

Replaces the heavy VLM image-encoding pipeline with a two-stage
approach optimized for 4 OCPU ARM hosts running Qwen2.5:3b.

  Stage A — RapidOCR:         ~0.3 s per image
      Extract text fragments with bounding-box coordinates.
      Preserve spatial ordering via Y/X sorting within 15 px rows.

  Stage B — Spatial Serialization:
      Group tokens into lines, prefix each token with its [y=..., x=...]
      coordinate so the LLM can infer visual layout without an image.

  Stage C — Text LLM Inference:
      Call Ollama /api/chat with a pure-text prompt (no images).
      Execution on 4 OCPU ARM: ~3 s per image.

Total per-image latency: ~3.5 s (vs ~10–15 s with qwen2.5vl:3b).

Disk/memory reclaimed by removing qwen2.5vl:3b (~3.2 GB).
"""

from __future__ import annotations

import json
import logging
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
Y_ROW_THRESHOLD = 15  # pixels — tokens within this gap share a line


# ---------------------------------------------------------------------------
# Platform-agnostic OCR token pre-processing (Sandwich Architecture)
# ---------------------------------------------------------------------------

def preprocess_ocr_boxes(
    ocr_results: List[Any],
    preset_config: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    """Filter platform-specific navigation noise and apply regex normalizations
    defined in the active platform preset configuration.

    Parameters
    ----------
    ocr_results : list
        Raw OCR output. Each item is either the RapidOCR format
        ``[box_coordinates, text, confidence]`` or a dict with ``text``
        and ``box`` keys (as produced by ``core.ocr_engine::raw_tokens``).
    preset_config : dict, optional
        The active platform preset dictionary (e.g.
        ``topology_parsing_presets["VMware ESXi"]``) containing
        ``noise_filters`` and ``token_normalizations`` keys.

    Returns
    -------
    list
        Filtered and normalized OCR result items in the same shape as input.
    """
    if not preset_config:
        return ocr_results

    noise_filters = preset_config.get("noise_filters", [])
    normalizations = preset_config.get("token_normalizations", [])

    cleaned_results: List[Any] = []
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

        if is_dict:
            cleaned_results.append({
                "text": text,
                "box": box,
                **{k: v for k, v in item.items() if k not in ("text", "box")},
            })
        else:
            cleaned_results.append([box, text, score])

    return cleaned_results


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
# Stage B: Spatial text serialization
# ---------------------------------------------------------------------------

def serialize_ocr_boxes_to_layout(ocr_results: List[Dict[str, Any]]) -> str:
    """Sort OCR tokens by Y then X and emit a layout string for the LLM.

    Parameters
    ----------
    ocr_results : list
        List of dicts with keys ``text`` and ``box`` where ``box`` is
        ``[xmin, ymin, width, height]``.

    Returns
    -------
    str
        Lines of ``[y=N, x=M] token`` pairs separated by two spaces.
    """
    if not ocr_results:
        return ""

    items: List[Dict[str, Any]] = []
    for tok in ocr_results:
        box = tok.get("box")
        if not box or len(box) < 2:
            continue
        text = str(tok.get("text", "")).strip()
        if not text:
            continue
        xmin = float(box[0])
        ymin = float(box[1])
        items.append({"text": text, "x": int(xmin), "y": int(ymin)})

    if not items:
        return ""

    # Group by row: round y to nearest Y_ROW_THRESHOLD multiple
    items.sort(key=lambda it: (it["y"] // Y_ROW_THRESHOLD * Y_ROW_THRESHOLD, it["x"]))

    lines: List[str] = []
    current_y: Optional[int] = None
    current_line: List[str] = []

    for item in items:
        rounded_y = item["y"] // Y_ROW_THRESHOLD * Y_ROW_THRESHOLD
        prefix = f"[y={item['y']}, x={item['x']}]"
        if current_y is None or abs(rounded_y - current_y) <= Y_ROW_THRESHOLD:
            current_line.append(f"{prefix} {item['text']}")
            current_y = rounded_y
        else:
            lines.append("  ".join(current_line))
            current_line = [f"{prefix} {item['text']}"]
            current_y = rounded_y

    if current_line:
        lines.append("  ".join(current_line))

    return "\n".join(lines)


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
) -> List[Dict[str, Any]]:
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
    List[Dict[str, Any]]
        Parsed JSON array items matching the topology schema.
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
            "num_predict": 1024,
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

    Handles markdown code fences, embedded objects, and regex fallback
    to avoid JSONDecodeError drops on truncated or malformed output.
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
    token_src_map: Dict[int, int] = {}  # global token index -> image index

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
# Progressive streaming generator
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
    """Yield (image_index, total_images, records) as each image completes.

    Each yielded tuple contains parsed JSON array items for one image.
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

    # Stage A+ pre-processing: noise filtering + regex normalization
    batch_tokens = preprocess_ocr_boxes(batch_tokens, preset_config)

    # Group by image index
    tokens_by_image: Dict[int, List[Dict[str, Any]]] = {}
    for t in batch_tokens:
        src = t.pop("_src", 0)
        tokens_by_image.setdefault(src, []).append(t)

    for idx, img in enumerate(image_files):
        img_tokens = tokens_by_image.get(idx, [])
        if not img_tokens:
            yield (idx, total, [])
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
            yield (idx, total, records, _raw_resp)
        except Exception as exc:
            logger.warning("Image #%d text LLM call failed: %s", idx + 1, exc)
            yield (idx, total, [])


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
