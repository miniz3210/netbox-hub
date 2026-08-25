import io
import zipfile
import pandas as pd
import streamlit as st
from concurrent.futures import ThreadPoolExecutor
from core.catalog import get_canonical_manufacturer, search_catalog_wildcard, fetch_raw_content
from core.yaml_generator import generate_device_yaml, generate_module_yaml, generate_rack_yaml

def render_batch_tab(catalog, active_model):
    st.write("Upload CSV/Excel with columns: `Category` (`device`, `module`, `rack`), `Manufacturer`, and `Model`.")
    sample_df = pd.DataFrame([
        {"Category": "device", "Manufacturer": "HP", "Model": "DL360 Gen10"},
        {"Category": "module", "Manufacturer": "Intel", "Model": "X550-T2"},
        {"Category": "rack", "Manufacturer": "APC", "Model": "NetShelter SX 42U"}
    ])
    st.download_button("📄 Download Template CSV", sample_df.to_csv(index=False).encode('utf-8'), "template.csv", "text/csv")

    batch_file = st.file_uploader("Upload Batch File (.xlsx, .csv)", type=["xlsx", "csv"])
    if batch_file:
        df = pd.read_csv(batch_file) if batch_file.name.endswith(".csv") else pd.read_excel(batch_file)
        st.dataframe(df.head(), use_container_width=True)
        
        if st.button("Start Parallel Batch Processing (5x Speed)", type="primary"):
            pbar = st.progress(0)
            zip_buf = io.BytesIO()
            results = []

            def process_row(row_data):
                idx, row = row_data
                cat = str(row.get("Category", "device")).lower().strip()
                mfg_raw = str(row.get("Manufacturer", "")).strip()
                model = str(row.get("Model", "")).strip()
                if not mfg_raw or not model or mfg_raw == "nan":
                    return None

                mfg = get_canonical_manufacturer(mfg_raw, catalog["manufacturers"])
                if cat == "module":
                    matches = search_catalog_wildcard(catalog["module_types"], mfg_raw, model)
                    content = fetch_raw_content(matches[0], binary=False) if matches else generate_module_yaml(mfg, model, model, active_model)
                    prefix = "module-types"
                elif cat == "rack":
                    matches = search_catalog_wildcard(catalog["rack_types"], mfg_raw, model)
                    content = fetch_raw_content(matches[0], binary=False) if matches else generate_rack_yaml(mfg, model, active_model)
                    prefix = "rack-types"
                else:
                    matches = search_catalog_wildcard(catalog["device_types"], mfg_raw, model)
                    content = fetch_raw_content(matches[0], binary=False) if matches else generate_device_yaml(mfg, model, active_model)
                    prefix = "device-types"

                src = f"Official ({matches[0]})" if matches else f"AI Generated ({active_model})"
                return (f"{prefix}/{mfg}/{model}.yaml".replace(" ", "_"), content, {"Category": cat, "Manufacturer": mfg, "Model": model, "Source": src})

            with zipfile.ZipFile(zip_buf, "w") as zf:
                rows = list(df.iterrows())
                with ThreadPoolExecutor(max_workers=5) as executor:
                    for i, res in enumerate(executor.map(process_row, rows)):
                        if res:
                            filename, content, meta = res
                            zf.writestr(filename, content)
                            results.append(meta)
                        pbar.progress((i + 1) / len(rows))

            st.success("Parallel Batch Generation Completed!")
            st.dataframe(pd.DataFrame(results), use_container_width=True)
            st.download_button("📦 Download All Assets (.zip)", zip_buf.getvalue(), "netbox_assets.zip", "application/zip")