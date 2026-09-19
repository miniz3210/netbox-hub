import os
import re
import streamlit as st
from core.catalog import get_canonical_manufacturer, search_catalog_wildcard, fetch_raw_content
from core.yaml_generator import generate_device_yaml
from core.exceptions import AIProviderError

def render_device_tab(catalog, active_model):
    col1, col2 = st.columns([1, 1])
    with col1:
        d_mfg_raw = st.text_input("Manufacturer", placeholder="e.g., HP, Cisco, Dell", key="d_mfg")
        d_model = st.text_input("Device Model", placeholder="e.g., dl360, PowerEdge R750", key="d_mod")
        d_mfg = get_canonical_manufacturer(d_mfg_raw, catalog["manufacturers"]) if d_mfg_raw else ""

        selected_dev_choice = None
        if d_mfg_raw or d_model:
            similar_devs = search_catalog_wildcard(catalog["device_types"], d_mfg_raw, d_model)
            cross_mods = search_catalog_wildcard(catalog["module_types"], d_mfg_raw, d_model)
            all_matches = similar_devs + cross_mods

            if d_mfg and d_mfg != d_mfg_raw.strip():
                st.caption(f"ℹ️ Matched manufacturer: `{d_mfg_raw}` ➔ **`{d_mfg}`**")

            if all_matches:
                st.success(f"🔍 Found {len(all_matches)} matching definition(s) in Official Library:")
                selected_dev_choice = st.selectbox("Select library definition or generate with AI:", all_matches + ["✨ Generate Fresh with AI"], key="dev_sel")
            else:
                st.warning("No match found in library. Click below to generate fresh with AI.")
                selected_dev_choice = "✨ Generate Fresh with AI"

        d_search = st.button("Load / Generate Device Type", type="primary", key="btn_dev")

    if d_search and selected_dev_choice:
        effective_mfg = d_mfg if d_mfg else d_mfg_raw
        
        if not d_model and not selected_dev_choice.startswith("✨"):
            file_name = os.path.splitext(os.path.basename(selected_dev_choice))[0]
            effective_model = file_name
        else:
            effective_model = d_model

        if selected_dev_choice.startswith("✨") and not effective_model:
            st.error("⚠️ Please specify a Device Model name to generate fresh YAML with AI.")
        else:
            with st.spinner("Processing..."):
                try:
                    if selected_dev_choice.startswith("✨"):
                        content = generate_device_yaml(effective_mfg, effective_model, active_model)
                        src = f"🤖 AI Generated ({active_model})"
                    else:
                        content = fetch_raw_content(selected_dev_choice, binary=False)
                        src = f"✅ Official Repository (`{selected_dev_choice}`)"
                    with col2:
                        st.markdown(f"**Source:** {src}")
                        st.code(content, language="yaml", line_numbers=True)
                        st.download_button(
                            "📥 Download YAML", 
                            content, 
                            f"{effective_mfg or 'device'}_{effective_model}.yaml".lower().replace(" ", "_"), 
                            "text/yaml"
                        )
                except AIProviderError as e:
                    st.error(f"❌ Generation Failed: {str(e)}")