import json
import time
import logging
import requests
from typing import Optional, Tuple
from config.settings import OPENROUTER_BASE_URL, OPENROUTER_API_KEY
from config.naming_rules import load_naming_rules, export_rules_as_prompt
from core.exceptions import AIProviderError

logger = logging.getLogger("netbox-hub")

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

def parse_raw_gateway_payload(raw_text: str) -> str:
    trimmed = raw_text.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            data = json.loads(trimmed)
            if "choices" in data and len(data["choices"]) > 0:
                return data["choices"][0].get("message", {}).get("content") or ""
            if "error" in data:
                raise AIProviderError(f"Gateway Error: {data['error']}")
        except json.JSONDecodeError:
            pass

    if "data:" in trimmed:
        content_tokens = []
        for line in trimmed.splitlines():
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                payload_str = line[5:].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload_str)
                    choices = chunk.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            content_tokens.append(delta["content"])
                        elif "message" in choices[0]:
                            content_tokens.append(choices[0]["message"].get("content", ""))
                except Exception:
                    continue
        if content_tokens:
            return "".join(content_tokens)

    return raw_text

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