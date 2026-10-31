"""24/7 watchman loop for GitHub Actions (no cron needed).

One run lasts ~5h45m. Inside it:
  - every hour at :02 UTC  -> hourly scan (ready alerts) + journal TP/SL check
  - 07:06 UTC (12:06 PKT)  -> London killzone digest
  - 12:06 UTC (17:06 PKT)  -> New York killzone digest
  - Sunday 15:30 UTC       -> weekly report
After each step alerts/journal are committed and pushed. Before the run ends, the workflow starts
the next run itself (see .github/workflows/watchman-loop.yml).

    python scripts/watch_loop.py --hours 5.75
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
RESULT = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "scan_result.json"


def log(msg: str) -> None:
    print(f"[loop {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def sh(*cmd: str, check: bool = False) -> int:
    r = subprocess.run(list(cmd), cwd=ROOT)
    if check and r.returncode:
        raise SystemExit(r.returncode)
    return r.returncode


def git(*args: str) -> int:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).returncode


def sync() -> None:
    """Get changes made from chat (closed alerts, journal) before scanning."""
    if git("pull", "-q", "--rebase", "origin", "main"):
        git("rebase", "--abort")
        git("fetch", "-q", "origin", "main")
        git("reset", "-q", "--hard", "origin/main")


def publish(msg: str) -> None:
    paths = ["alerts", "journal", "backtest/results/setup_stats.json"]
    git("add", *paths)
    if not git("diff", "--cached", "--quiet"):
        return
    git("commit", "-q", "-m", f"{msg} [skip ci]")
    for i in range(4):
        if not git("push", "-q", "origin", "HEAD:main"):
            log("pushed")
            return
        if git("pull", "-q", "--rebase", "origin", "main"):
            git("rebase", "--abort")
            git("fetch", "-q", "origin", "main")
            git("reset", "-q", "--hard", "origin/main")
            if RESULT.exists():
                sh(PY, "scanner/run_scan.py", "--apply", str(RESULT))
            git("add", *paths)
            if git("diff", "--cached", "--quiet"):
                git("commit", "-q", "-m", f"{msg} [skip ci]")
        time.sleep(3 * (i + 1))
    log("push failed after retries")


def events_between(start: datetime, end: datetime) -> list[tuple[datetime, str]]:
    out = []
    t = start.replace(minute=0, second=0, microsecond=0)
    while t <= end:
        for minute, kind in ((2, "hourly"), (6, "digest")):
            e = t.replace(minute=minute)
            if kind == "digest" and t.hour not in (7, 12):
                continue
            if start < e <= end:
                out.append((e, "london" if (kind == "digest" and t.hour == 7) else
                            "newyork" if kind == "digest" else "hourly"))
        if t.weekday() == 6 and t.hour == 15:
            e = t.replace(minute=30)
            if start < e <= end:
                out.append((e, "weekly"))
        t += timedelta(hours=1)
    return sorted(out)


def run_event(kind: str) -> None:
    sync()
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    if kind == "weekly":
        sh(PY, "scripts/journal_check.py", "--report", "week")
        return
    args = [PY, "scanner/run_scan.py", "--save-result", str(RESULT)]
    if kind in ("london", "newyork"):
        args += ["--digest", kind]
    sh(*args)
    sh(PY, "scripts/journal_check.py")
    publish(f"watchman ({kind}): alerts {stamp}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.75)
    ap.add_argument("--now", choices=["hourly", "london", "newyork"], help="run one event immediately first")
    a = ap.parse_args()
    start = datetime.now(timezone.utc)
    end = start + timedelta(hours=a.hours)
    if a.now:
        run_event(a.now)
    plan = events_between(start, end)
    log(f"loop until {end:%H:%M}Z, {len(plan)} events: " + json.dumps([f"{t:%H:%M} {k}" for t, k in plan]))
    for t, kind in plan:
        wait = (t - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        log(f"event {kind}")
        try:
            run_event(kind)
        except Exception as e:  # keep looping
            log(f"event {kind} failed: {e}")
    log("loop finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
