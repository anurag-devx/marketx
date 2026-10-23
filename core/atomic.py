"""Crash-safe file writes: write to a temporary file next to the target, then swap it in (os.replace is atomic).
A reader never sees a half-written file, and a crash mid-write leaves the old file intact."""
import os, tempfile
from pathlib import Path


def _tmp(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    return path, Path(tmp)


def write_text(path, text):
    path, tmp = _tmp(path)
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def to_parquet(df, path, **kw):
    path, tmp = _tmp(path)
    try:
        df.to_parquet(tmp, **{"index": False, **kw})
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def to_csv(df, path, **kw):
    path, tmp = _tmp(path)
    try:
        df.to_csv(tmp, **{"index": False, **kw})
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
