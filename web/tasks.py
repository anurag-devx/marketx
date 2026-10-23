"""Short in-process background tasks (deep search, Strategy Lab test): run in a thread, poll with api/task?id=.
Kept in memory only; a server restart forgets them (their results are saved by the code they run)."""
import threading, time, traceback, uuid

_tasks, _lock = {}, threading.Lock()
MAX_KEEP = 30


def start(kind, fn):
    """fn(log, progress) -> result (JSON-able). Returns the task id. One running task per kind."""
    with _lock:
        for t in _tasks.values():
            if t["kind"] == kind and t["running"]:
                raise RuntimeError("Already running: wait for it to finish.")
        tid = uuid.uuid4().hex[:12]
        t = dict(id=tid, kind=kind, running=True, log=[], frac=None, msg="Starting", result=None, error=None, started=time.time(), seconds=0)
        _tasks[tid] = t
        old = sorted(_tasks.values(), key=lambda x: x["started"])[:-MAX_KEEP]
        for o in old:
            if not o["running"]:
                _tasks.pop(o["id"], None)

    def log(m):
        t["log"] = (t["log"] + [str(m)])[-200:]
        t["msg"] = str(m)

    def progress(f, m=None):
        t["frac"] = None if f is None else max(0.0, min(1.0, float(f)))
        if m:
            t["msg"] = str(m)

    def run():
        try:
            t["result"] = fn(log, progress)
        except Exception as e:                      # reported to the page
            t["error"] = str(e) or type(e).__name__
            t["log"].append(traceback.format_exc(limit=3))
        finally:
            t["running"] = False
            t["seconds"] = round(time.time() - t["started"])

    threading.Thread(target=run, daemon=True, name=f"task-{kind}").start()
    return tid


def get(tid):
    t = _tasks.get(tid)
    if not t:
        return None
    return {k: v for k, v in t.items()} | {"seconds": t["seconds"] if not t["running"] else round(time.time() - t["started"])}


def running(kind):
    return next((t["id"] for t in _tasks.values() if t["kind"] == kind and t["running"]), None)
