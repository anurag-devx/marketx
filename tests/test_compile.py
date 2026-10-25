"""Every project file must at least compile (catches syntax errors in dashboard pages the other tests don't import)."""
import py_compile
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP = ("venv", "__pycache__", "backups", "logs", "data")
FILES = [p for p in ROOT.rglob("*.py") if not any(x.startswith(".venv") or x in SKIP for x in p.relative_to(ROOT).parts)]   # .venv, .venv-kronos ...


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_compiles(path):
    py_compile.compile(str(path), doraise=True)
