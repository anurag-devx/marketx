"""API: All NSE stock list and the liquidity floor for trading lists (core/allnse.py)."""
from web.api import route, UserError


@route("GET", "allnse")
def allnse(_):
    from core import allnse as A
    u = A.stocks()
    return dict(floor_cr=A.floor(), stocks=int(len(u)), active=int(u.active.sum()) if len(u) else 0,
                tradable=int(u.tradable.sum()) if len(u) else 0,
                at={f: int((u.active & (u.turnover_cr >= f)).sum()) for f in (0, 1, 5, 10, 25, 50, 100)} if len(u) else {})


@route("POST", "allnse/floor")
def set_floor(_, body):
    from core import allnse as A
    try:
        s = A.set_floor((body or {}).get("floor_cr"))
    except (TypeError, ValueError) as e:
        raise UserError(str(e))
    return dict(s, tradable=int(A.stocks(tradable_only=True).shape[0]))
