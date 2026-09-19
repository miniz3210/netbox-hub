import os
import streamlit as st
from core.catalog import get_canonical_manufacturer, search_catalog_wildcard, fetch_raw_content, extract_reference_interface_pattern
from core.yaml_generator import generate_module_yaml
from core.exceptions import AIProviderError

def render_module_tab(catalog, active_model):
    col1, col2 = st.columns([1, 1])
    with col1:
        m_mfg_raw = st.text_input("Manufacturer", placeholder="e.g., Broadcom, Mellanox, Intel", key="m_mfg")
        m_model = st.text_input("Module Name / Part #", placeholder="e.g., 57416, X550-T2", key="m_mod")
        m_mfg = get_canonical_manufacturer(m_mfg_raw, catalog["manufacturers"]) if m_mfg_raw else ""

        selected_mod_choice, discovered_pattern = None, None
        if m_mfg_raw or m_model:
            similar_mods = search_catalog_wildcard(catalog["module_types"], m_mfg_raw, m_model)
            cross_devs = search_catalog_wildcard(catalog["device_types"], m_mfg_raw, m_model)
            all_matches = similar_mods + cross_devs

            if all_matches:
                st.success(f"🔍 Found {len(all_matches)} matching module(s) in Official Library:")
                selected_mod_choice = st.selectbox("Select definition or generate with AI:", all_matches + ["✨ Generate Fresh with AI"], key="mod_sel")
                discovered_pattern = extract_reference_interface_pattern(fetch_raw_content(all_matches[0], binary=False))
            else:
                selected_mod_choice = "✨ Generate Fresh with AI"

        m_search = st.button("Load / Generate Module Type", type="primary", key="btn_mod")

    # Allow loading if an official library file is selected OR if generating with AI and model is given
    if m_search and selected_mod_choice:
        effective_mfg = m_mfg if m_mfg else m_mfg_raw
        
        # Derive module/part name from selected library path if user left text input blank
        if not m_model and not selected_mod_choice.startswith("✨"):
            file_name = os.path.splitext(os.path.basename(selected_mod_choice))[0]
            effective_model = file_name
        else:
            effective_model = m_model

        if selected_mod_choice.startswith("✨") and not effective_model:
            st.error("⚠️ Please specify a Module Name / Part # to generate fresh YAML with AI.")
        else:
            with st.spinner("Processing..."):
                try:
                    if selected_mod_choice.startswith("✨"):
                        content = generate_module_yaml(effective_mfg, effective_model, effective_model, active_model, ref_pattern=discovered_pattern)
                        src = f"🤖 AI Generated ({active_model})"
                    else:
                        content = fetch_raw_content(selected_mod_choice, binary=False)
                        src = f"✅ Official Repository (`{selected_mod_choice}`)"
                    with col2:
                        st.markdown(f"**Source:** {src}")
                        st.code(content, language="yaml", line_numbers=True)
                        st.download_button(
                            "📥 Download Module YAML", 
                            content, 
                            f"module_{effective_mfg or 'module'}_{effective_model}.yaml".lower().replace(" ", "_"), 
                            "text/yaml"
                        )
                except AIProviderError as e:
                    st.error(f"❌ Generation Failed: {str(e)}")