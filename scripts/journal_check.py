"""Track open journal trades automatically and tell Rans on Telegram.

    python scripts/journal_check.py            # autocheck open plans, Telegram on every change
    python scripts/journal_check.py --report week   # send the weekly report to Telegram
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import journal  # noqa: E402
from lib.config import fix_console  # noqa: E402
from lib.notify import send_telegram  # noqa: E402

TEXT = {
    "taken": "📥 {coin} entry fill ho gayi ({side}).",
    "tp1": "✅ {coin} TP1 hit! 50% profit book karo, SL entry par le aao.",
    "tp2": "✅✅ {coin} TP2 hit!",
    "tp3": "🏆 {coin} TP3 hit — poora target!",
    "be": "➖ {coin} break-even par band (TP1 ke baad).",
    "sl": "❌ {coin} SL hit. Loss limited — plan ke mutabiq.",
    "expired": "⌛ {coin} entry nahi mili — plan cancel.",
}


def main() -> int:
    fix_console()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", choices=["day", "week", "all"])
    a = ap.parse_args()
    if a.report:
        rep = journal.report(a.report)
        print(rep["text"])
        send_telegram(rep["text"])
        return 0
    rows = {r["id"]: r for r in journal.load()}
    changes = journal.autocheck()
    msgs = []
    for c in changes:
        if c["from"] == c["to"]:
            continue
        r = rows.get(c["id"], {})
        t = TEXT.get(c["to"])
        if t:
            msgs.append(t.format(coin=r.get("coin", "?"), side=(r.get("side") or "").upper()))
    print(f"autocheck: {len(changes)} change(s)")
    for m in msgs:
        print(m)
    if msgs:
        send_telegram("\n".join(msgs) + "\n\n(Paper/journal tracking — apne MEXC par bhi check karo.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
