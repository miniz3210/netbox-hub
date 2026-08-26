import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.ai_client import test_model_connection

SUGGESTED_TEST_MODELS = [
    "groq/openai/gpt-oss-120b",
    "gemini/gemini-3-flash-preview",
    "groq/qwen/qwen3.6-27b",
    "openrouter/qwen/qwen-2.5-coder-32b-instruct:free",
    "openrouter/deepseek/deepseek-r1:free",
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "aug/gemini-3.1-pro-preview",
    "aug/fable-5"
]

def render_sidebar() -> str:
    with st.sidebar:
        st.header("⚙️ AI Engine Selection")
        
        # 1. Preset Models strictly uses your configured AVAILABLE_MODELS
        selected_preset = st.selectbox(
            "Preset Models",
            options=AVAILABLE_MODELS,
            index=0,
            help="Configured environment presets from OPENROUTER_MODELS / GROQ_MODELS."
        )

        # 2. Suggested Models Pull-Down Menu (Select & Copy)
        quick_pick = st.selectbox(
            "Quick-Select Test Model",
            options=["-- None (Use Preset / Manual) --"] + SUGGESTED_TEST_MODELS,
            index=0,
            help="Select any popular route to load it instantly without typing."
        )

        # 3. Custom Manual Input
        default_manual = "" if quick_pick.startswith("--") else quick_pick
        custom_model = st.text_input(
            "Custom Model",
            value=default_manual,
            placeholder="Type or edit model slug...",
            help="Overrides preset when populated."
        ).strip()

        # Active Model Resolution
        active_model = custom_model if custom_model else selected_preset

        # Track model test results in session state
        if "model_test_history" not in st.session_state:
            st.session_state["model_test_history"] = {}

        # 4. Connection Test Button
        if st.button("🧪 Test Model Connection", key="btn_ping_model", use_container_width=True):
            with st.spinner(f"Testing `{active_model}`..."):
                ok, latency, msg = test_model_connection(active_model)
                st.session_state["model_test_history"][active_model] = {
                    "ok": ok,
                    "latency": latency,
                    "msg": msg
                }

        # 5. Active Model Card with Latency or Strikethrough
        history = st.session_state["model_test_history"]
        if active_model in history:
            res = history[active_model]
            if res["ok"]:
                st.success(f"**Selected:**\n`{active_model}` — ⚡ **{res['latency']}ms**")
            else:
                st.error(f"**Selected:**\n~~`{active_model}`~~ ❌ *(Offline)*\n\n`{res['msg']}`")
        else:
            st.info(f"**Selected:**\n`{active_model}`")

        # 6. Test Results History Log
        if history:
            with st.expander("📋 Model Test Log", expanded=False):
                for m_name, data in history.items():
                    if data["ok"]:
                        st.markdown(f"• `{m_name}`: 🟢 **{data['latency']}ms**")
                    else:
                        st.markdown(f"• ~~`{m_name}`~~: 🔴 **Fail**")

        st.caption(f"🔌 Routed via **OmniRoute** (`{OPENROUTER_BASE_URL}`)")

        # Pinned bottom-left corner badge
        st.markdown(
            f"""
            <style>
                .version-footer {{
                    position: fixed;
                    bottom: 15px;
                    left: 15px;
                    background-color: rgba(30, 41, 59, 0.85);
                    color: #94a3b8;
                    padding: 4px 10px;
                    border-radius: 6px;
                    font-size: 0.80rem;
                    font-family: monospace;
                    border: 1px solid rgba(71, 85, 105, 0.4);
                    z-index: 999999;
                    pointer-events: none;
                }}
            </style>
            <div class="version-footer">⚡ NetBox Hub {APP_VERSION}</div>
            """,
            unsafe_allow_html=True
        )
    return active_model