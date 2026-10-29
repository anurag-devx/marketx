"""API: the Research page (research/registry.json; research/RESEARCH_LOG.md is written from the same file)."""
from web.api import route, UserError


@route("GET", "research/log")
def log(_):
    from research import research_log as R
    r = R.load()
    items = r["items"]
    count = {s: sum(1 for x in items if x["status"] == s) for s in R.STATUS}
    return dict(title=r["title"], intro=r["intro"], rules=r["rules"], plan=r["plan"], items=items, count=count, entries=r.get("entries", [])[-20:])


@route("GET", "research/log/item")
def item(p):
    from research import research_log as R
    it = next((x for x in R.load()["items"] if x["id"] == str(p.get("id"))), None)
    if not it:
        raise UserError("no such test")
    return it
