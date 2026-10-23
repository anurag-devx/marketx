"""Strategy agent research run (weekly, or after new data):  python3 strategy_research.py"""
import uuid, json
from core.data import load_raw
from core.runlog import agent_run
from core import db
from agents import strategy

if __name__ == "__main__":
    with agent_run(uuid.uuid4().hex[:8], "strategy_research") as rec:
        px, idx, uni = load_raw()
        strategy.sync_registry(db.read("select * from strategy_cards"))
        v = strategy.research(px, idx)
        rec["message"] = f"{v['chosen']}: {'passes - proposal filed' if v['passes'] else 'no edge'}"
        print(json.dumps(v, default=float, indent=2))
