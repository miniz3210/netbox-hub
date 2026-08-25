import os

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "http://omniroute:20128/v1").rstrip("/")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "sk-12ffb60b09d8c11e-78b1f2-29cd1b92").strip()

DEFAULT_MODELS = "groq/openai/gpt-oss-120b,groq/qwen/qwen3.6-27b,gemini/gemini-2.5-flash"
models_env = os.getenv("OPENROUTER_MODELS", os.getenv("GROQ_MODELS", DEFAULT_MODELS))
AVAILABLE_MODELS = [m.strip() for m in models_env.split(",") if m.strip()]