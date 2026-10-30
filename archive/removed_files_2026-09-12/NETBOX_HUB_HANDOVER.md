# NetBox Hub — 交接文件 (Handover)

用途：記錄「目前狀態、做完咩、未做咩、陷阱、AI Agent 作業規則、發布與煙霧測試」。
永久架構規則請看 `ARCHITECTURE_SOP.md`。
基準版本：`<VERSION>`（以 `config/constants.py::APP_VERSION` 為準；撰寫時最新已見 tag 為 v4.8.70，APP_VERSION 落後 tag，下次 release 一併校正，不補做中間版本）。

狀態圖例：[x] 已完成並有證據（測試 / UI 截圖 / 副本 DB 實測 / git 輸出）；[~] 已實作但未完整驗證；[ ] 未做

---

## 1. 現況摘要

- 全套測試通過（最後見到 226 passed；Processing Modules 改動後你回報 all test passed）。
- Platform / Topology 框架已收斂：Processing Modules、平台 CRUD、排序持久化、通用 UI 去硬編碼。
- Backup Authority 模型已實作：Full catalogue、stale Full 整份拒絕、Minimal / CSV catalogue 閘、三種 Clear 邊界、Authority status UI、上傳階段進度條。
- Processing Modules 與其後 UI 修正需要新 tag 的 image 才進入 running container；先用 `docker exec ... grep` 確認。
- `data/naming_rules.yaml` 曾因 module migration 被改寫：commit 時要與 4 個 code 檔一起入（`config/naming_rules.py`、`core/extractors/__init__.py`、`ui/tabs/naming_tab.py`、`ui/tabs/standards_tab.py`）。
- UI cleanup（P1-P12）已完成：Ingest 區重排、Naming 截圖指引搬到 image slot、Reconciliation 持久化、Clear 邏輯、Pattern Variables merge、死代碼清理、Flask API 移除。使用者回報瀏覽器 test pass。
- b316275 把 ui/components.py 整檔去縮排（IndentationError），已由後續 commit 修復；修復版本待 release 為 v4.8.84，不覆寫 v4.8.83。

---

## 2. 做完咩 [x]

平台 / 拓撲
- [x] 本地確定性拓撲識別（零 LLM）；ESXi 文字優先 + OCR 補缺口 + 純 OCR；VLAN 雙路徑提取；OCR 重複 vlan 鍵修復。
- [x] `run_topology_pipeline(preset, text, get_ocr_records)` dispatcher；Processing Modules 取代固定 `text_parser` + `extractor`（空平台與 Proxmox 截圖畫面已驗證；無 ESXi fallback）。
- [x] `modules[]` 優先級、同 kind 可多個 module、unknown module 不阻斷其他 module、新平台預設 `modules: []`。
- [x] Naming 依 `input_kind` 閘控：無 module 時只剩 caption，Analyze 停用（自訂 Openshift 平台已驗證）。
- [x] 貼文字 live preview（只跑文字解析）、缺口提示合併、貼錯文字有明確警告。
- [x] 平台 CRUD：Auto-heal 只在 preset 全空時執行；最後平台 disabled 保護；已選平台被刪自動回退；`DEFAULT_PLATFORM` 已移除。
- [x] 平台排序持久化：`_normalize_rules()` 保存 `hypervisor_platform_order`（refresh 與 container 重啟驗證過）。
- [x] 通用 UI 去硬編碼：vSphere 字眼 / 指引 / 選項移入 module metadata 與 preset；`no_uplink_label` 外部化；失效 geometry 控制移除。
- [x] 死代碼清理：naming_tab 舊 chip/deck 系統與未使用 helper（約 346 行）、SQL Query Tool、spatial_redactor 及其測試。

備份 / IPAM
- [x] 逐 endpoint 合併（Full / Minimal / CSV）、`skip_unverified`、`skip_suspicious_empty`。
- [x] Full authoritative catalogue、`latest_full_backup_completed`、整份 stale Full 拒絕（UI 已實際見到 "stale Full rejected"）。
- [x] Minimal catalogue 閘與真正 `skip_stale`；CSV catalogue 閘；accepted Full 於 commit 後才清 CSV；stale Full 不動 CSV。
- [x] Clear All JSON 保留 catalogue / Full 時間戳 / CSV；Authority status UI 已見到 "Cleared / 145 endpoints / Latest Full / CSV 0"。
- [x] Minimal `record_count`、`object_counts` 由合併後 endpoint 推算，Full / Minimal 同一定義（副本驗證 inventory sum = record_count = object_counts sum = 3023）。
- [x] `clear_backup_records` 涵蓋 interfaces / cables / backup_endpoints / dynamic_*，含 `preserve_csv` 參數。
- [x] toolbar 統計以 `backup_stats_version` 即時刷新；IPAM `_build_backup_index` 讀合併 registry，cache key 隨內容變化。
- [x] `batch_tab.process_row` 單行容錯（原本已有 try/except）、失敗彙總 warning 與 logging。

UI / 清理
- [x] Reconciliation 表格改用 recon_bundle，編輯後不再消失；刪除無作用的 Copy to Clipboard 按鈕。
- [x] Clear Everything 不受 JSON loaded 限制；CSV / catalogue 存在時可用；Clear CSV 位於 CSV 區。
- [x] Pattern variables: factory defaults seed once (or on Reset to Defaults); Delete removes from YAML; undo via Change History.
- [x] Backup contents 單一 dataframe（Source 欄 / CSV 淡橙底 / Hide empty / Filter）；Custom field choice sets 改 dataframe；Manage CSV 搬入 CSV 區。
- [x] Naming：Screenshot Guidelines 屬 image slot；text / image slot 骨架一致；空狀態單一 info。
- [x] IPAM site fields (site / scope_id / site_supernet)：Edit Mode 支援 label + order（label 存 pattern_variables，order 存 token_order['ipam_site_fields']）。placeholder 由 Standards Pattern Variables 提供，非 example。
- [x] Hypervisor presets: factory defaults seed once (or Reset to Defaults); deleted platforms stay deleted; consumers handle a missing platform.
- [x] 移除 Flask API 服務與相關套件 / port；刪除 naming_tab / standards_tab / ipam_tab / components 內無引用死代碼。
- [x] Vault: every occurrence tokenised; cross-session restore; configured domain suffixes now match.

---

## 3. 已實作但待驗證 [~]

1. 新 Full 進度條：你只測過 stale Full（正確不顯示）；要用 `backup_completed >=` 保留的 Latest Full 的檔案確認階段進度與 "Processing time"。
2. Clear Everything：Catalogue 0 / Latest Full None / CSV 0，refresh 後不復活；之後舊 Full 可成為新 baseline。
3. 新 build 容器內 Processing Modules 完整 smoke：VMware text + 截圖補缺口、Proxmox OCR、Remove / Add module 後 refresh 與重啟仍保存。
4. "JSON Baseline" 以 `record_count > 0` 判斷：合法的全零 Full 會顯示 Cleared（邊緣案例）；改用 metadata / blob 是否存在較準。
5. Standards 的 `<small>` 字面顯示 bug 已改為 `:gray[kind]`，需確認新 build 已生效。
6. Copy All Descriptions 按鈕是否已移除（伺服器端按鈕無法寫剪貼簿；以 `st.code` 內建複製與 Download 為準）。
7. Minimal 後 IPAM 比對：Full-only IP 是否有結果、改動 Minimal 後結果是否更新（cache 失效實測）。
8. P2-2 的 `dynamic_` 分支：建 `dynamic_x` 與 `dynamicY` 於 `/tmp` 副本，確認只清 `dynamic_x`。
- [~] 刪除 legacy fallback（舊 get_backup_object_counts / get_choice_set_summary 分支）後，無 SharedBackupState 資料時 Backup contents 與 Choice sets 不顯示：確認是預期行為。
- [~] 完整 pytest 在 UI cleanup 後尚未跑（只跑過 test_no_undefined_names 與 test_import_graph，8 passed）；release 前在 /tmp 副本全跑。
- [~] data/naming_rules.yaml 於瀏覽器測試後被改寫：commit 前先 git diff 審查，只保留有意的改動，其餘 git checkout 還原。

---

## 4. 未做咩（Backlog）

P1
- [ ] Proxmox `/etc/network/interfaces` 文字 parser：新增 `proxmox_interfaces`（`input_kind: text`）並登記到 `PROCESSING_MODULES`，由 Standards 指派給 Proxmox。涵蓋 bridge-ports 多成員、bond-slaves 零遺漏、address，無推測值；最多 3 個測試。這是新 module 架構第一個跨平台實證。

P2
- [ ] `_normalize_rules()` 內 `DEFAULT_HYPERVISOR_PRESETS` 仍會補回缺失的 `hypervisor_presets` 平台：先 trace 該結構是否仍屬平台生命週期，再決定是否限縮（疑似第二條 auto-heal）。
- [ ] Platform module history：刪除平台後只記 `platform name -> module IDs`，同名重建時提示 Restore / Start Empty，不得靜默還原。
- [ ] Module reorder UI（順序即優先級，目前只能移除再新增）。
- [ ] 版本 drift：`APP_VERSION` 對齊 git tag。
- [ ] 新 Full 的 CSV 策略目前為保守全清；若要保留「比 Full 更新的 CSV」需另行設計 timestamp-based selective overlay。

P3
- [ ] Naming 資料處理段殘餘 "No Uplink" / "vmnic" 字面值，改讀 preset / token。
- [ ] `layout_geometry`：接線 extractor 或從 preset 刪除。
- [ ] `noise_filters` 13 個通用單字品質審查（防誤殺同名 Port Group）。
- [ ] 匯出腳本跳過 3 個非資料表 API 端點，並於 UI 標明失敗名稱。
- [ ] OCR Inspector 補信心度（conf）與座標框。
- [ ] Excel IPAM reconciliation script。
- [ ] Full payload 內重複 endpoint path 目前 first-wins，建議附 warning。
- [ ] pyflakes 既有警告（未使用變數 / 無佔位符 f-string）一次清理。
- [ ] Vault _reseed_counters casts "IP_12" to INT (always 0, reseed never effective); _SWITCH_NAME_RE and _PG_NAME_RE are unused (passes 10/11 bypassed) and _SWITCH_NAME_RE hard-codes site words ("PR ", "VLab").
- [ ] 同 kind 多 module 結果合併 / `execution_mode`（目前刻意不做）。
- [ ] 清走遺留 session key（`switch_deck`、`chips_unassigned`、`chips_session_corrections`）若 Clear All 流程仍引用。

P2-
- [ ] Standards 儲存流程重複：SSM.refresh_naming_rules() 約 16 處、rules.clear()/update() 約 7 處、nonce 遞增約 11 處；抽 _commit_rules() helper（已有多個真實重複，符合先有重複才抽象）。
- [ ] standards_tab 的 data-testid CSS（column / horizontalBlock / baseButton-secondary）在 Streamlit 1.66 可能已失效：DevTools 確認後刪除或改用 st.columns 參數。
- [ ] Naming：Visual Cards 與 Data Grid 雙重編輯同一份 records；決定 Cards 是否改唯讀摘要。

P3-
- [ ] naming_tab 以 radio 顯示文字當邏輯 key（如 naming_cat、Interactive Single Item Mode）：改 format_func + 常數 key 後才可改文案。
- [ ] IPAM st.tabs 兩個 tab 每次 rerun 都執行；改 radio + lazy render（Standards 已是此模式）。
- [ ] IPAM site fields：hide / add 字段需 ipam_engine 函數改收 values dict（目前為固定參數）；placeholder / help 尚未可編輯。
- [x] ipam_tab 從 naming_tab import _cached_fetch_toolbar_stats：移到共用模組。
- [x] Standards Hypervisor editor 混用即時保存（結構動作）與按 Save（行內編輯）：統一並於 caption 說明。
- [ ] components.py 失敗 endpoint 名單 popover（與既有「匯出腳本跳過 3 個非資料表 API 端點」合併處理，需 backup_manager 存名單）。
- [ ] Standards 內 _render_spatial_anchors_redaction_editor 無人呼叫、設定無讀取者（spatial_redactor 已刪）：刪除 editor 與設定 key。
- [ ] Raw text / Warnings Inspector 位於 Step 1 之外：收入 Step 1 或明確標示。
- [ ] Standards 偏好統一 Save / Reset 字眼與欄寬；preset 表格 / count badge HTML 重複約 6-8 次可抽 helper。

Note: factory token_normalizations 內有站點名（PR Spain Fuenmayor / Age VLab）；SOP 1.1 已豁免工廠預設 preset，列為 P3 觀察項，不屬違規。

---

## 5. 已知陷阱與教訓

- **Running container 可能是舊 code**：host source 已更新不代表 container 更新；用 `docker exec netbox-hub grep -n "<新字串>" /app/<檔案>` 驗證。不要 `docker cp` / 掛載原始碼快測。
- **`SUM(record_count)` 必須保留 `WHERE path IN (keys of _ENDPOINT_TO_OBJECT_TYPE)`**。曾有 agent 移除 filter 導致 Full 3023 → Minimal 16454 的跳水；agent 小型測試資料（9 行、全部 mapped）不會暴露此問題。任何涉及該查詢的 prompt 都要加這條 constraint。
- **UI 閘控變數先定義再使用**：`_img_mod_ids` 曾在定義前使用，造成 Naming tab `UnboundLocalError`。
- **token_order 可含非 pattern key**：`token_order` 現亦存 `ipam_site_fields` 等布局 key；`get_field_layout()` 需嚴格校驗 permutation（同 items、無重複、無未知）方可回傳 saved order。`ipam_site_fields` 不屬 naming_patterns，standards_tab 不 touch 此 key。
- **IPAM site fields 共用 'site' label**：`token_order` 的 `ipam_site_fields` 與 naming tab 共用同一 `pattern_variables['site'].label`；編輯任一方的 label 會同步反映，非 bug。
- **`data/naming_rules.yaml` 其實是 JSON**。
- **貼指令**：多行 `python -c`、heredoc 會被壓成一行；`<DB>` 之類佔位符要換成實際路徑；開頭有 `#` 的行不會執行；系統 `python3` 沒有 pandas，專案代碼要用 `/tmp/nbv/bin/python`。
- **瀏覽器上傳 vs server 處理**：上傳 ~90 MB 的 Full 時，瀏覽器到 server 的轉圈由 Streamlit uploader 控制，進度條只涵蓋 server 端處理。
- **Ctrl+V 區只收截圖**：文字經單行 input 傳輸會丟換行或被編碼，已移除文字分支。
- **Clear All JSON 後仍見 catalogue / CSV 是預期行為**；要全清用 Clear Everything。
- **刪除再新增平台 = 全新空 preset**，不自動復活；要找回舊設定用 git 還原 `data/naming_rules.yaml`（刪除前先 commit）。
- **舊測試數字（219）已過時**，以「全套通過」為準。
- **Agent 整檔重寫風險**：b316275 令 ui/components.py 約 2,600 行去縮排（IndentationError）且已 tag 推送。每次 agent 完成後必跑：for f in ui/components.py ui/tabs/*.py config/naming_rules.py utils/formatters.py; do python3 -m py_compile "$f" || echo FAIL $f; done，並比較 git diff --stat 與 git diff -w --stat；兩者差距巨大＝空白 / 縮排被整檔改動，不得 commit。
- **Agent 報告可能不實或籠統**（如把「pre-existing」當結論、合併報 3-x DONE）：以 git log / git diff / grep / docker logs 為準。
- **widget key 建立後再賦值 session_state（P6）與 st.dataframe width=None（P9）**：兩者只在 render 路徑（上傳 JSON 後）才爆，import / pytest 查不到。
- **host 的 /tmp/nbv 會消失（/tmp 清理）；host python3 沒有 streamlit / pyflakes**。檢查可用 docker run 一次性 container（見 SOP 8.2）。
- **Streamlit is not pinned in requirements.txt**; after each release record the real version: `docker exec netbox-hub pip freeze | grep -i -E '^(streamlit|pandas|openpyxl)='`.
- **data/naming_rules.yaml 會被瀏覽器測試改寫**（Standards 的 Save / Reset）；它屬 git 追蹤的設定，commit 前必須審 diff。
- **Code must never assume a pattern variable exists or hard-code variable names; derive from rules (e.g. get_domain_suffix_variables).**
- **New built-ins added to PATTERN_VARIABLES do not appear in existing installs; add them in Standards or use Reset to Defaults.**

---

## 6. AI Coding Agent 作業規則（Kilo / 免費模型）

- 一個 session 一個目標，完成即 commit；範圍以 diff 內容判斷，不以「恰好 N 個檔案」硬卡。
- 每 session 最多 2 個 production 檔、最多 4 個新測試（優先重用既有 fixture，不建大型合成備份）；使用者要求 no test 時只跑 `py_compile` 與必要 grep。
- 禁止顯示整檔 diff，只用 `git diff --stat` 與 `git diff -U2 -- <file>`；約 60K context 開始收尾，超過 80K 不發大型編輯。
- tool-call 格式錯誤（如 grep 缺 `pattern`）、API 401、或模型重複迷路 → 立即結束該 session，確認 `git status --short` 後開乾淨 session；grep 失敗一次就改用 view 行範圍，不重試。
- 標準 Task Prompt 開頭：
  `ONE TASK ONLY. Do not delete, move or rename any file. Do not run the full test suite. Do not write tests other than the ones asked. Do not plan other tasks. Output: changed lines, max 3 lines.`
- 涉及備份計數的 prompt 附加：`Constraint: SUM(record_count) in save_netbox_backup must keep WHERE path IN (keys of _ENDPOINT_TO_OBJECT_TYPE). Never remove that filter.`
- 嚴禁批次刪除 / 移動 `data/`（保護 `netbox-export-*.ps1` 與 `naming_rules.yaml`）。
- SQLite 寫入測試一律在 `/tmp` 副本；備份流程測試用 `/tmp/partc_test`（`run_upload.py` 內有路徑 assert）。
- Agent 報告需與實際 diff 對照：驗證用資料太小、只跑單一測試檔、只做 py_compile 的報告不當作完成證據。
- 禁止整檔重寫或整檔輸出；只用 str_replace 式小段編輯（<= 40 行），每次編輯後 py_compile；Phase 完成後再跑 compile loop。
- UI prompt 附加：Constraint: never assign st.session_state[key] after a widget with that key exists; st.dataframe width must be "stretch"; never rewrite or re-output a whole file.
- 多 Phase 工作用 'P1:'、'P2:' commit 前綴，session 中斷時以 git log --oneline -8 續做；約 90K token 時於 commit 後 CHECKPOINT 結束。
- 6.5 Pattern variables are seeded once; code must not assume any variable exists.

---

## 7. 發布流程

1. `git status --short` 確認只含預期檔案；按關注點分組 commit（platform/modules、backup、UI）。
2. `/tmp/nbv/bin/python -m pytest tests/ -q 2>&1 | tail -3` 全過。
3. 同步 `APP_VERSION` 與新 tag（不覆寫既有 tag）。
4. `docker build` / `docker push`（`<VERSION>` 與 `latest`），詳見 SOP 第 5 節。
5. 重建 container 後用 `docker exec ... grep` 驗證新 code，再做煙霧測試。
6. 回滾：compose 改回上一個 tag。

release 前：py_compile loop + docker import check + 全套 pytest（/tmp 副本）+ 瀏覽器逐 Tab 煙霧測試（上傳 JSON 與 CSV 路徑必測）。
不覆寫既有 tag；v4.8.83 的 ui/components.py 有縮排錯誤，修正用 v4.8.84。release 時順手把 config/constants.py::APP_VERSION 與 tag 對齊。
commit 只 add 預期檔案；不用 git add . / -A（data/ 內容需先審）。

---

## 8. 煙霧測試（約 10 分鐘）

0. `docker logs --since 5m netbox-hub 2>&1 | grep -i "crashed\|Traceback"` 無輸出。
1. 逐 Tab 切換（Naming、Standards、IPAM、Azure、Device、Rack、Batch、Module、Image）無紅字。
2. Azure：上傳 CSV → 預覽 → Clear Azure CSV → 上傳框清空。
3. Standards：
   - 刪 Microsoft Azure → rerun 不復活；新增自訂平台（modules 為空）可儲存 / 刪除；只剩一個平台時刪除鈕 disabled。
   - VMware = `vsphere_text` + `esxi_ocr`；Proxmox = `proxmox_ocr`；Remove / Add module 後 refresh 與重啟仍保存。
   - ⬆️⬇️ 排序後 YAML 有 `hypervisor_platform_order`，refresh / 重啟仍保持。
4. Naming：
   - VMware：貼 vSphere 文字 → 即時 preview → Analyze → Step 2；加缺口截圖得 `text+ocr`。
   - Proxmox：只有截圖區；移除 image module 後截圖區消失；全部移除後出現 caption 且 Analyze 停用。
   - Step 3 語法正常，Download 可用。
5. 備份（/tmp 副本）：
   - 較新 Full → 進度條逐階段前進 → "Processing time"；Authority status 顯示 Loaded / N endpoints / Latest Full。
   - 較舊 Full → `stale_full` 拒絕，catalogue / Latest Full / CSV 不變。
   - Minimal（只含少數 endpoint）→ Full 獨有 endpoint 保留；分項仍 9 項；`record_count` 與 inventory sum 一致、無跳水。
   - Full → CSV → Clear All JSON → CSV 與 catalogue 仍在，refresh 後 CSV 仍在 → Clear CSV → CSV 消失 → Clear Everything → Catalogue 0 / Latest Full None，refresh 後不復活 → 舊 Full 可成為新 baseline。
   - IPAM：Minimal 後含 Full-only IP 的 Excel 有比對結果；改動 Minimal 後重新比對結果更新。

---

## 9. 常用單行指令

```
git status --short
git diff --stat
python3 -m py_compile core/extractors/__init__.py config/naming_rules.py ui/tabs/naming_tab.py ui/tabs/standards_tab.py
/tmp/nbv/bin/python -m pytest tests/ -q 2>&1 | tail -3
docker exec netbox-hub grep -n "Processing Modules" /app/ui/tabs/standards_tab.py
docker logs --since 5m netbox-hub 2>&1 | grep -i "crashed\|Traceback"
for f in ui/components.py ui/tabs/*.py config/naming_rules.py utils/formatters.py; do python3 -m py_compile "$f" || echo FAIL $f; done; git diff --stat -- data/naming_rules.yaml; docker run --rm -e PYTHONDONTWRITEBYTECODE=1 -v <repo>:/work:ro -w /work --entrypoint sh <image> -c "python -c 'import ui.components, ui.tabs.ipam_tab, ui.tabs.naming_tab, ui.tabs.standards_tab'"
```
