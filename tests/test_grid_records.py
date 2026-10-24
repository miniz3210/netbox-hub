import pandas as pd
from ui.tabs.naming_tab import _records_to_grid_df, _grid_df_to_records, _GRID_COLS

MIXED = [  # None / True / False / "True" / "" : the same five inputs the old test used
    {"switch": "sw1", "is_uncertain": None, "source": None, "note": None, "vlan": None},
    {"switch": "sw1", "is_uncertain": True, "source": "ocr", "note": "n", "vlan": "100"},
    {"switch": "sw1", "is_uncertain": False, "source": None, "note": None, "vlan": None},
    {"switch": "sw1", "is_uncertain": "True", "source": "text", "note": "n2", "vlan": "200"},
    {"switch": "sw1", "is_uncertain": "", "source": None, "note": None, "vlan": None},
]


def test_is_uncertain_is_a_real_bool_and_check_column_is_derived():
    df = _records_to_grid_df(MIXED)
    assert df["is_uncertain"].tolist() == [False, True, False, True, False]
    assert str(df["is_uncertain"].dtype) == "bool"
    assert df["_check"].tolist() == ["", "OCR \u2013 verify", "", "OCR \u2013 verify", ""]


def test_text_columns_are_plain_strings_without_nan():
    df = _records_to_grid_df(MIXED)
    assert df["source"].tolist() == ["", "ocr", "", "text", ""]
    assert df["note"].tolist() == ["", "n", "", "n2", ""]
    assert df["vlan"].tolist() == ["", "100", "", "200", ""]
    for c in ("source", "note", "vlan"):
        assert df[c].map(type).eq(str).all()


def test_roundtrip_does_not_leak_check_and_keeps_flags():
    back = _grid_df_to_records(_records_to_grid_df(MIXED))
    assert all("_check" not in r for r in back)
    assert [r["is_uncertain"] for r in back] == [False, True, False, True, False]
    assert [r["vlan"] for r in back] == ["", "100", "", "200", ""]
    assert set(back[0].keys()) == set(_GRID_COLS)


def test_empty_records_keep_dtypes():
    df = _records_to_grid_df([])
    assert len(df) == 0 and str(df["is_uncertain"].dtype) == "bool"
    assert _grid_df_to_records(df) == []


def test_user_clears_the_uncertain_tick_in_the_editor():
    df = _records_to_grid_df(MIXED)
    df.loc[1, "is_uncertain"] = False
    back = _grid_df_to_records(df)
    assert back[1]["is_uncertain"] is False and back[1]["source"] == "ocr"


def test_new_row_added_in_the_editor_has_nan_and_none():
    df = _records_to_grid_df(MIXED[:1])
    new = {c: None for c in df.columns}
    df = pd.concat([df, pd.DataFrame([new])], ignore_index=True)
    back = _grid_df_to_records(df)
    assert back[1]["switch"] == "" and back[1]["is_uncertain"] is False
    assert not any(v in ("None", "nan") for v in back[1].values() if isinstance(v, str))


def test_user_extra_keys_are_dropped_by_design():
    rec = dict(MIXED[1], extra_field="x")
    back = _grid_df_to_records(_records_to_grid_df([rec]))
    assert "extra_field" not in back[0]