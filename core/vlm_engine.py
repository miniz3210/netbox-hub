"""
On-Demand Local VLM Engine — Qwen2.5-VL Pipeline

Implements progressive in-tab VLM inference with keep_alive: "10m" lifecycle
for on-premises vision-language extraction on constrained host hardware
(4 OCPU ARM).

Key design decisions:
  * Dynamic resolution capping to 1280 px max dimension — slashes visual
    tokens from ~1300 to ~450 and saves 15-20 s per image on CPU.
  * Progressive streaming: yields (idx, total, records) per image so the UI
    can append switch cards in real-time without waiting for the full batch.
  * Sequential auto-queue for multi-image uploads to prevent OOM spikes.
  * Full semantic JSON keys (switch, interface, type, ip, purpose, role)
    to maintain model reasoning integrity on 3 B / 7 B parameter models.
  * Hierarchical configuration resolution: UI session state > env vars > YAML defaults.
  * 300s HTTP timeout prevents premature kills on cold model load.
  * num_thread: 4 and num_ctx: 2048 optimized for 4-OCPU ARM execution.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Callable

try:
    import cv2
    from PIL import Image as PILImage
    HAS_VISION_DEPS = True
except ImportError:
    HAS_VISION_DEPS = False

logger = logging.getLogger(__name__)

DOCKER_DEFAULT_ENDPOINT = "http://netbox-ollama:11434"

DEFAULT_VLM_MODEL = os.getenv("DEFAULT_VLM_MODEL", "qwen2.5vl:3b")
DEFAULT_VLM_ENDPOINT = os.getenv(
    "OLLAMA_API_ENDPOINT",
    DOCKER_DEFAULT_ENDPOINT,
)
DEFAULT_VLM_KEEP_ALIVE = os.getenv("VLM_KEEP_ALIVE", "10m")
DEFAULT_VLM_MAX_DIM = int(os.getenv("VLM_MAX_IMAGE_DIM", "1280"))
DEFAULT_VLM_TIMEOUT = int(os.getenv("VLM_TIMEOUT", "300"))
DEFAULT_VLM_NUM_THREADS = int(os.getenv("VLM_NUM_THREADS", "4"))
DEFAULT_VLM_NUM_CTX = int(os.getenv("VLM_NUM_CTX", "2048"))


def resolve_vlm_config(
    ui_override: Optional[Dict[str, Any]] = None,
    naming_rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve VLM configuration hierarchically.

    Priority: UI session state override > .env variables > naming_rules.yaml defaults.
    Returns a dict with keys: model, endpoint, keep_alive, max_dimension, temperature.
    """
    result: Dict[str, Any] = {}

    yaml_cfg = (naming_rules.get("vlm_config") or {}) if isinstance(naming_rules, dict) else {}

    result["model"] = (
        (ui_override or {}).get("model")
        or os.getenv("DEFAULT_VLM_MODEL")
        or yaml_cfg.get("default_model", DEFAULT_VLM_MODEL)
    )
    result["endpoint"] = (
        (ui_override or {}).get("endpoint")
        or os.getenv("OLLAMA_API_ENDPOINT")
        or yaml_cfg.get("api_endpoint", DOCKER_DEFAULT_ENDPOINT)
    )
    result["keep_alive"] = (
        (ui_override or {}).get("keep_alive")
        if (ui_override or {}).get("keep_alive") is not None
        else os.getenv("VLM_KEEP_ALIVE") or str(yaml_cfg.get("keep_alive", DEFAULT_VLM_KEEP_ALIVE))
    )
    result["max_dimension"] = (
        (ui_override or {}).get("max_dimension")
        if (ui_override or {}).get("max_dimension") is not None
        else int(
            os.getenv("VLM_MAX_IMAGE_DIM", str(yaml_cfg.get("max_image_dimension", DEFAULT_VLM_MAX_DIM)))
        )
    )
    result["temperature"] = float(
        (ui_override or {}).get("temperature", yaml_cfg.get("temperature", 0.0))
    )
    result["timeout"] = int(
        (ui_override or {}).get("timeout")
        if (ui_override or {}).get("timeout") is not None
        else os.getenv("VLM_TIMEOUT", str(yaml_cfg.get("timeout", DEFAULT_VLM_TIMEOUT)))
    )
    result["num_ctx"] = int(
        (ui_override or {}).get("num_ctx")
        if (ui_override or {}).get("num_ctx") is not None
        else os.getenv("VLM_NUM_CTX", str(yaml_cfg.get("num_ctx", DEFAULT_VLM_NUM_CTX)))
    )
    result["num_thread"] = int(
        (ui_override or {}).get("num_thread")
        if (ui_override or {}).get("num_thread") is not None
        else os.getenv("VLM_NUM_THREADS", str(yaml_cfg.get("num_threads", DEFAULT_VLM_NUM_THREADS)))
    )

    return result


def get_vlm_endpoint(naming_rules: Optional[Dict[str, Any]] = None) -> str:
    """Return the resolved Ollama endpoint using hierarchical config."""
    config = resolve_vlm_config(naming_rules=naming_rules)
    return config["endpoint"]


def get_vlm_model(naming_rules: Optional[Dict[str, Any]] = None) -> str:
    """Return the resolved VLM model name using hierarchical config."""
    config = resolve_vlm_config(naming_rules=naming_rules)
    return config["model"]


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------

def preprocess_and_resize_for_vlm(
    image_input: Any,
    max_dimension: int = 1280,
) -> str:
    """Load an image from bytes / PIL / file buffer, resize if necessary,
    encode to JPEG (quality=90), and return a base64 string.

    If width or height exceeds ``max_dimension``, the image is scaled
    proportionally using INTER_AREA to minimise aliasing on downscale.
    """
    if not HAS_VISION_DEPS:
        raise RuntimeError(
            "cv2 and Pillow are required for VLM preprocessing. "
            "Install them first."
        )

    # -- Load image into a PIL Image --
    if isinstance(image_input, (bytes, bytearray)):
        pil_img = PILImage.open(io.BytesIO(bytes(image_input))).convert("RGB")
    elif isinstance(image_input, PILImage.Image):
        pil_img = image_input.convert("RGB")
    elif hasattr(image_input, "getvalue"):
        pil_img = PILImage.open(image_input).convert("RGB")
    elif hasattr(image_input, "read"):
        image_input.seek(0)
        pil_img = PILImage.open(image_input).convert("RGB")
    elif isinstance(image_input, np.ndarray if 'np' in globals() else object):
        pil_img = PILImage.fromarray(image_input)
    else:
        pil_img = PILImage.open(str(image_input)).convert("RGB")

    w, h = pil_img.size

    if w > max_dimension or h > max_dimension:
        scale = max_dimension / max(w, h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        pil_img = pil_img.resize((new_w, new_h), PILImage.Resampling.LANCZOS)
        logger.debug(
            "Resized %dx%d -> %dx%d (scale=%.3f)", w, h, new_w, new_h, scale
        )

    # -- Encode to JPEG bytes --
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=90, optimize=True)
    jpeg_bytes = buf.getvalue()

    # -- Base64 encode --
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    logger.debug("Encoded image to base64: %d bytes -> %d b64 chars", len(jpeg_bytes), len(b64))
    return b64


# ---------------------------------------------------------------------------
# Ollama / OpenAI-compatible API calls
# ---------------------------------------------------------------------------

def call_local_vlm_single(
    image_b64: str,
    prompt: str,
    model: str = "qwen2.5-vl:3b",
    endpoint: str = "http://localhost:11434",
    keep_alive: str = "10m",
    temperature: float = 0.0,
    num_ctx: int = 2048,
    num_predict: int = 1024,
    num_thread: int = 4,
    timeout: int = 300,
    system_prompt: str = "",
) -> List[Dict[str, Any]]:
    """Send a single image to a local VLM via Ollama /api/chat.

    Parameters
    ----------
    image_b64 : str
        Base64-encoded image payload.
    prompt : str
        User prompt text.
    model : str
        Model identifier (e.g. ``"qwen2.5-vl:3b"``).
    endpoint : str
        Ollama base URL.
    keep_alive : str
        Model retention duration (e.g. ``"10m"``).
    temperature : float
        Sampling temperature (0.0 = deterministic).
    num_ctx : int
        Context window size.
    num_predict : int
        Max tokens to generate.
    num_thread : int
        Number of CPU threads for matrix execution.
    timeout : int
        HTTP request timeout in seconds.
    system_prompt : str
        System prompt for the VLM (platform-specific extraction instructions).

    Returns
    -------
    List[Dict[str, Any]]
        Parsed JSON array items matching the topology schema.
    """
    import urllib.request

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "user",
        "content": prompt,
        "images": [image_b64],
    })

    payload = {
        "model": model,
        "messages": messages,
        "keep_alive": keep_alive,
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
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
        logger.error("VLM call failed after %.1fs: %s", timeout, exc)
        raise RuntimeError(f"VLM inference failed: {exc}") from exc

    message = raw.get("message", {})
    response_text = message.get("content", "") or ""

    return _parse_vlm_response(response_text)


def call_local_vlm_single_openai(
    image_b64: str,
    prompt: str,
    model: str = "qwen2.5-vl:3b",
    endpoint: str = "http://localhost:11434",
    keep_alive: str = "10m",
    temperature: float = 0.0,
    num_ctx: int = 2048,
    num_predict: int = 1024,
    num_thread: int = 4,
    timeout: int = 300,
) -> List[Dict[str, Any]]:
    """Send a single image to an OpenAI-compatible endpoint."""
    import urllib.request

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
        "temperature": temperature,
        "max_tokens": num_predict,
        "stream": False,
    }

    url = f"{endpoint.strip('/')}/v1/chat/completions"
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
        logger.error("VLM OpenAI call failed: %s", exc)
        raise RuntimeError(f"VLM inference failed: {exc}") from exc

    choice = raw.get("choices", [{}])[0]
    response_text = choice.get("message", {}).get("content", "") or ""
    return _parse_vlm_response(response_text)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_vlm_response(text: str) -> List[Dict[str, Any]]:
    """Extract JSON array from VLM response text."""
    import re as _re
    if not text:
        return []

    cleaned = _re.sub(r"^```(?:json)?\s*|```$", "", text.strip(), flags=_re.IGNORECASE).strip()

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

    m = _re.search(r"\[[\s\S]*\]", cleaned)
    if m:
        try:
            val = json.loads(m.group(0))
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
        except (json.JSONDecodeError, ValueError):
            pass

    return []


# ---------------------------------------------------------------------------
# Sequential queue processor
# ---------------------------------------------------------------------------

def process_vlm_queue(
    image_files: List[Any],
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    model: str = "qwen2.5-vl:3b",
    endpoint: str = "http://localhost:11434",
    keep_alive: str = "10m",
    temperature: float = 0.0,
    max_dimension: int = 1280,
    num_ctx: int = 2048,
    num_thread: int = 4,
    timeout: int = 300,
    system_prompt: str = "",
    user_prompt_template: str = (
        "Parse this sanitized topology text into JSON entities array:\n\n{text}"
    ),
) -> Dict[str, Any]:
    """Process images sequentially through the local VLM.

    Parameters
    ----------
    image_files : list
        List of image inputs (BytesIO, PIL Image, bytes, or file paths).
    progress_callback : callable, optional
        Signature: ``(current_idx, total_count, message) -> None``.
    model : str
        Ollama model identifier.
    endpoint : str
        Ollama base URL.
    keep_alive : str
        Model retention duration (e.g. "10m").
    temperature : float
        Sampling temperature.
    max_dimension : int
        Max image dimension before resize.
    num_ctx : int
        Context window size.
    num_thread : int
        Number of CPU threads for matrix execution.
    timeout : int
        HTTP request timeout in seconds.
    system_prompt : str
        System prompt for the VLM.
    user_prompt_template : str
        Template with ``{text}`` placeholder for OCR/sanitized text.

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
    if not HAS_VISION_DEPS:
        return {
            "records": [],
            "image_results": [],
            "total_time_s": 0.0,
            "errors": ["VLM dependencies (cv2, Pillow) not installed"],
        }

    start = time.time()
    all_records: List[Dict[str, Any]] = []
    image_results: List[Dict[str, Any]] = []
    errors: List[str] = []

    total = len(image_files)

    for idx, img in enumerate(image_files):
        img_start = time.time()
        msg = f"Image {idx + 1}/{total}: preprocessing..."
        if progress_callback:
            progress_callback(idx, total, msg)

        try:
            image_b64 = preprocess_and_resize_for_vlm(img, max_dimension=max_dimension)
        except Exception as exc:
            errors.append(f"Image #{idx + 1} preprocessing failed: {exc}")
            image_results.append({"index": idx, "status": "error", "error": str(exc)})
            continue

        msg = f"Image {idx + 1}/{total}: calling VLM ({model})..."
        if progress_callback:
            progress_callback(idx, total, msg)

        try:
            records = call_local_vlm_single(
                image_b64=image_b64,
                prompt=user_prompt_template.format(text=""),
                model=model,
                endpoint=endpoint,
                keep_alive=keep_alive,
                temperature=temperature,
                num_ctx=num_ctx,
                num_thread=num_thread,
                timeout=timeout,
                system_prompt=system_prompt,
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
            errors.append(f"Image #{idx + 1} VLM call failed: {err_msg}")
            image_results.append({
                "index": idx,
                "status": "error",
                "error": err_msg,
                "time_s": round(img_time, 2),
            })
            msg = f"Image {idx + 1}/{total}: FAILED ({img_time:.1f}s)"

        if progress_callback:
            progress_callback(idx, total, msg)

    total_time = time.time() - start

    return {
        "records": all_records,
        "image_results": image_results,
        "total_time_s": round(total_time, 2),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Progressive streaming generator for real-time UI rendering
# ---------------------------------------------------------------------------

def process_vlm_queue_stream(
    image_files: List[Any],
    model: str = "qwen2.5-vl:3b",
    endpoint: str = "http://localhost:11434",
    keep_alive: str = "10m",
    temperature: float = 0.0,
    max_dimension: int = 1280,
    num_ctx: int = 2048,
    num_thread: int = 4,
    timeout: int = 300,
    system_prompt: str = "",
):
    """Yield (image_index, total_images, single_image_records) as each image
    completes inference, enabling progressive UI rendering without batch
    wait. Returns parsed JSON array items for each image.

    Parameters
    ----------
    image_files : list
        List of image inputs (BytesIO, PIL Image, bytes, or file paths).
    model : str
        Ollama model identifier.
    endpoint : str
        Ollama base URL.
    keep_alive : str
        Model retention duration string (e.g. "10m").
    temperature : float
        Sampling temperature.
    max_dimension : int
        Max image dimension before resize.
    num_ctx : int
        Context window size.
    num_thread : int
        Number of CPU threads for matrix execution.
    timeout : int
        HTTP request timeout in seconds.
    system_prompt : str
        System prompt for the VLM (platform-specific extraction instructions).

    Yields
    ------
    tuple
        (image_index: int, total_images: int, records: List[Dict])
    """
    if not HAS_VISION_DEPS:
        yield (0, len(image_files), [])
        return

    total = len(image_files)
    for idx, img in enumerate(image_files):
        try:
            image_b64 = preprocess_and_resize_for_vlm(img, max_dimension=max_dimension)
        except Exception as exc:
            logger.warning("Image #%d preprocessing failed: %s", idx + 1, exc)
            yield (idx, total, [])
            continue

        try:
            records = call_local_vlm_single(
                image_b64=image_b64,
                prompt="Extract the network topology from this screenshot as JSON.",
                model=model,
                endpoint=endpoint,
                keep_alive=keep_alive,
                temperature=temperature,
                num_ctx=num_ctx,
                num_predict=1024,
                num_thread=num_thread,
                timeout=timeout,
                system_prompt=system_prompt,
            )
            yield (idx, total, records)
        except Exception as exc:
            logger.warning("Image #%d VLM call failed: %s", idx + 1, exc)
            yield (idx, total, [])


# ---------------------------------------------------------------------------
# VLM-mode analysis that mirrors the existing OCR pipeline
# ---------------------------------------------------------------------------

def run_vlm_topology_analysis(
    image_files: List[Any],
    naming_rules: Dict[str, Any],
    target_platform: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    model: Optional[str] = None,
    endpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """Full VLM-based topology analysis that mirrors the OCR pipeline.

    Parameters
    ----------
    model : str, optional
        Override the model resolved from hierarchical config.
    endpoint : str, optional
        Override the endpoint resolved from hierarchical config.
    """
    from core.vault import SanitizerVault

    vault = SanitizerVault()
    vlm_cfg = resolve_vlm_config(naming_rules=naming_rules)
    if model is not None:
        vlm_cfg["model"] = model
    if endpoint is not None:
        vlm_cfg["endpoint"] = endpoint

    tpp = naming_rules.get("topology_parsing_presets") or {}
    plat_cfg = tpp.get(target_platform, {}) if isinstance(tpp.get(target_platform), dict) else {}
    system_prompt_text = plat_cfg.get("instructions", "").strip()
    if not system_prompt_text:
        system_prompt_text = (
            "You are a network infrastructure topology extraction specialist. "
            "Analyze the provided hypervisor network topology screenshot. "
            "Extract each virtual switch and its interfaces as JSON."
        )

    user_prompt_template = (
        "You are a network infrastructure topology extraction specialist.\n"
        "Analyze the provided hypervisor network topology screenshot "
        "(VMware ESXi, Proxmox, or similar).\n\n"
        f"{system_prompt_text}\n\n"
        "Return ONLY a valid JSON array of objects."
    )

    start = time.time()

    results = process_vlm_queue(
        image_files=image_files,
        progress_callback=progress_callback,
        model=vlm_cfg["model"],
        endpoint=vlm_cfg["endpoint"],
        keep_alive=vlm_cfg.get("keep_alive", "10m"),
        temperature=vlm_cfg["temperature"],
        max_dimension=vlm_cfg["max_dimension"],
        num_ctx=vlm_cfg.get("num_ctx", 2048),
        num_thread=vlm_cfg.get("num_thread", 4),
        timeout=vlm_cfg.get("timeout", 300),
        system_prompt=system_prompt_text,
        user_prompt_template=user_prompt_template,
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
