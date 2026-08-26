import streamlit as st
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL

def render_sidebar():
    with st.sidebar:
        st.markdown("### ⚙️ AI Engine Selection")
        
        model_options = AVAILABLE_MODELS + ["✏️ Custom Model Input..."]
        selected_option = st.selectbox(
            "Active AI Model",
            options=model_options,
            index=0,
            help="Select a configured model or choose custom input to test any model identifier."
        )

        if selected_option == "✏️ Custom Model Input...":
            active_model = st.text_input(
                "Enter Model ID / Slug",
                value="",
                placeholder="e.g. groq/llama-3.3-70b-versatile",
                help="Type the exact model slug supported by your backend gateway."
            ).strip()
            if not active_model:
                active_model = AVAILABLE_MODELS[0] if AVAILABLE_MODELS else "groq/openai/gpt-oss-120b"
        else:
            active_model = selected_option

        st.info(f"**Selected:** `{active_model}`")
        st.caption(f"📡 Routed via OmniRoute (`{OPENROUTER_BASE_URL}`)")
        st.markdown("---")
        
    return active_model