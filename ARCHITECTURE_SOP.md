# NetBox Hub 核心架構交接與維護 SOP (True No-Hardcode, Future-Proof & Less is More)

當前基準版本 (Baseline)：以 `config/constants.py::APP_VERSION` 為準，並與 git tag 對齊（不再執行 Orphan Squash；舊基準 v4.8.48 為歷史記錄）
部署平台：Oracle Cloud Ampere A1 (ARM64 / aarch64, 4 OCPU, 24GB RAM, 100GB Disk, 純 CPU 運算)
Container：netbox-hub，Python 3.11，Streamlit 1.66.1（requirements.txt 刻意不 pin 版本，以跟進安全修補），pandas 3.1.0，openpyxl 3.1.5，TZ=UTC；主機 Python 3.12（只有 python3，沒有 python，亦沒有 streamlit / pyflakes）
版本查核：每次 release 後記錄 `docker exec netbox-hub pip freeze | grep -i -E "^(streamlit|pandas|openpyxl)=="`；UI 出現異常時先查 Streamlit 版本有否變動，再懷疑 code。
Container 只運行 Streamlit (port 8501)；Flask API 服務已移除（Flask / flask-cors / waitress / port 5000）
外部 AI 網關：OmniRoute（`<網關位址：原檔此處文字遺失，請補回>`），僅供 AI 助理使用（拓撲引擎已徹底移除任何 LLM 依賴）
AI 助理專屬模型：gemini/gemini-2.5-flash 或 deepseek/deepseek-chat（非推理型 Instruct 模型）
狀態圖例：[x] 已完成並有證據（測試 / 副本 DB 實測 / git 輸出 / 瀏覽器驗收）；[~] 已完成但未在 UI 驗證 / 進行中；[ ] 未做

新 session 使用方式：先貼第 10.2 節「新 Session 開頭區塊」，再附本檔；本檔第 9 節為唯一的狀態與待辦來源。

---

## 1. 核心哲學：NO HARDCODE、FUTURE-PROOF、LESS IS MORE (LEGO BRICKS)

### 1.1 絕對禁止業務硬編碼 (Strict No Hardcode)

- Python 代碼零特化：後端與 UI 邏輯嚴禁出現任何特定廠商、虛擬化平台或網路命名常數（嚴禁硬編碼 vSwitch0、vmnic、vmk、Active Uplink、vmbr、eth）。所有介面前綴、交換機標籤、角色選項與噪音過濾詞，必須 100% 由 `data/naming_rules.yaml`（Standards Tab）動態配置驅動。
- 平台平等與動態能力旗標 (Capabilities & LEGO CRUD)：
  - 所有虛擬化/雲端平台（VMware ESXi、Proxmox VE、Microsoft Azure、Nutanix 等）在系統內權限一律平等，無任何「特權預設平台」。
  - 任何平台均具備完整新增與刪除生命週期，嚴禁透過黑名單或隱性 Auto-heal 強制加回被用戶刪除之平台。
  - 平台輸入特徵外部化（Capabilities Flag）：平台自身宣告是否支援「免 OCR 文字貼上」或「專屬設定檔導入」；前端依能力動態渲染對應插槽，絕不將 vSphere 等專有字眼寫死於通版 UI。
- 業務規則外部化 (Lego Bricks)：
  - 平台雜訊黑名單 (noise_filters)：由 YAML 定義，引擎依清單動態過濾，不寫死任何 UI 目錄文字。
  - 確定性正則修復 (token_normalizations)：常見 OCR 拼寫失真（如字母 O/0 混淆）由 YAML 規則庫在微秒內完成替換。
  - 命名樣板 (Pattern Templates)：利用動態 Token（`<interface>`、`<parent>`、`<purpose>`、`<speed>`、`<ip>`、`<teaming>`、`<uplink_role>`）達成資料結構與渲染呈現徹底解耦。
- 純粹管線職責（純萃取與純代入）：
  - 後端僅負責：「清洗空間文字」、「萃取實體鍵值」與「樣板格式代入」。
  - 數據「有就有，無就留空」，嚴禁 Python 代碼自行做主補字或硬塞預設容器。
- 文字路徑例外：貼上的 vSphere 頁面文字是精確資料，不經 token_normalizations 與 noise_filters（否則真實 port group 名如 "Services"、"Firewall" 會被誤刪）。
- 工廠預設豁免：工廠預設 preset 的 token_normalizations 內含站點名（PR Spain Fuenmayor / Age VLab），因屬工廠預設而豁免，列為 P3 觀察項，不屬違規；但 code 內的站點字眼（如 Vault 的 `_SWITCH_NAME_RE`）不豁免。
- 預設值只播種一次 (Seed Once)：
  - 工廠預設（PATTERN_VARIABLES、DEFAULT_HYPERVISOR_PRESETS、DEFAULT_AZURE_MAPPINGS）只在 YAML 沒有對應 key（None / 不存在）時寫入，或由使用者按 Reset to Defaults 觸發；載入與儲存時一律不得合併回 YAML。空 dict 視為「使用者已全部刪除」，不得重新播種。
  - YAML 是唯一真相：Standards 刪除即真刪；需要還原時使用 Change History。
  - 程式碼不得假設某個 pattern variable 或平台一定存在，亦不得寫死變數名稱；需要時由 rules 動態取得（例如 `get_domain_suffix_variables`）。
  - 副作用：日後在 code 新增的內建預設不會自動出現在既有安裝，須在 Standards 手動新增或按 Reset to Defaults。
- 整合匯出輸出原始值：Azure KQL 等外部匯出只輸出原始值（Site = Azure region、Platform = OS type、Role 無值則留空），所有對應規則放在 Standards（azure_mappings），KQL 內不得出現 Site / Platform 對應或預設佔位字。

### 1.2 真正平台無關與未來適應力 (Platform-Agnostic & Future-Proof)

- 通用語義抽象模型 (Universal Semantic Abstraction)：
  - 同一套資料管線無縫支援 VMware ESXi、Proxmox VE、Cisco NX-OS、Azure VNet、OCI VCN 等架構。
  - 實體統一抽象為三大通用類型：
    1. Physical：實體介面、實體 Uplink、實體網卡。
    2. LogicalNetwork：虛擬交換機、Bridge、Port Group、Subnet、VLAN、VNet。
    3. Endpoint：管理介面、Kernel 接口、主機 IP、VIP。
- 實作隔離原則（Flat Strategy Pattern）：
  - 不隨意深層巢狀搬移檔案（保持 `core/extractors/` 扁平結構，維護靜態導入守門與現存測試相容性）。
  - 調度層與實現層物理隔離：UI 僅能經由總機分發入口（Dispatcher）調用，嚴禁前端直接 import 任何單一平台管道。修改 Proxmox 代碼絕對不應、亦不能影響 ESXi 管道。

### 1.3 極簡、零負擔與確定性算力 (Less is More & Deterministic Engine)

- 拓撲解析零 LLM：全程使用確定性 Python 狀態機 + RapidOCR 本地推論，徹底摒棄消耗 ARM 算力的本地 3B 模型。
- 算力預算自律 (Token & Latency Discipline)：Ollama 呼叫規則（num_predict: 1024、repeat_penalty: 1.1）僅限於 AI 助理選用本機模型時使用。
- 程式碼極致瘦身：嚴格清除不可達死代碼、無用註解與廢棄文檔，保持鏡像體積最小化。
- 同一件事只有一套寫法：同一類 UI 操作（排序、刪除、新增、儲存、還原）全站使用同一套 pattern（見 6.4），不允許出現兩套行為。
- 先有重複才抽象：已有多個真實重複才抽 helper（例如 `_commit_rules()`，見 9.3）；不為假想需求預先抽象。

---

## 2. 系統架構邊界與安全隔離 (Local Isolation & Zero Leakage)

### 2.1 本地安全閉環與引擎排他性

- 本地封閉運行 (Local Air-Gapped Mode)：
  - 拓撲識別 100% 本地、確定性運行（UI 標註 "Instant Local OCR (Deterministic Python Engine)"），零數據出網。
  - 介面 Dropdown（Vision / OCR Engine）為單一最高權限控制項，選取本地模型時嚴禁觸發外部 Cloud API 與舊版脫敏 fallback。
- 本地脫敏金庫 (Sanitizer Vault)：
  - 僅在明確切換至外部雲端模型（OmniRoute 網關）時啟用。
  - 本地確定性脫敏（IPv4、網域、MAC 替換為 `<IP_X>`、`<DOMAIN_X>`），雲端模型回傳後於本機記憶體查表還原。
  - 同一個值在文字中每一次出現都必須被替換（不得只替換第一次）；token 全域唯一，跨 session 皆可還原。
  - 網域後綴由 Standards 的 domain 變數動態取得（值以 "." 開頭者），Vault 每次使用均新建實例，故 Standards 修改無需重啟即生效。
  - 持久化檔案：`data/sanitizer_vault.db` 已由 .gitignore 排除。
- 版本控制邊界：`data/naming_rules.yaml` 與 `data/*.ps1` 納入 git 追蹤（commit 前必須審 diff，因瀏覽器測試會改寫）；`data/netbox_hub.db`、`data/*.csv`、`*.db-shm`、`*.db-wal`、`*.db-journal` 徹底排除。
- 測試隔離：`tests/conftest.py` 讓測試使用臨時 DB / vault 路徑，不再寫入 `data/` 內的生產檔案。（最近一次：226 passed, 1 skipped）

### 2.2 模組邊界職責清單

- `core/ocr_engine.py`：專責 RapidOCR 本地文字框提取，輸出 raw_tokens 座標與 image_height。
- `core/extractors/base.py`：BaseTopologyExtractor 抽象類與座標計算 helper。
- `core/extractors/__init__.py`：EXTRACTOR_REGISTRY 與拓撲通用調度總機 `run_topology_pipeline()`。
- `core/extractors/esxi.py`：ESXi OCR extractor（遵守標準 7 欄 + vlan 契約）。
- `core/extractors/esxi_text.py`：vSphere Client "Virtual switches" 頁面文字 parser，輸出 records / warnings / gaps。
- `core/extractors/esxi_pipeline.py`：`analyze_esxi()` 調度（文字優先 ➔ OCR 補缺口 ➔ 純 OCR）。
- `core/extractors/proxmox.py`：Proxmox OCR extractor（抓取 vmbr、bond、eno/ens）。
- `core/topology_reducer.py`：`reduce_topology_pipeline()`，保留 vlan / source / is_uncertain / note 欄位。
- `core/backup_endpoint_merge.py`：parse_ts / format_ts / plan_endpoint_updates（備份逐 endpoint 合併規則，純函數）。
- `core/backup_manager.py`：save_netbox_backup、restore_backup_to_session_state、init_backup_tables。
- `core/shared_backup_state.py`：session 層備份登記冊（load_backup / add_csv_override / clear_csv_only）。
- `core/azure_vm_importer.py`：Azure CSV 解析；以 Raw_Tags_JSON 為權威標籤來源（每個標量 tag 皆成為 NetBox tag「Key:Value」，大小寫不敏感去重）；Site / Platform / Role 經 Standards azure_mappings 對應（已是對應後的舊 CSV 值原樣通過）。
- `core/vault.py`：Sanitizer Vault（見 2.1）。
- `config/naming_rules.py`：規則載入 / 正規化 / 預設值（Seed Once）；get_azure_mappings、get_domain_suffix_variables、export_rules_as_prompt。
- `ui/components.py`：備份上傳 / 清除 / Authority status / Backup contents（單一 dataframe）/ 共用 toolbar stats（含 `_cached_fetch_toolbar_stats`，ipam_tab 與 naming_tab 共用）；不得 import `ui/tabs/*`。
- `ui/tabs/naming_tab.py`：標準 3 步線性工作流（Step 1 ➔ Step 2 ➔ Step 3），平台條件化渲染。
- `ui/tabs/standards_tab.py`：Standards 全部編輯器；表格統一使用 `_wc_*` working-copy helper（見 6.4）。
- `ui/tabs/azure_tab.py`：Azure VM 匯入與分析，檔案上傳器版本鍵隔離，支援安全清空重置；內建 KQL（`AZURE_KQL_QUERY`）為 raw string。
- 已刪除冗餘：docs/ 整組文檔、`core/spatial_redactor.py`、`data/naming_rules.yaml.example`、`scripts/sync-agent.ps1`、Flask API 服務與套件、Standards 內無人呼叫的 spatial anchors editor、重複的 tombstone / Restore 機制。

---

## 3. 全鏈路資料處理管線 (Pipeline Flow)

### 3.0 現行通用平台調度管線（Processing Modules 架構）

各平台的輸入插槽與處理模組由 `topology_parsing_presets[平台]["modules"]` 決定（順序即優先級）；UI 只經 `run_topology_pipeline()` 調度，不得直接 import 任何單一平台管道。ESXi 的「文字優先 ➔ OCR 補缺口 ➔ 純 OCR」三模式由 `esxi_pipeline.analyze_esxi()` 實現（source 標記為 "text" / "text+ocr" / "ocr"；全無輸入則 warning toast）。細節以 code 為準。

```
[UI: naming_tab.py]  使用者選擇 Target Platform
        │  讀取 topology_parsing_presets[平台]["modules"] → 決定輸入插槽與處理模組
        ▼
run_topology_pipeline(platform, text, get_ocr_records)    ← UI 唯一入口
        │  依 modules 調度對應 extractor / 文字 parser（保持標準欄位契約完整輸出）
        ▼
reduce_topology_pipeline    （去重、狀態聚合、展開優先覆蓋、保留 vlan 與標記）
        ▼
Step 2: Topology Data Grid & Visual Cards
        ▼
Step 3: Live NetBox Output Synthesizer
```

### 3.1 備份分層合併管線（NetBox JSON / CSV）

- 規則：devices / IP 都會新增或移除，按「最新上傳」逐 endpoint 決定來源。
  - Full 上傳 = 取代全部（重置 layers 並清除 CSV overlay；目前 CSV 策略為保守全清，見 9.3）。
  - Minimal（metadata.backup_type == "minimal"）= 只取代檔案內含之 endpoint；檔案未包含的 endpoint 保留 Full 資料；內含但為 0 筆（expected_count == 0）視為已移除並同步取代。
  - CSV = 取代自身所屬之動態 endpoint。
- 安全閘保護 (`plan_endpoint_updates`)：
  - skip_unverified：verified == False 或 downloaded_count < expected_count（部分匯出拒絕覆蓋完整數據）。
  - skip_suspicious_empty：0 筆但原已存有資料且 expected_count != 0。
  - skip_stale：新檔匯出時間比已存紀錄舊時跳過並記錄於 skipped。
- 交易與儲存：單一連線 BEGIN IMMEDIATE 交易控制；backup_endpoints 表維護各端點狀態；壓縮 JSON blob 支援無縫回滾與 Session 恢復。
- 清除邊界：Clear All JSON 只清 JSON；Clear CSV 只清 CSV；Clear Everything 全清；Clear Everything 於「有 JSON、或有 CSV override、或 catalogue 非空」時皆可用，不再受 JSON loaded 限制。
- Backup contents 的 JSON / CSV 判斷使用同一規則（source_type == csv，或 source 副檔名為 .csv / .xlsx）；標題寫成 JSON n  CSV n，不得與 catalogue 數字混為一談。

### 3.2 Azure VM 匯入管線

- Azure Resource Graph KQL（azure_tab 內建，用 app 的 copy 掣取得）只輸出原始值；匯出 CSV 後上傳。
- `parse_azure_vm_csv`：讀 Raw_Tags_JSON ➔ `vm_record["raw_tags"]` ➔ `_build_netbox_tags()` 逐一產生「Key:Value」tag；NetBox_Tags 欄僅為舊相容，非權威。
- Site / Platform / Role 經 Standards ➔ Azure Import Mappings（azure_mappings：region_site、platform、role_fallback）對應；找不到對應的 region 顯示「UNMAPPED - `<region>`」。
- KQL 規則：replace_regex 的 backreference 必須使用 verbatim 字串 `@'\0'` 形式（非 verbatim 字串會令冒號消失；確切寫法以 azure_tab 內建 KQL 為準）；extend 清單內不得加 `//` 註解；同一表格（ResourceContainers）作為 join 右表最多一次。

---

## 4. 關鍵拓撲解析不變律 (Topology Invariants)

### 4.1 欄位邊界硬隔離 (Strict Column Isolation)

- 左欄邏輯服務（Port Groups & VMkernel）：
  - Interface 欄位僅允許放置 `vmk<N>` 或為空字串（純 Port Group）。
  - 嚴禁將 IP 地址、冒號或連線速度填入 Interface 欄位。
  - IPv4 欄位專屬於 VMkernel 接口；實體網卡欄位之 IP 必須恆為空字串。
- 右欄實體架構（Physical Uplinks）：
  - 實體網卡 Interface 欄位必須為乾淨之 `vmnic<N>`（Proxmox 為 `eno` / `ens` / `eth<N>`）。
  - 網速標籤（如 10000 Full, 10 Gbit/s）必須剝離並獨立歸入 speed 欄位。
  - Uplink 容器標籤（如 Uplink 1, `*-DVUplinks-*`）屬於分組框架，嚴禁作為 Port Group 輸出。
  - 容器內 "uplinkN (0 NIC Adapters)" 空槽位不得變成 NIC [x]。

### 4.2 多網卡 Teaming 零遺漏律 (Zero Uplink Omission)

- 多實體網卡綁定（Active/Active, Active/Standby）：
  - 交換機下若配置多張實體網卡（如 vmnic4 與 vmnic5），必須為每張實體網卡產出獨立 Uplink 紀錄。
  - 解析器嚴禁在抓取第一張網卡後提早結束。
- OCR 容錯：vmnic 名被誤識為 vmnici1 / vmnlc2 仍取回尾端數字；名稱完全不可讀但速度可讀時輸出 "vmnic?"，嚴禁靜默丟棄 [~]。

### 4.3 狀態覆蓋與孤立交換機保護 (State Precedence & Isolated Switches)

- 展開狀態絕對優先 (Expanded State Invariant)：
  - 交換機於前期截圖呈折疊狀態（>）無網卡，後期截圖呈展開狀態（v）帶網卡與服務時，展開狀態具備絕對覆蓋權。
  - OCR 丟失 ">" 符號時，band 內無任何 token 視為收起，不輸出 "No Uplink" 佔位。
- 無網卡交換機防污染：明確標註 No physical network adapters 時輸出 interface: "No Uplink"，嚴禁跨邊界吸納相鄰網卡。
- 版面錨定：card 標題必須與其 "VLAN ID" 列左對齊（距離 <= 90px），防止右側雜訊被誤判為標題 [x]。

---

## 5. Standards 規範與平台自由 CRUD 架構

### 5.1 平台平等 CRUD 與唯一保護閘 (Platform Lifecycle Rules)

- 廢除殭屍自動復活 (Abolish Phantom Auto-heal)：
  - `_ensure_unified_parsing_defaults()` 僅在系統完全沒有任何平台預設集（`not rules.get("topology_parsing_presets")`）的全新初始化時注入預設工廠配置。
  - hypervisor_presets 同樣只在 key 不存在時播種（見 1.1 Seed Once）；`_normalize_rules` 不再補回被刪除的工廠平台（驗證狀態見 9.3）。
  - 運行中的系統絕對尊重使用者的刪除操作，嚴禁在儲存或重載時強制將已被使用者刪除之平台（如 Microsoft Azure）重新塞回 YAML。
- 唯一安全保護閘 (Last Platform Guardrail)：
  - 系統內部任何平台（VMware ESXi、Proxmox VE、Azure 或使用者自訂平台）地位完全平等，均可被編輯或刪除。
  - 唯一硬限制：當系統只剩下最後一個平台時（`len(platforms) <= 1`），禁止刪除（🗑 按鈕停用）。
  - 平台總數 >= 2 時，任一平台均可刪除；平台清單的排序、刪除與 Processing Modules 的增減屬暫存副本，按 Save & Apply Changes 才寫入 YAML（Create New Platform 為例外，立即儲存並加入暫存清單）。
- 動態首選平台回退 (Dynamic Fallback)：
  - 移除 `config/constants.py` 中對預設平台的死鎖綁定；若先前選定之平台已被刪除，自動平滑回退至現存平台清單之第一個項目。
  - Naming 平台選單與次序：saved order 中不存在的平台一律略過；選單來源為 topology_parsing_presets。

### 5.2 平台專屬預設集配置結構 (YAML 規範)

各平台專屬之正則修復、雜訊過濾與幾何錨點完整外部化收斂於 `data/naming_rules.yaml` 的 `topology_parsing_presets` 字典中。Processing Modules 儲存於 `topology_parsing_presets[平台]["modules"]`；`DEFAULT_PARSING_PRESETS` 並無出廠 modules 值，故平台層級 Reset 的 modules 還原為「已儲存版本」。

```yaml
topology_parsing_presets:
  VMware ESXi:
    platform: "VMware ESXi"
    aliases:
      parent: ["v_switch", "vswitch", "dvswitch"]
      interface: ["vmnic", "vmk", "port_group"]
      speed: ["speed", "link_speed"]
    layout_geometry:
      sidebar_x_cutoff: 280.0
      midline_ratio: 0.48
    noise_filters:
      - "Storage Adapters"
      - "VMkernel adapters"
    token_normalizations:
      - pattern: 'vSwitch[oO](\d*)'
        replace: 'vSwitch0\1'
      - pattern: '\bvm[nN][kKlL1]c(\d+)\b'
        replace: 'vmnic\1'
```

### 5.3 其他由 Standards 管理的 YAML 區塊

- pattern_variables：每個變數有 label / placeholder / default / optional / scope（shared / ipam / naming / hypervisor）；儲存為 scope 安全 merge（只改該 scope，其他 scope 不動，不得整份取代），並保存該 scope 的次序。刪除有被引用時顯示警告，由使用者選 Delete anyway / Cancel；不設 Reset All，單 scope Reset to Defaults 只還原該 scope。
- azure_mappings：region_site（key 小寫）、platform、role_fallback（預設空字串）；工廠預設為 DEFAULT_AZURE_MAPPINGS，只播種一次。
- host_vm_presets：Host 與 VM 共用同一個 list，以 `pattern_key == "vm_host"` 區分（暫定設計：所有 VM 共用一個 template；見 9.4）。
- token_order：除 pattern 外亦保存 ipam_site_fields（IPAM Site / Scope ID / Supernet 的顯示次序）。
- IPAM 三個欄位：label 與 placeholder 讀取 pattern_variables（site、scope_id、site_supernet 的 label / placeholder）；值的來源（資料庫 / 自動配對）不受影響；site 的 label 與 Naming 共用。
- `_normalize_rules` 對頂層 key 為白名單；新增頂層 key 必須加入白名單，否則儲存 / 載入時會被丟棄。

---

## 6. UI 規範、狀態管理與現代化

### 6.1 Naming 拓撲批次解析（標準 3 步線性工作流）

- 1️⃣ Platform & Data Ingestion（平台與資料載入）：
  - 置頂平台選擇列：頂部設置 Target Platform 下拉選單與引擎狀態標籤（Platform First）。
  - 條件化渲染插槽：
    - 當 platform == "VMware ESXi"：展開「📋 Paste vSphere page text (optional, recommended — no OCR)」與專屬 JavaScript 抓取指南，並提供「📸 Screenshot Guidelines」（Screenshot Guidelines 屬 image slot，不依賴是否已貼文字）。
    - 當 platform == "Proxmox VE" 或其他平台：自動隱藏 vSphere 專屬文字輸入區與說明，換為該平台對應指引。
  - 通用影像上傳區：Drag & Drop 多圖上傳 + 剪貼簿（Ctrl+V）貼上區。
  - 動作按鈕列：[ 🚀 Analyze & Auto-Populate ] 主按鈕與 [ 🗑️ Clear All ] 重設按鈕。
- 2️⃣ Topology Review & Visual Editing（審核與視覺微調）：
  - 視覺容器卡片 (Visual Overview Cards)：支援容器即時改名、整組刪除、Popover 新增網卡/服務。
  - 權威資料網格 (Topology Data Grid)：st.data_editor 雙向同步編輯，核驗 VLAN、IP、Link Speed 與 🟡 Uncertain 標籤。
  - Cards 與 Data Grid 兩處皆可編輯同一份 records（已決定：保留兩個可編輯）。
- 3️⃣ Live NetBox Output & Copy（產出與交付）：
  - 即時渲染 NetBox Description 代碼區塊。
  - [ 📋 Copy All Descriptions ] 與 [ 📥 Download (.txt) ] 交付按鈕。
- Naming 生成器：Host / VM 的 pattern key 取自 preset tuple `(code, label, pattern_key)`，不得 fallback 到其他類型的 pattern；pattern 內未填的必填 token（非 optional）顯示「Missing value for」警告（optional token 不警告）。
- Naming 的 radio / selectbox：選項使用固定 key，顯示文字經 format_func；邏輯判斷只比對 key，不得比對顯示字串。

### 6.2 Azure Tab 重設修復與元件生命週期

- 模組頂部統一匯入：`import os` 與 `import tempfile` 移至模組頂部，杜絕條件分支未執行導致之 UnboundLocalError [x]。
- 上傳器版本計數隔離 (Version-driven Key)：
  `st.file_uploader(..., key=f"azure_csv_uploader_{st.session_state.get('azure_uploader_key_ver', 0)}")`
  點擊 🗑️ Clear Azure CSV 時遞增 `azure_uploader_key_ver`，強制銷毀重建物件，確保暫存檔案即時由介面抹除（同時刪除磁碟上的暫存 CSV；解析並存入資料庫後亦立即清除暫存檔）。
- 狀態安全彈出：全檔禁用 `del st.session_state.X`，全面採用 `.pop("X", None)` 防禦未綁定鍵值。
- Preview 表以 `.get()` 讀取 VM 欄位並防禦 NaN，舊資料缺欄位不得令整個 tab 崩潰。

### 6.3 狀態隔離與版本驅動 (State Versioning)

- editor_ver 動態鑑別碼：卡片操作累加 editor_ver，強制刷新 `topology_data_editor_{editor_ver}` 內部緩存。
- Clear All 徹底重設：清空暫存圖片、拓撲紀錄、交換機快取、OCR 快取，並同步遞增 editor_ver、topo_uploader_key_ver 與 paste_text_ver。

### 6.4 Standards 統一表格寫法 (Working Copy Pattern)

- 規則（全站一致）：⬆ ⬇ 🗑 ➕ Add 只改畫面上的暫存副本，一撳即變；Save & Apply Changes 才寫入 YAML；未 Save 就 F5 則還原為已儲存版本；有未儲存修改時顯示「⚠️ Unsaved changes — click Save & Apply Changes to keep them.」；Reset to Defaults 即時還原。
- 實作：`_wc_rows(name, loader)` 建立暫存副本；每行帶固定 `_uid`，widget key 由 working-copy 名稱 + 該行 `_uid` + 欄位組成（不使用行號，換位後輸入內容跟行走；確切格式以 standards_tab 為準）；`_wc_row_actions`（⬆⬇🗑，show_delete=False 時只出 ⬆⬇）、`_wc_add_row`、`_wc_values`（比較 dirty）、`_wc_discard`（Save / Reset 前清除）。
- 每個 widget 的 label 使用常量並 `label_visibility="collapsed"`；每行必須各自 `st.columns`，不得共用表頭的 columns。
- Token 欄不得使用 `st.code`（自帶 Copy 掣令行高不一致）；使用固定高度的 markdown 行內 code。
- 已套用：Azure Import Mappings、Site Code、VLAN Description、IPAM Role、Auto-Correction、Device / Interface / Host / VM presets、Hypervisor（平台清單、Processing Modules、Description Templates、Token Normalizations）、Pattern Variables。Hypervisor editor 的結構動作與行內編輯已統一為暫存副本並以 caption 說明。
- 例外：Create New Platform 為獨立表單，立即儲存；VLAN preset 的 group 層級仍為舊寫法（見 9.3）。

### 6.5 UI 實作約定（由 runtime 事故歸納）

- widget 以 `key=` 建立後，同一次 run 不得再賦值 `st.session_state[該 key]`（StreamlitWidgetAlreadyInstantiatedError）；預設值在 widget 建立「前」用 `if key not in st.session_state` 初始化，或在 on_change / on_click callback 內改。
- `st.dataframe` / `st.data_editor` / `st.button` 的 width 只可用 `"stretch"`、`"content"` 或正整數，不得為 None；已棄用的 `use_container_width` 一律改用 `width="stretch"` / `"content"`。
- `st.success` / `st.warning` / `st.toast` 已有 `icon=` 時，訊息文字不再重複放 emoji。
- 操作回饋用 `st.toast`；不得在 `st.rerun()` 之前用 `st.success`（訊息會立即消失）。
- 標題層級：Step 標題 `####`，子標題 `#####`；同級區塊不得出現較大的子標題。
- on_change / on_click callback 內不得呼叫 `st.rerun()`；ipam_tab 內不得使用 `st.stop()`（兩個 `st.tabs` 同一次 run 執行）。
- 同一個 function 在一次 render 內不得被呼叫兩次；`st.form` 使用固定 key 時，重複呼叫會令 StreamlitDuplicateElementKey 使整個 tab 失敗。
- 多個 widget 的 session_state 賦值集中在「第一個 widget 之前」的同步區塊，不依賴顯示順序。
- 使用 pending / 暫存副本時，先處理動作再建立 widget。
- 新增 row 必須帶 `_uid`；render loop 讀取前以 `setdefault` 補 `_uid` 作保險。

---

## 7. 標準命名輸出規範 (NetBox Standards)

### 7.1 NetBox 虛擬化網路描述樣式

- Switch Teaming 標頭：`<v_switch> (<teaming>)`
  - 有網卡範例：dvSwitch0 (vmnic0 Active Uplink / vmnic1 Active Uplink)
  - 無網卡範例：vSwitch1 (No Uplink)
- 實體 Uplink 描述：`<interface> - <v_switch> <purpose> <uplink_role>`
  - 範例：vmnic0 - dvSwitch0 Active Uplink
- 邏輯連接埠群組：`<purpose> (<v_switch>)`
  - 範例：Management Network (dvSwitch0)
- 去重鍵規範：服務類輸出以 `(_svc_type, _svc_iface, _svc_name)` 三元組為去重鍵，解決同一 Port Group 下第二個 VMkernel 接口被覆蓋消失之缺陷 [x]。

---

## 8. 交付部署與發布 SOP

### 8.1 版本號全域同步檢查

發布前確認版本字串一致（以 `config/constants.py` 的 APP_VERSION 為準，並與 git tag 對齊）：
- `config/constants.py`（APP_VERSION）
- `data/naming_rules.yaml`
- `ARCHITECTURE_SOP.md`

### 8.2 Git 規則（取代舊 Orphan Squash）

- 【已停用】不再執行 Orphan Squash / force push / 刪除遠端 tag；已 push 的 tag 不覆寫，修正一律使用新版本號。
- Rollback：以 `git revert` 產生新 commit，再 bump 新版本號 release；不改寫歷史、不移動既有 tag。
- commit 前（由人手執行）：`python3 -m py_compile <file> && git diff --stat && git diff -w --stat`；無 COMPILE_OK、或兩個 diff 數字差距大（縮排被整檔改動）則不 commit。
- 只 add 指定檔案，不用 `git add .` / `git add -A`；`data/naming_rules.yaml` 先審 diff 才決定是否 commit。
- Release 流程：本機不 build；push 到 GitHub 後由 pipeline 建置，再 rebuild container；release 後記錄 Streamlit / pandas / openpyxl 實際版本。
- 歷史參考（v4.8.48 一次性基準重置，勿再使用）：orphan checkout ➔ 單一 commit ➔ 刪全部 tag ➔ `git push -f`。

### 8.3 Docker 建置與磁碟清理序列（Oracle Cloud A1 ARM64，舊流程參考）

```
docker build -t ghcr.io/miniz3210/netbox-hub:<version> -t ghcr.io/miniz3210/netbox-hub:latest . && \
docker push ghcr.io/miniz3210/netbox-hub:<version> && \
docker push ghcr.io/miniz3210/netbox-hub:latest

docker system prune -f
docker image prune -a -f
docker builder prune -a -f
df -h /
```

### 8.4 Release 前檢查與唯讀載入檢查

- 唯讀載入檢查（只限 import / py_compile，不得當功能測試）：
  `docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v <repo>:/work:ro -w /work --entrypoint sh <image> -c "python -c 'import ui.components, ui.tabs.ipam_tab, ui.tabs.naming_tab, ui.tabs.standards_tab'"`
- 完整 pytest 須在 /tmp 副本執行（測試會寫 SQLite）：`cp -r /opt/netbox-hub /tmp/nbh-test`，再於副本內以 image 執行 pytest。
- py_compile 與 import 通過 ≠ 可 release：多個 runtime 錯誤（上傳 JSON 後才觸發、重複 widget key、UI try/except 內的 NameError）都不會被 pytest 或 compile 抓到；release 前必須逐 Tab 瀏覽器煙霧測試並查 `docker logs --tail 80 netbox-hub 2>&1 | grep -i -B2 -A14 "crashed\|Traceback"`。
- Azure KQL 變更後，把 `/tmp/azure_runtime.kql`（或 app 內 copy 出來的版本）貼入 Azure Resource Graph Explorer 測試通過才 release；從終端機複製時用 `cat file; echo`，避免最後一行被 prompt 黏住而漏選。

---

## 9. 專案狀態檢核：做完咩 (Done) vs 未做咩 (Pending)

### 9.1 做完咩 (Completed & Verified)

- [x] 本地確定性拓撲識別架構確立，完全移除 LLM 依賴。
- [x] UI 路由與引擎排他控制（Authoritative UI Control）。
- [x] Streamlit 現代化標準相容（全面採用 width='stretch' / 'content'）。
- [x] ESXi 文字優先 + OCR 缺口補齊 + 純 OCR 三模式調度（esxi_pipeline）。
- [x] 貼文字與 OCR 雙路徑 VLAN ID 提取修復；OCR Extractor 重複 "vlan" 鍵值覆蓋修復。
- [x] 靜態代碼守門：Undefined-name 全數清零，AST 導入依賴圖防護生效（226 passed, 1 skipped）。
- [x] 備份分層合併（Part C）：逐 endpoint 合併、單連線交易 rollback、部分匯出攔截保護。
- [x] DataGrid 型別強制清洗器，根治 _check 欄位型別污染。
- [x] Naming Live NetBox Output 服務去重鍵改為三元組。
- [x] Standards IPAM 角色對應 🗑️ 按鈕綁定 `_persist_ipam_role_mappings` 與持久化。
- [x] Azure Tab：UnboundLocalError (os) 修正；File Uploader 增加 `azure_uploader_key_ver`；死代碼與跳脫字元清理；Role 無值不再於 KQL 填佔位字（由 Standards role_fallback 決定）。
- [x] Azure：KQL 輸出原始值；importer 經 Standards azure_mappings 對應；Raw_Tags_JSON 為權威來源，所有 tag 成為 NetBox tag。
- [x] 倉庫冗餘文檔與腳本清理；移除 Flask API 服務、無人呼叫的 spatial anchors editor 與其他死代碼。
- [x] UI cleanup（Ingest 區重排、Backup contents 單一 dataframe、Reconciliation 表格持久化、Clear Everything 解鎖、Naming 截圖指引歸 image slot、雙 icon / 標題層級統一）。
- [x] 預設值只播種一次（pattern variables、hypervisor presets、azure_mappings）；Delete 即真刪；移除 tombstone / Restore 清單與 Reset All Variables。
- [x] Pattern Variables：scope 安全儲存、次序保存、依賴警告（Delete anyway / Cancel）、單 scope Reset。
- [x] Vault：所有出現位置遮蔽、跨 session 還原、Standards domain 變數生效。
- [x] `export_rules_as_prompt`：domain 區塊由 Standards 變數動態組成（無寫死名稱、無假預設值）。
- [x] Naming：radio 固定 key、未填 token 警告、Host pattern key 由 preset 決定、無寫死 ESXi 後備。
- [x] Standards 統一表格寫法（見 6.4）套用至列表式編輯器（VLAN group 層級除外，見 9.3）；Hypervisor editor 即時保存 / 按 Save 行為已統一。
- [x] IPAM Site / Scope ID / Supernet：Edit Mode（label + 次序）、placeholder 讀取 Standards；`_cached_fetch_toolbar_stats` 已移到共用模組。
- [x] standards_tab 失效的 data-testid CSS 已刪除（commit 62521ec）；保留 `.action-header`、InputInstructions、stCheckbox / stTooltipIcon 三條。
- [x] Naming 3 步工作流（已由 Processing Modules 重構取代並多輪驗收）；Strategy Pattern 總機 `run_topology_pipeline()` 已取代寫死的 analyze_esxi 呼叫；平台自由 CRUD 與最後平台保護閘（暫存副本，剩一個平台時 🗑 停用）。

### 9.2 已確認的設計決定 (Decisions)

- Naming 的 Visual Cards 與 Data Grid 保留兩個都可編輯。
- Streamlit 刻意不 pin 版本（見文件開頭）。
- 工廠預設 token_normalizations 的站點名豁免（見 1.1）。
- 進行中 [~]：
  - [~] Pattern Variables Add 新行 KeyError '_uid'：修正（新行帶 `_uid` + render loop setdefault）需確認已 commit 並於瀏覽器驗收。
  - [~] VLAN preset 項目暫存副本：已改，未有獨立驗收記錄。

### 9.3 未做咩 (Pending Backlog)

**P1（立即阻斷性修復）**
- [ ] 確認 Pattern Variables Add 修正已 commit 並驗收。
- [ ] 清死代碼：`_render_preset_row_actions`、舊 `_pending_*` / swap session key；`find_variable_references` 若仍重複定義則刪除重複（先 grep 確認）。

**P2（系統完整度與邏輯一致性）**
- [ ] Minimal 備份 record_count 統計修復：目前 Minimal 覆蓋後誤顯示為當次 Minimal 筆數，應修正為從 backup_endpoints 統計或合併後有效總筆數。
- [ ] `clear_backup_records` 徹底清理：補充 DELETE FROM interfaces、DELETE FROM cables、DELETE FROM backup_endpoints，並動態掃描刪除所有 `dynamic_*` 表。
- [ ] `batch_tab.py` 平行處理容錯：process_row 缺乏 try/except，單行異常會導致整個執行緒池終止。
- [ ] Excel IPAM 資料庫比對補齊：`_build_backup_index` 填補 IP 集合與 db_record_map。
- [ ] VLAN preset 的 group 層級（新增 / 刪除 / 排序）仍為舊寫法，尚未統一為暫存副本。
- [ ] Naming Host 生成器後處理：合併連續 "." 與用第一個 "." 分 hostname / domain 仍寫死在 naming_tab（只在 Host 生成器）。
- [ ] 平台清單全空時 Naming 的 platform 選單防護（`naming_platform_options[0]` 可能 IndexError）。
- [ ] 驗證 `_normalize_rules()` 內 `DEFAULT_HYPERVISOR_PRESETS` 是否仍會補回缺失平台：5.1 記為已不補回，但 Handover 曾列為「疑似第二條 auto-heal」；用 grep + 副本 YAML 測試定案後刪除本項或修正。
- [ ] Platform module history：刪除平台後只記 `platform name -> module IDs`，同名重建時提示 Restore / Start Empty，不得靜默還原。
- [ ] Module reorder UI（順序即優先級，目前只能移除再新增）。
- [ ] 版本 drift：`APP_VERSION` 對齊 git tag。
- [ ] 新 Full 的 CSV 策略目前為保守全清；若要保留「比 Full 更新的 CSV」需另行設計 timestamp-based selective overlay。
- [ ] Standards 儲存流程重複：`SSM.refresh_naming_rules()` 約 16 處、`rules.clear()/update()` 約 7 處、nonce 遞增約 11 處；抽 `_commit_rules()` helper（先 grep 確認是否已做）。

**P3（長期維護與細節優化）**
- [ ] Naming 資料處理段殘餘 "No Uplink" / "vmnic" 字面值，改讀 preset / token（違反 1.1）。
- [ ] `noise_filters` 13 個通用單字品質審查（防止誤殺同名 Port Group）。
- [ ] `layout_geometry`：讓 Extractor 真正讀取該設定（及 Standards 滑桿連動），否則從 preset 刪除。
- [ ] 匯出腳本跳過 3 個非資料表 API 端點，並於 UI 標明失敗名稱（需 backup_manager 保存失敗名單；components.py 失敗 endpoint 名單 popover 併入此項）。
- [ ] OCR Inspector 補信心度（conf）與原始座標方框。
- [ ] Raw text / Warnings Inspector 位於 Step 1 之外：收入 Step 1 或明確標示。
- [ ] Excel IPAM reconciliation script。
- [ ] Full payload 內重複 endpoint path 目前 first-wins，建議附 warning。
- [ ] pyflakes 既有警告（未使用變數 / 無佔位符 f-string）一次清理。
- [ ] 清走遺留 session key（`switch_deck`、`chips_unassigned`、`chips_session_corrections`），若 Clear All 流程仍引用。
- [ ] 同 kind 多 module 結果合併 / `execution_mode`（目前刻意不做）。
- [ ] IPAM site fields：隱藏或新增欄位需先令 `ipam_engine` 函數收 values dict（目前為固定參數）；placeholder / help 尚未可編輯。
- [ ] Standards 統一 Save / Reset 字眼與欄寬；preset 表格 / count badge HTML 重複約 6-8 次可抽 helper。
- [ ] Azure：NetBox_Tags 欄為舊相容用，穩定後可取消。
- [ ] Vault：`_reseed_counters` 將 "IP_12" 轉整數（恆為 0，重設計數無效）；`_SWITCH_NAME_RE` / `_PG_NAME_RE` 未被使用（pass 10/11 被繞過）且 `_SWITCH_NAME_RE` 寫死站點字眼（"PR "、"VLab"）。

### 9.4 暫緩 (Deferred)

- Proxmox `/etc/network/interfaces` 文字 parser（由使用者決定時機）：新增 module `proxmox_interfaces`（`input_kind: text`）並登記到 `PROCESSING_MODULES`，由 Standards 指派給 Proxmox；涵蓋 bridge-ports 多成員、bond-slaves 零遺漏、address，無推測值；最多 3 個測試。這是新 module 架構第一個跨平台實證。
- VM presets 共用一個 template（pattern_key `vm_host`，過渡設計）：若需 per-VM template，host_vm_presets 加 `kind: host|vm`，舊資料由 `vm_host` 推斷，並更新 Standards 約 9 處與 Naming 2 處；不得再新增 `vm_host` 比較。
- Naming text / image slot 骨架統一：上次嘗試因縮排錯誤已 revert（317be36）；重試須一次一個 slot，且不得整段搬位。
- IPAM `st.tabs` 改 radio + lazy render：目前不安全，切換 view 會丟棄另一 view 的 widget state（Branch、Scope ID、Supernet、表格編輯）；需先把這些值另存為非 widget state。

---

## 10. AI Coding Agent 作業規則 (Kilo / 免費模型防斷線自律)

### 10.1 規則

- 單一任務原子性：一個 Session 僅處理單一目標，嚴禁跨模組擴大修改範圍。
- 分工：commit、版本 bump、tag、push 全部由人手執行；agent 不做 git commit / 版本 bump / tag / push。多 Phase 工作以 "P1:"、"P2:" 前綴 commit，session 中斷時以 `git log --oneline -8` 續做。
- Context 防熔斷機制：對話 Context 超過 80K tokens 時禁止發送大型編輯任務；一旦發生 tool-call 格式錯誤或模型重複迷路，立即結束該 Session，以乾淨 Session 重啟。
- 嚴禁物理破壞：不得指示 Agent 對 `data/` 目錄進行批次刪除或移動（保護 `netbox-export-*.ps1` 與 `naming_rules.yaml`）。
- 副本測試保護：所有涉及 SQLite 寫入之測試必須在 /tmp 副本路徑執行，嚴禁直接以 `data/netbox_hub.db` 進行破壞性測試。
- 禁止 sudo、`sed -i`、sub-agent（多個 sub-agent 各自重讀大檔會令 token 暴增）；agent 寫不到檔就停低報告，不繞路。
- agent 的 shell 沒有 python：不得要求 agent 執行 py_compile；compile、diff、commit 由人手執行。
- 一個 prompt 只做一個 Part，並限制 tool call 數量；大段搬位或縮排改動風險高（曾令 `ui/components.py` 約 2,600 行去縮排並已 push，需以新 commit 修復）。
- 禁止整檔重寫或整檔輸出；只用小段編輯（<= 40 行）。
- Read 工具參數出錯（例如 offset 傳字串）重試 3 次即停；改用 `grep -n` 加 `sed -n`。
- agent 回報「完成」或「inline 版本」必須用 grep 確認是否真的使用共用 helper，並核對 diff。
- UI 類 prompt 附加：never assign st.session_state[key] after a widget with that key exists; st.dataframe width must be "stretch"; never rewrite or re-output a whole file.
- Ponytail（最小改動 skill）：
  - 預設啟用；安裝：`npx skills add https://github.com/dietrichgebert/ponytail --skill ponytail`（裝完開新 session 驗證）。強度 `/ponytail lite|full|ultra`；停用說 "stop ponytail" 或 "normal mode"。
  - 每次回覆末尾須有一兩行「skipped / 未驗證 / 風險」；此行不計入下方 "max 3 lines" 的 changed lines。
  - 優先級：本 SOP 的規則（ONE TASK ONLY、禁止整檔重寫、不碰 data/、No Hardcode）高於 ponytail；ponytail 不得以「少寫 code」為由把平台 / 廠商常數寫死進 Python（見 1.1）。
  - 若 agent 沒有自動載入 ponytail，於 prompt 加一行 `Use ponytail skill.`。

### 10.2 新 Session 開頭區塊（每次貼在 prompt 最前）

```
ONE TASK ONLY. Do not delete, move or rename any file. Do not run the full test suite. Do not write tests other than the one asked. Do not plan other tasks. Do not run git commit / version bump / tag / push. Do not rewrite or re-output a whole file; edits <= 40 lines. Use ponytail skill; end with a one-line "skipped / risk" note. Output: changed lines, max 3 lines.
Follow ARCHITECTURE_SOP.md (No Hardcode, Seed Once, Working Copy pattern, UI conventions 6.5).
```

---

## 11. 發布後手動煙霧測試（約 10 分鐘）

1. 逐個 Tab 切換（Naming、Standards、IPAM、Azure、Device、Rack、Batch、Module、Image）：無 ImportError / NameError / 紅色執行期錯誤；同時查 docker logs 無 Traceback。
2. Azure Tab：上傳 VM CSV ➔ 正常預覽 ➔ 點擊 🗑️ Clear Azure CSV ➔ 畫面乾淨重設且檔案上傳框清空，無 os 相關報錯。
3. Standards 平台生命週期：
   - 平台清單 ⬆⬇🗑 一撳即變，未 Save 撳 F5 還原；Save 後 F5 保留；Processing Modules 同理。
   - 點選 Microsoft Azure 刪除 ➔ Save 後不再自動復活。
   - 新增自訂平台 ➔ 可正常儲存與刪除。
   - 嘗試刪除最後僅剩之單一平台 ➔ 🗑 停用，刪除被阻擋。
4. Naming 3 步工作流：
   - 切換至 VMware ESXi ➔ 出現 1️⃣ Platform 區塊、vSphere 專屬文字輸入框、Console JS 說明與截圖指引。
   - 切換至其他平台 ➔ vSphere 專有文字框與腳本自動隱藏。
   - 貼入 vSphere 文字 ➔ 點擊 Analyze & Auto-Populate ➔ 正確流向 2️⃣ Topology Review & Visual Editing。
   - Visual Cards 卡片編輯、新增 Uplink/Service 正常，Data Grid 雙向同步無誤。
   - 捲動到底部 3️⃣ Live NetBox Output & Copy ➔ 語法正常合成，複製與下載功能運作流暢。
   - Host / VM 生成器：留空必填 token 出現警告；optional token 留空不警告。
5. 備份整合：上傳 Full ➔ 記錄筆數 ➔ 上傳 Minimal ➔ 驗證未包含之端點完整保留，總計數維持合併總筆數無跳水；只剩 CSV 時 Clear Everything 仍可用。
6. Standards 每個列表式表格：⬆ 一撳即變 ➔ 出現 Unsaved 提示 ➔ F5 還原 ➔ Save 後 F5 保留 ➔ 🗑、➕ 一撳即變。
7. Pattern Variables：Add 新變數無紅框、同名有警告；改一格不 Save 就 Add，該格仍為未儲存；Save 一個 scope 後，其他三個 scope 的變數數量與次序不變；刪除被引用的變數出現警告與 Delete anyway / Cancel。
8. Azure KQL：用 app 的 copy 掣取得 KQL 於 Resource Graph 執行；上傳新 CSV，Site / Platform 經 Standards 對應，所有 Azure tag 出現在 NetBox Tags；舊 CSV（已是對應後的值）仍正常。
9. Vault：同一個 IP 在問題中出現兩次，兩次都變成 token，AI 回覆還原真實 IP；重啟 container 後新 token 編號接續。
