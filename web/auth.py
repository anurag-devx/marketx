"""Logins for the web app (when someone else uses it over Tailscale).

Users live in ~/.marketx_users (outside the project, never in git): name -> role ('owner' | 'tester'), salt, PBKDF2-SHA256
hash. Passwords are set in Terminal with `python3 cli.py users add NAME` (typed, never shown, never sent anywhere).
Sessions: a random token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is kept (~/.marketx_sessions), 30 days.
While no user exists, login is off and the app works as before (this Mac only). Once a user exists, everyone logs in.
Wrong passwords: 5 in 10 minutes from one address locks that address for 15 minutes."""
import getpass, hashlib, hmac, json, os, secrets, sys, threading, time
from pathlib import Path

USERS = Path.home() / ".marketx_users"
SESSIONS = Path.home() / ".marketx_sessions"
ROLES = ("owner", "tester")
DAYS = 30
ITER = 240_000
COOKIE = "mx_session"
_lock = threading.Lock()
_fails = {}                      # ip -> [times]
CURRENT = threading.local()      # the signed-in user of the request being served (None when logins are off)


# what a tester may change (everything else that changes something is the owner's: downloads and every other job, radar
# builds, replays, Live start/stop, settings, keys, strategies, positions, orders, the Strategy Lab ...)
TESTER_POST = {"auth/login", "auth/logout", "ask", "deep/run", "documents/ask",
               "desk/analyse", "desk/brief", "desk/decision", "desk/notes", "desk/review", "desk/watchlist",
               "marketlab/assistant", "marketlab/explain", "tradeplan/ask", "tradeplan/explain", "tradeplan/paper",
               "live/watch", "live/note", "screener/run", "workspace/save", "workspace/delete"}
OWNER_GET = {"settings", "job/log", "job/file"}          # settings and raw job logs stay private


def allowed(who, method, name):
    """True when this user may call this API route. Logins off (who None) -> the owner on this Mac."""
    if who is None or who.get("role") == "owner":
        return True
    if method == "POST":
        return name in TESTER_POST
    return name not in OWNER_GET


def current():
    return getattr(CURRENT, "who", None)


def _read(p):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _write(p, d):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def users():
    return _read(USERS).get("users", {})


def enabled():
    return bool(users())


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), ITER).hex()


def add(name, pw, role="tester"):
    name = str(name).strip().lower()
    if not name.isalnum() or len(name) > 30:
        raise ValueError("name: letters and digits only")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if len(pw) < 10:
        raise ValueError("password: at least 10 characters")
    with _lock:
        d = _read(USERS)
        salt = secrets.token_hex(16)
        d.setdefault("users", {})[name] = dict(role=role, salt=salt, hash=_hash(pw, salt), created=time.strftime("%Y-%m-%d %H:%M"))
        _write(USERS, d)


def remove(name):
    with _lock:
        d = _read(USERS)
        d.get("users", {}).pop(str(name).lower(), None)
        _write(USERS, d)
        s = _read(SESSIONS)
        _write(SESSIONS, {k: v for k, v in s.items() if v.get("user") != str(name).lower()})


def check(name, pw, ip="?"):
    """True when the password is right. Raises PermissionError while the address is locked."""
    now = time.time()
    with _lock:
        f = [t for t in _fails.get(ip, []) if now - t < 900]
        if len([t for t in f if now - t < 600]) >= 5:
            _fails[ip] = f
            raise PermissionError("too many wrong passwords: try again in 15 minutes")
        u = users().get(str(name or "").strip().lower())
        ok = bool(u) and hmac.compare_digest(_hash(str(pw or ""), u["salt"]), u["hash"])
        if not u:
            _hash(str(pw or ""), "00" * 16)              # same time either way
        if not ok:
            _fails[ip] = f + [now]
        return ok


def _sid(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(name):
    token = secrets.token_urlsafe(32)
    with _lock:
        s = {k: v for k, v in _read(SESSIONS).items() if v.get("exp", 0) > time.time()}
        s[_sid(token)] = dict(user=str(name).lower(), exp=time.time() + DAYS * 86400)
        _write(SESSIONS, s)
    return token


def end_session(token):
    if token:
        with _lock:
            s = _read(SESSIONS)
            s.pop(_sid(token), None)
            _write(SESSIONS, s)


def who(token):
    """{'user', 'role'} for a live session, else None."""
    if not token:
        return None
    s = _read(SESSIONS).get(_sid(token))
    if not s or s.get("exp", 0) < time.time():
        return None
    u = users().get(s["user"])
    return dict(user=s["user"], role=u["role"]) if u else None


def cookie_token(header):
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v
    return None


def set_cookie(token, secure):
    return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={DAYS * 86400}" + ("; Secure" if secure else "")


def clear_cookie(secure):
    return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" + ("; Secure" if secure else "")


# ---------------------------------------------------------------- python3 cli.py users ...
def cli(argv):
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        u = users()
        if not u:
            print("No users: login is off (the app is for this Mac only).")
        for n, x in sorted(u.items()):
            print(f"  {n:20s} {x['role']:8s} added {x.get('created', '')}")
        return 0
    if cmd in ("add", "passwd") and len(argv) >= 2:
        name = argv[1]
        role = next((a.split("=", 1)[1] for a in argv[2:] if a.startswith("role=")), None)
        if cmd == "passwd":
            if name.lower() not in users():
                print("No such user."); return 1
            role = users()[name.lower()]["role"]
        role = role or ("owner" if not users() else "tester")
        p1 = getpass.getpass(f"Password for {name} ({role}): ")
        p2 = getpass.getpass("Again: ")
        if p1 != p2:
            print("The two passwords differ: nothing saved."); return 1
        try:
            add(name, p1, role)
        except ValueError as e:
            print(e); return 1
        print(f"Saved {name} ({role}) in {USERS}. Restart the app if it is running." + ("" if len(users()) > 1 else
              "\nLogin is now ON: everyone, you too, signs in."))
        return 0
    if cmd == "remove" and len(argv) >= 2:
        remove(argv[1]); print(f"Removed {argv[1]} and their sessions."); return 0
    print("usage: python3 cli.py users [list | add NAME [role=owner|tester] | passwd NAME | remove NAME]")
    return 1


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))
