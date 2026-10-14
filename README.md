# NetBox Universal Library Hub

[![Version](https://img.shields.io/badge/version-4.4.33-blue.svg)](CHANGELOG.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A Streamlit-based hub for managing NetBox IPAM, device naming conventions, and infrastructure data — with offline-first architecture and zero cloud leakage.

## v4.4.33 — Standards-Driven Service Discovery, Teaming Synthesis & Version Bump

### Changes
* Bumped version to **4.4.33** across `config/constants.py` and documentation.

### New Features
* **Multi-Sheet Excel Parser (`core.ipam_excel_diff.ExcelIPAMParser`)**: High-performance `openpyxl` streaming parser with dynamic header discovery across all sheets. Supports arbitrary sheet counts and column-name variations (`ip`/`address`/`ipv4`, `subnet`/`cidr`/`prefix`, `vlan id`/`vid`/`vlan`, etc.). Fallback site = sheet name when column is absent.
* **Standards-Driven Normalization**: Site names normalized via `get_site_code_rules(rules)["exact_mappings"]` with `slugify()` fallback. Roles normalized via `core.ipam_engine.normalize_role_name()` using `get_ipam_role_mappings(rules)`. VLAN descriptions resolved via `core.ipam_engine.resolve_vlan_description()`.
* **In-Memory Audit Engine**: Queries existing DB state once, builds hash sets for $O(1)$ lookups. Classifies records into `matched`, `pending_add`, `conflicts`, and `unmapped_sites`. Returns clean Pandas DataFrames and metric summaries.
* **UI Ingest & Audit Drawer**: Unified toolbar in the IPAM tab with file upload (`.xlsx`, `.xls`, `.csv`, `.json`), metric cards, tabbed audit view (Pending Add / Conflicts / Matched), export download buttons, and site selector for live editing.

### Improvements
* Version bumped to **4.3** across `config/constants.py`, `data/naming_rules.yaml` (schema metadata), and CHANGELOG.
* Removed legacy standalone script `scripts/diff_ipam_sqlite.py` — replaced by in-memory core engine.
* Removed legacy test `tests/test_ipam_diff.py` — replaced by `tests/test_ipam_excel_diff.py`.

---

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
