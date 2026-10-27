import sys
from . import radar, evaluate

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "test":
        evaluate.run()
    elif a and a[0] == "candidates":               # test Delivery / Valuation before they are used in forecasts
        evaluate.run_candidates()
    elif a and a[0] == "replay":                   # replay days=21 · or start=YYYY-MM-DD end=YYYY-MM-DD
        from . import review
        kw = dict(x.split("=", 1) for x in a[1:] if "=" in x)
        review.replay(n_days=int(kw.get("days", 21)), start=kw.get("start"), end=kw.get("end"))
    elif a and a[0] == "grade":
        from . import review
        for k in review.KINDS:
            review.grade(k)
    else:
        day = next((x.split("=", 1)[1] for x in a if x.startswith("date=")), None)
        radar.run(day=day)
