## 1. 已完成（Done）

- [x] Naming radios use stable option keys; display text via format_func (labels can change safely).
- [x] Removed unused spatial anchors editor from Standards.
- [x] Host/VM hostname: unfilled required tokens show a 'Missing value for' warning (optional tokens ignored); parenthesis stripping removed.
- [x] Host type pattern key comes from the preset tuple; no hard-coded ESXi fallback.

## 2. 暫緩（Deferred）

- VM presets share one template via pattern_key 'vm_host' (interim design). If per-VM templates are needed: add kind: host|vm to host_vm_presets, infer from vm_host for old data, update Standards (9 refs) and Naming (2 refs). Do not add new 'vm_host' comparisons.
- Naming text/image slot layout unify: deferred. A previous attempt broke indentation (reverted in 317be36). If retried, do one slot per change.

## 3. 混亂（Pitfalls）

- [ ] Large re-indent edits by agents break files. Before every commit run: python3 -m py_compile <file> && git diff --stat && git diff -w --stat. No COMPILE_OK, no commit.

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
