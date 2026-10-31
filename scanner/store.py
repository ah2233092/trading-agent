"""Alert storage: alerts/pending.json, alerts/state.json (cooldowns), alerts/history/.

Files are small JSON documents so they diff cleanly in git:
    pending.json  {"alerts": [ {...}, ... ]}
    state.json    {"cooldowns": {"SOL/USDT:USDT": "2026-10-31T06:02:11Z"}}
    history/YYYY-MM-DD.json  {"alerts": [ {..., "status": "expired", "closed_at": ...} ]}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.config import ALERTS_DIR, iso_utc

PENDING = ALERTS_DIR / "pending.json"
STATE = ALERTS_DIR / "state.json"
HISTORY = ALERTS_DIR / "history"


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def load_pending() -> list[dict]:
    data = load_json(PENDING, {"alerts": []})
    return data.get("alerts", []) if isinstance(data, dict) else list(data)


def save_pending(alerts: list[dict]) -> None:
    save_json(PENDING, {"alerts": alerts})


def load_state() -> dict:
    data = load_json(STATE, {})
    data.setdefault("cooldowns", {})
    return data


def in_cooldown(state: dict, symbol: str, now: datetime, hours: float) -> bool:
    last = state.get("cooldowns", {}).get(symbol)
    return bool(last) and now - parse_iso(last) < timedelta(hours=hours)


def append_history(alerts: list[dict], status: str, now: datetime) -> None:
    if not alerts:
        return
    path = HISTORY / f"{now:%Y-%m-%d}.json"
    data = load_json(path, {"alerts": []})
    for a in alerts:
        data["alerts"].append(dict(a, status=status, closed_at=iso_utc(now)))
    save_json(path, data)


def add_alerts(new: list[dict], now: datetime, cooldown_hours: float, expire_hours: float) -> dict:
    """Append new alerts, update cooldowns, move stale pending alerts to history.

    Returns {"added": n, "expired": n, "changed": bool}. Writes files only when
    something actually changed (keeps git history quiet).
    """
    pending = load_pending()
    state = load_state()
    ids = {a.get("id") for a in pending}

    keep, expired = [], []
    for a in pending:
        try:
            old = now - parse_iso(a["created_at"]) > timedelta(hours=expire_hours)
        except Exception:
            old = False
        (expired if old else keep).append(a)

    added = [a for a in new if a.get("id") not in ids]
    keep.extend(added)
    for a in added:
        state["cooldowns"][a["symbol"]] = a["created_at"]
    # forget cooldowns that ended long ago
    state["cooldowns"] = {s: t for s, t in state["cooldowns"].items()
                          if now - parse_iso(t) < timedelta(hours=max(cooldown_hours, 1) * 4)}

    changed = bool(added or expired)
    if changed:
        append_history(expired, "expired", now)
        save_pending(keep)
        save_json(STATE, state)
    return {"added": len(added), "expired": len(expired), "changed": changed}
