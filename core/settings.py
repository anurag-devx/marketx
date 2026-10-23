"""Editable settings: saved to settings.json, every change recorded in settings_history.jsonl."""
import json, copy
import pandas as pd
from . import config as C
from . import atomic

HISTORY = C.BASE / "settings_history.jsonl"


def current():
    return {k: copy.deepcopy(getattr(C, k)) for k in C.EDITABLE}


def save(changes, note=""):
    """changes: {key: new value}. Returns the new revision number."""
    old = json.loads(C.SETTINGS_JSON.read_text()) if C.SETTINGS_JSON.exists() else {}
    cur = current()
    diff = {k: {"from": cur.get(k), "to": v} for k, v in changes.items() if cur.get(k) != v}
    if not diff:
        return int(old.get("_rev", 0))
    new = {**old, **changes, "_rev": int(old.get("_rev", 0)) + 1}
    atomic.write_text(C.SETTINGS_JSON, json.dumps(new, indent=2))
    with open(HISTORY, "a") as f:
        f.write(json.dumps(dict(at=str(pd.Timestamp.now().floor("s")), rev=new["_rev"], note=note, diff=diff), default=str) + "\n")
    return new["_rev"]


def reset():
    old = json.loads(C.SETTINGS_JSON.read_text()) if C.SETTINGS_JSON.exists() else {}
    rev = int(old.get("_rev", 0)) + 1
    atomic.write_text(C.SETTINGS_JSON, json.dumps({"_rev": rev}, indent=2))
    with open(HISTORY, "a") as f:
        f.write(json.dumps(dict(at=str(pd.Timestamp.now().floor("s")), rev=rev, note="reset to defaults", diff={})) + "\n")
    return rev


def history():
    if not HISTORY.exists():
        return pd.DataFrame()
    rows = [json.loads(l) for l in HISTORY.read_text().splitlines() if l.strip()]
    for r in rows:
        r["changed"] = ", ".join(f"{k}: {v['from']} → {v['to']}" for k, v in r.get("diff", {}).items()) or r.get("note", "")
    return pd.DataFrame(rows)[["at", "rev", "note", "changed"]].iloc[::-1]
