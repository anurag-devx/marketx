"""After-close schedule on macOS (launchd): runs `cli.py daily` on weekdays at a set time.
If the Mac is asleep at that time, launchd runs it when it wakes."""
import platform, plistlib, subprocess, sys
from pathlib import Path
from .config import BASE

LABEL = "com.swing.daily"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = BASE / "logs" / "scheduled.log"


def supported():
    return platform.system() == "Darwin"


def status():
    if not PLIST.exists():
        return None
    d = plistlib.loads(PLIST.read_bytes())
    t = d.get("StartCalendarInterval", [{}])[0]
    return dict(hour=t.get("Hour"), minute=t.get("Minute"), weekdays=sorted({x.get("Weekday") for x in d.get("StartCalendarInterval", [])}))


def install(hour=16, minute=20, weekdays=(1, 2, 3, 4, 5)):
    if not supported():
        raise RuntimeError("Scheduling is set up for macOS only.")
    LOG.parent.mkdir(exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    d = dict(Label=LABEL, ProgramArguments=[sys.executable, str(BASE / "cli.py"), "daily"], WorkingDirectory=str(BASE),
             StartCalendarInterval=[dict(Weekday=w, Hour=int(hour), Minute=int(minute)) for w in weekdays],
             StandardOutPath=str(LOG), StandardErrorPath=str(LOG), RunAtLoad=False)
    if PLIST.exists():
        subprocess.run(["launchctl", "unload", str(PLIST)], check=False, capture_output=True)
    PLIST.write_bytes(plistlib.dumps(d))
    r = subprocess.run(["launchctl", "load", str(PLIST)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "launchctl load failed")


def remove():
    if PLIST.exists():
        subprocess.run(["launchctl", "unload", str(PLIST)], check=False, capture_output=True)
        PLIST.unlink()
