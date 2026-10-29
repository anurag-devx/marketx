"""Research log: research/registry.json is the source; research/RESEARCH_LOG.md is written from it, so the file and the
Research page in the app always say the same thing.

Status per test: pending | running | passed | failed. A test's pass mark is written before it runs.
Update a test (after running it):  python -m research.research_log set R1 status=passed headline="..." date=2026-11-02
Rewrite the markdown only:        python -m research.research_log"""
import json, sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
REG = BASE / "research" / "registry.json"
MD = BASE / "research" / "RESEARCH_LOG.md"
STATUS = ("pending", "running", "passed", "failed")
MARK = {"passed": "PASSED", "failed": "FAILED", "pending": "pending", "running": "RUNNING"}


def load():
    return json.loads(REG.read_text())


def save(r):
    REG.write_text(json.dumps(r, indent=1, ensure_ascii=False))
    MD.write_text(render(r))


def render(r):
    L = [f"# {r['title']}", "", r["intro"], "", "## Ground rules"] + [f"- {x}" for x in r["rules"]] + ["", "## Summary", "",
         "| Test | Question | Status | Key result |", "|---|---|---|---|"]
    for it in r["items"]:
        L.append(f"| {it['id']} {it['title']} | {it['question']} | **{MARK[it['status']]}**{' (' + it['date'] + ')' if it.get('date') else ''} | "
                 f"{it.get('headline') or 'needs: ' + it.get('needs', '')} |")
    L += ["", "## Plan", "", r["plan"], ""]
    for kind, head in (("done", "Done"), ("planned", "Planned")):
        L += [f"## {head}", ""]
        for it in [x for x in r["items"] if x["kind"] == kind]:
            L += [f"### {it['id']}. {it['title']} - {MARK[it['status']]}" + (f" ({it['date']})" if it.get("date") else ""), "",
                  f"- **Question:** {it['question']}", f"- **Pass mark (set before running):** {it['pass_mark']}"]
            if it.get("headline"):
                L.append(f"- **Result:** {it['headline']}")
            if it.get("numbers"):
                L += ["", "| Measure | Value |", "|---|---|"] + [f"| {a} | {b} |" for a, b in it["numbers"]]
            if it.get("notes"):
                L += ["", it["notes"]]
            if it.get("needs"):
                L.append(f"- **Needs:** {it['needs']}")
            if it.get("script"):
                L.append(f"- **Script:** `{it['script']}`" + (f" · **Results:** `{it['results']}`" if it.get("results") else ""))
            L.append("")
    L += ["## Entries", ""] + [f"- {e['date']}: {e['text']}" for e in r.get("entries", [])] + [""]
    return "\n".join(L)


def set_item(tid, **kw):
    r = load()
    it = next((x for x in r["items"] if x["id"] == tid), None)
    if it is None:
        raise SystemExit(f"no test {tid}")
    if "status" in kw and kw["status"] not in STATUS:
        raise SystemExit(f"status must be one of {STATUS}")
    it.update(kw)
    if kw.get("status") in ("passed", "failed"):
        it["kind"] = "done"
    r.setdefault("entries", []).append(dict(date=kw.get("date") or it.get("date") or "", text=f"{tid} {it['title']}: {kw.get('status', it['status'])}"
                                            + (f" - {kw['headline']}" if kw.get("headline") else "")))
    save(r)
    return it


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "set":
        set_item(a[1], **dict(x.split("=", 1) for x in a[2:] if "=" in x))
    else:
        save(load())
    print(f"written: {MD.relative_to(BASE)}")
