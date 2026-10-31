"""Fill .env with the Telegram bot token and chat id (asks only for what is missing).

    python scripts/fill_env.py [--chat-id <your chat id>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"


def read_env() -> list[str]:
    if ENV.exists():
        return ENV.read_text(encoding="utf-8").splitlines()
    return (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()


def get(lines: list[str], key: str) -> str:
    for ln in lines:
        if ln.startswith(key + "="):
            return ln.split("=", 1)[1].strip()
    return ""


def put(lines: list[str], key: str, value: str) -> list[str]:
    out, done = [], False
    for ln in lines:
        if ln.startswith(key + "="):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{key}={value}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chat-id", default="")
    args = ap.parse_args()

    lines = read_env()
    if args.chat_id and not get(lines, "TELEGRAM_CHAT_ID"):
        lines = put(lines, "TELEGRAM_CHAT_ID", args.chat_id.strip())

    if not get(lines, "TELEGRAM_CHAT_ID"):
        print()
        print("Telegram chat id chahiye (sirf number, jaise 123456789). Paste karo, phir Enter:")
        cid = input("> ").strip()
        if not cid.lstrip("-").isdigit():
            print("❌ Chat id sirf number hota hai. Setup dobara chalao.")
            return 1
        lines = put(lines, "TELEGRAM_CHAT_ID", cid)

    if not get(lines, "TELEGRAM_BOT_TOKEN"):
        print()
        print("Telegram bot token chahiye (BotFather wala, '123456789:AA...' jaisa).")
        print("Copy karo, yahan right-click (ya Ctrl+V) se paste karo, phir Enter dabao:")
        token = input("> ").strip().strip('"').strip("'")
        if ":" not in token or len(token) < 30:
            print("❌ Ye token sahi nahi lag raha. Setup dobara chalao aur poora token paste karo.")
            return 1
        lines = put(lines, "TELEGRAM_BOT_TOKEN", token)

    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("✅ .env tayyar (token + chat id).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
