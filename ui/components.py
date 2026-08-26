import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.ai_client import test_model_connection

RAW_SUGGESTED_MODELS = [
    "ox-alpha",
    "groq/qwen/qwen3.6-27b",
    "gemini/gemini-3-flash-preview",
    "aug/gemini-3.1-pro-preview",
    "aug/glm-5.2",
    "aug/gpt5.4",
    "aug/gpt5.2",
    "aug/gpt5.1",
    "aug/gpt5.4-mini",
    "aug/fable-5"
]

def render_sidebar() -> str:
    with st.sidebar:
        st.header("⚙️ AI Engine Selection")
        
        # 1. Preset Models
        selected_preset = st.selectbox(
            "Preset Models",
            options=AVAILABLE_MODELS,
            index=0,
            help="Configured environment presets."
        )

        # 2. Filter out any models already present in Preset Models
        filtered_suggestions = [
            m for m in RAW_SUGGESTED_MODELS 
            if m not in AVAILABLE_MODELS
        ]

        # Quick-Select Test Model Pull-Down Menu
        quick_pick = st.selectbox(
            "Quick-Select Test Model",
            options=["-- None (Use Preset / Manual) --"] + filtered_suggestions,
            index=0,
            help="Select any candidate route to load into Custom Model."
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