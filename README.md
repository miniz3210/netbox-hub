# NetBox Universal Library Hub

[![Version](https://img.shields.io/badge/version-4.4.36-blue.svg)](CHANGELOG.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A Streamlit-based hub for managing NetBox IPAM, device naming conventions, and infrastructure data — with offline-first architecture and zero cloud leakage.

## v4.4.36 — Critical State Overwrite Fix, Delimiter Regex & Auto-Fill in Naming Tab

### Changes
* Bumped version to **4.4.36** across `config/constants.py`, `README.md`, and `ARCHITECTURE_SOP.md`.

### Bug Fixes
* **Fatal data wipe fix**: Incremented `editor_ver` during analysis so `st.data_editor` key changes and no longer overwrites topology records with empty data on every analyze pass.
* **Service regex look-ahead**: Line-by-line service detection now correctly uses next-line look-ahead against `_service_delimiters` instead of broken regex matching on single-line strings.
* **Ghost vmnic0 eliminated**: Blank Adapter Name in "+ Uplink" popover now shows a warning instead of inventing `vmnic0`. Speed is optional (empty default). Switches with no physical adapters output `(No Uplink)`.
* **Popover dynamic sync**: "+ Service" popover now uses dynamic widget keys based on `selected_svc_choice`, so selecting a discovered service (e.g. `Management Network`) dynamically fills Name, sets Type to `VMkernel`, and populates the IP immediately.

### New Configuration
* **`<uplink_role>` variable**: Added under `pattern_variables` with select options (Active Uplink, Standby Uplink, Primary, Backup, None), `empty_role_token`, and `no_uplink_fallback`.
* **`<teaming_role>` variable**: Added under `pattern_variables` with select options (Active, Standby, Primary, Backup, None).
* **VMware ESXi platform config**: Extended `teaming_rules` with `uplink_to_teaming_mapping` dictionary.

---

## v4.4.33 — Standards-Driven Service Discovery, Teaming Synthesis & Version Bump

## Getting Started

### Requirements

- Python 3.11+
- ARM64 / aarch64 (Oracle Cloud Ampere A1) or x86_64
- Streamlit

Install dependencies:

```bash
pip install -r requirements.txt
```

### Running the Application

```bash
streamlit run app.py
```

### Running Tests

```bash
pytest tests/ -v
```

---

## Architecture

```
┌──────────────────┐     ┌───────────────────────────┐     ┌──────────────────┐
│  Excel/CSV/JSON  │────▶│  ExcelIPAMParser          │────▶│  In-Memory       │
│  (multi-sheet)   │     │  (streaming, openpyxl)    │     │  Audit Engine    │
└──────────────────┘     └───────────┬───────────────┘     └────────┬─────────┘
                                     │                              │
                     ┌───────────────┼──────────────────────────────┘
                     │               │
                     ▼               ▼
          ┌──────────────────┐  ┌────────────────────┐
          │  DB State (SQLite│  │  Output DataFrames  │
          │   hash sets)     │  │  + Metric Summary   │
          └──────────────────┘  └────────────────────┘
```

**Design Principles:**
- **Zero Hardcoded Values**: All naming conventions driven by `data/naming_rules.yaml`.
- **Zero Cloud Leakage**: All processing is local; no API calls to external services.
- **Less is More**: Minimal dependencies, no unnecessary abstractions.

---

## Modules

| Module | Description |
|--------|-------------|
| `config/constants.py` | Application version and path constants |
| `config/naming_rules.py` | Naming convention engine, schema loader, migration |
| `core/ipam_excel_diff.py` | Multi-sheet Excel parser, in-memory audit engine, classification |
| `core/ipam_engine.py` | IPAM overlap detection, subnet allocation, CIDR validation |
| `core/db_manager.py` | SQLite database manager for NetBox data |
| `ui/tabs/ipam_tab.py` | Streamlit IPAM tab with Ingest & Audit Drawer UI |

---

## Release Notes

See [CHANGELOG.md](CHANGELOG.md) for full release history.

---

## License

MIT
