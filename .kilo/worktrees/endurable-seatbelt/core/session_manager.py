"""
Session State Manager - Centralized management of Streamlit session state
Reduces scattered st.session_state calls and improves maintainability
"""

import streamlit as st
from typing import Any, Optional, List


class SessionStateManager:
    """Centralized session state management to reduce st.session_state calls"""
    
    # IPAM Tab States
    @staticmethod
    def get_ipam_site_found_in_db(default: bool = False) -> bool:
        return st.session_state.get("ipam_site_found_in_db", default)
    
    @staticmethod
    def set_ipam_site_found_in_db(value: bool) -> None:
        st.session_state["ipam_site_found_in_db"] = value
    
    @staticmethod
    def get_ipam_loaded_from_db(default: bool = False) -> bool:
        return st.session_state.get("ipam_loaded_from_db", default)
    
    @staticmethod
    def set_ipam_loaded_from_db(value: bool) -> None:
        st.session_state["ipam_loaded_from_db"] = value
    
    @staticmethod
    def get_ipam_persisted_rows(default: List[dict] = None) -> List[dict]:
        if default is None:
            default = []
        return st.session_state.get("ipam_persisted_rows", default)
    
    @staticmethod
    def set_ipam_persisted_rows(value: List[dict]) -> None:
        st.session_state["ipam_persisted_rows"] = value
    
    # Naming Tab States
    @staticmethod
    def get_naming_case_mode(default: str = "UPPERCASE") -> str:
        return st.session_state.get("naming_case_mode", default)
    
    @staticmethod
    def set_naming_case_mode(value: str) -> None:
        st.session_state["naming_case_mode"] = value
    
    @staticmethod
    def get_naming_rules(default: dict = None) -> dict:
        if default is None:
            default = {}
        return st.session_state.get("naming_rules", default)
    
    @staticmethod
    def set_naming_rules(value: dict) -> None:
        st.session_state["naming_rules"] = value
    
    @staticmethod
    def get_naming_rules_loaded(default: bool = False) -> bool:
        return st.session_state.get("naming_rules_loaded", default)
    
    @staticmethod
    def set_naming_rules_loaded(value: bool) -> None:
        st.session_state["naming_rules_loaded"] = value
    
    # AI Model States
    @staticmethod
    def get_free_models_cache(default: List[str] = None) -> List[str]:
        if default is None:
            default = []
        return st.session_state.get("free_models_cache", default)
    
    @staticmethod
    def set_free_models_cache(value: List[str]) -> None:
        st.session_state["free_models_cache"] = value
    
    @staticmethod
    def get_models_loaded(default: bool = False) -> bool:
        return st.session_state.get("models_loaded", default)
    
    @staticmethod
    def set_models_loaded(value: bool) -> None:
        st.session_state["models_loaded"] = value
    
    @staticmethod
    def get_model_test_history(default: dict = None) -> dict:
        if default is None:
            default = {}
        return st.session_state.get("model_test_history", default)
    
    @staticmethod
    def set_model_test_history(value: dict) -> None:
        st.session_state["model_test_history"] = value
    
    # Chat History States
    @staticmethod
    def get_chat_history(key: str, default: List[dict] = None) -> List[dict]:
        if default is None:
            default = []
        return st.session_state.get(f"{key}_chat_history", default)
    
    @staticmethod
    def set_chat_history(key: str, value: List[dict]) -> None:
        st.session_state[f"{key}_chat_history"] = value
    
    # Data Editor States
    @staticmethod
    def get_data_editor_state(key: str, default: dict = None) -> dict:
        if default is None:
            default = {}
        return st.session_state.get(f"{key}_data_editor_live", default)
    
    @staticmethod
    def set_data_editor_state(key: str, value: dict) -> None:
        st.session_state[f"{key}_data_editor_live"] = value
    
    @staticmethod
    def clear_data_editor_state(key: str) -> None:
        if f"{key}_data_editor_live" in st.session_state:
            del st.session_state[f"{key}_data_editor_live"]
    
    # Catalog States
    @staticmethod
    def get_catalog(default: Any = None) -> Any:
        return st.session_state.get("catalog", default)
    
    @staticmethod
    def set_catalog(value: Any) -> None:
        st.session_state["catalog"] = value
    
    # Generic methods for flexibility
    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """Generic getter for any session state key"""
        return st.session_state.get(key, default)
    
    @staticmethod
    def set(key: str, value: Any) -> None:
        """Generic setter for any session state key"""
        st.session_state[key] = value
    
    @staticmethod
    def clear(key: str) -> None:
        """Clear a session state key if it exists"""
        if key in st.session_state:
            del st.session_state[key]
    
    @staticmethod
    def clear_all() -> None:
        """Clear all session state (use with caution)"""
        for key in list(st.session_state.keys()):
            del st.session_state[key]
    
    @staticmethod
    def exists(key: str) -> bool:
        """Check if a key exists in session state"""
        return key in st.session_state
