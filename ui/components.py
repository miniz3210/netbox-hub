import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL

def render_sidebar() -> str:
    with st.sidebar:
        st.header("⚙️ AI Engine Selection")
        active_model = st.selectbox("Active AI Model", AVAILABLE_MODELS, index=0)
        st.info(f"**Selected:**\n`{active_model}`")
        st.caption(f"🔌 Routed via **OmniRoute** (`{OPENROUTER_BASE_URL}`)")

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
                    z-index: 999;
                }}
            </style>
            <div class="version-footer">⚡ NetBox Hub {APP_VERSION}</div>
            """,
            unsafe_allow_html=True
        )
    return active_model