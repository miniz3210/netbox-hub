# NetBox Universal Library Hub

[![Version](https://img.shields.io/badge/version-4.4.34-blue.svg)](CHANGELOG.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A Streamlit-based hub for managing NetBox IPAM, device naming conventions, and infrastructure data — with offline-first architecture and zero cloud leakage.

## v4.4.34 — Pure Lego Modular Refactor: Standards-Driven Tokens, Popover Crash Fix & Version Bump

### Changes
* Bumped version to **4.4.34** across `config/constants.py`, `data/naming_rules.yaml`, `README.md`, and `ARCHITECTURE_SOP.md`.

### Bug Fixes
* **Popover crash fix**: Added defensive dictionary resolution for `discovered_service_map` — prevents `AttributeError: 'NoneType' object has no attribute 'get'` when the session state map is missing or not a dict.
* **Ghost uplinks eliminated**: Removed dummy empty-row initialization in the topology DataGrid; empty records now render a clean empty DataFrame instead of a phantom "Uplink" row. Visual card uplink rendering already skips empty/junk interfaces.
* **Switch name normalization**: Fixed letter 'o' → digit '0' OCR substitution (e.g. `vSwitcho` → `vSwitch0`, `vSwitcho1` → `vSwitch01`) while preserving leading zeros.
* **Pure Lego teaming synthesis**: Replaced Python `re.sub(r"\s*Uplink$", "", role_raw)` regex stripping with pure Standards-driven dictionary mapping via `teaming_rules.uplink_to_teaming_mapping`.
* **Multi-line service & IP discovery**: Fixed single-line regex matching failure by switching to line-by-line iteration with look-ahead to the next line for service delimiters. Services and VMkernel IPs are now accurately harvested and linked to their parent switch container.

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
