import streamlit as st
from core.catalog import get_canonical_manufacturer, search_catalog_wildcard, fetch_raw_content
from core.yaml_generator import generate_placeholder_svg

def render_image_tab(catalog):
    col1, col2 = st.columns([1, 2])
    with col1:
        img_cat = st.selectbox("Image Target", ["Elevation Images (Rack Face)", "Module Images"])
        i_mfg_raw = st.text_input("Manufacturer", placeholder="e.g., HP, Cisco, Dell", key="i_mfg")
        i_model = st.text_input("Model Name", placeholder="e.g., DL360", key="i_mod")
        i_mfg = get_canonical_manufacturer(i_mfg_raw, catalog["manufacturers"]) if i_mfg_raw else ""
        i_search = st.button("Find / Render Image", type="primary", key="btn_img")

    with col2:
        if i_search and (i_mfg or i_mfg_raw) and i_model:
            target_list = catalog["elevation_images"] if "Elevation" in img_cat else catalog["module_images"]
            matched_images = search_catalog_wildcard(target_list, i_mfg_raw, i_model)
            effective_mfg = i_mfg if i_mfg else i_mfg_raw

            if matched_images:
                st.success(f"Found {len(matched_images)} matching image(s):")
                for img_path in matched_images:
                    raw_data = fetch_raw_content(img_path, binary=True)
                    st.image(raw_data, caption=img_path.split("/")[-1], use_container_width=True)
                    st.download_button(f"📥 Download {img_path.split('/')[-1]}", raw_data, img_path.split('/')[-1])
            else:
                st.warning("No official image found. Generated standard vector SVG template:")
                svg_front = generate_placeholder_svg(effective_mfg, i_model, u_height=2, view="front")
                st.image(svg_front, caption="Auto-Generated Front SVG", use_container_width=True)
                st.download_button("📥 Download Vector (.svg)", svg_front, f"{effective_mfg}_{i_model}.front.svg", "image/svg+xml")