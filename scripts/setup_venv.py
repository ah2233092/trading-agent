"""One-time setup: create .venv and install requirements (Windows / Mac / Linux).

    Windows:  py scripts/setup_venv.py      (or: python scripts/setup_venv.py)
    Mac:      python3 scripts/setup_venv.py
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"


def main() -> int:
    is_win = os.name == "nt"
    print(f"OS: {platform.system()} {platform.release()} | Python {platform.python_version()}")
    if sys.version_info < (3, 10):
        print("❌ Python 3.10+ chahiye. python.org se naya Python install karo.")
        return 1
    if not VENV.exists():
        print("Creating .venv ...")
        venv.EnvBuilder(with_pip=True).create(VENV)
    py = VENV / ("Scripts/python.exe" if is_win else "bin/python")
    subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    env_file = ROOT / ".env"
    if not env_file.exists():
        env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print("Created .env from .env.example — ab is mein Telegram token aur chat id daalo.")
    act = r".venv\Scripts\activate" if is_win else "source .venv/bin/activate"
    print(f"\n✅ Setup complete.\nHar naye terminal mein pehle yeh chalao:  {act}")
    print("Phir: python scripts/test_data.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
