# NetBox Hub — Living Architecture SOP & Handover Guide
# Target Baseline: v4.3.3 | Platform: Oracle Cloud Ampere A1 (ARM64)

---

## 1. Executive Architecture Baseline

| Property              | Value                                                        |
|-----------------------|--------------------------------------------------------------|
| Application           | NetBox Universal Library Hub                                 |
| Target Version        | v4.3.3                                                       |
| Runtime               | Python 3.11+ / Streamlit                                     |
| Cloud Infrastructure  | Oracle Cloud Ampere A1 (ARM64 / aarch64, Linux)              |
| Container Registry    | `ghcr.io/miniz3210/netbox-hub`                               |
| Primary Backend       | SQLite (`data/netbox_hub.db`) + YAML-based naming standards  |
| AI / OCR Layer        | Multi-pass LLM extraction with spatial-anchored parsing      |

### Core Philosophy

1. **Strict No-Hardcode** — All vendor strings, interface names (`vmnic`, `eth`, `vmbr`), and site identifiers are discovered at runtime from the Standards Tab configuration (`naming_rules.yaml`). Zero baked-in assumptions.
2. **Platform-Agnostic Abstraction** — The system models three universal entity layers:
   - **Physical** (devices, ports, NICs, interfaces)
   - **LogicalNetwork** (prefixes, VLANs, IPAM allocations, VLAN descriptions)
   - **Endpoint** (VMs, vNICs, MAC addresses, IP assignments)
3. **Less is More** — Minimal UI chrome; no nested containers; flat component hierarchy; toast-only notifications.
4. **Zero Data Leakage** — All sensitive tokens (RFC1918 IPs, MACs, internal DNS domains) are redacted in-memory before any external LLM call and restored in-memory after extraction.

---

## 2. Security & Zero-Leakage Boundary

### Sanitizer Vault (`core/sanitizer_vault.py`)

- Maintains an in-memory mapping of redacted tokens back to original values.
- Never serializes vault state to disk, logs, or network.
- Supports deterministic redaction strategies for IPv4, IPv6, MAC, and domain tokens.

### Local Spatial Redaction (`core/spatial_redactor.py`)

- Performs card-centric upward binding on OCR token lists.
- Respects resolution-agnostic geometry (bounding-box ratios).
- Accumulates Y-axis offsets across multi-screenshot payloads: `cumulative_y += img_height`.
- Returns a redacted prompt payload + a restoration map for post-LLM de-tokenization.

### In-Memory NAT (De-tokenization)

- After the LLM returns structured JSON, the restoration map is applied locally to reinject real IPs/MACs/domains.
- The original data never leaves the process memory boundary.

---

## 3. Core Processing Pipeline

```
[Screen Screenshot(s)]
        │
        ▼
┌──────────────────────┐
│  Screen OCR Engine    │   (PIL / pytesseract or vendor SDK)
│  + Y-offset acc.      │   cumulative_y += img_height per image
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Spatial Anchoring    │   Group tokens into cards/boundaries
│  & Relative Clustering│   using bounding-box ratios
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Redaction (Vault)    │   Replace RFC1918 / MAC / domains
│  Sanitizer Vault      │   with deterministic tokens
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  LLM Structured       │   Extract JSON via multi-pass AI
│  Extraction           │   (ai_two_pass_system.py)
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Local De-tokenization│   Restore original IPs/MACs/domains
│  In-Memory NAT        │   from sanitizer vault map
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  DB Commit / Sync     │   SQLite write + NetBox upload
└──────────────────────┘
```

---

## 4. UI & Engineering Quality Standards

### Layout Rules

- **Flattened UI hierarchy**: No `st.container` inside `st.expander` (no "box-in-a-box").
- **Unified header layout**: Every preset editor shares the same header bar with right-aligned badge counts.
- **Non-blocking feedback**: Use `st.toast()` exclusively. Never `st.success()` / `st.info()` / `st.error()` banners that persist and distort layouts.

### Performance Caching

- `@st.cache_data` decorators **must** be at module top level — never inside functions, classes, or loops.
- Cache keys should be stable and derived from explicit parameters.

### Cross-Tab State Synchronization

- Any Standards Tab update that modifies `naming_rules.yaml` must call `SSM.refresh_naming_rules()` and increment `standards_nonce`.
- This propagates rule changes to the Naming, IPAM, and Device tabs instantly.

---

## 5. Deployment & Release SOP

### Step 1 — Rule Solidification
Capture a baseline snapshot of the Standards Tab configuration into `data/naming_rules.yaml`. Verify all section definitions (2.5 UX rules, 4.0 Blueprint guide) are present and consistent.

### Step 2 — Version Assert Verification
Confirm `config/constants.py::APP_VERSION == "4.3.3"`. Ensure no release notes or version bump scripts are pending.

### Step 3 — Safe Local Container Cache Cleanup
```bash
docker image prune -a -f
docker builder prune -a -f
# NEVER run docker volume prune — protects data/netbox_hub.db
```

### Step 4 — Docker Build & Tag Release
```bash
docker build -t ghcr.io/miniz3210/netbox-hub:v4.3.3 -t ghcr.io/miniz3210/netbox-hub:latest .
docker push ghcr.io/miniz3210/netbox-hub:v4.3.3
docker push ghcr.io/miniz3210/netbox-hub:latest
```

---

## 6. Living Task Checklist

### ✅ Completed Checklist (v4.3.3 Baseline)

- [x] Relative spatial clustering engine & resolution-agnostic geometry partitioning.
- [x] Boundary card fix (eliminated collapsed header overlap, e.g. iSCSI Veeam).
- [x] Multi-screenshot Y-axis offset accumulation (`cumulative_y += img_height`).
- [x] Zero-leakage deterministic local redaction & UI de-tokenization.
- [x] Standards tab Section 2.5 UX & `naming_rules.yaml` schema refactoring.
- [x] Standards tab Section 4 refactored to read-only AI Blueprint Guide.
- [x] Flattened UI: eliminated nested `st.container(border=True)` across preset cards.
- [x] Unified header layout and right-aligned badge count formatting.
- [x] Notification migration: replaced disruptive alert banners with `st.toast`.
- [x] Naming tab performance cache fix (`_interface_ref_examples_cached` at module top level).
- [x] Cleaned up obsolete standalone scripts and consolidated tests (114 passing tests).

### ⬜ Pending Roadmap

- [ ] Prune stale local git tags prior to v4.2.4 (audit `git tag -l` for orphaned pre-v4.2.4 refs).
- [ ] Add unit test coverage for `spatial_redactor.py` in `tests/test_spatial_redactor.py`.
- [ ] Build platform expansion templates for Proxmox VE and Azure Portal.
- [ ] Release Docker image tag `v4.3.3` to GHCR (`ghcr.io/miniz3210/netbox-hub:v4.3.3`).

---

## 7. Key File Map

| File / Directory                        | Purpose                                                            |
|-----------------------------------------|--------------------------------------------------------------------|
| `app.py`                                | Streamlit entry point                                              |
| `config/constants.py`                   | `APP_VERSION`, paths, feature flags                                |
| `config/naming_rules.py`                | Dynamic loading of `naming_rules.yaml`                             |
| `core/spatial_redactor.py`              | Card-centric spatial parsing + in-memory redaction                 |
| `core/sanitizer_vault.py`               | Token↔original value mapping (in-memory only)                      |
| `core/ai_two_pass_system.py`            | Multi-pass LLM extraction orchestration                            |
| `core/db_manager.py`                    | SQLite schema, CRUD, site/prefix lookups                           |
| `core/ipam_excel_diff.py`               | Offline Excel → IPAM reconciliation (multi-sheet, O(1) hash sets)  |
| `ui/tabs/standards_tab.py`              | Standards definition editor (Sections 1–4)                         |
| `ui/tabs/naming_tab.py`                 | Dynamic hostname/IPAM naming generation                            |
| `ui/tabs/ipam_tab.py`                   | IP address management, diff viewer                                 |
| `ui/tabs/device_tab.py`                 | Device / NIC import via OCR                                        |
| `ui/components.py`                      | Reusable header / badge / card layout components                   |
| `tests/`                                | 114 unit & integration tests (`pytest -q`)                         |
| `data/naming_rules.yaml`                | Live naming standard definitions (authoritative source of truth)   |
| `data/netbox_hub.db`                    | Local SQLite persistence (protected from volume prune)             |
| `.cursorrules`                          | Persistent agent operational invariants (local-only)               |
| `ARCHITECTURE_SOP.md`                   | This document — living handover guide (local-only)                 |

---

## 8. Constrained Operations Cheat-Sheet

```bash
# Run full test suite
python3 -m pytest tests/ -q

# Check version alignment
python3 -c "from config.constants import APP_VERSION; print(APP_VERSION)"

# Inspect git state before any change
git status && git diff --stat

# Syntax check a module
python3 -m py_compile core/spatial_redactor.py

# Safe Docker cache cleanup (NO volume prune)
docker image prune -a -f && docker builder prune -a -f
```
