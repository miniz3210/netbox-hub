import streamlit as st
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from config.constants import APP_VERSION

def render_sidebar():
    with st.sidebar:
        st.markdown("### ⚙️ AI Engine Selection")
        
        # 1. Preset Dropdown
        selected_preset = st.selectbox(
            "Preset Models",
            options=AVAILABLE_MODELS,
            index=0,
            help="Select from configured environment presets."
        )

        # 2. On-the-fly Custom Model Text Field
        custom_model = st.text_input(
            "Custom Model",
            value="",
            placeholder="e.g. gemini/gemini-3.1-flash-lite",
            help="Type any valid model slug here to override the preset on the fly."
        ).strip()

        # Custom model overrides the dropdown when typed
        active_model = custom_model if custom_model else selected_preset

        st.info(f"**Active Model:** `{active_model}`")
        st.caption(f"📡 Routed via OmniRoute (`{OPENROUTER_BASE_URL}`)")
        st.markdown("---")
        return active_model