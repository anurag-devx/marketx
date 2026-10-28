"""Last step of Fetch + run all: starts a Kronos forecast in Kronos's own lane when the nightly toggle is on
(Market Lab → Kronos → Run → Nightly). Runs in the main environment and only launches the job; it never waits for it,
so the night's run finishes as before. Off, not installed, or a Kronos job already running: does nothing."""
import json, sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SETTINGS = BASE / "results" / "kronos" / "settings.json"


def main():
    try:
        s = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        s = {}
    if not s.get("nightly"):
        print("Kronos nightly forecast: off (Market Lab → Kronos → Run).")
        return 0
    if not ((BASE / ".venv-kronos" / "bin" / "python").exists() and (BASE / "kronos_lab" / "vendor" / "Kronos" / "model").exists()):
        print("Kronos nightly forecast: on, but Kronos isn't installed. Skipped.")
        return 0
    sys.path.insert(0, str(BASE))
    from core import jobs
    if jobs.busy("kronos-forecast"):
        print("Kronos nightly forecast: a Kronos job is already running. Skipped.")
        return 0
    args = [a for a in s.get("args") or [] if not a.startswith(("replay=", "step="))]
    log = jobs.start("kronos-forecast", args)
    print(f"Kronos nightly forecast started in its own lane ({' '.join(args) or 'default options'}): {log.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
