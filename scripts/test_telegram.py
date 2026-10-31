"""Telegram check.

    python scripts/test_telegram.py            # sends "✅ Trading agent connected"
    python scripts/test_telegram.py --chat-id  # shows chat ids of people who messaged your bot
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.config import fix_console  # noqa: E402
from lib.notify import _creds, _post, get_updates, send_telegram  # noqa: E402


def show_chat_ids() -> int:
    token, _ = _creds()
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN missing. .env file mein token daalo pehle.")
        return 1
    me = _post("getMe", token)
    if not me:
        print("❌ Token kaam nahi kar raha (Telegram ne reject kiya). Secret mein token dobara sahi paste karo.")
        return 1
    bot = (me.get("result") or {}).get("username", "?")
    print(f"✅ Token sahi hai — bot: @{bot}")
    hook = _post("getWebhookInfo", token) or {}
    if (hook.get("result") or {}).get("url"):
        _post("deleteWebhook", token)
        print("(webhook hata diya taake messages mil sakein)")
    updates = get_updates()
    print(f"Bot ko mile messages: {len(updates)}")
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            chats[chat["id"]] = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
    if not chats:
        print("Koi message nahi mila. Telegram mein apne bot ko 'hi' bhejo, phir dobara chalao.")
        return 1
    for cid, name in chats.items():
        print(f"chat id: {cid}   ({name})")
    print("Yeh number .env mein TELEGRAM_CHAT_ID= ke aage likho.")
    return 0


def main() -> int:
    fix_console()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if "--chat-id" in sys.argv:
        return show_chat_ids()
    ok = send_telegram("✅ Trading agent connected")
    print("✅ Message bhej diya — phone check karo." if ok else
          "❌ Message nahi gaya. Upar wala error dekho (token / chat id check karo).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
