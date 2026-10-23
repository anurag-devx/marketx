"""Alerts after the daily run: a macOS notification and, if configured in ~/.swing_env, Telegram."""
import platform, subprocess
from . import config as C, secrets


def send(title, message):
    sent = []
    if C.ALERTS.get("macos") and platform.system() == "Darwin":
        try:
            t = title.replace('"', "'"); m = message.replace('"', "'")[:240]
            subprocess.run(["osascript", "-e", f'display notification "{m}" with title "{t}"'], timeout=10, check=False)
            sent.append("macOS")
        except Exception:
            pass
    if C.ALERTS.get("telegram") and secrets.has("TELEGRAM_BOT_TOKEN") and secrets.has("TELEGRAM_CHAT_ID"):
        try:
            import requests
            requests.post(f"https://api.telegram.org/bot{secrets.get('TELEGRAM_BOT_TOKEN')}/sendMessage",
                          data=dict(chat_id=secrets.get("TELEGRAM_CHAT_ID"), text=f"{title}\n{message}"), timeout=10)
            sent.append("Telegram")
        except Exception:
            pass
    return sent
