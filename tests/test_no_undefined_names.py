"""Static guard: undefined names / duplicate dict keys are runtime bugs that unit tests on pure functions never see."""
import io
import os
import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
from pyflakes.reporter import Reporter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["ui/tabs/naming_tab.py", "core/extractors/esxi.py", "core/extractors/esxi_text.py",
          "core/extractors/esxi_pipeline.py", "core/topology_reducer.py",
          "core/ai_helper.py", "core/backup_manager.py"]
BAD = ("undefined name", "repeated with different values", "undefined local variable")


def _problems(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    out, err = io.StringIO(), io.StringIO()
    pyflakes_api.check(src, path, Reporter(out, err))
    return [l for l in out.getvalue().splitlines() if any(b in l for b in BAD)]


@pytest.mark.parametrize("rel", FILES)
def test_no_undefined_names_or_duplicate_keys(rel):
    path = os.path.join(ROOT, rel)
    if not os.path.exists(path):
        pytest.skip(f"{rel} not present")
    assert _problems(path) == []
