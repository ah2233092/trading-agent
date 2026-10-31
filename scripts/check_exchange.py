"""Connectivity check for the data exchange (run first in GitHub Actions).

Tests the main exchange (config.yaml `exchange`, normally MEXC). If it is
blocked or down, tests `fallback_exchanges` in order and picks the first one
that works. With --github it exports DATA_EXCHANGE=<id> to $GITHUB_ENV so the
scanner uses it, and writes a warning + step summary.

    python scripts/check_exchange.py
    python scripts/check_exchange.py --github
Exit code 1 if no exchange works.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import cfg_get, fix_console, load_config  # noqa: E402
from lib.data import check_exchange  # noqa: E402


def main() -> int:
    fix_console()
    github = "--github" in sys.argv
    cfg = load_config()
    main_id = str(cfg_get(cfg, "exchange", "mexc")).lower()
    order = [main_id] + [e for e in (cfg_get(cfg, "fallback_exchanges", []) or []) if e != main_id]

    chosen, report = None, []
    for ex_id in order:
        ok, msg, sec = check_exchange(ex_id)
        report.append((ex_id, ok, msg, sec))
        print(f"{'OK  ' if ok else 'FAIL'} {ex_id:<14} {sec:5.1f}s  {msg}", flush=True)
        if ok:
            chosen = ex_id
            break

    if github:
        if chosen:
            with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as f:
                f.write(f"DATA_EXCHANGE={chosen}\n")
        if chosen and chosen != main_id:
            print(f"::warning title=MEXC blocked on this runner::{main_id} unreachable "
                  f"({report[0][2][:150]}). Using {chosen} public data for the same coins.")
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write("### Data source check\n\n| Exchange | Result | Time | Detail |\n|---|---|---:|---|\n")
                for ex_id, ok, msg, sec in report:
                    f.write(f"| {ex_id} | {'✅' if ok else '❌'} | {sec:.1f}s | {msg[:120]} |\n")
                f.write(f"\n**Using:** `{chosen or 'none'}`\n\n")

    if not chosen:
        print("No exchange reachable — scan cannot run.", file=sys.stderr)
        return 1
    print(f"Using data from: {chosen}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
