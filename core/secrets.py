"""Optional keys for the dashboard's extras, read from ~/.swing_env (outside the project folder):
    ANTHROPIC_API_KEY=...        Ask the system (chat)
    TELEGRAM_BOT_TOKEN=...       alerts to Telegram (optional)
    TELEGRAM_CHAT_ID=...
Never printed, logged or stored anywhere else."""
import os
from pathlib import Path

ENV = Path.home() / ".swing_env"


def get(name):
    v = os.environ.get(name)
    if v:
        return v
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, val = line.split("=", 1)
                k = k.strip().removeprefix("export ").strip()
                if k == name:
                    return val.strip().strip('"').strip("'") or None
    return None


def has(name):
    return get(name) is not None
