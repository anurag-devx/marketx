import sys
from . import analyse

if __name__ == "__main__":
    kw = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    analyse.run(kw.get("symbols", "").split(","), as_of=kw.get("as_of"), topup="--no-topup" not in sys.argv, run_id=kw.get("run"))
