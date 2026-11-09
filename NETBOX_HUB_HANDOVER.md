# NETBOX_HUB_HANDOVER

> 本檔係現況與待辦；架構規則以 ARCHITECTURE_SOP.md 為準。

## 0. Agent 作業規則（新 session 開頭貼這段）

- ONE TASK ONLY. 不刪除 / 搬移 / 改名任何檔案；不跑完整 test suite；不寫額外 test；不規劃其他任務。
- 預設啟用 ponytail（最小改動）：每次回覆末尾列出「skipped / 未驗證 / 風險」。
- 規則優先級：本檔與 SOP 的 HARD RULES 高於 ponytail。ponytail 不得以「少寫 code」為由把平台 / 廠商常數寫死進 Python（SOP 1.1 No-Hardcode）。
- 禁止整檔重寫；只做小段編輯，每次編輯後 `python3 -m py_compile <file>`。
- Agent 不做 git commit / 版本 bump / tag / push，由使用者自行處理。
- 不批次刪除或搬移 `data/`；SQLite 寫入測試只在 /tmp 副本執行。
- 每次 commit 前（由使用者）：`python3 -m py_compile <file> && git diff --stat && git diff -w --stat`；兩個 stat 差距巨大 = 縮排被整檔改動，不得 commit。

## 1. 已完成（Done）

- [x] Naming radios 使用穩定 option key，顯示文字走 format_func（可安全改文案）。
- [x] 移除 Standards 未使用的 spatial anchors editor。
- [x] Host/VM hostname：必填 token 未填時顯示 'Missing value for' warning（選填 token 忽略）；已移除括號剝除邏輯。
- [x] Host type pattern key 取自 preset tuple，無硬編碼 ESXi fallback。
- [x] standards_tab 失效 data-testid CSS 已刪除（commit 62521ec）；保留 `.action-header`、InputInstructions、stCheckbox/stTooltipIcon 三條。
- [x] `_cached_fetch_toolbar_stats` 已由 naming_tab 移到共用模組，ipam_tab 不再跨 tab import。
- [x] Standards Hypervisor editor 即時保存 / 按 Save 行為已統一並以 caption 說明。
- [x] Per-VM hostname presets implemented.
- [x] Host FQDN formatting moved to core.naming_dynamic_helper.format_generated_hostname; apply_case shared.
- [x] Data Bindings compact editor, optional segments [[...]], presentation in Patterns.
- [x] clear_backup_records completeness; minimal merged record_count; skipped/failed/duplicate endpoint reporting.
- [x] batch_tab.process_row try/except; _build_backup_index returns prefixes/IPs/sites/db_record_map.

## 1.5 Standards 配置生命週期（已驗證 Done）

- Current Rules 優先：最新儲存的 Current Rules 權威；缺失/不可讀時退回 Global Baseline；兩者皆不可用則回傳空安全 schema；Save & Apply 後 Current Rules 始終為權威。
- 無運行期工廠注入：DEFAULT_* 常數不再靜默填入使用者可見運行配置；Current Rules 與 Baseline 均缺失時回傳空安全 schema；不建立 ESXi、Proxmox、預設、變數、範本、雜訊過濾等內容；DEFAULT_* 僅供參考、遷移或明確範例。
- Global Baseline：Solidify 將最新儲存 Current Rules 原封不變做快照，不注入工廠內容、不修改 Current Rules；Restore All Standards to Baseline 還原整個 Baseline、刷新規則/快取、清 Standards 工作複本、遞增 standards_nonce、立即重新執行、無需 F5。
- Local Restore from Baseline：Naming/IPAM/Hypervisor/Hardware/Azure 分段重設皆讀取相同 Global Baseline；僅還原自身分段/預設集/範圍/平台/類別，保留無關 Current Rules；持久化、立即刷新快取與小工具狀態、清相關工作複本、遞增 nonce、無需 F5；本地與全域恢復僅範圍不同。
- Baseline 缺失/空值：Baseline 路徑缺失時顯示警告、Current Rules 不變、無工廠回退；Baseline 中明確空 list/dict 還原明確空值。
- 移除控制項與術語：Reset Unsaved Changes 已移除；Reset to Defaults / Reset / Factory Reset 已統一為 Restore from Baseline；Noise Filters 僅保留 Save & Apply Noise Filters 與 Restore from Baseline；未儲存 UI 編輯可由重載/導覽自然捨棄，不需專用重設按鈕。
- Noise Filters 已驗證：Baseline 26 個、Save 後 Current Rules 可 27 個、正常重載遵循最新 Current Rules、Restore from Baseline 回到 26 個、Restore All Standards to Baseline 還原完整規則集、本地與全域恢復立即更新 UI 無需 F5、舊 39 項 DEFAULT_UNIFIED_PLATFORM_PRESETS 不再為 Noise Filters 恢復來源。
- 空安全配置：標準空字典/列表防止崩潰、空配置屬有效、Standards 可明確建立第一個平台/預設集/變數、使用者明確空值被保留。
- 版本門禁：不變更版本或標籤；使用者擁有 commit、APP_VERSION 更新、標籤、推送、映像建置/發布。

## 2. 已決定（Decisions）

- Naming：Visual Cards 與 Data Grid **保留兩個都可編輯**。
- Streamlit 刻意**不 pin 版本**（為跟進安全修補）。UI 異常時先查 Streamlit 版本有否變動，再懷疑 code。每次 release 後記錄：`docker exec netbox-hub pip freeze | grep -i -E "^(streamlit|pandas|openpyxl)=="`。
- 工廠預設 `token_normalizations` 內的站點名（PR Spain Fuenmayor / Age VLab）：SOP 1.1 已豁免工廠預設 preset，列為 P3 觀察項，不屬違規。

## 3. 暫緩（Deferred）

- VM presets 共用一個 template（pattern_key `vm_host`，過渡設計）：若需 per-VM template，host_vm_presets 加 `kind: host|vm`，舊資料由 `vm_host` 推斷，並更新 Standards 約 9 處與 Naming 2 處；不得再新增 `vm_host` 比較。
- Naming text/image slot 骨架統一：暫緩。先前嘗試弄壞縮排（已於 317be36 revert）。重試時一次只改一個 slot。
- IPAM `st.tabs` 改 radio + lazy render：**目前不安全**。切換 view 後另一個 view 的 widget 不再 render，state 會被丟棄（Branch、Scope ID、Supernet、表格編輯）。需先把這些值另存為非 widget state。
- Proxmox `/etc/network/interfaces` 文字 parser：使用者決定稍後再做（見 P1）。

## 4. 陷阱（Pitfalls）

- Agent 大幅 re-indent 會弄壞檔案（`b316275` 事故：components.py 縮排被整檔改動並已 push 到 v4.8.83）。見第 0 節 commit 前檢查。
- `data/naming_rules.yaml` 納入 git；測試（Standards、Auto-Correction）可能寫入它，commit 前先 `git diff` 審視。
- compile / import 通過不等於可 release：runtime 錯誤（重複 widget key、session_state 賦值）只在瀏覽器出現。

## 5. 未做（Backlog）

### P1
- [ ] Proxmox `/etc/network/interfaces` 文字 parser：新增 `proxmox_interfaces`（`input_kind: text`）並登記到 `PROCESSING_MODULES`，由 Standards 指派給 Proxmox。涵蓋 bridge-ports 多成員、bond-slaves 零遺漏、address，無推測值；最多 3 個測試。（狀態：暫緩，由使用者決定時機）

### P2
- [ ] 版本 drift：`APP_VERSION` 對齊 git tag（release-time gate，使用者擁有）。
- [ ] 新 Full 的 CSV 策略目前為保守全清；若要保留「比 Full 更新的 CSV」需另行設計 timestamp-based selective overlay（P3 design decision）。
- [ ] Standards 儲存流程重複：Add normalization block calls SSM.set_naming_rules without refresh（P3 consistency review）。

### P3
- [ ] P3: in `_vlan_presets_editor`, Save only writes items and patterns of the SELECTED group; unsaved item edits in another group are lost when user switches group and saves. Structure changes (order, new, deleted groups) apply to all groups.
- [ ] P3: the `is_custom` branch in the Save block of `_vlan_presets_editor` is dead code (`is_custom` is never true), candidate for removal.
- [ ] P3: Noise Filters text_area key "txt_noise_filters_{active_plat}" has no nonce, so Reset may show stale text until F5. Status: not confirmed.
- [ ] Naming 資料處理段殘餘 "No Uplink" / "vmnic" 字面值，改讀 preset / token。
- [ ] `layout_geometry`：接線 extractor 或從 preset 刪除。
- [ ] `noise_filters` 13 個通用單字品質審查（防誤殺同名 Port Group）。
- [ ] 匯出腳本跳過 3 個非資料表 API 端點，並於 UI 標明失敗名稱；components.py 失敗 endpoint 名單 popover 與此合併處理（需 backup_manager 存名單）。
- [ ] OCR Inspector 補信心度（conf）與座標框。
- [ ] Excel IPAM reconciliation script。
- [ ] Full payload 內重複 endpoint path 目前 first-wins，建議附 warning。
- [ ] pyflakes 既有警告（未使用變數 / 無佔位符 f-string）一次清理。
- [ ] Vault `_reseed_counters` 將 "IP_12" cast 成 INT（恆為 0，reseed 從不生效）；`_SWITCH_NAME_RE`、`_PG_NAME_RE` 未使用（pass 10/11 被繞過），且 `_SWITCH_NAME_RE` 硬編碼站點字（"PR "、"VLab"）。
- [ ] 同 kind 多 module 結果合併 / `execution_mode`（目前刻意不做）。
- [ ] 清走遺留 session key（`switch_deck`、`chips_unassigned`、`chips_session_corrections`），若 Clear All 流程仍引用。
- [ ] IPAM site fields：hide / add 欄位需 `ipam_engine` 函數改收 values dict（目前為固定參數）；placeholder / help 尚未可編輯。
- [ ] Raw text / Warnings Inspector 位於 Step 1 之外：收入 Step 1 或明確標示。
- [ ] Standards 統一 Save / Reset 字眼與欄寬；preset 表格 / count badge HTML 重複約 6-8 次可抽 helper。
