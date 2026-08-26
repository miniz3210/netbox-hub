import streamlit as st
from core.catalog import get_canonical_manufacturer, search_catalog_wildcard, fetch_raw_content
from core.ai_client import call_ai

def generate_device_yaml_ai(mfg: str, model: str, active_model: str) -> str:
    system_msg = """You are a NetBox Device-Type schema generator.
Output ONLY a valid, standard NetBox Device-Type YAML specification.
Include: manufacturer, model, slug, part_number, u_height, is_full_depth, airflow, weight, weight_unit, power-ports, interfaces, and module-bays if applicable.
Do not output conversational text or markdown explanation, ONLY valid YAML."""

    prompt = f"Generate NetBox Device-Type YAML for:\nManufacturer: {mfg}\nModel: {model}"
    raw_res = call_ai(prompt, active_model, custom_system_msg=system_msg)
    
    cleaned = raw_res.strip()
    if cleaned.startswith("```yaml"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()

def render_device_tab(catalog, active_model):
    col_left, col_right = st.columns([1, 1])

    catalog_mfgs = catalog.get("manufacturers", []) if catalog else []

    with col_left:
        mfg_input = st.text_input("Manufacturer", value="", placeholder="e.g., HP, Cisco, Dell", key="dt_mfg_in").strip()
        model_input = st.text_input("Device Model", value="", placeholder="e.g., dl360, microserver, PowerEdge R750", key="dt_model_in").strip()

        resolved_mfg = get_canonical_manufacturer(mfg_input, catalog_mfgs) if mfg_input else ""
        if mfg_input and resolved_mfg and mfg_input.lower() != resolved_mfg.lower():
            st.info(f"ℹ️ Matched manufacturer: `{mfg_input}` ➔ **`{resolved_mfg}`**")

        target_mfg = resolved_mfg if resolved_mfg else mfg_input
        matches = search_catalog_wildcard(catalog, target_mfg, model_input, category="device-types") if (target_mfg or model_input) else []

        selected_dev = None
        if matches:
            st.success(f"🔍 Found {len(matches)} matching device(s) in Official Library:")
            options = {d["rel_path"]: d for d in matches}
            selected_path = st.selectbox("Select definition or generate with AI:", list(options.keys()), key="dt_sel_match")
            selected_dev = options[selected_path]
        elif target_mfg and model_input:
            st.warning("No match found in library. Click below to generate fresh with AI.")

        clicked = st.button("Load / Generate Device Type", type="primary", key="btn_load_dt")

    with col_right:
        if clicked and target_mfg and model_input:
            if selected_dev:
                content = fetch_raw_content(selected_dev["full_path"])
                if content:
                    st.markdown(f"**Source:** 🟢 Official Repository (`{selected_dev['rel_path']}`)")
                    st.code(content, language="yaml")
                    st.download_button(
                        label="⬇️ Download YAML",
                        data=content,
                        file_name=selected_dev["filename"],
                        mime="text/yaml",
                        key="dt_dl_btn"
                    )
                else:
                    st.error("Error reading file content.")
            else:
                with st.spinner(f"Generating Device-Type YAML using `{active_model}`..."):
                    try:
                        ai_yaml = generate_device_yaml_ai(target_mfg, model_input, active_model)
                        st.markdown(f"**Source:** 🤖 AI Generated ({active_model})")
                        st.code(ai_yaml, language="yaml")
                        st.download_button(
                            label="⬇️ Download YAML",
                            data=ai_yaml,
                            file_name=f"{target_mfg.lower()}_{model_input.lower().replace(' ', '-')}.yaml",
                            mime="text/yaml",
                            key="dt_ai_dl_btn"
                        )
                    except Exception as e:
                        st.error(f"Generation Failed: {e}")