"""Where a person's own things live when someone else (a tester) signs in over Tailscale.

The owner's files stay exactly where they always were (data/desk, data/paper, data/workspace, data/live); a tester's
watchlist, notes, decisions, paper takes, saved charts and added stocks live in data/users/<name>/ with the same layout.
Market data, radar, plans, grades and Desk snapshots are shared. Outside a web request (jobs, the live feed thread) it is
always the owner."""
from core import config as C


def who():
    try:
        from web import auth
        return auth.current()
    except Exception:
        return None


def tester():
    w = who()
    return w["user"] if w and w.get("role") != "owner" else None


def path(owner_path, *rel):
    """owner_path for the owner (or no login); data/users/<tester>/<rel...> for a tester."""
    t = tester()
    return owner_path if t is None else C.DATA.joinpath("users", t, *rel)
