# NetBox Universal Library Hub

[![Version](https://img.shields.io/badge/version-4.4.40-blue.svg)](CHANGELOG.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A Streamlit-based hub for managing NetBox IPAM, device naming conventions, and infrastructure data — with offline-first architecture and zero cloud leakage.

## v4.4.40 — Standards-Driven Description Rendering (Eliminate Teaming Duplication & Zero Hardcode)

### Changes
* Bumped version to **4.4.40** across `config/constants.py`, `README.md`, and `ARCHITECTURE_SOP.md`.
* **Switch Teaming template added**: New `Switch_Teaming` preset in `hypervisor_presets["VMware ESXi"]` with pattern `<v_switch> (<teaming>)` rendered ONCE per switch container.
* **PortGroup / VMkernel templates simplified**: Changed from legacy `<v_switch> (<active_vmnics> Active / <standby_vmnics> Standby)` to `<purpose> (<v_switch>)` — eliminating teaming clause duplication across every service line.
* **Uplink template unchanged**: `<vmnic> - <v_switch> <purpose> <uplink_role>` renders per physical adapter only.
* **Zero hardcoded string formats**: All descriptions now rendered through `render_dynamic_pattern(template, row_vals, pattern_vars)` driven by `data/naming_rules.yaml` standards templates. Removed hardcoded `f"{_gparent} ({teaming_str})"` and `f"{_label} ({_gparent})"` lines.

### Bug Fixes
* **Teaming duplication eliminated**: Switch teaming summary now appears exactly once as a header; Port Groups and VMkernel interfaces render only their `<purpose> (<v_switch>)` without repeating the teaming clause.
* **No uplink switches**: Switches with zero physical uplinks emit `<switch> (No Uplink)` exactly once via the Switch_Teaming template.

### Bug Fixes
* (Previous fixes from v4.4.39 remain)

### Changes
* Bumped version to **4.4.39** across `config/constants.py`, `README.md`, and `ARCHITECTURE_SOP.md`.
* **Pure standards-driven noise filtering**: All UI noise words are now driven exclusively by `ui_noise_blacklist` in `data/naming_rules.yaml` (Standards Tab → Section 2.6). Removed hardcoded `UI_CHROME_WORDS`, `_SIDEBAR_NOISE`, and `_UI_CHROME` sets from `ui/tabs/naming_tab.py`.
* **Added noise tokens**: `"addnetworking"`, `"refres"`, `"e热"` added to `ui_noise_blacklist` for VMware ESXi platform.
* **Auto-correction for service names**: Removed hardcoded `iscsi` → `iSCSI` replacement. Service name casing is now handled via `apply_auto_corrections(name, "vmware")` when `auto_correct` is enabled.
* **Inline text editing in popovers**: Both "+ Service" and "+ Uplink" popovers now feature editable text inputs that pre-fill from detected candidates, allowing instant typo correction before adding.
* **Inline rename on overview cards**: Service and Uplink names on topology cards are now inline-editable via `st.text_input` with auto-rerun on change.

### Bug Fixes
* **Noise filtering normalization**: Both OCR tokens and blacklist entries are normalized by stripping spaces and trailing punctuation when comparing, so `"add networking"` in Section 2.6 automatically matches `"ADDNETWORKING"`, `"ADD NETWORKING..."`, etc.

### Bug Fixes
* **OCR sidebar interleaving fix**: Replaced fragile next-line `i+1` delimiter check with a cleaned token-stream approach that strips all sidebar noise and right-column physical adapter segments from pipe-split lines before look-ahead. Every Port Group is now correctly detected even when sidebar menu lines (Certificate, Licensing, Host Profile, etc.) interleave between the service title and its `VLAN ID:` line.
* **Harvesting runs on existing OCR text**: Added `_harvest_ocr_topology_candidates()` helper called both during analysis and live render pass, so existing `latest_ocr_raw_text` immediately populates `discovered_service_candidates`, `discovered_service_map`, and `discovered_ip_candidates` without requiring a re-analyze.
* **"+ Service" popover IP dropdown**: Replaced manual-only IP text input with a selectable `st.selectbox` of all OCR-detected IP addresses, defaulting to the service's matched IP (e.g. `10.27.177.246` for Management Network/vmk0). Includes `"None (No IP)"` and `"➕ Custom / Manual IP"` options. Fixed bug where `_init_name` was incorrectly set to `"➕ Custom / Manual Entry"` instead of `""`.

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
