import json
import time
import logging
import requests
from typing import Optional, Tuple
from config.settings import OPENROUTER_BASE_URL, OPENROUTER_API_KEY
from config.naming_rules import load_naming_rules, export_rules_as_prompt
from core.exceptions import AIProviderError

logger = logging.getLogger("netbox-hub")

# Vendor families that are known to expose reliable OpenAI-compatible chat endpoints.
RELIABLE_VENDORS = (
    "openai",
    "anthropic",
    "google",
    "gemini",
    "deepseek",
    "meta-llama",
    "llama",
    "mistralai",
    "mistral",
    "qwen",
    "groq",
    "cohere",
    "command",
    "gpt",
    "claude",
    "grok",
)

# Model families that are NOT chat/text-generation endpoints and must never be offered.
NON_CHAT_SUBSTRINGS = (
    "embedding",
    "embeddings",
    "rerank",
    "moderation",
    "whisper",
    "transcribe",
    "stt",
    "tts",
    "text-to-speech",
    "speech",
    "audio",
    "image",
    "dall-e",
    "vision",
)

# Prefixes that indicate experimental / junk / internal placeholder models.
EXPERIMENTAL_PREFIXES = (
    "antigravity/",
    "test/",
    "testing/",
    "experimental/",
    "internal/",
    "tmp/",
    "scratch/",
    "dev/",
    "debug/",
    "junk/",
    ".internal/",
    "org:",
)


def sanitize_model_id(model_id: str) -> bool:
    """Return True when a raw model id is a credible, usable chat candidate.

    Filters out non-chat model families, experimental/junk prefixes, and models that
    do not map to a known reliable vendor family. This keeps the "Quick-Select Test
    Model" dropdown compact and free of models that are almost guaranteed to fail.
    """
    if not model_id or not isinstance(model_id, str):
        return False

    lower = model_id.lower().strip()

    # Experimental / junk prefixes are vetoed outright (e.g. "antigravity/").
    if any(lower.startswith(prefix) for prefix in EXPERIMENTAL_PREFIXES):
        return False

    # Non-chat model families are vetoed outright.
    if any(sub in lower for sub in NON_CHAT_SUBSTRINGS):
        return False

    # Must belong to a known reliable vendor family.
    if not any(vendor in lower for vendor in RELIABLE_VENDORS):
        return False

    return True


def sanitize_free_models(raw_models: list[str]) -> list[str]:
    """Return only sanitized, de-duplicated, sorted model ids from raw candidates."""
    seen: set[str] = set()
    result: list[str] = []
    for m in raw_models or []:
        m = (m or "").strip()
        if not m or m in seen or not sanitize_model_id(m):
            continue
        seen.add(m)
        result.append(m)
    return sorted(result)


def healthcheck_ai(selected_model: str) -> Tuple[bool, int, str]:
    """Lightweight ping through the app's own ``call_ai`` pipeline.

    Sends the smallest possible completion (prompt "ping", ``max_tokens=2``) via the
    existing ``call_ai`` logic so the check exercises the exact code path used for real
    generation (token auth, gateway routing, payload parsing). Returns ``(ok, latency_ms, msg)``.

    Args:
        selected_model: The model id to healthcheck (e.g. ``"openai/gpt-4o-mini"``).
    """
    start = time.time()
    try:
        call_ai("ping", selected_model)
    except AIProviderError as exc:
        latency = round((time.time() - start) * 1000)
        return False, latency, str(exc)
    except Exception as exc:  # pragma: no cover - defensive belt-and-suspenders
        latency = round((time.time() - start) * 1000)
        return False, latency, f"Unexpected error: {exc}"
    latency = round((time.time() - start) * 1000)
    return True, latency, f"Connected ({latency}ms)"


def ping_model(model_name: str, timeout: int = 5) -> Tuple[bool, int, str]:
    """Run a lightweight pre-flight healthcheck against a single model.

    Sends the smallest possible chat payload (one token) with a tight timeout so the
    check is quick and cheap. Returns ``(ok, latency_ms, message)``.

    Args:
        model_name: The model id to ping (e.g. ``"openai/gpt-4o-mini"``).
        timeout: Seconds to wait before failing. Kept short (3-5s) on purpose.
    """
    base = OPENROUTER_BASE_URL.rstrip("/")
    endpoint = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    clean_token = OPENROUTER_API_KEY.replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": "NetBox Hub",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "max_tokens": 1,
        "stream": False,
        "messages": [
            {"role": "user", "content": "hi"},
        ],
    }
    start = time.time()
    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
        latency = round((time.time() - start) * 1000)
        if resp.status_code == 200:
            return True, latency, f"Operational ({latency}ms)"
        try:
            err_data = resp.json()
            msg = err_data.get("error", {}).get("message", resp.text)
        except Exception:
            msg = resp.text
        return False, 0, f"HTTP {resp.status_code}: {msg}"
    except requests.exceptions.Timeout:
        return False, 0, f"Timeout after {timeout}s"
    except Exception as e:
        return False, 0, f"Connection Error: {e}"


def fetch_free_models() -> list[str]:
    """Fetch list of free models suitable for NetBox YAML generation from OmniRoute/OpenRouter API."""
    base = OPENROUTER_BASE_URL.rstrip("/")
    endpoint = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    clean_token = OPENROUTER_API_KEY.replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Content-Type": "application/json"
    }
    
    try:
        resp = requests.get(endpoint, headers=headers, timeout=10)
        logger.info(f"Fetching models from {endpoint}, status: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("data", [])
            logger.info(f"Total models received: {len(models)}")
            
            raw_ids = [m.get("id", "") for m in models if m.get("id")]
            available_models = sanitize_free_models(raw_ids)
            logger.info(f"Sanitized models count: {len(available_models)}")
            return available_models
        else:
            logger.warning(f"Failed to fetch models: HTTP {resp.status_code}, response: {resp.text[:200]}")
            return []
    except Exception as e:
        logger.error(f"Error fetching free models: {e}", exc_info=True)
        return []

def test_model_connection(model_name: str) -> Tuple[bool, int, str]:
    """Tests model health through OmniRoute using full request structure."""
    base = OPENROUTER_BASE_URL.rstrip("/")
    endpoint = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    clean_token = OPENROUTER_API_KEY.replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": "NetBox Hub",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "temperature": 0.0,
        "max_tokens": 100,
        "stream": False,
        "messages": [
            {"role": "user", "content": "Respond with: OK"}
        ]
    }
    start = time.time()
    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=25)
        latency = round((time.time() - start) * 1000)
        if resp.status_code == 200:
            return True, latency, f"Online ({latency}ms)"
        else:
            try:
                err_data = resp.json()
                msg = err_data.get("error", {}).get("message", resp.text)
            except Exception:
                msg = resp.text
            return False, 0, f"HTTP {resp.status_code}: {msg}"
    except Exception as e:
        return False, 0, f"Connection Error: {str(e)}"

def _parse_sse_stream(raw_text: str) -> str:
    """
    Decode a Server-Sent Events (SSE) `data: {...}` stream into concatenated content tokens.

    Handles both Chat Completions delta format and a few gateway/proxy quirks:
      - data: {"choices":[{"delta":{"content":"..."}}]}
      - data: {"choices":[{"message":{"content":"..."}}]}
      - data: [DONE] (end marker)
      - Lines that are not strict SSE (e.g. bare JSON, whitespace) are tolerated.

    Args:
        raw_text: Raw response body from the gateway.

    Returns:
        Decoded content string, or empty string if no tokens could be extracted.
    """
    if "data:" not in raw_text:
        return ""

    content_tokens = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue

        # Some gateways prepend whitespace/prefix before "data:"
        data_idx = line.find("data:")
        payload_str = line[data_idx + 5:].strip() if data_idx != -1 else line
        if payload_str == "[DONE]":
            break

        try:
            chunk = json.loads(payload_str)
        except (json.JSONDecodeError, TypeError):
            # Not JSON on this line: ignore and move on (tolerant parsing)
            continue

        choices = chunk.get("choices")
        if not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue

        # delta.content is the standard Chat Completions streaming field
        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if content:
                content_tokens.append(content)
            continue

        # Some gateways return full "message" objects per chunk
        message = choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if content:
                content_tokens.append(content)

    if content_tokens:
        return "".join(content_tokens)
    return ""


def _extract_from_json_object(raw_text: str) -> str:
    """
    Attempt to parse a single top-level JSON object and pull the assistant content.

    Handles the standard structure:
      {"id": ..., "choices": [{"message": {"content": "..."}}]}

    Also tolerates leading/trailing gateway decoration so a JSON block buried
    inside other text can still be recovered.

    Args:
        raw_text: Trimmed raw response body.

    Returns:
        Content string if successfully extracted, else empty string.
    """
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""

    candidate = raw_text[start:end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return ""

    if "error" in data:
        raise AIProviderError(f"Gateway Error: {data['error']}")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if isinstance(first, dict):
        message = first.get("message")
        if isinstance(message, dict):
            return message.get("content") or ""
        text = first.get("text")
        if text:
            return str(text)

    return ""


def parse_raw_gateway_payload(raw_text: str) -> str:
    """
    Extract assistant content from a raw gateway/proxy response.

    Strategy (in order):
    1. If the body is a single JSON object, parse it and pull "choices[0].message.content".
    2. If the body is a Server-Sent Events stream (lines starting with "data:"), decode each
       chunk and concatenate the content deltas.
    3. Fall back to returning the raw text unchanged so the caller can surface an informative
       error or display the raw gateway output instead of failing on a parse error.

    This makes the app resilient to API gateways and proxies that wrap, chunk, or decorate
    the original OpenAI-compatible response.
    """
    trimmed = (raw_text or "").strip()

    # Strategy 1: single JSON object
    if trimmed.startswith("{") and trimmed.endswith("}"):
        extracted = _extract_from_json_object(trimmed)
        if extracted:
            return extracted

    # Strategy 2: Server-Sent Events stream
    if "data:" in trimmed:
        sse_content = _parse_sse_stream(trimmed)
        if sse_content:
            return sse_content

    # Strategy 3: text fallback — return the raw body unchanged
    return raw_text or ""

def call_ai(prompt: str, selected_model: str, custom_system_msg: Optional[str] = None) -> str:
    rules = load_naming_rules()
    naming_context = export_rules_as_prompt(rules)
    system_msg = custom_system_msg or (
        "You are a strict NetBox hardware YAML specification generator and infrastructure architect. "
        "You MUST verify hardware specifications directly from official manufacturer datasheets. "
        f"Strictly align with these infrastructure conventions:\n{naming_context}\n"
        "Output ONLY valid, raw YAML starting with '---'. Use exact kebab-case hyphenated keys. "
        "Do NOT output explanation, reasoning, or markdown fences outside the YAML block. "
        "Omit 'comments' key entirely if no verified official datasheet URL is available."
    )

    clean_token = OPENROUTER_API_KEY.replace("Bearer ", "").strip()
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "HTTP-Referer": "http://localhost:8501",
        "X-Title": "NetBox Hub",
        "Content-Type": "application/json"
    }
    payload = {
        "model": selected_model,
        "temperature": 0.0,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt}
        ]
    }
    
    base = OPENROUTER_BASE_URL.rstrip("/")
    endpoint = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=90)
        if response.status_code != 200:
            raise AIProviderError(f"HTTP {response.status_code}: {response.text}")
            
        raw_text = response.text.strip()
        if not raw_text:
            raise AIProviderError("Gateway returned empty body (account in cooldown).")
            
        content = parse_raw_gateway_payload(raw_text)
        if not content:
            raise AIProviderError("Unable to extract valid content tokens from gateway stream.")
        return content
            
    except requests.exceptions.ConnectionError:
        raise AIProviderError(f"Unable to connect to gateway at {endpoint}.")
    except requests.exceptions.Timeout:
        raise AIProviderError(f"Gateway timeout for model {selected_model} (>90s).")
    except Exception as e:
        logger.error(f"OmniRoute Error: {e}")
        raise AIProviderError(str(e))