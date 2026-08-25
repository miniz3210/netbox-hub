import streamlit as st
from core.catalog import get_canonical_manufacturer, search_catalog_wildcard, fetch_raw_content
from core.yaml_generator import generate_rack_yaml
from core.exceptions import AIProviderError

def render_rack_tab(catalog, active_model):
    col1, col2 = st.columns([1, 1])
    with col1:
        r_mfg_raw = st.text_input("Rack Manufacturer", placeholder="e.g., APC, Eaton, Rittal", key="r_mfg")
        r_model = st.text_input("Rack Model", placeholder="e.g., NetShelter SX", key="r_mod")
        r_mfg = get_canonical_manufacturer(r_mfg_raw, catalog["manufacturers"]) if r_mfg_raw else ""

        selected_rack_choice = None
        if r_mfg_raw or r_model:
            similar_racks = search_catalog_wildcard(catalog["rack_types"], r_mfg_raw, r_model)
            if similar_racks:
                st.success(f"🔍 Found {len(similar_racks)} matching rack(s):")
                selected_rack_choice = st.selectbox("Select rack:", similar_racks + ["✨ Generate Fresh with AI"], key="rack_sel")
            else:
                selected_rack_choice = "✨ Generate Fresh with AI"

        r_search = st.button("Load / Generate Rack Type", type="primary", key="btn_rack")

    if r_search and (r_mfg or r_mfg_raw or selected_rack_choice) and r_model and selected_rack_choice:
        effective_mfg = r_mfg if r_mfg else r_mfg_raw
        with st.spinner("Processing..."):
            try:
                if selected_rack_choice.startswith("✨"):
                    content = generate_rack_yaml(effective_mfg, r_model, active_model)
                    src = f"🤖 AI Generated ({active_model})"
                else:
                    content = fetch_raw_content(selected_rack_choice, binary=False)
                    src = f"✅ Official Repository (`{selected_rack_choice}`)"
                with col2:
                    st.markdown(f"**Source:** {src}")
                    st.code(content, language="yaml", line_numbers=True)
                    st.download_button("📥 Download Rack YAML", content, f"rack_{effective_mfg}_{r_model}.yaml", "text/yaml")
            except AIProviderError as e:
                st.error(f"❌ Generation Failed: {str(e)}")