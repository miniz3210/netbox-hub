# NetBox Universal Library Hub

[![Version](https://img.shields.io/badge/version-4.3-blue.svg)](CHANGELOG.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

A Streamlit-based hub for managing NetBox IPAM, device naming conventions, and infrastructure data — with offline-first architecture and zero cloud leakage.

## v4.3 — Offline IPAM Diff & Ingestion Engine

### New Features
* **Offline IPAM Diff Engine** (`scripts/diff_ipam_sqlite.py`): Standalone CLI tool that compares Excel-based IP/VLAN inventories against the local SQLite vault and produces deterministic `ipam_matched.csv`, `ipam_pending_add.csv`, and `ipam_conflicts.csv` outputs — no cloud or LLM calls.
* **Multi-Sheet Excel Parser** (`ExcelIPAMParser`): Handles 42-sheet workbooks with dynamic header discovery, supporting column name variations (`ip`/`IP`, `subnet`/`network`/`CIDR`, `VLAN ID`/`VID`, etc.).
* **Local SQLite Manager** (`LocalSQLiteManager`): Connects directly to `data/sanitizer_vault.db`, auto-creates the `ipam_inventory` table if absent, and queries existing records keyed by IP or subnet.

### Improvements
* Version bumped to **4.3** across `config/constants.py`, `data/naming_rules.yaml` (schema metadata), and CHANGELOG.
* Naming rules schema now carries `_meta.version` and `_meta.schema_version` for pipeline compatibility checks.

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

### Running the IPAM Diff Script

```bash
python scripts/diff_ipam_sqlite.py \
  --excel "AW IPs & VLANs_Final (2).xlsx" \
  --db data/sanitizer_vault.db \
  --out-dir data/ipam_diff_output
```

### Running Tests

```bash
pytest tests/ -v
```

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Excel Input    │────▶│  ExcelIPAMParser │────▶│  LocalSQLiteMgr │
│  (multi-sheet)  │     │  (offline)       │     │  (sqlite vault) │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                    ┌─────────────────────────────────────┘
                    │
                    ▼
         ┌────────────────────────┐
         │  Deterministic Diff    │
         │  ─ Matched / Add /     │
         │    Conflict            │
         └────────────────────────┘
                    │
                    ▼
         ┌────────────────────────┐
         │  Output CSVs           │
         │  ─ ipam_matched.csv    │
         │  ─ ipam_pending_add.csv│
         │  ─ ipam_conflicts.csv  │
         └────────────────────────┘
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
| `core/ipam_engine.py` | IPAM overlap detection, subnet allocation, CIDR validation |
| `core/db_manager.py` | SQLite database manager for NetBox data |
| `core/vault.py` | Local sanitization vault (IP/CIDR/domain tokenization) |
| `scripts/diff_ipam_sqlite.py` | Offline Excel → SQLite IPAM diff (v4.3) |

---

## Release Notes

See [CHANGELOG.md](CHANGELOG.md) for full release history.

---

## License

MIT
