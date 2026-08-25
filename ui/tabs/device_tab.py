import streamlit as st
from core.catalog import search_device_type, get_device_yaml_from_github
from core.yaml_generator import generate_device_yaml
from utils.formatters import normalize_manufacturer_name

def render_device_tab(catalog, active_model):
    st.subheader("NetBox Device Library Hub")
    
    col1, col2 = st.columns([1, 1])
    with col1:
        manufacturer = st.text_input("Manufacturer", value="hp", placeholder="e.g. hp, cisco, dell", key="dev_mfg_input")
    with col2:
        device_model = st.text_input("Device Model", value="ProLiant MicroServer Gen8", placeholder="e.g. Catalyst 9300", key="dev_model_input")

    normalized_mfg = normalize_manufacturer_name(manufacturer)
    if normalized_mfg.lower() != manufacturer.strip().lower():
        st.markdown(f"ℹ️ Matched manufacturer: `{manufacturer}` $\\rightarrow$ **`{normalized_mfg}`**")

    st.markdown("---")

    match_path = search_device_type(catalog, normalized_mfg, device_model)

    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        if match_path:
            st.success(f"Found in official library:\n`{match_path}`")
            load_action = st.button("Load Official Device Type", type="primary", key="load_official_btn")
            if load_action:
                yaml_content = get_device_yaml_from_github(match_path)
                st.session_state['current_device_yaml'] = yaml_content
                st.session_state['source_label'] = f"Official Library ({match_path})"
        else:
            st.warning("No match found in library. Click below to generate fresh with AI.")
            gen_action = st.button("Load / Generate Device Type", type="primary", key="gen_ai_dev_btn")
            if gen_action:
                with st.spinner(f"Generating YAML specification using {active_model}..."):
                    yaml_content = generate_device_yaml(normalized_mfg, device_model, active_model)
                    st.session_state['current_device_yaml'] = yaml_content
                    st.session_state['source_label'] = f"AI Generated ({active_model})"

    with col_right:
        if 'current_device_yaml' in st.session_state:
            st.markdown(f"**Source:** 🌐 {st.session_state.get('source_label', 'Loaded Spec')}")
            st.code(st.session_state['current_device_yaml'], language="yaml")
            st.download_button(
                "📥 Download YAML",
                st.session_state['current_device_yaml'],
                file_name=f"{normalized_mfg}_{device_model}.yaml".lower().replace(" ", "_"),
                mime="text/yaml",
                key="dl_dev_yaml"
            )