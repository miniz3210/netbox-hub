#!/usr/bin/env python3
"""
Patch ui/tabs/naming_tab.py: fix the Topology Data Grid crash

    StreamlitAPIException: The configured column type `text` for column `is_uncertain`
    is not compatible for editing the underlying data type `boolean`

and the silent loss of the is_uncertain flag on every rerun.

Usage (from the repo root, e.g. /opt/netbox-hub):
    python3 fix_grid.py                      # patches ui/tabs/naming_tab.py
    python3 fix_grid.py path/to/naming_tab.py

What it does
  1. replaces the helpers  _normalise_extra_cols / _convert_back_to_records
     with                  _records_to_grid_df   / _grid_df_to_records
  2. replaces the grid block (from the "Topology Data Grid" heading to
     st.session_state["topology_records"] = topology_records)
  3. backs the original up to ~/nb-backups/ (outside the repo, so it is never built into the image)
  4. compiles the result and refuses to write if anything does not match exactly once
"""
import datetime
import pathlib
import re
import shutil
import sys

NEW_HELPERS = '''_GRID_COLS = ["switch", "interface", "type", "ip", "purpose", "vlan", "role", "speed", "source", "is_uncertain", "note"]
_GRID_TEXT_COLS = [_c for _c in _GRID_COLS if _c != "is_uncertain"]


def _is_blank(v) -> bool:
    """True for None / NaN / pd.NA. Strings (even empty) are never 'blank' here."""
    if v is None:
        return True
    if isinstance(v, str):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _as_bool(v) -> bool:
    """Tolerant bool: True/False, numpy bool, 1/0, 'true'/'yes'/'⚠️', None/NaN -> False."""
    if _is_blank(v):
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "\\u26a0\\ufe0f", "\\u26a0")
    return bool(v)


def _records_to_grid_df(records: List[Dict[str, Any]]) -> pd.DataFrame:
    """topology_records -> DataFrame for st.data_editor.

    Text columns are always object/str, `is_uncertain` is always a real bool (also when there are
    zero rows, where pandas .map() would otherwise keep the wrong dtype), `_check` is derived.
    """
    df = pd.DataFrame(list(records) if records else [], columns=_GRID_COLS)
    for col in _GRID_TEXT_COLS:
        df[col] = pd.Series(["" if _is_blank(v) else str(v) for v in df[col]], index=df.index, dtype="object")
    df["is_uncertain"] = pd.Series([_as_bool(v) for v in df["is_uncertain"]], index=df.index, dtype="bool")
    df["_check"] = pd.Series(["OCR \\u2013 verify" if u else "" for u in df["is_uncertain"]], index=df.index, dtype="object")
    return df


def _grid_df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Edited grid DataFrame -> topology_records (plain str cells, real bool flag, no derived `_check`)."""
    out: List[Dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        rec = {c: ("" if _is_blank(row.get(c)) else str(row.get(c))) for c in _GRID_TEXT_COLS}
        rec["is_uncertain"] = _as_bool(row.get("is_uncertain"))
        out.append(rec)
    return out


'''

NEW_GRID = '''    st.markdown("#### \\U0001F39B\\uFE0F Topology Data Grid")
    st.caption("Edit switch assignments, interface roles, and IPs directly. Changes update NetBox descriptions instantly.")
    df_for_editor = _records_to_grid_df(records)
    column_config = {
        "switch": st.column_config.SelectboxColumn("Switch Container", options=sw_options if sw_options else [_dynamic_switch_pfx + "0"], required=False),
        "interface": st.column_config.TextColumn("Interface / Port", required=False),
        "type": st.column_config.SelectboxColumn("Type", options=["Uplink", "VMkernel", "Port Group"], required=False),
        "ip": st.column_config.TextColumn("IPv4 Address", required=False),
        "purpose": st.column_config.TextColumn("Service / Purpose", required=False),
        "vlan": st.column_config.TextColumn("VLAN", required=False),
        "role": st.column_config.SelectboxColumn("Role", options=role_options, required=False),
        "speed": st.column_config.TextColumn("Link Speed", required=False),
        "source": st.column_config.TextColumn("Source", required=False),
        "is_uncertain": st.column_config.CheckboxColumn(
            "Uncertain",
            help="Ticked = value came from OCR or a filled gap and is not verified yet. Untick it after you have checked it.",
            default=False,
        ),
        "note": st.column_config.TextColumn("Note", required=False),
        "_check": st.column_config.TextColumn("Check", disabled=True),
    }
    editor_key = f"topology_data_editor_{st.session_state.get('editor_ver', 0)}"
    edited_df = st.data_editor(
        df_for_editor,
        column_config=column_config,
        num_rows="dynamic",
        width="stretch",
        key=editor_key,
    )
    topology_records = _grid_df_to_records(edited_df)
    st.session_state["topology_records"] = topology_records
'''

HELPERS_RE = re.compile(r"def _normalise_extra_cols\(.*?(?=^def _render_screenshot_batch_mode\()", re.S | re.M)
GRID_RE = re.compile(
    r'^    st\.markdown\("#### [^"\n]*Topology Data Grid"\)\n.*?^    st\.session_state\["topology_records"\] = topology_records\n',
    re.S | re.M,
)


def patch_text(text: str):
    """Return (new_text, message). Raises ValueError if the file does not match."""
    if "_records_to_grid_df" in text and "_normalise_extra_cols" not in text:
        return text, "already patched - nothing to do"
    n_help = len(HELPERS_RE.findall(text))
    n_grid = len(GRID_RE.findall(text))
    if n_help != 1 or n_grid != 1:
        raise ValueError(
            f"expected exactly 1 helper block and 1 grid block, found {n_help} and {n_grid}. "
            "The file differs from the version I reviewed - nothing was changed."
        )
    new = HELPERS_RE.sub(lambda _m: NEW_HELPERS, text, count=1)
    new = GRID_RE.sub(lambda _m: NEW_GRID, new, count=1)
    leftovers = [n for n in ("_normalise_extra_cols", "_convert_back_to_records", "_ensure_text_cols") if n in new]
    if leftovers:
        raise ValueError(f"old helper(s) still referenced after patch: {leftovers} - nothing was changed.")
    compile(new, "naming_tab.py", "exec")
    return new, "patched"


def main(argv):
    path = pathlib.Path(argv[1] if len(argv) > 1 else "ui/tabs/naming_tab.py")
    if not path.is_file():
        print(f"ERROR: {path} not found. Run from the repo root or pass the path.")
        return 2
    text = path.read_text(encoding="utf-8")
    try:
        new, msg = patch_text(text)
    except (ValueError, SyntaxError) as e:
        print("ERROR:", e)
        return 1
    if new == text:
        print(msg)
        return 0
    backup_dir = pathlib.Path.home() / "nb-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"naming_tab.py.{datetime.datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(path, backup)
    path.write_text(new, encoding="utf-8")
    print(f"{msg}: {path}\nbackup: {backup}")
    print("Next: bump the app version, rebuild/push, then test the Naming tab with no records, then Analyze.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
