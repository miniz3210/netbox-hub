import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL

def render_sidebar() -> str:
    with st.sidebar:
        st.header("⚙️ AI Engine Selection")
        
        # 1. Preset Dropdown
        selected_preset = st.selectbox(
            "Preset Models",
            options=AVAILABLE_MODELS,
            index=0,
            help="Select from configured environment presets."
        )

        # 2. Custom Model On-The-Fly Input Field
        custom_model = st.text_input(
            "Custom Model",
            value="",
            placeholder="e.g. gemini/gemini-3.1-flash-lite",
            help="Type any model slug here to override the preset and test on the fly."
        ).strip()

        # Custom model overrides dropdown if typed
        active_model = custom_model if custom_model else selected_preset

        st.info(f"**Selected:**\n`{active_model}`")
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