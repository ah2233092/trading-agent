"""SessionStart hook: pull the repo and show pending watchman alerts.

Standard library only (no venv needed), finishes in a few seconds, never fails
the session. Output is JSON for Claude Code: `systemMessage` is shown to Rans,
`additionalContext` goes to Claude. Use --text for plain output.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "alerts" / "pending.json"
CONFIG = ROOT / "config.yaml"
PKT = timezone(timedelta(hours=5), "PKT")  # Pakistan has no DST

LABELS = {"sweep": "sweep", "structure": "1H BOS/CHoCH", "near_poi": "near 4H POI", "volume": "volume"}


def git_pull() -> bool:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    try:
        r = subprocess.run(["git", "pull", "--quiet", "--ff-only"], cwd=ROOT, env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


def max_age_hours() -> float:
    try:
        m = re.search(r"^alert_max_age_hours:\s*([\d.]+)", CONFIG.read_text(encoding="utf-8"), re.M)
        return float(m.group(1)) if m else 3.0
    except Exception:
        return 3.0


def load_alerts() -> list[dict]:
    try:
        data = json.loads(PENDING.read_text(encoding="utf-8"))
        alerts = data.get("alerts", []) if isinstance(data, dict) else data
        return [a for a in alerts if isinstance(a, dict)]
    except Exception:
        return []


def line(a: dict, now: datetime, max_age: float) -> str:
    pair = a.get("pair") or str(a.get("symbol", a.get("coin", "?"))).split(":")[0]
    hint = a.get("direction_hint", "?")
    try:
        created = datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))
        local = created.astimezone(PKT)
        when = local.strftime("%H:%M PKT") if local.date() == now.astimezone(PKT).date() \
            else local.strftime("%d %b %H:%M PKT")
        age_h = (now - created).total_seconds() / 3600
    except Exception:
        when, age_h = a.get("created_at_pkt", "?"), 0.0
    trig = " + ".join(a.get("trigger_labels") or [LABELS.get(t, t) for t in a.get("triggers", [])]) or "-"
    stale = f" ⏳ {age_h:.0f}h purana, re-check" if age_h > max_age else ""
    return f"- {pair} ({hint} hint) — {when} — {trig} — score {a.get('light_score', '?')}{stale}"


def build_message() -> tuple[str, int]:
    pulled = git_pull()
    alerts = load_alerts()
    now = datetime.now(timezone.utc)
    offline = "" if pulled else " (git pull nahi hua — local copy)"
    if not alerts:
        return f"📭 Koi pending alert nahi.{offline}", 0
    max_age = max_age_hours()
    rows = [line(a, now, max_age) for a in alerts]
    head = f"📬 {len(alerts)} pending alert{'s' if len(alerts) != 1 else ''}:{offline}"
    return "\n".join([head, *rows, 'Rans ko pucho: "Scan karoon?" → /deep-scan']), len(alerts)


def main() -> None:
    try:
        msg, n = build_message()
    except Exception as e:  # never break the session
        msg, n = f"(session_start: {type(e).__name__})", 0
    if "--text" in sys.argv:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print(msg)
        return
    context = msg
    if n:
        context += ("\n\nThese are watchman alerts from alerts/pending.json. Show them to Rans and ask "
                    "(in Roman Urdu) whether to run /deep-scan. Do not give entry/SL/TP before a deep scan.")
    # ensure_ascii keeps the output ASCII-safe on Windows code pages
    print(json.dumps({
        "systemMessage": msg,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context},
    }))


if __name__ == "__main__":
    main()
