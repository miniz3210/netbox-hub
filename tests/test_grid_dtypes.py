import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Reproduce the builder pattern used in ui/tabs/naming_tab.py around line 2107
# so the tests can exercise it without importing Streamlit.
# ---------------------------------------------------------------------------
def build_topology_grid(records: list) -> pd.DataFrame:
    """Build the DataFrame shown by the topology data_editor."""
    df = pd.DataFrame(
        records if records else [],
        columns=["switch", "interface", "type", "ip", "purpose", "vlan",
                 "role", "speed", "source", "is_uncertain", "note"],
    )
    _TEXT_COLS = ("source", "note")
    for col in _TEXT_COLS:
        df[col] = df[col].fillna("").astype(str)
    if "is_uncertain" in df.columns:
        df["is_uncertain"] = df["is_uncertain"].fillna(False).astype(bool)
    df["_check"] = df.apply(
        lambda r: "OCR – verify"
        if str(r.get("is_uncertain", "")).lower() in ("true", "1", "yes")
        else "",
        axis=1,
    )
    df["_check"] = df["_check"].fillna("").astype(str)
    return df


def _coerce_edited(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the same post-editor coercion that naming_tab does now."""
    _TEXT_COLS = ("_check", "source", "note")
    for col in _TEXT_COLS:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)
    if "is_uncertain" in df.columns:
        df["is_uncertain"] = df["is_uncertain"].fillna(False).astype(bool)
    return df


def _is_str_dtype(dtype) -> bool:
    """Accept both legacy object strings and pandas' nullable StringDtype."""
    return pd.api.types.is_string_dtype(dtype) or dtype == object


class TestTopologyGridDtypes:
    """Validate that text-configured columns never become float64."""

    def test_zero_records(self):
        df = build_topology_grid([])
        assert len(df) == 0
        for col in ("_check", "source", "note"):
            assert _is_str_dtype(df[col].dtype), f"{col} dtype={df[col].dtype}"
            assert df[col].isna().sum() == 0

    def test_records_without_is_uncertain(self):
        records = [
            {"switch": "sw1", "interface": "vmnic0", "type": "Uplink",
             "ip": "", "purpose": "", "vlan": "", "role": "Uplink",
             "speed": "1000", "source": "ocr", "note": ""},
        ]
        df = build_topology_grid(records)
        assert df["is_uncertain"].dtype == bool
        assert df["is_uncertain"].iloc[0] == False
        for col in ("_check", "source", "note"):
            assert _is_str_dtype(df[col].dtype), f"{col} dtype={df[col].dtype}"
            assert df[col].isna().sum() == 0

    def test_nan_cells_in_new_row_after_coerce(self):
        # Simulate what Streamlit returns when the user adds a blank row:
        # unspecified cells come back as NaN, which pandas would upcast to float64.
        raw = pd.DataFrame({
            "switch": ["sw1", np.nan],
            "interface": ["vmnic0", np.nan],
            "type": ["Uplink", np.nan],
            "ip": ["", np.nan],
            "purpose": ["", np.nan],
            "vlan": ["", np.nan],
            "role": ["Uplink", np.nan],
            "speed": ["1000", np.nan],
            "source": ["ocr", np.nan],
            "is_uncertain": [False, np.nan],
            "note": ["", np.nan],
            "_check": ["", np.nan],
        })
        df = _coerce_edited(raw)
        for col in ("_check", "source", "note"):
            assert _is_str_dtype(df[col].dtype), f"{col} dtype={df[col].dtype}"
            assert df[col].isna().sum() == 0
        assert df["is_uncertain"].dtype == bool
        assert df["is_uncertain"].isna().sum() == 0

    def test_uncertain_row_sets_check(self):
        records = [
            {"switch": "sw1", "interface": "vmk0", "type": "VMkernel",
             "ip": "10.0.0.1", "purpose": "Management", "vlan": "100",
             "role": "Mgmt", "speed": "", "source": "ocr",
             "is_uncertain": True, "note": "verify"},
        ]
        df = build_topology_grid(records)
        assert df["_check"].iloc[0] == "OCR – verify"
        assert df["is_uncertain"].iloc[0] == True
