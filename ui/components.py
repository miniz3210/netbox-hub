from typing import Callable

import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.ai_client import call_ai, test_model_connection, fetch_free_models
from core.backup_manager import (
    OBJECT_LABELS,
    clear_backup_records,
    get_backup_metadata,
    get_backup_object_counts,
    save_netbox_backup,
    set_backup_enabled,
)

CHAT_HEIGHT = 380

# Standalone PowerShell exporter that produces the NetBox_Backup_<timestamp>.json
# master file consumed by the Option A uploader below.
NETBOX_EXPORT_PS1 = r"""param(
    [Parameter(Mandatory = $true)]
    [string]$NetBoxUrl,

    [Parameter(Mandatory = $true)]
    [string]$ApiToken,

    [int]$PageSize = 2000
)

# Remove trailing slash
$NetBoxUrl = $NetBoxUrl.TrimEnd('/')

# API headers
$Headers = @{
    Authorization = "Token $ApiToken"
    Accept        = "application/json"
}

# Output file
$TimeStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$OutputFile = "NetBox_Backup_$TimeStamp.json"

# Storage object
$BackupData = [ordered]@{}

function Get-PaginatedData {
    param(
        [string]$Endpoint
    )

    $Results = @()
    $Url = "$NetBoxUrl/api/$Endpoint/?limit=$PageSize"

    do {

        Write-Host "Fetching: $Url"

        $Response = Invoke-RestMethod `
            -Uri $Url `
            -Method GET `
            -Headers $Headers `
            -ErrorAction Stop

        if ($Response.results) {

            $Results += $Response.results

            Write-Host ("Retrieved {0} records" -f $Results.Count)

            $Url = $Response.next
        }
        else {

            $Url = $null
        }

    } while ($Url)

    return $Results
}

# Test API connection
try {

    $Status = Invoke-RestMethod `
        -Uri "$NetBoxUrl/api/status/" `
        -Method GET `
        -Headers $Headers `
        -ErrorAction Stop

    Write-Host ""
    Write-Host "Connected to NetBox"
    Write-Host ("NetBox Version: {0}" -f $Status.'netbox-version')
    Write-Host ""
}
catch {

    Write-Error "Unable to connect to NetBox API"
    exit 1
}

# Endpoints to export
$Endpoints = @(
    "dcim/sites",
    "dcim/regions",
    "dcim/racks",
    "dcim/manufacturers",
    "dcim/device-types",
    "dcim/device-roles",
    "dcim/platforms",
    "dcim/devices",
    "dcim/interfaces",

    "ipam/vrfs",
    "ipam/vlans",
    "ipam/prefixes",
    "ipam/ip-addresses",

    "virtualization/clusters",
    "virtualization/virtual-machines",

    "tenancy/tenants",

    "circuits/providers",
    "circuits/circuits"
)

Write-Host "====================================="
Write-Host "STARTING NETBOX BACKUP"
Write-Host "====================================="

foreach ($Endpoint in $Endpoints) {

    Write-Host ""
    Write-Host "Exporting $Endpoint"

    try {

        $Key = $Endpoint.Replace("/", "_")

        $Data = Get-PaginatedData -Endpoint $Endpoint

        $BackupData[$Key] = $Data

        Write-Host ("Completed: {0} ({1} records)" -f $Endpoint, $Data.Count)
    }
    catch {

        Write-Warning ("Failed: {0}" -f $Endpoint)
        Write-Warning $_.Exception.Message
    }
}

Write-Host ""
Write-Host "Writing JSON backup..."

$BackupData |
    ConvertTo-Json -Depth 100 |
    Set-Content -Path $OutputFile -Encoding UTF8

Write-Host ""
Write-Host "====================================="
Write-Host "BACKUP COMPLETED"
Write-Host "====================================="
Write-Host "File : $OutputFile"
Write-Host ""
"""

def _clear_ai_chat(history_key: str, open_key: str) -> None:
    st.session_state[history_key] = []
    st.session_state[open_key] = True

def _keep_ai_chat_open(open_key: str) -> None:
    st.session_state[open_key] = True

def render_ai_chat(
    history_key: str,
    caption: str,
    placeholder: str,
    active_model: str,
    build_system_prompt: Callable[[str], str],
    label: str = "🤖 AI Assistant",
    height: int = CHAT_HEIGHT,
) -> None:
    """Render a self-contained AI chat panel.

    The transcript lives in a fixed-height scrolling container and the input box is
    rendered after it, so the input always stays at the bottom of the chat. Clearing
    and submitting both run as widget callbacks, which fire before the panel is drawn
    and therefore apply on the same run without an extra st.rerun().
    """
    open_key = f"{history_key}_open"
    if history_key not in st.session_state:
        st.session_state[history_key] = []
    if open_key not in st.session_state:
        st.session_state[open_key] = False

    with st.expander(label, expanded=st.session_state[open_key]):
        st.caption(caption)
        history = st.session_state[history_key]

        # Reserve the header row now, but render the button after this turn is
        # processed so its disabled state reflects the messages just added.
        _, c_clear = st.columns([3, 1])

        transcript = st.container(height=height, border=True)
        for message in history:
            with transcript.chat_message(message["role"]):
                st.markdown(message["content"])

        prompt = st.chat_input(
            placeholder,
            key=f"{history_key}_input",
            on_submit=_keep_ai_chat_open,
            args=(open_key,),
        )

        if prompt:
            history.append({"role": "user", "content": prompt})
            with transcript.chat_message("user"):
                st.markdown(prompt)

            with transcript.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        response = call_ai(
                            prompt,
                            active_model,
                            custom_system_msg=build_system_prompt(prompt),
                        )
                    except Exception as exc:
                        response = f"❌ AI Assistant temporarily unavailable: {exc}"
                st.markdown(response)

            history.append({"role": "assistant", "content": response})

        c_clear.button(
            "🗑️ Clear Chat",
            key=f"{history_key}_clear",
            on_click=_clear_ai_chat,
            args=(history_key, open_key),
            disabled=not history,
            width="stretch",
        )

def _handle_backup_upload(uploader_key: str, scope_key: str) -> None:
    uploaded = st.session_state.get(uploader_key)
    result_key = f"backup_upload_result_{scope_key}"
    error_key = f"backup_upload_error_{scope_key}"

    if not uploaded:
        return

    files = uploaded if isinstance(uploaded, list) else [uploaded]
    for file_obj in files:
        try:
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            result = save_netbox_backup(file_obj, filename=file_obj.name)
        except Exception as exc:
            st.session_state[error_key] = f"**{file_obj.name}**: {exc}"
            st.session_state[result_key] = None
            return

        st.session_state[error_key] = ""
        st.session_state[result_key] = result
        # Keep the enable checkbox in step with the freshly ingested file.
        st.session_state[f"netbox_backup_enabled_{scope_key}"] = True


def _handle_backup_toggle(checkbox_key: str) -> None:
    set_backup_enabled(bool(st.session_state.get(checkbox_key)))


def _handle_backup_clear(scope_key: str) -> None:
    clear_backup_records()
    st.session_state[f"backup_upload_result_{scope_key}"] = None
    st.session_state[f"backup_upload_error_{scope_key}"] = ""
    st.session_state.pop(f"netbox_backup_enabled_{scope_key}", None)


def render_backup_uploader(scope_key: str) -> dict:
    """Render the NetBox master backup (JSON) upload block.

    Shows the upload date and an enable checkbox that controls whether the AI
    Assistant is allowed to read the backup. Returns the backup metadata dict.
    """
    meta = get_backup_metadata()
    uploader_key = f"netbox_backup_uploader_{scope_key}"
    checkbox_key = f"netbox_backup_enabled_{scope_key}"
    result_key = f"backup_upload_result_{scope_key}"
    error_key = f"backup_upload_error_{scope_key}"

    st.markdown("**Option A: Upload Netbox_Backup**")
    st.caption(
        "Upload the full `NetBox_Backup_<timestamp>.json` master export. "
        "Every object (sites, devices, interfaces, VLANs, prefixes, IPs, VMs, "
        "clusters, tenants, circuits) becomes searchable by the AI Assistant."
    )

    st.markdown("**Step 1 — Generate `NetBox_Backup.json` with PowerShell:**")
    st.code(
        '.\\netbox-export.ps1 -NetBoxUrl "https://xxxx" -ApiToken "xxxx"',
        language="powershell",
    )
    st.download_button(
        "⬇️ Download netbox-export.ps1",
        NETBOX_EXPORT_PS1,
        file_name="netbox-export.ps1",
        mime="text/plain",
        key=f"dl_export_ps1_{scope_key}",
    )
    with st.expander("📄 View netbox-export.ps1", expanded=False):
        st.code(NETBOX_EXPORT_PS1, language="powershell")

    st.markdown("**Step 2 — Upload the generated JSON file:**")
    st.file_uploader(
        "Upload NetBox master backup (JSON)",
        type=["json"],
        accept_multiple_files=False,
        key=uploader_key,
        on_change=_handle_backup_upload,
        args=(uploader_key, scope_key),
        label_visibility="collapsed",
    )

    error = st.session_state.get(error_key)
    if error:
        st.error(f"❌ {error}")

    result = st.session_state.get(result_key)
    if result:
        st.success(
            f"✅ Ingested {result['total']} NetBox objects "
            f"({result['sites']} sites, {result['ipam']} IPAM records, "
            f"{result['devices']} devices, {result['vms']} VMs)."
        )
        st.session_state[result_key] = None

    if not meta["loaded"]:
        st.caption("⚪ No backup file uploaded — AI Assistant uses CSV/agent data only.")
        return meta

    # The database holds the authoritative enabled flag; seed the widget from it.
    if checkbox_key not in st.session_state:
        st.session_state[checkbox_key] = meta["enabled"]

    c_chk, c_clr = st.columns([3, 1])
    with c_chk:
        st.checkbox(
            f"📦 `{meta['filename']}` — uploaded {meta['uploaded_at']} "
            f"({meta['record_count']} objects)",
            key=checkbox_key,
            on_change=_handle_backup_toggle,
            args=(checkbox_key,),
            help="Tick to let the AI Assistant read this backup file. Untick to exclude it without deleting.",
        )
    with c_clr:
        st.button(
            "🗑️ Remove Backup",
            key=f"btn_clear_backup_{scope_key}",
            on_click=_handle_backup_clear,
            args=(scope_key,),
            width="stretch",
        )

    counts = get_backup_object_counts()
    if counts:
        with st.expander(f"📊 Backup contents ({len(counts)} object types)", expanded=False):
            for object_type, count in counts.items():
                label = OBJECT_LABELS.get(object_type, object_type.replace("_", " ").title())
                st.markdown(f"* **{label}**: `{count}`")

    if not meta["enabled"]:
        st.warning("⚠️ Backup is uploaded but excluded from AI Assistant lookups.", icon="⚠️")

    return meta


def render_sidebar() -> str:
    with st.sidebar:
        st.header("⚙️ AI Engine Selection")
        
        # 1. Preset Models from environment
        selected_preset = st.selectbox(
            "Preset Models",
            options=AVAILABLE_MODELS,
            index=0,
            help="Configured environment presets."
        )

        # 2. Load and cache free models list (only when user clicks refresh)
        if "free_models_cache" not in st.session_state:
            st.session_state["free_models_cache"] = []
        if "models_loaded" not in st.session_state:
            st.session_state["models_loaded"] = False
        
        # Use cached models (empty by default until refresh is clicked)
        test_models = st.session_state["free_models_cache"]
        
        # Filter candidate models to exclude any already in Preset Models
        filtered_suggestions = [
            m for m in test_models 
            if m not in AVAILABLE_MODELS
        ]

        # Quick-Select Test Model Pull-Down Menu
        col1, col2 = st.columns([4, 1])
        with col1:
            if not st.session_state["models_loaded"]:
                placeholder = "-- Click refresh to load models --"
            elif len(filtered_suggestions) == 0:
                placeholder = "-- No free models available --"
            else:
                placeholder = "-- Select a model --"
            
            quick_pick = st.selectbox(
                "Quick-Select Test Model",
                options=[placeholder] + filtered_suggestions,
                index=0,
                help="Click the refresh button to load free models from API."
            )
        with col2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔄", key="btn_refresh_models", help="Refresh model list"):
                with st.spinner("Loading free models..."):
                    fetched_models = fetch_free_models()
                    st.session_state["free_models_cache"] = fetched_models
                    st.session_state["models_loaded"] = True
                    if len(fetched_models) == 0:
                        st.warning("No free models found. Check logs for details.")
                    else:
                        st.success(f"Loaded {len(fetched_models)} free models")
                st.rerun()

        # 3. Custom Manual Input
        default_manual = "" if quick_pick.startswith("--") else quick_pick
        custom_model = st.text_input(
            "Custom Model",
            value=default_manual,
            placeholder="Type or edit model slug...",
            help="Overrides preset when populated."
        ).strip()

        # Active Model Resolution
        active_model = custom_model if custom_model else selected_preset

        # Track model test results in session state
        if "model_test_history" not in st.session_state:
            st.session_state["model_test_history"] = {}

        # 4. Connection Test Button
        if st.button("🧪 Test Model Connection", key="btn_ping_model", use_container_width=True):
            with st.spinner(f"Testing `{active_model}`..."):
                ok, latency, msg = test_model_connection(active_model)
                st.session_state["model_test_history"][active_model] = {
                    "ok": ok,
                    "latency": latency,
                    "msg": msg
                }

        # 5. Active Model Card with Latency or Strikethrough
        history = st.session_state["model_test_history"]
        if active_model in history:
            res = history[active_model]
            if res["ok"]:
                st.success(f"**Selected:**\n`{active_model}` — ⚡ **{res['latency']}ms**")
            else:
                st.error(f"**Selected:**\n~~`{active_model}`~~ ❌ *(Offline)*\n\n`{res['msg']}`")
        else:
            st.info(f"**Selected:**\n`{active_model}`")

        # 6. Test Results History Log
        if history:
            with st.expander("📋 Model Test Log", expanded=False):
                for m_name, data in history.items():
                    if data["ok"]:
                        st.markdown(f"• `{m_name}`: 🟢 **{data['latency']}ms**")
                    else:
                        st.markdown(f"• ~~`{m_name}`~~: 🔴 **Fail**")

        st.caption(f"🔌 Routed via **OmniRoute** (`{OPENROUTER_BASE_URL}`)")

    return active_model