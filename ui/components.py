import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.ai_client import fetch_gateway_models, test_model_connection

def render_sidebar() -> str:
    with st.sidebar:
        st.header("⚙️ AI Engine Selection")
        
        # 1. Discover live models from OmniRoute gateway
        live_models = fetch_gateway_models()
        model_options = live_models if live_models else AVAILABLE_MODELS
        
        selected_preset = st.selectbox(
            "Preset Models",
            options=model_options,
            index=0,
            help="Live models discovered directly from OmniRoute gateway."
        )

        # 2. Custom Model On-The-Fly Input Field with suggestions
        custom_model = st.text_input(
            "Custom Model",
            value="",
            placeholder="e.g. ox-alpha, groq/openai/gpt-oss-120b",
            help="Type any valid OmniRoute route ID or model slug. Tested suggestions: ox-alpha, groq/openai/gpt-oss-120b, gemini/gemini-3-flash-preview, groq/qwen/qwen3.6-27b"
        ).strip()

        # Custom model overrides dropdown when typed
        active_model = custom_model if custom_model else selected_preset

        st.info(f"**Selected:**\n`{active_model}`")
        st.caption(f"🔌 Routed via **OmniRoute** (`{OPENROUTER_BASE_URL}`)")

        # 3. Test Model Connection Button
        if st.button("🧪 Test Model Connection", key="btn_ping_model", use_container_width=True):
            with st.spinner(f"Testing `{active_model}` via OmniRoute..."):
                ok, msg = test_model_connection(active_model)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

        # Fixed bottom-left corner badge
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