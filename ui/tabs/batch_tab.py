import io
import logging
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
        st.dataframe(df.head(), width="stretch")
        
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

                try:
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
                except Exception as e:
                    logging.exception("batch row failed")
                    return ("__error__", "", {"Category": cat, "Manufacturer": mfg_raw, "Model": model, "Source": f"❌ Error: {e}"})

            with zipfile.ZipFile(zip_buf, "w") as zf:
                rows = list(df.iterrows())
                with ThreadPoolExecutor(max_workers=5) as executor:
                    for i, res in enumerate(executor.map(process_row, rows)):
                        if res is None:
                            pbar.progress((i + 1) / len(rows))
                            continue
                        try:
                            filename, content, meta = res
                            if filename == "__error__":
                                results.append(meta)
                                continue
                            zf.writestr(filename, content)
                            results.append(meta)
                        except Exception as e:
                            results.append({"Category": "", "Manufacturer": "", "Model": "", "Source": f"❌ Error: {e}"})
                        pbar.progress((i + 1) / len(rows))

            failed = sum(1 for r in results if str(r.get("Source", "")).startswith("❌"))
            if failed:
                st.warning(f"{failed} row(s) failed, see table")
            else:
                st.success("Parallel Batch Generation Completed!")
            st.dataframe(pd.DataFrame(results), width="stretch")
            st.download_button("📦 Download All Assets (.zip)", zip_buf.getvalue(), "netbox_assets.zip", "application/zip")