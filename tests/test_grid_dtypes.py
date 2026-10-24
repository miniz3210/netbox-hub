import numpy as np
import pandas as pd
import pytest


def _to_str_col(series: pd.Series) -> pd.Series:
    """Coerce a series to str, replacing NaN with empty string without
    triggering the Pandas future warning about fillna on float64."""
    return series.astype(object).where(series.notna(), "").astype(str)


def _ensure_text_cols(df: pd.DataFrame) -> None:
    """Ensure text-configured columns exist and are coerced to str; map is_uncertain
    from various string representations to bool without relying on astype(bool)."""
    for col in ("_check", "source", "note"):
        if col not in df.columns:
            if len(df) == 0:
                df[col] = pd.Series([], dtype=str)
            else:
                df[col] = ""
        else:
            df[col] = _to_str_col(df[col])
    if "is_uncertain" not in df.columns:
        if len(df) == 0:
            df["is_uncertain"] = pd.Series([], dtype=bool)
        else:
            df["is_uncertain"] = False
    elif len(df) == 0:
        df["is_uncertain"] = pd.Series([], dtype=bool)
    else:
        df["is_uncertain"] = (
            df["is_uncertain"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map(lambda s: s in ("true", "1", "yes"))
        )


def _set_check_column(df: pd.DataFrame) -> None:
    df["_check"] = df.apply(
        lambda r: "OCR – verify"
        if str(r.get("is_uncertain", "")).lower() in ("true", "1", "yes")
        else "",
        axis=1,
    ).astype(str)


def build_topology_grid(records: list) -> pd.DataFrame:
    """Build the DataFrame shown by the topology data_editor."""
    df = pd.DataFrame(
        records if records else [],
        columns=["switch", "interface", "type", "ip", "purpose", "vlan",
                 "role", "speed", "source", "is_uncertain", "note"],
    )
    _ensure_text_cols(df)
    _set_check_column(df)
    return df


def _coerce_edited(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the same post-editor coercion that naming_tab does now."""
    _ensure_text_cols(df)
    _set_check_column(df)
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

    def test_pure_7_classic_keys_no_keyerror(self):
        """Records with only the 7 classic keys must not raise KeyError."""
        records = [
            {"switch": "sw1", "interface": "vmnic0", "type": "Uplink",
             "ip": "10.0.0.1", "purpose": "Mgmt", "vlan": "100",
             "role": "Uplink"},
        ]
        df = build_topology_grid(records)
        assert len(df) == 1
        for col in ("_check", "source", "note"):
            assert _is_str_dtype(df[col].dtype), f"{col} dtype={df[col].dtype}"
            assert df[col].isna().sum() == 0
        assert df["is_uncertain"].iloc[0] == False

    def test_empty_dataframe_no_keyerror(self):
        df = build_topology_grid([])
        assert len(df) == 0
        for col in ("_check", "source", "note"):
            assert _is_str_dtype(df[col].dtype), f"{col} dtype={df[col].dtype}"
            assert df[col].isna().sum() == 0
        assert df["is_uncertain"].dtype == bool

    @pytest.mark.parametrize("val,expected", [
        (True, True),
        ("True", True),
        ("False", False),
        (None, False),
        ("", False),
    ])
    def test_is_uncertain_various_inputs(self, val, expected):
        records = [{"switch": "sw1", "interface": "vmnic0", "type": "Uplink",
                     "ip": "", "purpose": "", "vlan": "", "role": "Uplink",
                     "speed": "", "is_uncertain": val}]
        df = build_topology_grid(records)
        assert df["is_uncertain"].iloc[0] == expected
        assert df["is_uncertain"].dtype == bool

    def test_is_uncertain_string_false_not_true(self):
        """String 'False' must map to False, not True (old astype(bool) bug)."""
        records = [{"switch": "sw1", "interface": "vmnic0", "type": "Uplink",
                     "ip": "", "purpose": "", "vlan": "", "role": "Uplink",
                     "speed": "", "is_uncertain": "False"}]
        df = build_topology_grid(records)
        assert df["is_uncertain"].iloc[0] == False
