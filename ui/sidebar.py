import streamlit as st
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL

def render_sidebar():
    with st.sidebar:
        st.markdown("### ⚙️ AI Engine Selection")
        
        # 1. Preset Dropdown
        selected_preset = st.selectbox(
            "Preset Models",
            options=AVAILABLE_MODELS,
            index=0,
            help="Choose from your configured environment presets."
        )

        # 2. Custom Model Input Box
        custom_model = st.text_input(
            "Custom Model",
            value="",
            placeholder="e.g. groq/llama-3.3-70b-versatile",
            help="Type any valid model slug here to override the preset and test on the fly."
        ).strip()

        # Custom model overrides dropdown if typed
        active_model = custom_model if custom_model else selected_preset

        st.info(f"**Active Model:** `{active_model}`")
        st.caption(f"📡 Routed via OmniRoute (`{OPENROUTER_BASE_URL}`)")
        st.markdown("---")
        
    return active_model