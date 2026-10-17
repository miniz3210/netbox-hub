# NetBox Hub — Living Architecture SOP & Handover Guide
# Target Baseline: v4.5.10 | Platform: Oracle Cloud Ampere A1 (ARM64)

---

## 1. Executive Architecture Baseline

| Property              | Value                                                        |
|-----------------------|--------------------------------------------------------------|
| Application           | NetBox Universal Library Hub                                 |
| Target Version        | v4.5.10                                                      |
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

### Topology & Spatial Invariants (v4.4.34)

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
│  + is_uncertain/NOTE  │   LLM flags only; no template assembly
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  Local De-tokenization│   Restore original IPs/MACs/domains
│  In-Memory NAT        │   from sanitizer vault map
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
Confirm `config/constants.py::APP_VERSION == "4.5.10"`. Ensure no release notes or version bump scripts are pending.

### Step 3 — Safe Local Container Cache Cleanup
```bash
docker image prune -a -f
docker builder prune -a -f
# NEVER run docker volume prune — protects data/netbox_hub.db
```

### Step 4 — Docker Build & Tag Release
```bash
docker build -t ghcr.io/miniz3210/netbox-hub:v4.4.48 -t ghcr.io/miniz3210/netbox-hub:latest .
docker push ghcr.io/miniz3210/netbox-hub:v4.4.45
docker push ghcr.io/miniz3210/netbox-hub:latest
```

---

## 6. Living Task Checklist

### ✅ Completed Checklist (v4.4.44 Baseline)

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
- [x] **Topology Spatial Reconciliation Axioms** — Left/right parent column separation, ghost interface filtering, switch name fidelity, icon stripping all verified and implemented in `spatial_redactor.py` v4.4.44.
- [x] **DataGrid uncertainty tagging** — `is_uncertain` flag + `note` field propagation through the pipeline, verified in `tests/test_spatial_redactor.py` and naming tab integration.
- [x] **Session-only in-memory cascade replacement** — `_apply_cascade_to_rows()` in `naming_tab.py` cascades cell corrections across all rows in `hypervisor_parsed_descriptions` without persisting to disk.

### ⬜ Pending Roadmap

- [ ] Implement unit test coverage for `core/spatial_redactor.py` in `tests/test_spatial_redactor.py` (expand existing test suite to cover v4.4.34 invariants: ghost interface filtering, icon stripping, switch name fidelity, cascade replacement edge cases).
- [ ] Build Excel IPAM offline reconciliation script against `AW IPs & VLANs_Final (2).xlsx`.
- [ ] Prune stale local git tags prior to v4.4.34 release.
- [ ] Publish GHCR image release `v4.4.34` (`ghcr.io/miniz3210/netbox-hub:v4.4.34`).
- [ ] Build platform expansion templates for Proxmox VE and Azure Portal.

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

## 9. 專案狀態檢核：做完咩 (Done) vs 未做咩 (Pending)
======================================================================

9.1 做完咩 (Completed & Verified in v4.5.10)
- [x] 混合編輯架構落地 (Hybrid Architecture): 廢除 Chips Pool，改用頂部視覺卡片概覽 + 中間原生 st.data_editor 表格。
- [x] RapidOCR 極致效能壓榨: 關閉 use_angle_cls，鎖定 intra_op_num_threads=2，單圖 OCR 耗時降至 2-3s。
- [x] 本地脫敏金庫非破壞性重構: 永久停用 vault Step 10 與 Step 11，保留原生交換機與端口組拓撲關鍵字。
- [x] 多平台標準字典解耦: 於 naming_rules.yaml 定義 allowed_types, role_options, sentinel_no_adapter，Python 零硬編碼。
- [x] 零 Fallback 確定性拼裝引擎 (Zero-Hardcode Strict Lego Engine): 廢除所有隱式全局 Fallback，模板 100% 由 Standards 平台字典驅動，缺漏模板或變數未賦值直接拋錯 (Fail-Fast)。
- [x] 跨截圖全域去重防線 (Cross-Screenshot Deduplication): 建立 (switch, interface, type, purpose, ip) 五元組簽名機制，徹底根除重疊截圖造成的雙倍網卡與重複服務。
- [x] 交換機名稱空白正規化 (Switch Whitespace Canonicalization): 自動壓縮連續空格與字符黏合，杜絕同名交換機分裂成多張分身卡片。
- [x] 邊界隔絕防吞併機制: 徹底防止無實體卡交換機 (PR Spain Fuenmayor VLab) 吞併緊鄰實體交換機 (vSwitch0)。
- [x] On-Demand Local VLM Pipeline: 零留存模型生命周期 (`keep_alive: 0`)，防止 2 vCPU / 11.6 GB 主機 RAM 飢渴。
- [x] 動態解析度 capped: 輸入影像限制至 1280px 最大維度，視覺 Token 減少 65%，CPU 單圖節省 15-20 秒。
- [x] 序列自動隊列: 多圖上傳以串行方式處理，避免低資源伺服器 OOM 崩潰。
- [x] 完整語義 Schema 保留: 維持 `{"switch", "interface", "type", "ip", "purpose", "role"}` 結構，不使用緊湊單字母鍵，保障 3B/7B 模型推理完整性。
- [x] 全域五元組跨圖去重: 解決重疊截圖重複問題 (如 vSwitch02 上重複的 vmnic3 / iSCSI02)。
- [x] 交換機邊界與間距隔離: 正規化交換機名稱防止錯誤合併或卡片分裂。

9.2 未做咩 (Pending & Future Roadmap)
- [ ] 緊鄰 Switch 物理文字切片 (Python-Side Switch Chunking):
  * 在 10vs2 等密集截圖中，PR Spain Fuenmayor VLab 與 vSwitch0 距離過近偶發容器重疊。
  * 規劃於 Python 端先按 Standard Switch: / Distributed Switch: 進行純文字區間切片，再分段交由 AI 處理，徹底隔絕跨 Switch 污染。
- [ ] 長列表首尾邊緣截斷保護 (Edge-Truncation Guard):
  * 在極長端口組截圖中，加強首項 (如 Backplane Network) 與尾項 (如 iSCSI Veeam) 的清點校驗。
- [ ] 空間幾何分欄聚類 (Spatial Bounding-Box Clustering - 終極免 LLM 方案):
  * 直接利用 RapidOCR 輸出的 raw_tokens 幾何座標 [x1, y1, x2, y2]，在 Python 端用 X 軸 (<45% 為 Service，>65% 為 Uplink) 與 Y 軸 (Switch 區間) 直接完成幾何歸屬組裝，將 LLM 角色退居為純文字校對器。
- [ ] Excel IPAM 資料庫比對與差異補齊 (IPAM Database Diff):
  * 撰寫本地獨立比對腳本，直連 SQLite 比對 Excel IPAM 紀錄與 NetBox 現存資料，產出差異報告與正規化 CSV。
- [ ] 多平台視覺卡片渲染預設 (Proxmox VE / Nutanix Prism 適配)。
- [ ] 單元測試套件全面補齊:
    * 更新 tests/ 中對 APP_VERSION 的斷言為 4.5.10。
   * 補齊多平台 Presets 動態載入與 _safe_parse_json_array 截斷修復單元測試。
