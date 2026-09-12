from pathlib import Path
from typing import Callable
from datetime import datetime

import streamlit as st
from config.constants import APP_VERSION
from config.settings import AVAILABLE_MODELS, OPENROUTER_BASE_URL
from core.ai_client import call_ai, test_model_connection, fetch_free_models
from core.backup_manager import (
    CSV_FILENAMES,
    OBJECT_LABELS,
    clear_backup_records,
    get_backup_metadata,
    get_backup_object_counts,
    get_choice_set_summary,
    save_netbox_backup,
    set_backup_enabled,
)
from core.shared_backup_state import SharedBackupState

CHAT_HEIGHT = 380

# The PowerShell exporters ship in the repo and are read from disk so the scripts and
# the download buttons can never drift apart. They walk the NetBox REST API and
# write NetBox_Full_Backup_<timestamp>.json or NetBox_Minimal_Backup_<timestamp>.json.
NETBOX_EXPORT_FULL_PS1_PATH = Path(__file__).resolve().parent.parent / "data" / "netbox-export-full.ps1"
NETBOX_EXPORT_MIN_PS1_PATH = Path(__file__).resolve().parent.parent / "data" / "netbox-export-min.ps1"

_PS1_MISSING = (
    "# netbox-export script was not found in this deployment.\n"
    "# Expected at: data/netbox-export-full.ps1 or data/netbox-export-min.ps1\n"
)


@st.cache_data(show_spinner=False)
def _load_export_script(path_str: str, mtime: float) -> str:
    """Read the exporter from disk. `mtime` busts the cache when the file changes."""
    try:
        # utf-8-sig strips the BOM PowerShell editors add, which would otherwise
        # render as a stray character at the top of the code block.
        return Path(path_str).read_text(encoding="utf-8-sig")
    except OSError:
        return _PS1_MISSING


def get_netbox_export_script(script_type: str = "full") -> str:
    """Get the PowerShell export script content.
    
    Args:
        script_type: Either "full" or "min" to select which script to load
    """
    if script_type == "min":
        script_path = NETBOX_EXPORT_MIN_PS1_PATH
    else:
        script_path = NETBOX_EXPORT_FULL_PS1_PATH
    
    try:
        mtime = script_path.stat().st_mtime
    except OSError:
        return _PS1_MISSING
    return _load_export_script(str(script_path), mtime)


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

def _handle_json_backup_upload(uploader_key: str, scope_key: str) -> None:
    """Handle JSON backup file upload only - optimized for speed."""
    uploaded = st.session_state.get(uploader_key)
    result_key = f"backup_upload_result_{scope_key}"
    error_key = f"backup_upload_error_{scope_key}"

    if not uploaded:
        return

    file_obj = uploaded
    file_size_mb = file_obj.size / (1024 * 1024) if hasattr(file_obj, 'size') else 0
    
    # Show progress for large files
    progress_placeholder = st.empty()
    progress_bar = st.progress(0)
    
    def progress_callback(message: str):
        """Update Streamlit UI with progress."""
        progress_placeholder.info(f"⏳ {message}")
        if "Parsing JSON" in message:
            progress_bar.progress(20)
        elif "Organizing" in message:
            progress_bar.progress(40)
        elif "Ingesting" in message or "Processing" in message:
            progress_bar.progress(60)
        elif "Committing" in message:
            progress_bar.progress(80)
        elif "Complete" in message:
            progress_bar.progress(100)
    
    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        
        # Read and parse JSON for dynamic inspection
        import json
        file_content = file_obj.read()
        
        # Parse JSON and load into dynamic backup state + schema registry
        try:
            # Handle UTF-8 BOM if present (common in Windows-generated files)
            content_str = file_content.decode('utf-8-sig')
            backup_data = json.loads(content_str)
            SharedBackupState.load_backup(backup_data, file_obj.name)
            
            # Initialize universal schema registry from backup
            from core.universal_schema_registry import initialize_schema_registry_from_uploaded_file
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            registry = initialize_schema_registry_from_uploaded_file(file_obj)
        except json.JSONDecodeError as e:
            st.session_state[error_key] = f"**{file_obj.name}**: Invalid JSON - {e}"
            progress_placeholder.empty()
            progress_bar.empty()
            return
        except UnicodeDecodeError as e:
            st.session_state[error_key] = f"**{file_obj.name}**: File encoding error - {e}"
            progress_placeholder.empty()
            progress_bar.empty()
            return
        
        # Reset for save_netbox_backup
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        
        with st.spinner(f'Uploading {file_obj.name} ({file_size_mb:.1f} MB)...'):
            result = save_netbox_backup(
                file_obj, 
                filename=file_obj.name,
                enable_schema_discovery=False,  # Fast upload - schema already initialized above
                progress_callback=progress_callback
            )
        
        # Clear progress indicators
        progress_placeholder.empty()
        progress_bar.empty()
            
    except Exception as exc:
        progress_placeholder.empty()
        progress_bar.empty()
        st.session_state[error_key] = f"**{file_obj.name}**: {exc}"
        st.session_state[result_key] = None
        return

    st.session_state[error_key] = ""
    st.session_state[result_key] = result
    st.session_state[f"netbox_backup_enabled_{scope_key}"] = True
    
    # Clear the file uploader by incrementing the counter
    counter_key = f"json_uploader_counter_{scope_key}"
    st.session_state[counter_key] = st.session_state.get(counter_key, 0) + 1


def _handle_csv_backup_upload(uploader_key: str, scope_key: str) -> None:
    """Handle CSV/Excel file uploads with user confirmation for endpoint classification."""
    uploaded = st.session_state.get(uploader_key)
    error_key = f"backup_upload_error_{scope_key}"

    if not uploaded:
        return

    files = uploaded if isinstance(uploaded, list) else [uploaded]
    
    # Store pending files for confirmation
    pending_key = f"csv_pending_confirmation_{scope_key}"
    st.session_state[pending_key] = {
        'files': files,
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Trigger rerun to show confirmation UI
    st.rerun()


def _process_confirmed_csv_uploads(scope_key: str, confirmed_classifications: dict) -> None:
    """Process CSV uploads after user confirmation."""
    from core.universal_uploader import UniversalUploader
    
    pending_key = f"csv_pending_confirmation_{scope_key}"
    error_key = f"backup_upload_error_{scope_key}"
    
    pending_data = st.session_state.get(pending_key)
    if not pending_data:
        return
    
    files = pending_data['files']
    uploaded_at = pending_data['timestamp']
    
    try:
        uploader = UniversalUploader()
        
        with st.spinner(f'Processing {len(files)} confirmed file(s)...'):
            upload_result = uploader.process_uploaded_files_with_overrides(
                files, 
                confirmed_classifications
            )
        
        if upload_result['errors']:
            st.session_state[error_key] = "\n".join(upload_result['errors'])
            return
        
        # Update SharedBackupState for dynamic backup display
        source_files = ", ".join([f.name for f in files])
        
        for model_key, count in upload_result['by_model'].items():
            # Map model keys to endpoint paths for SharedBackupState
            endpoint = model_key.replace('.', '/')
            SharedBackupState.add_csv_override(endpoint, count, source_files, uploaded_at)
        
        # Show success message
        summary = uploader.get_processing_summary(upload_result)
        st.toast(summary, icon="✅")
        
    except Exception as exc:
        st.session_state[error_key] = f"Upload failed: {exc}"
        return

    st.session_state[error_key] = ""
    
    # Clear pending confirmation
    del st.session_state[pending_key]
    
    # Clear the file uploader by incrementing the counter
    counter_key = f"csv_uploader_counter_{scope_key}"
    st.session_state[counter_key] = st.session_state.get(counter_key, 0) + 1


def _handle_remove_csv_entry(endpoint: str, scope_key: str):
    """Remove a specific CSV entry from the backup registry."""
    if SharedBackupState.remove_csv_entry(endpoint):
        st.toast(f"Removed {endpoint}", icon="🗑️")
    else:
        st.error(f"Could not remove {endpoint} (may be from JSON backup)")


def _handle_backup_toggle(checkbox_key: str) -> None:
    set_backup_enabled(bool(st.session_state.get(checkbox_key)))


def _handle_backup_clear(scope_key: str) -> None:
    clear_backup_records()
    SharedBackupState.clear()  # Clear dynamic backup state
    
    # Clear Schema Registry from session state
    if "netbox_runtime_signatures" in st.session_state:
        del st.session_state["netbox_runtime_signatures"]
    
    st.session_state[f"backup_upload_result_{scope_key}"] = None
    st.session_state[f"backup_upload_error_{scope_key}"] = ""
    st.session_state.pop(f"netbox_backup_enabled_{scope_key}", None)


def _handle_csv_clear(scope_key: str) -> None:
    """Clear only CSV data, preserving JSON backup."""
    count = SharedBackupState.clear_csv_only()
    if count > 0:
        st.toast(f"🗑️ Cleared {count} CSV entries", icon="✅")
    else:
        st.toast("No CSV data to clear", icon="ℹ️")


def _render_json_backup_section(scope_key: str, meta: dict, json_uploader_key: str, 
                                checkbox_key: str, result_key: str, error_key: str) -> None:
    """Render Step 1: JSON Backup upload, PowerShell scripts, and status display."""
    # Get both scripts (function is defined in this same file)
    export_script_full = get_netbox_export_script("full")
    export_script_min = get_netbox_export_script("min")

    st.markdown("**Step 1 — Generate & Upload NetBox JSON Backup (PowerShell)**")
    st.caption("Upload JSON backup to initialize the schema registry and enable automatic CSV classification.")
    
    col_full, col_min = st.columns(2)
    
    with col_full:
        st.markdown("**Full Backup (All Data)**")
        st.code(
            '.\\netbox-export-full.ps1 -NetBoxUrl "https://netbox.example.com" -ApiToken "<TOKEN>"',
            language="powershell",
        )
        st.download_button(
            "⬇️ Download netbox-export-full.ps1",
            export_script_full,
            file_name="netbox-export-full.ps1",
            mime="text/plain",
            key=f"dl_export_full_ps1_{scope_key}",
            width="stretch",
        )
        with st.expander("📄 View netbox-export-full.ps1", expanded=False):
            st.code(export_script_full, language="powershell")
    
    with col_min:
        st.markdown("**Minimal Backup (Essential Only)**")
        st.code(
            '.\\netbox-export-min.ps1 -NetBoxUrl "https://netbox.example.com" -ApiToken "<TOKEN>"',
            language="powershell",
        )
        st.download_button(
            "⬇️ Download netbox-export-min.ps1",
            export_script_min,
            file_name="netbox-export-min.ps1",
            mime="text/plain",
            key=f"dl_export_min_ps1_{scope_key}",
            width="stretch",
        )
        with st.expander("📄 View netbox-export-min.ps1", expanded=False):
            st.code(export_script_min, language="powershell")
    
    st.caption(
        "**Full backup** exports all 145+ endpoints (audit logs, jobs, users, plugins). "
        "**Minimal backup** exports 68 essential endpoints only (sites, devices, IPAM, VMs, config). "
        "Both support `-PageSize 1000` and `-OutputDirectory .` options."
    )

    # JSON Upload Section
    st.file_uploader(
        "📦 Upload NetBox JSON Backup (Full or Minimal)",
        type=["json"],
        accept_multiple_files=False,
        key=json_uploader_key,
        on_change=_handle_json_backup_upload,
        args=(json_uploader_key, scope_key),
        help="Upload the JSON backup file generated by PowerShell export scripts."
    )

    error = st.session_state.get(error_key)
    if error:
        st.error(f"❌ {error}")

    result = st.session_state.get(result_key)
    if result:
        message = (
            f"✅ Ingested {result['total']} NetBox objects across "
            f"{result.get('object_types', 0)} object types "
            f"({result['sites']} sites, {result['ipam']} IPAM records, "
            f"{result['devices']} devices, {result['vms']} VMs"
        )
        if result.get("choice_values"):
            message += f", {result['choice_values']} custom field choices"
        st.success(message + ").")
        st.session_state[result_key] = None

    if not meta["loaded"]:
        st.caption("⚪ No backup file uploaded — AI Assistant uses CSV/agent data only.")
    else:
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
                "🗑️ Clear All JSON",
                key=f"btn_clear_backup_{scope_key}",
                on_click=_handle_backup_clear,
                args=(scope_key,),
                width="stretch",
                help="Clear all JSON backup data from backup_records table",
            )

        source = meta.get("source_info") or {}
        if source:
            bits = []
            if source.get("netbox_url"):
                bits.append(f"Source: `{source['netbox_url']}`")
            if source.get("netbox_version"):
                bits.append(f"NetBox `{source['netbox_version']}`")
            if source.get("successful_endpoints") is not None:
                bits.append(
                    f"Endpoints: {source['successful_endpoints']}/"
                    f"{source.get('endpoints_processed', '?')} OK"
                )
            if source.get("failed_endpoints"):
                bits.append(f"⚠️ {source['failed_endpoints']} endpoint(s) failed")
            if bits:
                st.caption(" | ".join(bits))

        if not meta["enabled"]:
            st.warning("⚠️ Backup is uploaded but excluded from AI Assistant lookups.", icon="⚠️")


def _render_backup_contents_section(scope_key: str, meta: dict) -> None:
    """Render the Backup contents expander with grouped endpoints."""
    # Try to restore dynamic state from database if needed
    if not SharedBackupState.has_backup() and meta["loaded"]:
        # JSON backup exists in DB but not in session state - restore it
        try:
            from core.backup_manager import restore_backup_to_session_state
            restore_backup_to_session_state()
        except:
            pass  # Fall back to legacy system if restore fails
    
    if SharedBackupState.has_backup():
        # Use dynamic inspection system
        object_registry = SharedBackupState.get_object_registry()
        object_count = len(object_registry)
        
        with st.expander(f"📋 Backup contents ({object_count} object types - dynamic)", expanded=False):
            st.caption("🟡 = Essential endpoint (minimal backup) | 📦 = JSON backup | 📊 = CSV upload")
            
            # Group endpoints by prefix (dcim/, ipam/, extras/, etc.)
            grouped_objects = {}
            ungrouped_objects = []
            
            for endpoint, metadata in object_registry.items():
                ep = str(endpoint)
                if "/" in ep:
                    prefix = ep.split("/")[0]
                    if prefix not in grouped_objects:
                        grouped_objects[prefix] = []
                    grouped_objects[prefix].append((endpoint, metadata))
                else:
                    ungrouped_objects.append((endpoint, metadata))
            
            # Sort groups by prefix, then sort items within each group by label
            sorted_groups = sorted(grouped_objects.items())
            for prefix, items in sorted_groups:
                items.sort(key=lambda x: x[1]["label"])
            
            # Sort ungrouped items by label
            ungrouped_objects.sort(key=lambda x: x[1]["label"])
            
            # Display in 2 columns for compact view
            col1, col2 = st.columns(2)
            
            all_items = []
            # Add grouped items with prefix headers
            for prefix, items in sorted_groups:
                all_items.extend(items)
            # Add ungrouped items at the end
            all_items.extend(ungrouped_objects)
            
            mid_point = (len(all_items) + 1) // 2  # Round up for odd numbers
            
            # Essential endpoints (from minimal backup script)
            ESSENTIAL_ENDPOINTS = {
                # DCIM - Core infrastructure
                "dcim/regions", "dcim/site-groups", "dcim/sites", "dcim/locations",
                "dcim/rack-roles", "dcim/rack-groups", "dcim/rack-types", "dcim/racks",
                "dcim/manufacturers", "dcim/platforms", "dcim/device-roles", "dcim/device-types",
                "dcim/devices", "dcim/interfaces", "dcim/cables", "dcim/cable-terminations",
                "dcim/console-ports", "dcim/console-server-ports", "dcim/power-ports",
                "dcim/power-outlets", "dcim/power-panels", "dcim/power-feeds",
                "dcim/modules", "dcim/module-types", "dcim/module-bays",
                # IPAM - IP Address Management
                "ipam/rirs", "ipam/asn-ranges", "ipam/asns", "ipam/aggregates",
                "ipam/roles", "ipam/vrfs", "ipam/prefixes", "ipam/ip-ranges",
                "ipam/ip-addresses", "ipam/vlan-groups", "ipam/vlans",
                "ipam/service-templates", "ipam/services", "ipam/fhrp-groups",
                "ipam/fhrp-group-assignments",
                # Virtualization
                "virtualization/cluster-types", "virtualization/cluster-groups",
                "virtualization/clusters", "virtualization/virtual-machine-types",
                "virtualization/virtual-machines", "virtualization/interfaces",
                "virtualization/virtual-disks",
                # Tenancy
                "tenancy/tenant-groups", "tenancy/tenants", "tenancy/contact-groups",
                "tenancy/contact-roles", "tenancy/contacts", "tenancy/contact-assignments",
                # Circuits
                "circuits/providers", "circuits/provider-accounts", "circuits/provider-networks",
                "circuits/circuit-types", "circuits/circuits", "circuits/circuit-terminations",
                # Extras - Configuration only
                "extras/tags", "extras/custom-fields", "extras/custom-field-choice-sets",
                "extras/config-contexts", "extras/config-templates"
            }
            
            def render_item(endpoint, metadata):
                label = metadata["label"]
                count = metadata["count"]
                timestamp = metadata["timestamp"]
                source_type = metadata.get("source_type", "json")
                
                # Determine icon based on source
                is_csv = source_type == "csv" or metadata.get("source", "").lower().endswith((".csv", ".xlsx"))
                icon = "📊" if is_csv else "📦"
                
                # Check if endpoint is essential (minimal backup)
                ep = metadata.get("endpoint", "")
                is_essential = ep in ESSENTIAL_ENDPOINTS
                essential_marker = " 🟡" if is_essential else ""
                
                # Format timestamp as dd-mm-yy HH:mm
                try:
                    if isinstance(timestamp, str):
                        # Parse timestamp: "2026-09-11 09:31:52" or similar
                        dt = datetime.fromisoformat(timestamp.replace("UTC", "").strip())
                        compact_time = dt.strftime("%d-%m-%y %H:%M")
                    else:
                        compact_time = str(timestamp)
                except:
                    compact_time = str(timestamp)[:16] if timestamp else "N/A"
                
                # Show endpoint path in parentheses
                example = f" ({ep})" if ep else ""
                
                # Create a row with remove button if CSV
                if is_csv:
                    col_text, col_btn = st.columns([19, 1])
                    with col_text:
                        st.caption(f"**{label}**: {count} {icon}{essential_marker}{example} `{compact_time}`")
                    with col_btn:
                        st.markdown("<div style='margin-top:-8px'></div>", unsafe_allow_html=True)
                        if st.button("×", key=f"remove_{endpoint}_{scope_key}", help=f"Remove {label}"):
                            _handle_remove_csv_entry(endpoint, scope_key)
                            st.rerun()
                else:
                    st.caption(f"**{label}**: {count} {icon}{essential_marker}{example} `{compact_time}`")
            
            with col1:
                current_prefix = None
                for endpoint, metadata in all_items[:mid_point]:
                    ep = str(endpoint)
                    if "/" in ep:
                        prefix = ep.split("/")[0]
                        if prefix != current_prefix:
                            st.markdown(f"**{prefix.upper()}/**")
                            current_prefix = prefix
                    render_item(endpoint, metadata)
            
            with col2:
                current_prefix = None
                for endpoint, metadata in all_items[mid_point:]:
                    ep = str(endpoint)
                    if "/" in ep:
                        prefix = ep.split("/")[0]
                        if prefix != current_prefix:
                            st.markdown(f"**{prefix.upper()}/**")
                            current_prefix = prefix
                    render_item(endpoint, metadata)
    else:
        # Fall back to legacy static counts
        counts = get_backup_object_counts()
        if counts:
            with st.expander(f"📊 Backup contents ({len(counts)} object types - legacy)", expanded=False):
                for object_type, (count, timestamp, source) in counts.items():
                    label = OBJECT_LABELS.get(object_type, object_type.replace("_", " ").title())
                    csv_filename = CSV_FILENAMES.get(object_type, "")
                    
                    if csv_filename:
                        # Show with CSV filename for required data
                        st.markdown(f"* **{label}** (`{csv_filename}`): `{count}` — {source} `{timestamp}`")
                    else:
                        # Show without CSV filename for other data
                        st.markdown(f"* **{label}**: `{count}` — {source} `{timestamp}`")


def _render_choice_sets_section() -> None:
    """Render the Custom field choice sets expander."""
    # Show custom field choice sets dynamically
    if SharedBackupState.has_backup():
        choice_sets = SharedBackupState.get_choice_sets()
        if choice_sets:
            with st.expander(
                f"⚙️ Custom field choice sets ({len(choice_sets)})", expanded=False
            ):
                st.caption(
                    "Dynamically discovered from backup. These are the "
                    "authoritative values used when checking whether custom field values "
                    "already exist in NetBox."
                )
                
                # Display in 2 columns for compact view
                sorted_sets = sorted(choice_sets.items(), key=lambda x: x[1]["name"])
                
                # Split into two columns
                col1, col2 = st.columns(2)
                mid_point = (len(sorted_sets) + 1) // 2  # Round up for odd numbers
                
                with col1:
                    for set_name, set_data in sorted_sets[:mid_point]:
                        label = set_data["label"]
                        field_key = set_data["field_key"]
                        count = set_data["count"]
                        timestamp = set_data["timestamp"]
                        source_type = set_data.get("source_type", "json")
                        
                        # Determine icon based on source
                        if source_type == "csv" or set_data.get("source", "").lower().endswith(".csv"):
                            icon = "📊"
                        else:
                            icon = "📦"
                        
                        # Custom field choice sets are essential (extras/custom-field-choice-sets)
                        essential_marker = " 🟡"
                        
                        # Format timestamp as dd-mm-yy HH:mm
                        try:
                            from datetime import datetime
                            if isinstance(timestamp, str):
                                dt = datetime.fromisoformat(timestamp.replace("UTC", "").strip())
                                compact_time = dt.strftime("%d-%m-%y %H:%M")
                            else:
                                compact_time = str(timestamp)
                        except:
                            compact_time = str(timestamp)[:16] if timestamp else "N/A"
                        
                        # Compact format: Label → field: count icon time
                        st.caption(f"**{label}** → `{field_key}`: {count} {icon}{essential_marker} `{compact_time}`")
                
                with col2:
                    for set_name, set_data in sorted_sets[mid_point:]:
                        label = set_data["label"]
                        field_key = set_data["field_key"]
                        count = set_data["count"]
                        timestamp = set_data["timestamp"]
                        source_type = set_data.get("source_type", "json")
                        
                        # Determine icon based on source
                        if source_type == "csv" or set_data.get("source", "").lower().endswith(".csv"):
                            icon = "📊"
                        else:
                            icon = "📦"
                        
                        # Custom field choice sets are essential (extras/custom-field-choice-sets)
                        essential_marker = " 🟡"
                        
                        # Format timestamp as dd-mm-yy HH:mm
                        try:
                            from datetime import datetime
                            if isinstance(timestamp, str):
                                dt = datetime.fromisoformat(timestamp.replace("UTC", "").strip())
                                compact_time = dt.strftime("%d-%m-%y %H:%M")
                            else:
                                compact_time = str(timestamp)
                        except:
                            compact_time = str(timestamp)[:16] if timestamp else "N/A"
                        
                        # Compact format: Label → field: count icon time
                        st.caption(f"**{label}** → `{field_key}`: {count} {icon}{essential_marker} `{compact_time}`")
    else:
        # Fall back to legacy static choice sets
        choice_sets = get_choice_set_summary()
        if choice_sets:
            with st.expander(
                f"⚙️ Custom field choice sets ({len(choice_sets)})", expanded=False
            ):
                st.caption(
                    "Read from `extras/custom-field-choice-sets`. These are the "
                    "authoritative values used when checking whether an Instance Type "
                    "or Resource Group already exists in NetBox."
                )
                for row in choice_sets:
                    fields = row.get("fields") or "—"
                    timestamp = row.get("uploaded_at") or "Unknown"
                    source = row.get("source", "NetBox Backup")
                    st.markdown(
                        f"* **{row['choice_set']}** → `{fields}`: "
                        f"`{row['value_count']}` values — {source} `{timestamp}`"
                    )


def _render_csv_upload_section(scope_key: str, csv_uploader_key: str) -> None:
    """Render Step 2: CSV/Excel upload with confirmation UI and Clear button in header."""
    st.markdown("---")
    
    # Header with Clear All CSV button on same row
    # Check if there are any CSV entries
    registry = SharedBackupState.get_object_registry()
    csv_count = sum(1 for metadata in registry.values() 
                   if metadata.get("source_type") == "csv" or 
                      metadata.get("source", "").lower().endswith((".csv", ".xlsx")))
    
    col_header, col_clear = st.columns([4, 1])
    with col_header:
        st.markdown("**Step 2 — Upload CSV/Excel Files (Auto-Classified)**")
        st.caption("Upload individual CSV/Excel exports. Files are automatically classified and routed based on their columns.")
    with col_clear:
        if csv_count > 0:
            if st.button(
                f"🗑️ Clear All CSV ({csv_count})",
                key=f"btn_clear_csv_{scope_key}",
                on_click=_handle_csv_clear,
                args=(scope_key,),
                help="Clear all CSV data (preserves JSON backup)",
                use_container_width=True
            ):
                st.rerun()
    
    # Check if there are pending files awaiting confirmation
    pending_key = f"csv_pending_confirmation_{scope_key}"
    pending_data = st.session_state.get(pending_key)
    
    if pending_data:
        # Show confirmation UI
        st.warning("⚠️ **Confirm Endpoint Classification** — Review and confirm the detected NetBox endpoints before importing:", icon="⚠️")
        
        from core.universal_uploader import UniversalUploader
        uploader = UniversalUploader()
        
        files = pending_data['files']
        confirmed_classifications = {}
        
        # Pre-read all files once to avoid multiple reads
        file_previews = []
        for file_obj in files:
            if hasattr(file_obj, "seek"):
                file_obj.seek(0)
            
            # Read columns
            import pandas as pd
            import io
            filename_lower = file_obj.name.lower()
            
            try:
                if filename_lower.endswith('.csv'):
                    content = file_obj.getvalue().decode('utf-8', errors='replace')
                    df = pd.read_csv(io.StringIO(content), nrows=0)  # Just read headers
                elif filename_lower.endswith('.xlsx'):
                    import openpyxl
                    wb = openpyxl.load_workbook(io.BytesIO(file_obj.getvalue()), data_only=True)
                    ws = wb[wb.sheetnames[0]]
                    df = pd.DataFrame(columns=[str(cell.value) for cell in ws[1] if cell.value])
                else:
                    continue
                
                columns = list(df.columns)
                
                # Get suggested classification using BOTH filename and columns
                # Try filename first (high confidence), then columns
                filename_classification = uploader._classify_by_filename(file_obj.name)
                column_classification = uploader._classify_columns(columns)
                
                # Choose the best classification based on confidence
                if filename_classification and column_classification:
                    # Both methods returned a result - pick the one with higher confidence
                    filename_endpoint, filename_conf = filename_classification
                    column_endpoint, column_conf = column_classification
                    
                    if filename_conf >= column_conf:
                        classification = filename_classification
                    else:
                        classification = column_classification
                elif filename_classification:
                    # Only filename classification worked
                    classification = filename_classification
                elif column_classification:
                    # Only column classification worked
                    classification = column_classification
                else:
                    # Neither worked
                    classification = None
                
                if classification:
                    suggested_endpoint, confidence = classification
                    # Convert dot notation to slash notation for consistency (e.g., users.owners -> users/owners)
                    suggested_endpoint = suggested_endpoint.replace('.', '/')
                    confidence_pct = int(confidence * 100)
                else:
                    suggested_endpoint = f"unclassified/{file_obj.name.replace('.csv', '').replace('.xlsx', '').replace(' ', '_').lower()}"
                    confidence_pct = 0
                
                file_previews.append({
                    'file_obj': file_obj,
                    'columns': columns,
                    'suggested_endpoint': suggested_endpoint,
                    'confidence_pct': confidence_pct
                })
            except Exception as e:
                st.error(f"Error reading {file_obj.name}: {e}")
        
        # Build endpoint options dynamically from backup registry + common fallbacks
        # Get all endpoints from the JSON backup if available
        backup_registry = SharedBackupState.get_object_registry()
        dynamic_endpoints = []
        
        if backup_registry:
            # Extract endpoints from JSON backup (excluding CSV entries for the dropdown)
            for endpoint, metadata in backup_registry.items():
                source_type = metadata.get("source_type", "json")
                # Only include JSON backup endpoints, not CSV uploads
                if source_type == "json":
                    dynamic_endpoints.append(endpoint)
        
        # Common fallback endpoints (used if no backup is loaded)
        fallback_endpoints = [
            # Tenancy & Contacts
            "tenancy/contact-groups",
            "tenancy/contacts",
            "tenancy/tenant-groups", 
            "tenancy/tenants",
            # Users & Permissions
            "users/owner-groups",
            "users/owners",
            "users/groups",
            "users/users",
            # DCIM
            "dcim/regions",
            "dcim/site-groups",
            "dcim/sites",
            "dcim/locations",
            "dcim/racks",
            "dcim/rack-roles",
            "dcim/devices",
            "dcim/device-types",
            "dcim/device-roles",
            "dcim/platforms",
            "dcim/manufacturers",
            "dcim/interfaces",
            "dcim/cables",
            # IPAM
            "ipam/vlans",
            "ipam/vlan-groups",
            "ipam/prefixes",
            "ipam/ip-addresses",
            "ipam/vrfs",
            "ipam/aggregates",
            "ipam/rirs",
            # Virtualization
            "virtualization/virtual-machines",
            "virtualization/clusters",
            "virtualization/cluster-types",
            "virtualization/cluster-groups",
            # Circuits
            "circuits/circuits",
            "circuits/providers",
            "circuits/circuit-types",
            # Wireless
            "wireless/wireless-lans",
            "wireless/wireless-lan-groups",
        ]
        
        # Merge dynamic and fallback endpoints, remove duplicates, and sort
        if dynamic_endpoints:
            # Use dynamic endpoints from backup, add any missing fallbacks
            all_endpoints = dynamic_endpoints + [ep for ep in fallback_endpoints if ep not in dynamic_endpoints]
        else:
            # No backup loaded, use fallback list
            all_endpoints = fallback_endpoints
        
        # Sort by category prefix, then alphabetically
        common_endpoints = sorted(all_endpoints, key=lambda x: (x.split('/')[0], x))
        
        # Display confirmation UI for each file
        for idx, preview in enumerate(file_previews):
            file_obj = preview['file_obj']
            columns = preview['columns']
            suggested_endpoint = preview['suggested_endpoint']
            confidence_pct = preview['confidence_pct']
            
            with st.expander(f"📄 {file_obj.name}", expanded=True):
                st.markdown(f"**Detected Columns:** `{', '.join(columns)}`")
                
                # Put suggested endpoint first if not already in list
                if suggested_endpoint not in common_endpoints and not suggested_endpoint.startswith('unclassified'):
                    endpoint_options = [f"{suggested_endpoint} (Suggested)"] + common_endpoints
                    # Store actual endpoint without suffix
                    actual_suggested = suggested_endpoint
                else:
                    endpoint_options = common_endpoints
                    actual_suggested = suggested_endpoint
                
                # Find index of suggested endpoint
                if suggested_endpoint in common_endpoints:
                    default_idx = common_endpoints.index(suggested_endpoint)
                elif not suggested_endpoint.startswith('unclassified'):
                    default_idx = 0  # First item is the suggested one
                else:
                    default_idx = 0
                
                col1, col2 = st.columns([3, 1])
                with col1:
                    selected_endpoint = st.selectbox(
                        "Select NetBox Endpoint",
                        options=endpoint_options,
                        index=default_idx,
                        key=f"endpoint_select_{scope_key}_{idx}",
                        help="Choose the correct NetBox endpoint for this file"
                    )
                with col2:
                    if confidence_pct > 0:
                        st.metric("Confidence", f"{confidence_pct}%")
                    else:
                        st.caption("⚪ Manual")
                
                # Clean up " (Suggested)" suffix if present
                selected_clean = selected_endpoint.replace(" (Suggested)", "")
                confirmed_classifications[file_obj.name] = selected_clean
        
        # Confirmation buttons
        col_confirm, col_cancel = st.columns([1, 1])
        with col_confirm:
            if st.button("✅ Confirm & Import", key=f"btn_confirm_csv_{scope_key}", type="primary", use_container_width=True):
                _process_confirmed_csv_uploads(scope_key, confirmed_classifications)
                st.rerun()
        with col_cancel:
            if st.button("❌ Cancel", key=f"btn_cancel_csv_{scope_key}", use_container_width=True):
                # Clear pending confirmation
                del st.session_state[pending_key]
                # Clear the file uploader
                counter_key = f"csv_uploader_counter_{scope_key}"
                st.session_state[counter_key] = st.session_state.get(counter_key, 0) + 1
                st.rerun()
    else:
        # CSV Upload Section
        st.file_uploader(
            "📊 Upload CSV/Excel Files (Multiple Files Supported)",
            type=["csv", "xlsx"],
            accept_multiple_files=True,
            key=csv_uploader_key,
            on_change=_handle_csv_backup_upload,
            args=(csv_uploader_key, scope_key),
            help="Upload CSV or Excel files exported from NetBox. Multiple files can be uploaded at once."
        )


def render_backup_uploader(scope_key: str) -> dict:
    """
    Main orchestrator for NetBox backup and CSV upload UI.
    
    Renders a clean, modular interface for:
    - Step 1: JSON backup upload with PowerShell scripts
    - Backup contents display
    - Custom field choice sets display
    - Step 2: CSV/Excel upload with auto-classification
    
    Args:
        scope_key: Unique key for this uploader instance (e.g., "ipam", "naming")
        
    Returns:
        Backup metadata dictionary
    """
    meta = get_backup_metadata()
    
    # Initialize uploader key counters if not present
    json_counter_key = f"json_uploader_counter_{scope_key}"
    csv_counter_key = f"csv_uploader_counter_{scope_key}"
    if json_counter_key not in st.session_state:
        st.session_state[json_counter_key] = 0
    if csv_counter_key not in st.session_state:
        st.session_state[csv_counter_key] = 0
    
    json_uploader_key = f"netbox_json_uploader_{scope_key}_{st.session_state[json_counter_key]}"
    csv_uploader_key = f"netbox_csv_uploader_{scope_key}_{st.session_state[csv_counter_key]}"
    checkbox_key = f"netbox_backup_enabled_{scope_key}"
    result_key = f"backup_upload_result_{scope_key}"
    error_key = f"backup_upload_error_{scope_key}"
    
    # Step 1: JSON Backup Upload
    _render_json_backup_section(scope_key, meta, json_uploader_key, checkbox_key, result_key, error_key)
    
    # Display backup contents
    _render_backup_contents_section(scope_key, meta)
    
    # Display custom field choice sets
    _render_choice_sets_section()
    
    # Step 2: CSV Upload (moved to bottom per user request)
    _render_csv_upload_section(scope_key, csv_uploader_key)
    
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
        if st.button("🧪 Test Model Connection", key="btn_ping_model", width="stretch"):
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
        
        # Version badge at the bottom of sidebar
        st.markdown("---")
        st.markdown(
            f"""
            <div style="text-align: left; padding: 8px 0;">
                <span style="background: #1e293b; color: #38bdf8; padding: 5px 12px; 
                             border-radius: 6px; font-size: 12px; 
                             font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; 
                             font-weight: 600; border: 1px solid #334155; 
                             box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2); display: inline-block;">
                    📦 NetBox Hub v{APP_VERSION}
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    return active_model