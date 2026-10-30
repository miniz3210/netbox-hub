# NetBox Hub 核心架構交接與維護 SOP (True No-Hardcode, Future-Proof & Less is More)

基準版本：`<VERSION>`（以 `config/constants.py::APP_VERSION` 為唯一來源；release 時與 git tag 同步。歷史基準 v4.8.48 為 Orphan Squash 快照）
部署平台：Oracle Cloud Ampere A1 (ARM64 / aarch64, 4 OCPU, 24GB RAM, 100GB Disk, 純 CPU)
Container：netbox-hub，Python 3.11，Streamlit 1.66.1（pandas 3.1.0、openpyxl 3.1.5；以 docker exec netbox-hub pip freeze 為準），TZ=UTC；主機 Python 3.12（只有 python3，沒有 python；測試用 venv ~/nbv/bin/python（/tmp 會被清理，不要放 /tmp）；亦可用 docker run 一次性 container 跑檢查）；Container 只運行 Streamlit（port 8501）；Flask API 服務已移除。
外部 AI 網關：OmniRoute（只服務 AI 助理，拓撲引擎零 LLM 依賴）
AI 助理模型：gemini/gemini-2.5-flash 或 deepseek/deepseek-chat（非推理型 Instruct）

狀態圖例：[x] 已完成並有證據（測試 / UI 截圖 / 副本 DB 實測 / git 輸出）；[~] 已實作但未完整驗證；[ ] 未做

注意：`data/naming_rules.yaml` 副檔名為 yaml，但實際讀寫格式為 JSON（`save_naming_rules()` 使用 `json.dump`）。

======================================================================
1. 核心哲學：NO HARDCODE、FUTURE-PROOF、LESS IS MORE (LEGO BRICKS)
======================================================================

1.1 禁止業務硬編碼
- 通用層（UI、reducer、dispatcher、Standards）嚴禁出現特定廠商、平台名或網路命名常數（vSwitch0、vmnic、vmk、Active Uplink、vmbr、eth 等）。平台專屬 extractor / parser 實作檔（`esxi*.py`、`proxmox*.py`）與工廠預設 preset 為豁免範圍。
- 所有介面前綴、交換機標籤、角色選項、速度選項、`no_uplink_label`、噪音過濾詞，由 `topology_parsing_presets` 的平台 preset 驅動；缺欄位時預設為空，不由 Python 補字。
- 平台平等：VMware ESXi、Proxmox VE、自訂平台權限一致，無「特權預設平台」，無平台名 fallback。
- 文字路徑例外：貼上的 vSphere 頁面文字是精確資料，不經 `token_normalizations` 與 `noise_filters`。
- 純粹管線職責：後端只做「清洗」「萃取」「樣板代入」；數據有就有，無就留空。

1.2 Processing Modules（取代舊的固定 text_parser + extractor 欄位）
- 平台能力由 preset 的 `modules: [module_id, ...]` 宣告，可有 0 個、1 個或多個。
- `PROCESSING_MODULES`（`core/extractors/__init__.py`）是簡單的 Python dict，每粒 module 提供：`id`、`input_kind`（目前 `text` / `image`）、`display_name`、handler、可選 preview 與 UI guidance metadata。不做 plugin 掃描、DAG、依賴系統。
- 執行規則：`modules[]` 順序即優先級；每種 `input_kind` 只執行第一個相容 module（first compatible wins），不合併同 kind 多個結果。
- 同一平台可配置多個相同 `input_kind` 的 module（Standards 只阻止重複 module ID）。
- Unknown module ID：警告並略過，不得令其他有效 module 失效；無有效 module 時回傳 source "none" + "No processing module configured for this platform."；嚴禁 fallback 到 ESXi。
- 新建自訂平台預設 `modules: []`，不自動指派任何 module。
- 舊欄位 `text_parser` / `extractor` 只存在於 `config/naming_rules.py` 的一次性遷移（缺 `modules` 時才遷移），不得作為運行期能力狀態。
- 通用 UI 只依 `input_kind` 渲染輸入插槽：text → 文字貼上區 + preview；image → 截圖上傳 + Ctrl+V 區 + "Instant Local OCR" 標籤；兩者皆無 → 一行 caption 並停用 Analyze。

1.3 通用語義抽象與實作隔離
- 實體抽象為 Physical / LogicalNetwork / Endpoint 三類。
- `core/extractors/` 保持扁平結構；UI 僅能經 dispatcher 調用，嚴禁直接 import 單一平台管道。修改 Proxmox 不得影響 ESXi。
- 先有第二個真實實作才抽象：Proxmox 文字 parser 完成後，再評估 module 是否需要 reorder / history / 多結果合併。

1.4 極簡與確定性
- 拓撲解析零 LLM：確定性 Python 狀態機 + RapidOCR 本地推論。
- 嚴格清除不可達死代碼、無用註解、廢棄文檔。

======================================================================
2. 系統架構邊界與安全隔離
======================================================================

2.1 本地安全閉環
- 拓撲識別 100% 本地、確定性（UI 標註 "Instant Local OCR (Deterministic Python Engine)"），零數據出網。
- Sanitizer Vault（`data/sanitizer_vault.db`，已 .gitignore）僅在明確切換至外部雲端模型時啟用。
- 版本控制邊界：`data/naming_rules.yaml`、`data/*.ps1` 納入 git；`data/netbox_hub.db`、`data/*.csv`、`*.db-shm`、`*.db-wal`、`*.db-journal` 排除。
- 測試隔離：`tests/conftest.py` 使用臨時 DB / vault 路徑；涉及 SQLite 寫入的測試一律在 `/tmp` 副本執行。

2.2 模組邊界
- `core/ocr_engine.py`：RapidOCR 本地文字框提取。
- `core/extractors/base.py`：BaseTopologyExtractor 與座標 helper。
- `core/extractors/__init__.py`：`PROCESSING_MODULES`、`run_topology_pipeline(preset, text, get_ocr_records)`、`preview_topology_text()`、`validate_processing_modules()`；內部保留 `TEXT_PARSERS` / `EXTRACTORS` 作 implementation adapter。
- `core/extractors/esxi.py` / `esxi_text.py` / `esxi_pipeline.py`：ESXi OCR extractor、vSphere 文字 parser、`analyze_esxi()`（文字優先 → OCR 補缺口 → 純 OCR）。
- `core/extractors/proxmox.py`：Proxmox OCR extractor。
- `core/topology_reducer.py`：`reduce_topology_pipeline()`。
- `core/backup_endpoint_merge.py`：`parse_ts` / `format_ts` / `plan_endpoint_updates`（純函數）。
- `core/backup_manager.py`：`save_netbox_backup`、`clear_backup_records(preserve_csv=False)`、`init_backup_tables`、`_derive_full_catalogue`、`_restore_csv_overrides`。
- `core/shared_backup_state.py`：session 層登記冊（`load_backup`、`add_csv_override`、`clear()`、`clear_json_only()`、`clear_csv_only()`）。
- `core/ipam_excel_diff.py`：`_build_backup_index()`（優先讀合併後 session registry，cache key 為各 endpoint 的 `(name, len(data), timestamp)`）。
- `ui/components.py`：備份上傳 / 清除 / Authority status / 上傳進度。 Ingest 區結構：JSON Backup（Get a backup 預設收埋 → uploader → 單行 Authority status → Backup contents 單一 dataframe，含 Source 欄與 CSV 淡橙底、Hide empty、Filter）→ CSV / Excel Overrides (optional)（分類確認卡、Manage CSV、Clear CSV）→ Clear Everything（底部 secondary 按鈕）。
- `ui/tabs/naming_tab.py`、`standards_tab.py`、`azure_tab.py`、`batch_tab.py`。
- 已刪除：`docs/`、`core/spatial_redactor.py` 及其測試、`scripts/sync-agent.ps1`、SQL Query Tool、chip/deck 舊系統。 Flask API 服務（entrypoint 啟動、Flask / flask-cors / waitress 套件、port 5000）、spatial anchors editor 與 spatial_anchors_and_redaction 設定（無任何讀取者）。

======================================================================
3. 資料處理管線
======================================================================

3.0 拓撲調度管線（Processing Modules）

    [UI: naming_tab.py] 讀取所選平台 preset 的 modules[]
           │  依 input_kind 渲染輸入插槽（無平台名判斷）
           ▼
    run_topology_pipeline(preset, text, get_ocr_records)
           │  第一個相容 text module（例如 vsphere_text → analyze_esxi：文字優先、缺口才跑 OCR）
           │  第一個相容 image module（例如 esxi_ocr / proxmox_ocr）
           │  無有效 module → source "none" + 明確警告
           ▼
    reduce_topology_pipeline → Step 2（Visual Cards + Data Grid）→ Step 3（NetBox 輸出）

- `get_ocr_records` 是零參數 callable（lazy）或 None，不得傳 list。
- 貼文字 preview 經 module 的 preview 函數，只跑文字解析、不跑 OCR。

3.1 備份權威模型（Full 定義 endpoint 宇宙）
規則：
1. 已驗證的 Full 備份定義權威 endpoint catalogue（`full_endpoint_catalogue`：path / full_exported_at / expected_count / verified）。成員資格直接取自 Full payload 的 endpoint metadata，不得由 `_ENDPOINT_TO_OBJECT_TYPE` 或 `_skip_endpoint` 決定；零筆但已驗證的 endpoint 仍屬 catalogue。
2. 較新（或同時間）的 Full 整份取代 catalogue（replace，非 merge）；`backup_metadata.latest_full_backup_completed` 記錄最後接受的 Full 時間。
3. 較舊的 Full 整份拒絕（`stale_full`），在任何 DELETE / 寫入 / CSV 清除之前返回，零 mutation。
4. Minimal 只能更新 catalogue 內已存在的 endpoint：unknown endpoint → `skip_unknown_catalogue_endpoint`；較舊 → `skip_stale`（真正 skip）；`catalogue_paths=None` 為遷移相容（舊行為）；空 set 代表權威空 catalogue（全部拒絕）。保留 `skip_unverified`（verified == False 或 downloaded_count < expected_count）與 `skip_suspicious_empty`；`expected_count == 0` 視為權威空狀態並取代。
5. CSV 只能 override catalogue 內已存在的 endpoint（`add_csv_override` 閘），否則 `skip_unknown_catalogue_endpoint`；CSV 不得新增 catalogue 項目。
6. 已接受的 Full 在最終 metadata commit 成功後才呼叫 `clear_csv_only()`（保守策略：新 Full 清除全部 CSV overlay）；stale Full 不動 CSV。
7. `backup_endpoints` 表示「目前有效的 endpoint 資料」（Minimal 會覆寫其 kind / exported_at / data），不兼任 catalogue。
8. `record_count`：Full 與 Minimal 使用同一定義，僅計 `_ENDPOINT_TO_OBJECT_TYPE` 內 9 類庫存 endpoint 的合併筆數（`SUM(record_count) ... WHERE path IN (mapped paths)`，此 filter 不得移除）；`object_counts` 與 sites / ipam / devices / vms / interfaces / cables 於 Minimal 時由合併後 `backup_endpoints` 推算（為空時才退回 `effective_counts`）。全表 SUM 會含 `core/object-changes` 等日誌而虛高，不採用。
- 交易：單一連線 `BEGIN IMMEDIATE`；Full 路徑的 catalogue 與資料同交易 rollback。壓縮 JSON blob 支援回滾與 session 恢復。

3.2 清除邊界
| 操作 | JSON / Minimal 資料與 backup_endpoints | catalogue + latest Full 時間 | CSV（session + dynamic_*） |
|---|---|---|---|
| Clear All JSON（`clear_backup_records(preserve_csv=True)` + `clear_json_only()`） | 清除 | 保留 | 保留（refresh / 重啟後經 `_restore_csv_overrides()` 恢復） |
| Clear CSV（`clear_csv_only()`） | 保留 | 保留 | 清除 |
| Clear Everything（`clear_backup_records(preserve_csv=False)` + `SharedBackupState.clear()`） | 清除 | 清除 | 清除 |
- `clear_backup_records` 預設行為（`preserve_csv=False`）不變；dynamic 表以 `LIKE 'dynamic\_%' ESCAPE '\'` 探測，識別字加引號。
- Clear 與上傳成功後遞增 `st.session_state["backup_stats_version"]`，作為 `_cached_fetch_toolbar_stats(backup_stats_version)` 的 cache key；不使用 `st.cache_data.clear()`，不得由 `ui/components.py` import tab。
- Authority status（JSON Baseline / Full Catalogue N endpoints / Latest Full / CSV Overrides N）以單行 status 唯讀顯示於 JSON 備份區（Latest Full 格式 YYYY-MM-DD HH:MM）。
- Clear Everything 於「有 JSON、或有 CSV override、或 catalogue 非空」時皆可用（不再受 JSON loaded 限制）；Clear All JSON 僅於 JSON loaded 時顯示；Clear CSV 位於 CSV 區。
- Backup contents 的 JSON / CSV 數量以同一規則判斷：source_type == csv 或 source 副檔名為 .csv / .xlsx；標題寫成 JSON n | CSV n，不得與 catalogue 數字混為一談。
- 清除 / 還原後再上傳舊 Full：Clear All JSON 後會被判 stale（時間戳保留）；Clear Everything 後可成為新 baseline。

3.3 上傳進度
- `save_netbox_backup(..., progress_callback=...)` 的訊息對應固定階段清單（階段完成度，非 ETA）；stale / 錯誤時不顯示 100%；完成後顯示 `timings` 的總耗時。瀏覽器 → server 的上傳階段由 Streamlit uploader 自己的 spinner 負責，不受此進度條控制。

======================================================================
4. 關鍵拓撲解析不變律
======================================================================

4.1 欄位邊界硬隔離
- 左欄邏輯服務：Interface 欄位僅允許 vmk<N> 或空字串；不得放 IP、冒號、速度。IPv4 專屬於 VMkernel；實體網卡 IP 恆為空。
- 右欄實體架構：Interface 為乾淨 vmnic<N>（Proxmox 為 eno/ens/eth<N>）；速度剝離入 speed 欄；Uplink 容器標籤不得作為 Port Group；`uplinkN (0 NIC Adapters)` 空槽不得成為 NIC。

4.2 多網卡零遺漏
- 交換機下每張實體網卡各出一筆 Uplink，不得抓第一張即結束。OCR 容錯：vmnici1 / vmnlc2 仍取尾端數字；名稱不可讀但速度可讀輸出 "vmnic?"，不得靜默丟棄。

4.3 狀態覆蓋與孤立交換機
- 展開狀態絕對優先於折疊狀態；band 內無 token 視為收起，不輸出 "No Uplink"。
- 明確 "No physical network adapters" 才輸出 no-uplink 標籤（來自 preset `no_uplink_label`）。
- card 標題與 "VLAN ID" 列左對齊（距離 <= 90px）。

======================================================================
5. Standards 規範與平台生命週期
======================================================================

5.1 平台 CRUD
- `topology_parsing_presets` 是現行平台集合；`_ensure_unified_parsing_defaults()` 與 legacy migration 僅在該 dict 缺失或為空時注入工廠預設；運行中不得復活被刪平台。
- 最後平台保護：只剩一個平台時刪除按鈕 disabled（help："At least one platform must remain."）。
- 平台排序 `hypervisor_platform_order` 是使用者偏好：`_normalize_rules()` 原樣保存字串 list；UI 以現有平台過濾，其餘依 dict 順序附加；Naming 與 Standards 共用同一排序。
- 已選平台被刪除時自動回退到清單第一項（先 pop stale `naming_target_platform`）；`DEFAULT_PLATFORM` 常數已移除。

5.2 平台 preset 結構（節錄）

    topology_parsing_presets:
      VMware ESXi:
        platform: "VMware ESXi"
        modules: ["vsphere_text", "esxi_ocr"]
        aliases: { parent: [...], interface: [...], speed: [...] }
        noise_filters: [...]
        token_normalizations: [ {pattern: '...', replace: '...'} ]
        role_options / default_connected_role / default_disconnected_role / sentinel_no_adapter
        no_uplink_label / speed_options / interface_options / service_interface_options
      Proxmox VE:
        modules: ["proxmox_ocr"]
      <自訂平台>:
        modules: []

- 失效的 geometry 滑桿（無 extractor 讀取）已自 UI 移除；`layout_geometry` 欄位暫留 YAML，待真正接線或刪除。

5.3 Standards：Processing Modules 區塊
- 以一行一個 module（名稱 + input_kind + ✕）顯示，每行最多 2 個；"+ Add Module" 列出所有未指派的 module；順序即優先級；移除 / 新增即時保存。
- 刪除再新增同名平台視為全新空 preset（不自動復活）；需要舊設定請用 git 還原 `data/naming_rules.yaml`。

======================================================================
6. UI 規範與狀態管理
======================================================================

6.1 Naming 標準 3 步工作流
- 1️⃣ Platform & Data Ingestion：平台下拉 → 依 modules 渲染插槽（text 區 / 截圖上傳 / Ctrl+V 截圖貼上，標籤 "Paste screenshot (Ctrl+V)"）→ Analyze 與 Clear All。無 module 時顯示 caption 並停用 Analyze。Ctrl+V 區只接收圖片（文字貼上經 JS 傳輸會被單行 input 破壞，已移除）。
- 2️⃣ Topology Review & Visual Editing：Visual Cards + `st.data_editor` Data Grid。
- 3️⃣ Live NetBox Output：`st.code` + Download（.txt）。

6.2 Azure Tab：模組頂部 import os / tempfile；`azure_uploader_key_ver` 版本鍵；全檔以 `.pop("X", None)` 取代 `del st.session_state.X`。
6.3 狀態版本：`editor_ver`、`topo_uploader_key_ver`、`paste_text_ver`、`backup_stats_version`；`_build_backup_index` 的 cache key 隨 registry 內容變化。
6.4 已知 UI 差異：已移除 "Copy All Descriptions" 按鈕的需求（伺服器端按鈕無法寫入剪貼簿）仍需確認是否已落地；以 `st.code` 內建複製圖示與 Download 為準。

6.5 UI 實作約定（由 P6 / P9 事故歸納）
- widget 以 key= 建立後，同一次 run 不得再賦值 st.session_state[該 key]（StreamlitWidgetAlreadyInstantiatedError）；預設值在 widget 建立「前」用 `if key not in st.session_state` 初始化，或在 on_change / on_click callback 內改。
- st.dataframe / st.data_editor / st.button 的 width 只可用 "stretch"、"content" 或正整數，不得為 None。
- st.success / st.warning / st.toast 若已有 icon=，訊息文字不再重複放 emoji。
- 操作回饋用 st.toast；不要在 st.rerun() 之前用 st.success（訊息會即時消失）。 
- 標題層級：Step 標題 ####，子標題 #####；同級區塊不得出現較大的子標題。
- on_change / on_click callback 內不得呼叫 st.rerun()。
- Assign session_state for several widgets in ONE pre-widget sync block placed before any of them is rendered, regardless of display order.
- Pattern Variables 儲存為 merge：只改動該 scope 的變數，不得整份取代其他 scope。
- Auto-Correction Reset 以 category 為範圍（只重置該 category，其餘保留已儲存規則）。

======================================================================
7. NetBox 命名輸出規範
======================================================================

- Switch Teaming 標頭：`<v_switch> (<teaming>)`；無網卡：`vSwitch1 (No Uplink)`。
- 實體 Uplink：`<interface> - <v_switch> <purpose> <uplink_role>`。
- 邏輯連接埠群組：`<purpose> (<v_switch>)`。
- 服務類去重鍵為 `(_svc_type, _svc_iface, _svc_name)`。
- Token：`<interface>`、`<parent>`、`<purpose>`、`<speed>`、`<ip>`、`<teaming>`、`<uplink_role>`。

======================================================================
8. 交付部署與發布 SOP
======================================================================

8.1 版本號：release 前確認 `config/constants.py::APP_VERSION` 與 git tag 一致；SOP 與 `data/naming_rules.yaml` 不寫死版本。不得覆寫已存在的 tag。（目前已知 APP_VERSION 落後 tag，下次 release 一併校正，不補做中間版本。）
8.2 Docker 建置與推送（Oracle Cloud A1 ARM64）

    docker build -t ghcr.io/miniz3210/netbox-hub:<VERSION> -t ghcr.io/miniz3210/netbox-hub:latest . && \
    docker push ghcr.io/miniz3210/netbox-hub:<VERSION> && \
    docker push ghcr.io/miniz3210/netbox-hub:latest

- 部署後必須驗證 running container 真的是新 code：`docker exec netbox-hub grep -n "<新字串>" /app/<檔案>`；不得 `docker cp` 或掛載原始碼做快測。
- 回滾：compose 改回上一個版本 tag。
- 磁碟清理：`docker system prune -f`、`docker builder prune -a -f`、`df -h /`；`docker image prune -a -f` 會清掉本機舊 tag，僅在確認可由 GHCR 回滾時使用。
- 唯讀載入檢查（只限 import / py_compile，不得當功能測試、不得取代 docker exec 驗證）：docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v <repo>:/work:ro -w /work --entrypoint sh <image> -c "python -c 'import ui.components, ui.tabs.ipam_tab, ui.tabs.naming_tab, ui.tabs.standards_tab'"。完整 pytest 因涉及 SQLite 寫入，須在 /tmp 副本執行。
- py_compile 與 import 通過 ≠ 可 release：P6 / P9（上傳 JSON 後才觸發的 runtime 錯誤）與 b316275（整檔去縮排）都通過編譯類檢查或在其後才出現；release 前必須逐 Tab 瀏覽器煙霧測試並查 docker logs。已 push 的 tag 不覆寫，修正用新版本號。
- 8.3 原 Orphan Squash / force-push 程序為一次性歷史操作，已移除，禁止重跑。

======================================================================
9. 專案狀態：做完咩 vs 未做咩
======================================================================

9.1 做完咩（[x] 有證據）
平台 / 拓撲
- [x] 本地確定性拓撲識別，零 LLM 依賴；UI 路由與引擎排他控制。
- [x] ESXi 文字優先 + OCR 補缺口 + 純 OCR；VLAN 雙路徑提取；OCR 重複 vlan 鍵修復。
- [x] dispatcher 由 `run_topology_pipeline(preset, ...)` 接管，並以 Processing Modules 取代固定 text_parser + extractor（空平台、Proxmox 截圖畫面已驗證；無 ESXi fallback）。
- [x] modules[] 優先級規則、同 kind 多 module、unknown module 不阻斷、新平台預設空 modules。
- [x] Naming 依 module 的 input_kind 閘控插槽；無 module 時 caption + Analyze 停用。
- [x] 貼文字 preview（只跑文字解析）與缺口提示合併。
- [x] Pattern Variables：內建變數（PATTERN_VARIABLES）現在可刪除（附帶依賴警告），並在 YAML 的 deleted_pattern_variables 中記錄，可經由 Deleted built-ins 展開器還原。
- [x] 平台 CRUD：Auto-heal 僅在 preset 全空時執行；最後平台 disabled 保護；動態回退；`DEFAULT_PLATFORM` 移除。
- [x] 平台排序持久化（`hypervisor_platform_order` 經 `_normalize_rules()` 保存；refresh 與 container 重啟後驗證）。
- [x] Generic UI 平台專屬字眼 / 選項外部化至 module metadata 與 preset；失效 geometry 控制移除。
- [x] naming_tab 死代碼清理（chip/deck、未使用 helper 約 346 行）；SQL Query Tool、spatial_redactor 移除；DataGrid 型別清洗；Azure Tab 修復。
備份 / IPAM
- [x] 備份逐 endpoint 合併（Full / Minimal / CSV）、skip_unverified / skip_suspicious_empty。
- [x] Full authoritative catalogue、`latest_full_backup_completed`、整份 stale Full 拒絕（UI 已實際見到 stale 拒絕訊息）。
- [x] Minimal catalogue 閘與真正 skip_stale；CSV catalogue 閘；accepted Full 於 commit 後清 CSV。
- [x] Clear All JSON 保留 catalogue / Full 時間戳 / CSV；Clear CSV；Clear Everything 已實作；Authority status UI 顯示 "Cleared / 145 endpoints / Latest Full / CSV 0" 已驗證。
- [x] Minimal `record_count` 與 `object_counts` 以合併後 endpoint 推算，Full / Minimal 定義一致（副本驗證 inventory sum = record_count = object_counts sum = 3023）。
- [x] `clear_backup_records` 涵蓋 interfaces / cables / backup_endpoints / dynamic_*（`preserve_csv` 參數）。
- [x] toolbar 統計以 `backup_stats_version` 即時刷新；IPAM `_build_backup_index` 讀合併 registry 且 cache key 隨內容變化。
- [x] `batch_tab.process_row` 單行容錯（原已有 try/except），失敗彙總 warning 與 logging。
- [x] 測試：全套通過（已無遺留的 spatial_redactor 測試）。

9.2 已實作但待驗證（[~]）
- [~] 新 Full 上傳進度條：僅在 stale Full 情境測過（正確不出現）；需用 backup_completed >= 保留的 Latest Full 的檔案確認階段進度與 "Processing time"。
- [~] Clear Everything 手動 smoke：Catalogue 0 / Latest Full None / CSV 0，refresh 後不復活，之後舊 Full 可成為新 baseline。
- [~] Processing Modules 在新 build 容器內的完整 smoke：VMware text + 截圖補缺口、Proxmox OCR、Remove / Add module 後 refresh 與重啟仍保存。
- [~] "JSON Baseline" 以 `backup_metadata.record_count > 0` 判斷，合法的全零 Full 會顯示 Cleared；邊緣案例，改用 metadata / blob 存在判斷較準。
- [~] Copy All Descriptions 按鈕是否已移除需確認（見 6.4）。
- [~] Standards 的 `<small>` 字面顯示 bug 已改用 `:gray[kind]`，需確認新 build 已生效。

9.3 未做咩（Pending Backlog）
P1
- [ ] Proxmox `/etc/network/interfaces` 文字 parser：新增 `proxmox_interfaces`（input_kind text）並登記到 `PROCESSING_MODULES`，由 Standards 指派給 Proxmox；需涵蓋 bridge-ports 多成員、bond-slaves 零遺漏、address、無推測值。這是 Processing Modules 的第一個跨平台實證。
P2
- [ ] `_normalize_rules()` 內 `DEFAULT_HYPERVISOR_PRESETS` 仍會補回缺失的 `hypervisor_presets` 平台；先 trace `hypervisor_presets` 是否仍屬 platform lifecycle，再決定是否限縮。
- [ ] Platform module history（刪除平台後保留 `platform name → module IDs`，同名重建時提示 Restore / Start Empty，不得靜默還原）。
- [ ] Module reorder UI（順序即優先級，目前只能移除再新增）。
- [ ] 版本 drift：`APP_VERSION` 與 git tag 對齊（下次 release 處理）。
- [ ] 新 Full 的 CSV 策略目前為保守全清；若要保留「比 Full 更新的 CSV」需另行設計 timestamp-based selective overlay。
P3
- [ ] Naming 內殘餘 "No Uplink" / "vmnic" 字面值（資料處理段落）改讀 preset / token。
- [ ] `layout_geometry` 欄位：真正接線 extractor 或從 preset 刪除。
- [ ] `noise_filters` 內 13 個通用單字品質審查（防誤殺同名 Port Group）。
- [ ] 匯出腳本跳過 3 個非資料表 API 端點並於 UI 標明失敗名稱。
- [ ] OCR Inspector 補信心度（conf）與座標框。
- [ ] Excel IPAM reconciliation script。
- [ ] Full payload 內重複 endpoint path 目前 first-wins，建議附帶 warning。
- [ ] pyflakes 既有警告（未使用變數 / 無佔位符 f-string）一次性清理；`clear_backup_records` 內多餘 f-string。
- [ ] 同類 input_kind 多 module 的結果合併 / `execution_mode`（目前刻意不做）。

======================================================================
10. AI Coding Agent 作業規則（Kilo / 免費模型防斷線）
======================================================================

- 單一任務原子性：一個 session 一個目標，完成即 commit；範圍以 diff 內容判斷，不以「恰好 N 個檔案」硬卡。
- Context：每 session 最多 2 個 production 檔、最多 4 個新測試（能重用既有 fixture 就重用）；禁止顯示整檔 diff，只用 `git diff --stat` 與 `git diff -U2 -- <file>`；約 60K 開始收尾，超過 80K 不發送大型編輯。
- tool-call 格式錯誤（例如 grep 缺 pattern）或模型重複迷路 → 立即結束 session，確認 `git status` 後以乾淨 session 重啟；grep 失敗一次即改用 view 行範圍，不重試。
- 標準 Task Prompt 開頭：
  ONE TASK ONLY. Do not delete, move or rename any file. Do not run the full test suite. Do not write tests other than the ones asked. Do not plan other tasks. Output: changed lines, max 3 lines.
- 任何涉及 `SUM(record_count)` 的 prompt 必須附加：`Constraint: SUM(record_count) in save_netbox_backup must keep WHERE path IN (keys of _ENDPOINT_TO_OBJECT_TYPE). Never remove that filter.`
- 使用 module 變數控制 UI 前，必須先確認變數定義位置早於首次使用（曾因此造成 UnboundLocalError）。
- 嚴禁物理破壞：不得批次刪除 / 移動 `data/`（保護 `netbox-export-*.ps1` 與 `naming_rules.yaml`）。
- 副本測試保護：SQLite 寫入測試一律在 `/tmp` 副本；備份相關測試以 `/tmp/partc_test` 為準，`run_upload.py` 內含 assert 確認路徑在 `/tmp/partc_test`。
- 貼多行 shell 指令時，heredoc / 多行 python -c 易被壓成一行，請用單行或分條執行；`<DB>` 類佔位符必須替換成實際路徑。

======================================================================
11. 發布後手動煙霧測試（約 10 分鐘）
======================================================================

0. 先確認 container 為新版：`docker exec netbox-hub grep -n "<新字串>" /app/<檔案>`。
1. 逐個 Tab 切換（Naming、Standards、IPAM、Azure、Device、Rack、Batch、Module、Image）：無 ImportError / NameError / UnboundLocalError；`docker logs --since 5m netbox-hub 2>&1 | grep -i "crashed\|Traceback"` 應無輸出。
2. Azure：上傳 VM CSV → 預覽 → Clear Azure CSV → 畫面與上傳框清空。
3. Standards 平台與 module：
   - 刪 Microsoft Azure → rerun 不復活；新增自訂平台（modules 為空）可儲存 / 刪除；只剩一個平台時刪除鈕 disabled。
   - VMware = vsphere_text + esxi_ocr；Proxmox = proxmox_ocr；Remove / Add module 後 refresh 與 container 重啟仍保存。
   - 平台排序 ⬆️⬇️ 後 YAML 有 `hypervisor_platform_order`，refresh / 重啟仍保持。
4. Naming：
   - VMware：貼 vSphere 文字 → 即時 preview → Analyze → Step 2；加缺口截圖得 `text+ocr`。
   - Proxmox：只有截圖區，OCR 正常；移除 image module 後截圖區消失；全部移除後出現 caption 且 Analyze 停用。
   - 貼錯文字會出明確警告，不污染 Step 2。
   - Step 3 語法正常、Download 可用。
5. 備份（在 /tmp 副本或測試環境）：
   - 上傳較新 Full → 進度條逐階段前進 → 完成顯示 Processing time；Authority status 顯示 Loaded / N endpoints / Latest Full。
   - 上傳較舊 Full → `stale_full` 拒絕，catalogue / Latest Full / CSV 不變。
   - 上傳 Minimal（只含少數 endpoint）→ Full 獨有 endpoint 保留；分項仍為 9 項；record_count 與 inventory sum 一致、無跳水。
   - Full → CSV → Clear All JSON → CSV 與 catalogue 仍在，refresh 後 CSV 仍在 → Clear CSV → CSV 消失 → Clear Everything → Catalogue 0 / Latest Full None，refresh 後不復活。
   - IPAM：Minimal 後以含 Full-only IP 的 Excel 比對有結果；改動 Minimal 後重新比對結果隨之變化。
