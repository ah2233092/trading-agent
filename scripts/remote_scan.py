"""Run the live deep scan on GitHub Actions and bring the results here (for Claude chat sessions
that cannot reach MEXC directly).

    python scripts/remote_scan.py                 # coins in alerts/pending.json
    python scripts/remote_scan.py --coins SOL,ETH

Needs the GitHub CLI (`gh`) with access to the repo. Results land in scan_output/:
  scan_output/out/<COIN>.txt                 human answer + details
  scan_output/out/reports/<COIN>/latest.json full report (finalize it after the news check:
      python lib/deep_scan.py --finalize scan_output/out/reports/<COIN>/latest.json --news clear)
  scan_output/charts/<COIN>/*.png            1D / 4H / 1H / 15m charts
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "ah2233092/trading-agent"
WF = "deep-scan-test.yml"
OUT = ROOT / "scan_output"


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default="pending")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    req = uuid.uuid4().hex[:8]
    gh("api", "-X", "POST", f"repos/{a.repo}/actions/workflows/{WF}/dispatches", "-f", "ref=main",
       "-F", f"inputs[coins]={a.coins}", "-F", f"inputs[request_id]={req}")
    print(f"Deep scan started on GitHub ({a.coins}, id {req}) ...", flush=True)
    t0 = time.time()
    run = None
    while time.time() - t0 < a.timeout:
        time.sleep(8)
        runs = json.loads(gh("api", f"repos/{a.repo}/actions/workflows/{WF}/runs?per_page=10"))["workflow_runs"]
        run = next((r for r in runs if req in (r.get("display_title") or r.get("name") or "")), None)
        if run and run["status"] == "completed":
            break
    if not run or run["status"] != "completed":
        print("❌ Timed out waiting for the GitHub run.")
        return 1
    print(f"Run {run['id']}: {run['conclusion']} ({time.time() - t0:.0f}s)")
    if OUT.exists():
        shutil.rmtree(OUT)
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", "scan-output",
                    f"https://github.com/{a.repo}", str(OUT)], check=True)
    rq = (OUT / "out" / "request.txt")
    if not rq.exists() or req not in rq.read_text():
        print("⚠️ scan-output branch belongs to another run — try again.")
        return 1
    for p in sorted((OUT / "out").glob("*.txt")):
        if p.name == "request.txt":
            continue
        print(f"\n===== {p.stem} =====")
        print(p.read_text(encoding="utf-8"))
    charts = sorted((OUT / "charts").rglob("*.png")) if (OUT / "charts").exists() else []
    if charts:
        print("\nCharts:")
        for c in charts:
            print(f"  {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
