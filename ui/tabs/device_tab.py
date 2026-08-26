import streamlit as st
import yaml
from core.catalog import search_library, read_yaml_content, normalize_mfg
from core.ai_client import call_ai

def generate_device_with_ai(manufacturer: str, model: str, active_model: str) -> str:
    system_msg = """You are a NetBox Device-Type schema generator.
Output ONLY a valid, standard NetBox Device-Type YAML specification.
Include: manufacturer, model, slug, part_number, u_height, is_full_depth, airflow, weight, weight_unit, power-ports, interfaces, and module-bays/console-ports if applicable.
Do not output conversational text or markdown explanation, ONLY valid YAML."""

    prompt = f"Generate NetBox YAML for: Manufacturer: {manufacturer}, Model: {model}"
    raw_res = call_ai(prompt, active_model, custom_system_msg=system_msg)
    
    # Strip markdown block wrappers if present
    cleaned = raw_res.strip()
    if cleaned.startswith("```yaml"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()

def render_device_tab(catalog, active_model):
    st.markdown("#### 🖥️ Device Type Definition (Library Search & AI Generator)")
    
    col1, col2 = st.columns(2)
    with col1:
        mfg_input = st.text_input("Manufacturer", value="", placeholder="e.g. Dell, HPE, Cisco, Palo Alto, Fortinet", key="dt_mfg").strip()
    with col2:
        model_input = st.text_input("Device Model", value="", placeholder="e.g. PowerEdge R750, ProLiant DL360 Gen10, Catalyst 9300", key="dt_model").strip()

    if not mfg_input or not model_input:
        st.info("Enter a Manufacturer and Device Model to search the NetBox community library or generate with AI.")
        return

    # Check for library match
    matched_dev = search_library(catalog, mfg_input, model_input)

    if matched_dev:
        real_mfg = matched_dev["manufacturer"]
        slug = matched_dev["slug"]
        
        st.success(f"✅ Found in NetBox Device-Type Library: **`{real_mfg} / {slug}`**")
        yaml_content = read_yaml_content(matched_dev["file_path"])
        
        c_left, c_right = st.columns([1.5, 8.5])
        with c_left:
            st.download_button(
                label="⬇️ Download YAML",
                data=yaml_content,
                file_name=f"{slug}.yaml",
                mime="text/yaml",
                use_container_width=True
            )
        st.code(yaml_content, language="yaml")
    else:
        st.warning("⚠️ No exact match found in library. Click below to generate fresh specification with AI.")
        
        if st.button("🚀 Load / Generate Device Type", type="primary", key="btn_gen_dt"):
            with st.spinner(f"Generating Device-Type YAML using `{active_model}`..."):
                try:
                    generated_yaml = generate_device_with_ai(mfg_input, model_input, active_model)
                    
                    st.markdown(f"**Source:** 🤖 AI Generated (`{active_model}`)")
                    st.download_button(
                        label="⬇️ Download YAML",
                        data=generated_yaml,
                        file_name=f"{mfg_input.lower()}_{model_input.lower().replace(' ', '-')}.yaml",
                        mime="text/yaml"
                    )
                    st.code(generated_yaml, language="yaml")
                except Exception as e:
                    st.error(f"Generation Failed: {e}")