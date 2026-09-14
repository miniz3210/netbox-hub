"""
Custom NetBox Backup Script Generator UI

Interactive component that allows users to customize which NetBox endpoints
are included in their PowerShell export script. Provides checkboxes for all
available endpoints with visual indicators for the 68 essential endpoints,
and a "Reset to Default Minimal" button.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Set
import streamlit as st
from config.backup_endpoints import NETBOX_ENDPOINTS, get_essential_endpoints, get_endpoint_count


# Session state keys
STATE_SELECTED_ENDPOINTS = "custom_backup_selected_endpoints"
STATE_IS_CUSTOM = "custom_backup_is_custom"


def _initialize_session_state():
    """Initialize session state with default minimal backup (68 essential endpoints)."""
    if STATE_SELECTED_ENDPOINTS not in st.session_state:
        st.session_state[STATE_SELECTED_ENDPOINTS] = set(get_essential_endpoints())
        st.session_state[STATE_IS_CUSTOM] = False


def _reset_to_default_minimal():
    """Reset selection to the 68 essential endpoints."""
    st.session_state[STATE_SELECTED_ENDPOINTS] = set(get_essential_endpoints())
    st.session_state[STATE_IS_CUSTOM] = False
    st.toast("✅ Reset to default minimal backup (68 endpoints)", icon="🔄")


def _toggle_endpoint(endpoint_path: str):
    """Toggle an endpoint selection on/off."""
    selected = st.session_state[STATE_SELECTED_ENDPOINTS]
    if endpoint_path in selected:
        selected.remove(endpoint_path)
    else:
        selected.add(endpoint_path)
    
    # Check if current selection differs from default minimal
    default_essential = set(get_essential_endpoints())
    st.session_state[STATE_IS_CUSTOM] = selected != default_essential


def _select_all_in_category(category: str):
    """Select all endpoints in a category."""
    selected = st.session_state[STATE_SELECTED_ENDPOINTS]
    for endpoint in NETBOX_ENDPOINTS[category]:
        selected.add(endpoint["path"])
    
    # Mark as custom if not equal to default minimal
    default_essential = set(get_essential_endpoints())
    st.session_state[STATE_IS_CUSTOM] = selected != default_essential


def _deselect_all_in_category(category: str):
    """Deselect all endpoints in a category."""
    selected = st.session_state[STATE_SELECTED_ENDPOINTS]
    for endpoint in NETBOX_ENDPOINTS[category]:
        selected.discard(endpoint["path"])
    
    # Mark as custom if not equal to default minimal
    default_essential = set(get_essential_endpoints())
    st.session_state[STATE_IS_CUSTOM] = selected != default_essential


def _generate_custom_script(selected_endpoints: Set[str]) -> str:
    """Generate a custom PowerShell export script based on selected endpoints."""
    
    # Read the base minimal script as a template
    base_script_path = Path(__file__).resolve().parent.parent / "data" / "netbox-export-min.ps1"
    
    try:
        with open(base_script_path, 'r', encoding='utf-8-sig') as f:
            base_script = f.read()
    except Exception as e:
        return f"# Error reading base script: {e}\n"
    
    # Generate the custom endpoint list
    sorted_endpoints = sorted(selected_endpoints)
    endpoint_array_lines = ['$MinimalEndpoints = @(']
    
    # Group by category for better organization
    for category in NETBOX_ENDPOINTS.keys():
        category_endpoints = [ep["path"] for ep in NETBOX_ENDPOINTS[category] 
                            if ep["path"] in selected_endpoints]
        if category_endpoints:
            endpoint_array_lines.append(f'    # {category}')
            for ep in sorted(category_endpoints):
                endpoint_array_lines.append(f'    "{ep}",')
    
    # Remove trailing comma from last endpoint
    if endpoint_array_lines[-1].endswith(','):
        endpoint_array_lines[-1] = endpoint_array_lines[-1][:-1]
    
    endpoint_array_lines.append(')')
    
    custom_endpoint_section = '\n'.join(endpoint_array_lines)
    
    # Replace the $MinimalEndpoints array in the base script
    # Find the start and end of the original array definition
    import re
    pattern = r'\$MinimalEndpoints = @\([^)]*\)'
    
    # Use a more robust pattern that handles multi-line arrays
    pattern = r'\$MinimalEndpoints\s*=\s*@\([^)]+\)'
    
    custom_script = re.sub(
        pattern,
        custom_endpoint_section,
        base_script,
        flags=re.DOTALL
    )
    
    # Update the description to indicate custom backup
    custom_script = custom_script.replace(
        'NetBox Minimal Backup - Essential Data Only',
        f'NetBox Custom Backup - {len(selected_endpoints)} Selected Endpoints'
    )
    custom_script = custom_script.replace(
        'backup_type          = "minimal"',
        'backup_type          = "custom"'
    )
    custom_script = custom_script.replace(
        'description          = "Minimal NetBox backup containing only essential data for restore"',
        f'description          = "Custom NetBox backup with {len(selected_endpoints)} selected endpoints"'
    )
    custom_script = custom_script.replace(
        'NetBox_Minimal_Backup_$TimeStamp.json',
        'NetBox_Custom_Backup_$TimeStamp.json'
    )
    custom_script = custom_script.replace(
        'NETBOX MINIMAL BACKUP',
        'NETBOX CUSTOM BACKUP'
    )
    custom_script = custom_script.replace(
        'MINIMAL BACKUP SUMMARY',
        'CUSTOM BACKUP SUMMARY'
    )
    
    return custom_script


def render_custom_backup_selector(scope_key: str = "naming") -> None:
    """
    Render the custom backup script generator UI.
    
    Provides an interactive interface where users can:
    - View all available NetBox endpoints grouped by category
    - Select/deselect endpoints with checkboxes
    - See visual indicators for the 68 essential endpoints (gold coin icon)
    - Reset to the default minimal backup
    - Download a customized PowerShell export script
    
    Args:
        scope_key: Unique identifier for this UI instance (e.g., "ipam", "naming")
    """
    _initialize_session_state()
    
    selected_endpoints = st.session_state[STATE_SELECTED_ENDPOINTS]
    is_custom = st.session_state[STATE_IS_CUSTOM]
    
    counts = get_endpoint_count()
    selected_count = len(selected_endpoints)
    
    # Dynamic title based on whether it's custom or default minimal
    if is_custom:
        backup_type = "Custom Backup"
        status_icon = "🎨"
    else:
        backup_type = "Minimal Backup (Essential Only)"
        status_icon = "🟡"
    
    st.markdown(f"**{status_icon} {backup_type}** — {selected_count} of {counts['total']} endpoints selected")
    st.caption("Customize which NetBox endpoints to include in your PowerShell export script. 🟡 = Essential endpoint (recommended minimal set)")
    
    # Action buttons row
    col_reset, col_download, col_view = st.columns([2, 2, 2])
    
    with col_reset:
        if st.button(
            "🔄 Reset to Default Minimal",
            key=f"btn_reset_backup_{scope_key}",
            use_container_width=True,
            help="Reset to the 68 essential endpoints",
            type="secondary" if not is_custom else "primary"
        ):
            _reset_to_default_minimal()
            st.rerun()
    
    with col_download:
        # Generate custom script
        custom_script = _generate_custom_script(selected_endpoints)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"netbox-export-custom_{timestamp}.ps1"
        
        st.download_button(
            "⬇️ Download Custom Script",
            data=custom_script,
            file_name=filename,
            mime="text/plain",
            key=f"btn_download_custom_{scope_key}",
            use_container_width=True,
            help=f"Download PowerShell script with {selected_count} selected endpoints"
        )
    
    with col_view:
        view_script = st.checkbox(
            "👁️ View Script",
            key=f"chk_view_script_{scope_key}",
            help="Show generated PowerShell script code"
        )
    
    # Show script preview if requested
    if view_script:
        with st.expander("📄 Generated PowerShell Script", expanded=True):
            custom_script = _generate_custom_script(selected_endpoints)
            st.code(custom_script, language="powershell", line_numbers=False)
    
    st.markdown("---")
    
    # Endpoint selection interface - organized by category
    st.markdown("##### 📋 Select Endpoints by Category")
    
    # Create tabs for each category
    category_tabs = st.tabs(list(NETBOX_ENDPOINTS.keys()))
    
    for idx, (category, endpoints) in enumerate(NETBOX_ENDPOINTS.items()):
        with category_tabs[idx]:
            # Category header with select all/none buttons
            col_header, col_sel_all, col_sel_none = st.columns([4, 1, 1])
            
            with col_header:
                # Count selected in this category
                category_selected = sum(1 for ep in endpoints if ep["path"] in selected_endpoints)
                category_essential = sum(1 for ep in endpoints if ep["essential"])
                st.markdown(f"**{category}** — {category_selected}/{len(endpoints)} selected ({category_essential} essential)")
            
            with col_sel_all:
                if st.button("✅ All", key=f"btn_select_all_{category}_{scope_key}", use_container_width=True):
                    _select_all_in_category(category)
                    st.rerun()
            
            with col_sel_none:
                if st.button("❌ None", key=f"btn_deselect_all_{category}_{scope_key}", use_container_width=True):
                    _deselect_all_in_category(category)
                    st.rerun()
            
            st.markdown("")
            
            # Render endpoints in 2 columns for compact display
            col1, col2 = st.columns(2)
            
            # Split endpoints into two columns
            mid_point = (len(endpoints) + 1) // 2
            
            with col1:
                for endpoint in endpoints[:mid_point]:
                    _render_endpoint_checkbox(endpoint, selected_endpoints, scope_key)
            
            with col2:
                for endpoint in endpoints[mid_point:]:
                    _render_endpoint_checkbox(endpoint, selected_endpoints, scope_key)
    
    # Summary footer
    st.markdown("---")
    essential_count = counts['essential']
    essential_selected = sum(1 for ep in get_essential_endpoints() if ep in selected_endpoints)
    
    col_summary1, col_summary2, col_summary3 = st.columns(3)
    
    with col_summary1:
        st.metric("Total Selected", f"{selected_count}/{counts['total']}")
    
    with col_summary2:
        st.metric("Essential Selected", f"{essential_selected}/{essential_count}")
    
    with col_summary3:
        if is_custom:
            st.metric("Backup Type", "Custom", delta="Modified")
        else:
            st.metric("Backup Type", "Minimal", delta="Default")


def _render_endpoint_checkbox(endpoint: dict, selected_endpoints: Set[str], scope_key: str):
    """Render a single endpoint checkbox with appropriate styling."""
    endpoint_path = endpoint["path"]
    is_selected = endpoint_path in selected_endpoints
    is_essential = endpoint["essential"]
    
    # Build label with essential indicator
    label = endpoint["label"]
    if is_essential:
        label = f"🟡 {label}"
    
    # Unique key for checkbox
    checkbox_key = f"chk_endpoint_{endpoint_path.replace('/', '_')}_{scope_key}"
    
    # Render checkbox with callback
    checked = st.checkbox(
        label,
        value=is_selected,
        key=checkbox_key,
        help=f"{endpoint['description']} ({endpoint_path})",
        on_change=_toggle_endpoint,
        args=(endpoint_path,)
    )
