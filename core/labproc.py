"""Starts the Strategy Lab app (port 8502) in the background from the dashboard, if it isn't running."""
import socket, subprocess, sys
from .config import BASE

PORT = 8502
URL = f"http://localhost:{PORT}"


def running():
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def start():
    if running():
        return
    log = open(BASE / "logs" / "lab.log", "a")
    subprocess.Popen([sys.executable, "-m", "streamlit", "run", "lab_app.py", "--server.port", str(PORT), "--server.headless", "true",
                      "--server.address", "localhost", "--browser.gatherUsageStats", "false", "--logger.level", "error"],
                     cwd=BASE, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
