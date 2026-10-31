"""Telegram notifications via the Bot API.

Token and chat id come from the environment (or .env locally):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

CLI:
    python lib/notify.py "hello"
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from lib.config import cfg_get, load_config

log = logging.getLogger("notify")
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096


def _creds() -> tuple[str, str]:
    load_config()  # also loads .env
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()


def is_configured() -> bool:
    token, chat = _creds()
    return bool(token and chat)


def _redact(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


def _post(method: str, token: str, retries: int = 3, **kwargs) -> dict | None:
    url = API.format(token=token, method=method)
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, timeout=20, **kwargs)
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200 and data.get("ok"):
                return data
            if r.status_code == 429:  # flood control
                wait = int((data.get("parameters") or {}).get("retry_after", 5))
                time.sleep(min(wait, 30))
                continue
            log.error("Telegram %s failed: HTTP %s %s", method, r.status_code,
                      _redact(str(data.get("description", r.text))[:200], token))
            if 400 <= r.status_code < 500:
                return None  # bad token / chat id — retrying will not help
        except requests.RequestException as e:
            log.warning("Telegram %s error (try %d/%d): %s", method, attempt, retries,
                        _redact(str(e), token)[:200])
        time.sleep(2 * attempt)
    return None


def send_telegram(text: str, silent: bool = False) -> bool:
    """Send a plain-text message. Returns True on success, False otherwise (never raises)."""
    if not cfg_get(load_config(), "telegram.enabled", True):
        log.info("Telegram disabled in config.yaml")
        return False
    token, chat = _creds()
    if not (token and chat):
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing) — message not sent")
        return False
    payload = {"chat_id": chat, "text": text[:MAX_LEN], "disable_web_page_preview": True,
               "disable_notification": silent}
    return _post("sendMessage", token, json=payload) is not None


def send_photo(path: str | Path, caption: str = "") -> bool:
    """Send a PNG (e.g. a chart) with an optional caption."""
    token, chat = _creds()
    if not (token and chat):
        log.warning("Telegram not configured — photo not sent")
        return False
    with open(path, "rb") as f:
        return _post("sendPhoto", token, data={"chat_id": chat, "caption": caption[:1024]},
                     files={"photo": f}) is not None


def get_updates() -> list[dict]:
    """Recent messages sent to the bot (used to find your chat id)."""
    token, _ = _creds()
    if not token:
        return []
    data = _post("getUpdates", token, json={"limit": 20})
    return (data or {}).get("result", [])


if __name__ == "__main__":
    from lib.config import fix_console

    fix_console()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print('usage: python lib/notify.py "message text"   (sends to TELEGRAM_CHAT_ID)')
        sys.exit(0)
    msg = " ".join(sys.argv[1:]) or "Test message from trading agent"
    print("sent" if send_telegram(msg) else "NOT sent")
