from typing import Callable

import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.ai_client import call_ai, test_model_connection, fetch_free_models

CHAT_HEIGHT = 380

def _clear_ai_chat(history_key: str, open_key: str) -> None:
    st.session_state[history_key] = []
    st.session_state[open_key] = True

def _keep_ai_chat_open(open_key: str) -> None:
    st.session_state[open_key] = True

def render_ai_chat(
    history_key: str,
    caption: str,
    placeholder: str,
    active_model: str,
    build_system_prompt: Callable[[str], str],
    label: str = "🤖 AI Assistant",
    height: int = CHAT_HEIGHT,
) -> None:
    """Render a self-contained AI chat panel.

    The transcript lives in a fixed-height scrolling container and the input box is
    rendered after it, so the input always stays at the bottom of the chat. Clearing
    and submitting both run as widget callbacks, which fire before the panel is drawn
    and therefore apply on the same run without an extra st.rerun().
    """
    open_key = f"{history_key}_open"
    if history_key not in st.session_state:
        st.session_state[history_key] = []
    if open_key not in st.session_state:
        st.session_state[open_key] = False

    with st.expander(label, expanded=st.session_state[open_key]):
        st.caption(caption)
        history = st.session_state[history_key]

        # Reserve the header row now, but render the button after this turn is
        # processed so its disabled state reflects the messages just added.
        _, c_clear = st.columns([3, 1])

        transcript = st.container(height=height, border=True)
        for message in history:
            with transcript.chat_message(message["role"]):
                st.markdown(message["content"])

        prompt = st.chat_input(
            placeholder,
            key=f"{history_key}_input",
            on_submit=_keep_ai_chat_open,
            args=(open_key,),
        )

        if prompt:
            history.append({"role": "user", "content": prompt})
            with transcript.chat_message("user"):
                st.markdown(prompt)

            with transcript.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        response = call_ai(
                            prompt,
                            active_model,
                            custom_system_msg=build_system_prompt(prompt),
                        )
                    except Exception as exc:
                        response = f"❌ AI Assistant temporarily unavailable: {exc}"
                st.markdown(response)

            history.append({"role": "assistant", "content": response})

        c_clear.button(
            "🗑️ Clear Chat",
            key=f"{history_key}_clear",
            on_click=_clear_ai_chat,
            args=(history_key, open_key),
            disabled=not history,
            width="stretch",
        )

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