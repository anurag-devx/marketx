"""Cross-process lock for files written by more than one job (news store, NSE filings store)."""
import fcntl, time
from contextlib import contextmanager
from .config import DATA

LOCKS = DATA / ".locks"


@contextmanager
def locked(name, timeout=300):
    LOCKS.mkdir(parents=True, exist_ok=True)
    with open(LOCKS / f"{name}.lock", "w") as f:
        t0 = time.time()
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - t0 > timeout:
                    raise TimeoutError(f"{name} is busy (another job is writing it)")
                time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
