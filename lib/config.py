"""Load config.yaml + .env, plus small shared helpers (paths, time zones, console).

Usage:
    from lib.config import load_config, ROOT, display_tz
    cfg = load_config()
    cfg["scanner"]["min_alert_score"]
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"
ALERTS_DIR = ROOT / "alerts"
CACHE_DIR = ROOT / "data" / "cache"


def load_env() -> None:
    """Load .env into os.environ (real environment variables win)."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is in requirements, but never crash on it
        return
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    """Return config.yaml as a dict. Also loads .env once."""
    load_env()
    p = Path(path) if path else CONFIG_PATH
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def cfg_get(cfg: dict, dotted: str, default=None):
    """cfg_get(cfg, "scanner.min_alert_score", 60)"""
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def display_tz(cfg: dict | None = None):
    """Time zone used for human-facing times (default Asia/Karachi = PKT)."""
    name = cfg_get(cfg or load_config(), "timezone_display", "Asia/Karachi")
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        # Windows without the tzdata package has no IANA database.
        # Pakistan has no DST, so a fixed +05:00 is exact for PKT.
        if name == "Asia/Karachi":
            return timezone(timedelta(hours=5), "PKT")
        return timezone.utc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_local(dt: datetime, cfg: dict | None = None, with_date: bool = True) -> str:
    tz = display_tz(cfg)
    local = dt.astimezone(tz)
    label = local.strftime("%Z") or "local"
    if label.startswith("+") or label.startswith("UTC"):
        label = "PKT" if local.utcoffset() == timedelta(hours=5) else label
    fmt = "%Y-%m-%d %H:%M" if with_date else "%H:%M"
    return f"{local.strftime(fmt)} {label}"


def fix_console() -> None:
    """Make emoji / Urdu output safe on Windows consoles and pipes."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ensure_root_on_path() -> None:
    """Allow `python lib/x.py` and `python scanner/x.py` to import `lib.*`."""
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


if __name__ == "__main__":
    import json

    fix_console()
    c = load_config()
    print(json.dumps(c, indent=2, default=str))
    print("telegram token set:", bool(os.getenv("TELEGRAM_BOT_TOKEN")))
    print("telegram chat id set:", bool(os.getenv("TELEGRAM_CHAT_ID")))
    print("now:", fmt_local(utc_now(), c))
