# NetBox Hub — Living Architecture SOP & Handover Guide
# Target Baseline: 4.8.29 | Platform: Oracle Cloud Ampere A1 (ARM64)

---

## 1. Executive Architecture Baseline

| Property              | Value                                                        |
|-----------------------|--------------------------------------------------------------|
| Application           | NetBox Universal Library Hub                                 |
| Target Version        | 4.8.29                                                      |
| Runtime               | Python 3.11+ / Streamlit                                     |
| Cloud Infrastructure  | Oracle Cloud Ampere A1 (ARM64 / aarch64, Linux)              |
| Container Registry    | `ghcr.io/miniz3210/netbox-hub:4.8.29`                       |
| Primary Backend       | SQLite (`data/netbox_hub.db`) + YAML-based naming standards  |
| AI / OCR Layer        | Strategy/Adapter Extractor Pattern — Pure Python, sub-second, Zero LLM, Zero Ollama dependency |

### Core Philosophy

1. **Strict No-Hardcode** — All vendor strings, interface names (`vmnic`, `eth`, `vmbr`), and site identifiers are discovered at runtime from the Standards Tab configuration (`naming_rules.yaml`). Zero baked-in assumptions.
2. **Platform-Agnostic Abstraction** — The system models three universal entity layers:
   - **Physical** (devices, ports, NICs, interfaces)
   - **LogicalNetwork** (prefixes, VLANs, IPAM allocations, VLAN descriptions)
   - **Endpoint** (VMs, vNICs, MAC addresses, IP assignments)
3. **Less is More** — Minimal UI chrome; no nested containers; flat component hierarchy; toast-only notifications.
4. **Zero Data Leakage** — All sensitive tokens (RFC1918 IPs, MACs, internal DNS domains) are redacted in-memory before any external LLM call and restored in-memory after extraction.
5. **Lean Extraction + Human-in-the-Loop** — The LLM focuses on pure factual extraction and discrepancy detection only, emitting `is_uncertain` flags and `note` fields for anomalies. Final string template assembly is handled deterministically from the Standards Tab naming patterns. The UI review step (`st.data_editor`) is the human checkpoint where corrections are applied before any data reaches the database.
6. **Session-only In-Memory Cascade Replace** — When a user corrects a cell value (e.g. an OCR typo like `dvSwitcho` → `dvSwitch0`) in the DataGrid, the replacement cascades to every row in the active session's memory that contains the same substring in `interface`, `parent`, or `note` fields. The correction is **not persisted to disk** and does **not pollute future hosts or sessions** — it is ephemeral and scoped to the current browser session only.

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

### Topology & Spatial Invariants (4.8.17)

#### Left/Right Parent Column Separation

- **Port Groups and VMkernel interfaces** belong strictly to the root Switch (`parent = <SwitchName>`), NEVER to a `-DVUplinks-` container.
- **Physical adapters (`vmnic*`)** in a DVS belong strictly to the `-DVUplinks-<ID>` container.
- The column split is determined by content-pattern heuristics in `_step_c_column_split_card_centric()`: tokens containing `vmnic`, `Full`, or "no physical network adapters" go to the uplink bucket; all others go to the port-group bucket.

#### Ghost Interface Filtering

- DVS slot headers matching `Uplink <N> [<N> NIC Adapters]` are **discarded** — they are section markers, not valid data.
- Valid nested `vmnic*` adapter entries inside those slots **are extracted**.
- Empty slots `(0 NIC Adapters)` must **never** trigger hallucinated NIC increments; the pipeline treats them as an explicit empty-state signal and skips further uplink inference.

#### Switch Name & Number Fidelity

- Verbatim preservation of valid switch numbers: `vSwitch0`, `vSwitch01`, `vSwitch02`, `vSwitch1` are distinct and must not be silently normalized against each other.
- OCR letter 'o' → digit '0' correction is applied **only on switch suffixes** (e.g. `dvSwitcho` → `dvSwitch0`). This is a deterministic normalization, not an uncertainty flag.
- Two-digit and multi-digit switch suffixes are preserved as-is.

#### Icon Stripping

- Leading UI status symbols (`❌`, `!`, `✔`, and the virtual-machine-style `X` prefix like `Xvmnic1`) are stripped silently during text normalization to their canonical form (`vmnic1`).
- This stripping does **not** trigger `is_uncertain` flags — it is treated as pure UI noise removal.

#### DataGrid Uncertainty Tagging

- Fields that fail anomaly-detection (e.g. isolated letters between digits like `6Q5`, or `'o'` next to a digit) are flagged with `is_uncertain = True` and a descriptive `note`.
- The `is_uncertain` / `note` fields are purely informational in the DataGrid; human review resolves them before commit.

---

## 3. Core Processing Pipeline

```
[Screen Screenshot(s)]
        │
        ▼
┌──────────────────────┐
│  Screen OCR Engine    │   (PIL / RapidOCR — deterministic, no LLM)
│  + Y-offset acc.      │   cumulative_y += img_height per image
└──────────┬───────────┘
            ▼
┌──────────────────────┐
│  Spatial Anchoring    │   Group tokens into cards/boundaries
│  & Relative Clustering│   using bounding-box ratios
└──────────┬───────────┘
            ▼
┌──────────────────────┐
│  Platform Extractor   │   core/extractors/ — deterministic Python
│  (Strategy Pattern)   │   Zero LLM, zero Ollama, sub-second
└──────────┬───────────┘
            ▼
┌──────────────────────┐
│  Universal Reducer    │   core/topology_reducer.py — dedup,
│  & Normalization      │   prefix normalization, state precedence
└──────────┬───────────┘
            ▼
┌──────────────────────┐
│  Human-in-the-Loop    │   DataGrid review: edit cells, cascade
│  DataGrid Review      │   corrections across active session rows
└──────────┬───────────┘
            ▼
┌──────────────────────┐
│  Deterministic Template│  Apply naming patterns from Standards
│  Assembly             │  (naming_rules.yaml) — no LLM here
└──────────┬───────────┘
            ▼
┌──────────────────────┐
│  DB Commit / Sync     │   SQLite write + NetBox upload
└──────────────────────┘
```

### In-Memory Cascade Replace (Session-Scoped)

When a cell in the DataGrid is edited, `_apply_cascade_to_rows()` in `ui/tabs/naming_tab.py` performs a single-pass scan:

1. For each changed cell in `interface`, `parent`, or `note` fields:
2. Walk all rows in the current session's `hypervisor_parsed_descriptions`.
3. If the old value appears as a substring in the same field, replace it with the new value.
4. If `interface` is corrected and no anomaly pattern remains, clear `is_uncertain = False` and `note = None`.
5. The corrected state lives only in `st.session_state` — it is not written to `data/netbox_hub.db` until the user explicitly commits.

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
Confirm `config/constants.py::APP_VERSION == "4.8.27"`. Ensure no release notes or version bump scripts are pending.

### Step 3 — Safe Local Container Cache Cleanup
```bash
docker image prune -a -f
docker builder prune -a -f
# NEVER run docker volume prune — protects data/netbox_hub.db
```

### Step 4 — Docker Build & Tag Release
```bash
docker build -t ghcr.io/miniz3210/netbox-hub:4.8.27 -t ghcr.io/miniz3210/netbox-hub:latest .
docker push ghcr.io/miniz3210/netbox-hub:4.8.27
docker push ghcr.io/miniz3210/netbox-hub:latest
```

---

## 6. Living Task Checklist

### ✅ Completed Checklist (4.8.27 Baseline)

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
- [x] Cleaned up obsolete standalone scripts and consolidated tests (114+ passing tests).
- [x] **Topology Spatial Reconciliation Axioms** — Left/right parent column separation, ghost interface filtering, switch name fidelity, icon stripping all verified and implemented in `spatial_redactor.py` 4.8.17.
- [x] **DataGrid uncertainty tagging** — `is_uncertain` flag + `note` field propagation through the pipeline, verified in `tests/test_spatial_redactor.py` and naming tab integration.
- [x] **Session-only in-memory cascade replacement** — `_apply_cascade_to_rows()` in `naming_tab.py` cascades cell corrections across all rows in `hypervisor_parsed_descriptions` without persisting to disk.

### ⬜ Pending Roadmap

- [x] Implement unit test coverage for `core/spatial_redactor.py` in `tests/test_spatial_redactor.py` (expand existing test suite to cover 4.8.17 invariants: ghost interface filtering, icon stripping, switch name fidelity, cascade replacement edge cases).
- [x] **4.8.17 Collapsed Header Discard**: Python-side detection and removal of `> Standard Switch:` collapsed headers before extraction.
- [x] **4.8.17 Vertical Y-Slicing**: Switch band bounding via expanded anchor detection; zero vertical leakage across switches.
- [x] **4.8.17 Input Token Budget**: < 400 tokens per extraction call via compact serialization; eradicated prompt truncation.
- [x] **4.8.17 Switch Suffix Fidelity**: `vSwitch01` vs `vSwitch02` distinct; OCR `o`→`0` normalization fixed.
- [x] **4.8.17 Hardware Sidebar Pre-Crop**: Bounded cropping eliminates VMware left navigation tokens before extraction.
- [x] **4.8.17 Natural Line-Grouping Serialization**: Compact line syntax replaces verbose coordinate tags, dropping configuration size by >80%.
- [x] **4.8.17 Deterministic Processing**: Python-side noise filtering and token normalization ensure consistent extraction without stochastic model behavior.
- [x] **4.8.17 Resilient JSON Object Recovery**: Regex-based extraction recovers valid JSON objects from partial streams, eliminating 0-record errors.
- [x] **4.8.17 Expanded State Precedence**: Automatically purge placeholder 'No Uplink' records once real physical adapters are detected.
- [ ] Build Excel IPAM offline reconciliation script against `AW IPs & VLANs_Final (2).xlsx`.
- [ ] Prune stale local git tags prior to 4.8.17 release.
- [ ] Publish GHCR image release `4.8.27` (`ghcr.io/miniz3210/netbox-hub:4.8.27`).
- [ ] Build platform expansion templates for Proxmox VE and Azure Portal.
- [ ] Publish GHCR image release `4.8.27` (`ghcr.io/miniz3210/netbox-hub:4.8.27`).

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

---

## 9. Production Verification Checklist (4.8.27)
================================================================================

- [x] **Deterministic Extraction Only**: Topology extraction is 100% deterministic, zero-LLM, sub-second execution via `core/extractors/`. No Ollama, no VLM, no AI inference required for topology parsing.
- [x] **Ollama Infrastructure Removed**: All Ollama service definitions, environment variables, and model pull scripts eradicated from docker-compose, Dockerfile, and codebase.
- [x] **Legacy OCR Spatial Engine Purged**: `core/ocr_spatial_engine.py` (with `process_hybrid_queue_stream`, token serializers, coordinate injectors) deleted. All callers migrated to `core/extractors/`.
- [x] **Multi-NIC Teaming Support**: Complete extraction of all active physical uplinks without truncation on NIC teams (e.g. vmnic4 + vmnic5, vmnic2 + vmnic6).
- [x] **Zero-Bleed Switch Scoping**: Boundary containment ensuring isolated switches without uplinks (e.g. PRSpainFuenmayorAgeVLab) do not absorb subsequent switch components.
- [x] **Generation Safety Boundaries**: Python-side noise filtering and token normalization prevent any malformed input from reaching extraction logic.
- [x] **Prompt Fallback Chain**: Platform configs gracefully degrade — missing keys use structured defaults, no blank-prompt dispatch possible.
- [x] **Deterministic JSON Parser**: Regex-based recovery from partial text ensures valid records are extracted even from unusual OCR artifacts.
- [x] **Observability & Diagnostics**: OCR raw text preserved in OCR Raw Text Inspector for real-time validation.
- [x] **Framework Deprecation Compliance**: Complete transition from Streamlit `use_container_width` to `width='stretch'`.
- [x] **Per-Image Queue Isolation**: Each screenshot processed independently; no bundling, no state leakage across images.
- [x] **Geometric Midline Hard Slicing (X < 50% vs X >= 50%)**: Complete column isolation preventing IP addresses from bleeding into physical NICs.
- [x] **Universal State Reducer (core/topology_reducer.py)**: Unified single source of truth for deduplication, prefix normalization, and expanded-state precedence.
- [x] **Generator Unpack Crash Guard**: Standardized return types across all extraction paths.
- [x] **Prompt Optimization**: Structured engine configs replace verbose natural-language prompts, reducing configuration size by >90%.
- [x] **Strict No-Hardcode Platform Abstraction**: All naming prefixes and role options dynamically loaded from YAML.
 - [x] **Pre-processing Collapsed Switch Header Filter**: Discard collapsed headers (> Standard Switch:) in Python before extraction.
 - [x] **Vertical Switch Bounding Slices**: Hardware adapters strictly bound to their respective switch section; zero vertical leakage across switches.
 - [x] **Input Token Budget Enforcement**: Structured engine configs keep extraction prompts compact and bounded.
 - [x] **Hardware Sidebar Pre-Crop**: Bounded cropping eliminates VMware left navigation tokens before extraction.
 - [x] **Natural Line-Grouping Serialization**: Compact `[SWITCH]/[LOGICAL]/[PHYSICAL]` syntax replaces verbose coordinate tags, reducing config size by >80%.
 - [x] **Deterministic Processing**: Python-side filtering ensures consistent extraction without stochastic model behavior.
 - [x] **Resilient JSON Object Recovery**: Regex-based extraction recovers valid records from partial or malformed OCR output.
- [x] **Expanded State Precedence**: Automatically purge placeholder 'No Uplink' records once real physical adapters are detected.
- [x] **Accurate Multi-Switch Suffix Fidelity**: Preserved vSwitch01 vs vSwitch02 distinction; fixed letter 'o' substitution in switch suffixes.
- [x] **Expanded State Precedence Enforcement**: Automatically purge No Uplink records when physical adapters are discovered in subsequent frames.
- [x] **4.8.17 Strategy/Adapter Extractor Pattern**: Platform-specific topology extraction isolated into `core/extractors/` — `EsxiTopologyExtractor` for VMware ESXi, `ProxmoxTopologyExtractor` for Proxmox VE. Zero LLM spatial dependency, sub-second execution, 100% configuration-driven rules via `data/naming_rules.yaml`.
- [x] **4.8.17 Spaceless Key Canonicalization**: Zero-hardcode switch name deduplication via spaceless key clustering in `topology_reducer.py` — merges OCR whitespace variants (e.g. "Fuenmayor Age" vs "FuenmayorAge") without hardcoded rules.
- [x] **4.8.17 Horizontal Line Clustering**: Multi-word port groups (e.g. "iSCSI Veeam") preserved via geometric token line-clustering in `esxi.py` — tokens on the same horizontal band are concatenated before filtering.
- [x] **4.8.17 Dynamic Sidebar X-Cutoff**: Switch-header text ("StandardSwitch:...") no longer leaks into Port Group services. Sidebar boundary computed dynamically from leftmost switch header minus 15px — works on both full-screen and cropped screenshots without hardcoding.
- [x] **4.8.17 Deterministic Vertical Y-Slicing**: Switch headers detected via geometric anchor scoring, then bands sliced by Y-coordinate with zero vertical leakage.
- [x] **4.8.17 Deterministic Left/Right Column Split**: 48% midline hard slice separates logical services from physical adapters without LLM assistance.
- [x] **4.8.17 Universal Reducer v2**: State machine precedence, expanded-state purging, and 5-tuple deduplication unified across all inference paths.

---

## 10. 專案狀態檢核：做完咩 (Done) vs 未做咩 (Pending)
======================================================================

9.1 做完咩 (Completed & Verified in 4.8.17)
- [x] Hybrid OCR + Deterministic Extraction: RapidOCR extracts text with bounding boxes; `core/extractors/` parses topology via pure Python — zero LLM, zero Ollama dependency.
- [x] Legacy VLM & Ollama Removal: All VLM model references (`qwen2.5-vl:3b`, `qwen2.5:3b`), Ollama endpoints, and model selector UI removed. Topology extraction is now 100% deterministic.
- [x] Deterministic Spatial Processing: Sidebar filtering, collapsed header discard, and noise token removal all handled in Python before any extraction — no LLM serialization required.
- [x] Pandas Float Type Crash Fix: `st.data_editor` 輸入前強制 `df[col].fillna("").astype(str)` 避免類型衝突。
- [x] UI Container Stacking Bug Fix: 改用 `results_placeholder = st.empty()` 替代累加的 `st.container()`，實現原地 progressive 刷新。
- [x] Engine Selector Simplified: Screenshot Batch Mode now uses fixed "⚡ Instant Local OCR (Deterministic Python Engine)" — no model dropdown or AI inspector.
- [x] **Multi-Platform Prompt Decoupling**: Legacy `ocr_llm_prompt` and `vlm_prompt` fields removed from all platform presets; only structured engine configs retained.
- [x] 混合編輯架構落地 (Hybrid Architecture): 廢除 Chips Pool，改用頂部視覺卡片概覽 + 中間原生 st.data_editor 表格。
- [x] RapidOCR 極致效能壓榨: 關閉 use_angle_cls，鎖定 intra_op_num_threads=2，單圖 OCR 耗時降至 0.3s。
- [x] 本地脫敏金庫非破壞性重構: 永久停用 vault Step 10 與 Step 11，保留原生交換機與端口組拓撲關鍵字。
- [x] 多平台標準字典解耦: 於 naming_rules.yaml 定義 allowed_types, role_options, sentinel_no_adapter，Python 零硬編碼。
- [x] 零 Fallback 確定性拼裝引擎 (Zero-Hardcode Strict Lego Engine): 廢除所有隱式全局 Fallback，模板 100% 由 Standards 平台字典驅動，缺漏模板或變數未賦值直接拋錯 (Fail-Fast)。
- [x] 跨截圖全域去重防線 (Cross-Screenshot Deduplication): 建立 (switch, interface, type, purpose, ip) 五元組簽名機制，徹底根除重疊截圖造成的雙倍網卡與重複服務。
- [x] 交換機名稱空白正規化 (Switch Whitespace Canonicalization): 自動壓縮連續空格與字符黏合，杜絕同名交換機分裂成多張分身卡片。
- [x] 邊界隔絕防吞併機制: 徹底防止無實體卡交換機 (PR Spain Fuenmayor VLab) 吞併緊鄰實體交換機 (vSwitch0)。
- [x] **Unified Parsing Config**: `topology_parsing_presets` contains only structured engine configurations (`layout_geometry`, `noise_filters`, `token_normalizations`, `templates`, `allowed_types`). All LLM instruction/prompts are blank.
- [x] Progressive Container Rendering (無 st.rerun): 使用 `st.empty()` + `results_placeholder.container()` 進行原地渲染刷新，消除頁面重新執行造成的流程中斷。
- [x] **Deterministic Screenshot Pipeline**: UI engine selector replaced with fixed "⚡ Instant Local OCR (Deterministic Python Engine)" — no model dropdown, no AI inspector.
- [x] In-Tab Vision Engine Selector: 視覺引擎選擇器內嵌於 Topology Analysis 標籤頁，與側邊欄 AI Assistant 解耦。
- [x] **Config Resolution Hierarchy**: UI selection > `.env` environment variables > `naming_rules.yaml` defaults, with Ollama endpoint references fully removed.
- [x] Per-Image Queue Isolation: Single-image queue stream keeping input tokens < 350 and execution safe on 4 OCPU ARM.
- [x] Geometric Midline Hard Slicing (X < 50% vs X >= 50%): Complete column isolation preventing IP addresses from bleeding into physical NICs.
- [x] Universal State Reducer (core/topology_reducer.py): Unified single source of truth for deduplication, prefix normalization, and expanded-state precedence.
- [x] Generator Unpack Crash Guard: Standardized return types across all extraction paths.
- [x] Prompt Optimization: Structured engine configs replace verbose natural-language prompts, reducing configuration size by >90%.
- [x] Strict No-Hardcode Platform Abstraction: All naming prefixes and role options dynamically loaded from YAML.
- [x] 4.8.17 Hardware Sidebar Pre-Crop: Bounded cropping eliminates VMware left navigation tokens before extraction.
- [x] 4.8.17 Natural Line-Grouping Serialization: Compact line syntax replaces verbose coordinate tags, dropping config size by >80%.
- [x] 4.8.17 Deterministic Processing: Python-side noise filtering and token normalization ensure consistent extraction.
- [x] 4.8.17 Resilient JSON Recovery: Regex-based extraction recovers valid records from partial or malformed OCR output.
- [x] 4.8.17 Expanded State Precedence: Automatically purge placeholder 'No Uplink' records once real physical adapters are detected.

9.2 未做咩 (Pending & Future Roadmap)
- [ ] 緊鄰 Switch 物理文字切片 (Python-Side Switch Chunking):
  * 在 10vs2 等密集截圖中，PR Spain Fuenmayor VLab 與 vSwitch0 距離過近偶發容器重疊。
  * 規劃於 Python 端先按 Standard Switch: / Distributed Switch: 進行純文字區間切片，再分段交由 AI 處理，徹底隔絕跨 Switch 污染。
- [ ] 長列表首尾邊緣截斷保護 (Edge-Truncation Guard):
  * 在極長端口組截圖中，加強首項 (如 Backplane Network) 與尾項 (如 iSCSI Veeam) 的清點校驗。
- [ ] Excel IPAM 資料庫比對與差異補齊 (IPAM Database Diff).
- [ ] Multi-Platform presets full layout test for Nutanix Prism & Proxmox VE 8.x.
