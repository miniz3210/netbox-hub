import pandas as pd

from ui.tabs.naming_tab import _normalise_extra_cols, _convert_back_to_records


class TestNormaliseExtraCols:
    """is_uncertain must become a string ("⚠️"/"") before st.data_editor,
    otherwise the TextColumn config conflicts with the bool dtype."""

    def test_none_true_false_true_str_empty_str(self):
        df = pd.DataFrame({
            "switch": ["sw1"] * 5,
            "is_uncertain": [None, True, False, "True", ""],
            "source": [None, "ocr", None, "text", None],
            "note": [None, "n", None, "n2", None],
            "vlan": [None, "100", None, "200", None],
        })
        out = _normalise_extra_cols(df)
        assert out["is_uncertain"].tolist() == ["", "⚠️", "", "⚠️", ""]
        assert pd.api.types.is_string_dtype(out["is_uncertain"].dtype)
        assert out["source"].tolist() == ["", "ocr", "", "text", ""]
        assert out["note"].tolist() == ["", "n", "", "n2", ""]
        assert out["vlan"].tolist() == ["", "100", "", "200", ""]
        for col in ("source", "note", "vlan"):
            assert pd.api.types.is_string_dtype(out[col].dtype)
            assert out[col].isna().sum() == 0

    def test_none_true_false_true_str_empty_str_roundtrip(self):
        df = pd.DataFrame({
            "switch": ["sw1"] * 5,
            "is_uncertain": [None, True, False, "True", ""],
            "source": [None, "ocr", None, "text", None],
            "note": [None, "n", None, "n2", None],
            "vlan": [None, "100", None, "200", None],
        })
        out = _normalise_extra_cols(df)
        records = _convert_back_to_records(out)
        assert [r["is_uncertain"] for r in records] == [False, True, False, True, False]
        for r in records:
            assert isinstance(r["is_uncertain"], bool)
            assert isinstance(r["source"], str)
            assert isinstance(r["note"], str)
            assert isinstance(r["vlan"], str)

    def test_missing_extra_cols_created(self):
        df = pd.DataFrame({"switch": ["sw1"]})
        out = _normalise_extra_cols(df)
        for col in ("is_uncertain", "source", "note", "vlan"):
            assert col in out.columns
        assert out["is_uncertain"].tolist() == [""]
        records = _convert_back_to_records(out)
        assert records[0]["is_uncertain"] is False

    def test_bools_true_false_none_roundtrip(self):
        df = pd.DataFrame({
            "switch": ["sw1", "sw2", "sw3"],
            "is_uncertain": [True, False, None],
        })
        records = _convert_back_to_records(df)
        assert [r["is_uncertain"] for r in records] == [True, False, False]
        assert all(isinstance(r["is_uncertain"], bool) for r in records)

    def test_nan_note_vlan_becomes_empty_str(self):
        df = pd.DataFrame({
            "switch": ["sw1"],
            "note": [pd.NA],
            "vlan": [pd.NaT],
        })
        records = _convert_back_to_records(df)
        assert records[0]["note"] == ""
        assert records[0]["vlan"] == ""

    def test_check_column_filled(self):
        df = pd.DataFrame({"switch": ["sw1"], "_check": [pd.NA]})
        out = _normalise_extra_cols(df)
        assert "_check" in out.columns
        assert out["_check"].tolist() == [""]
        assert pd.api.types.is_string_dtype(out["_check"].dtype)