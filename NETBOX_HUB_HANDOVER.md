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

## 2. 已決定（Decisions）

- Naming：Visual Cards 與 Data Grid **保留兩個都可編輯**。
- Streamlit 刻意**不 pin 版本**（為跟進安全修補）。UI 異常時先查 Streamlit 版本有否變動，再懷疑 code。每次 release 後記錄：`docker exec netbox-hub pip freeze | grep -i -E "^(streamlit|pandas|openpyxl)=="`。
- 工廠預設 `token_normalizations` 內的站點名（PR Spain Fuenmayor / Age VLab）：SOP 1.1 已豁免工廠預設 preset，列為 P3 觀察項，不屬違規。

## 3. 暫緩（Deferred）

- VM presets 共用一個 template（pattern_key `vm_host`，過渡設計）。若需 per-VM template：host_vm_presets 加 `kind: host|vm`，舊資料由 `vm_host` 推斷，更新 Standards（9 處）與 Naming（2 處）。不得新增 `vm_host` 比較。
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
- [ ] `_normalize_rules()` 內 `DEFAULT_HYPERVISOR_PRESETS` 仍會補回缺失的 `hypervisor_presets` 平台：先 trace 該結構是否仍屬平台生命週期，再決定是否限縮（疑似第二條 auto-heal）。
- [ ] Platform module history：刪除平台後只記 `platform name -> module IDs`，同名重建時提示 Restore / Start Empty，不得靜默還原。
- [ ] Module reorder UI（順序即優先級，目前只能移除再新增）。
- [ ] 版本 drift：`APP_VERSION` 對齊 git tag。
- [ ] 新 Full 的 CSV 策略目前為保守全清；若要保留「比 Full 更新的 CSV」需另行設計 timestamp-based selective overlay。
- [ ] Standards 儲存流程重複：`SSM.refresh_naming_rules()` 約 16 處、`rules.clear()/update()` 約 7 處、nonce 遞增約 11 處；抽 `_commit_rules()`。（待確認 T4 是否已做；以 grep 為準）

### P3
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
