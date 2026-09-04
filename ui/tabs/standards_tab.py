import streamlit as st
from config.naming_rules import load_naming_rules, save_naming_rules, export_rules_as_prompt
from core.naming_engine import parse_prompt_to_rules

def render_standards_tab(active_model):
    st.subheader("📖 Infrastructure Naming Standards (Natural Language Prompt Engine)")
    current_rules = load_naming_rules()
    prompt_rep = export_rules_as_prompt(current_rules)

    p_col1, p_col2 = st.columns([1, 1])
    with p_col1:
        st.markdown("#### 📝 Active Infrastructure Guidelines Prompt")
        st.text_area("System Context", value=prompt_rep, height=380, disabled=True, key="standards_display")
        
        col_download, col_clear = st.columns([2, 1])
        with col_download:
            st.download_button("📥 Download Guidelines Prompt (.txt)", prompt_rep, "naming_standards.txt", "text/plain")
        with col_clear:
            if st.button("🗑️ Clear", help="Reset to default naming standards", use_container_width=True):
                from config.naming_rules import DEFAULT_RULES
                save_naming_rules(DEFAULT_RULES)
                st.session_state["naming_rules"] = load_naming_rules()
                st.success("✅ Reset to default standards!")
                st.rerun()

    with p_col2:
        st.markdown("#### 📥 Import / Update from Natural Language Prompt")
        imported_text = st.text_area("Paste Updated Prompt", placeholder="e.g. Switch naming should be...", height=260, key="import_prompt_text")
        if st.button("🔄 Parse & Apply Prompt", type="primary"):
            if imported_text.strip():
                with st.spinner(f"Parsing using {active_model}..."):
                    try:
                        extracted = parse_prompt_to_rules(imported_text, active_model)
                        save_naming_rules(extracted)
                        # Reload naming rules in session state
                        st.session_state["naming_rules"] = load_naming_rules()
                        st.success("✅ Standards updated successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to parse prompt: {str(e)}")