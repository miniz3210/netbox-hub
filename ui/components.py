import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.ai_client import test_model_connection, fetch_free_models

def render_sidebar() -> str:
    with st.sidebar:
        st.header("⚙️ AI Engine Selection")
        
        # 1. Preset Models from environment
        selected_preset = st.selectbox(
            "Preset Models",
            options=AVAILABLE_MODELS,
            index=0,
            help="Configured environment presets."
        )

        # 2. Load and cache free models list (only when user clicks refresh)
        if "free_models_cache" not in st.session_state:
            st.session_state["free_models_cache"] = []
        if "models_loaded" not in st.session_state:
            st.session_state["models_loaded"] = False
        
        # Use cached models (empty by default until refresh is clicked)
        test_models = st.session_state["free_models_cache"]
        
        # Filter candidate models to exclude any already in Preset Models
        filtered_suggestions = [
            m for m in test_models 
            if m not in AVAILABLE_MODELS
        ]

        # Quick-Select Test Model Pull-Down Menu
        col1, col2 = st.columns([4, 1])
        with col1:
            if not st.session_state["models_loaded"]:
                placeholder = "-- Click refresh to load models --"
            elif len(filtered_suggestions) == 0:
                placeholder = "-- No free models available --"
            else:
                placeholder = "-- Select a model --"
            
            quick_pick = st.selectbox(
                "Quick-Select Test Model",
                options=[placeholder] + filtered_suggestions,
                index=0,
                help="Click the refresh button to load free models from API."
            )
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔄", key="btn_refresh_models", help="Refresh model list"):
                with st.spinner("Loading free models..."):
                    fetched_models = fetch_free_models()
                    st.session_state["free_models_cache"] = fetched_models
                    st.session_state["models_loaded"] = True
                    if len(fetched_models) == 0:
                        st.warning("No free models found. Check logs for details.")
                    else:
                        st.success(f"Loaded {len(fetched_models)} free models")
                st.rerun()

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

    return active_model