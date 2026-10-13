#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
push_secret.py — заливает sessions/token.json в секрет GitHub YT_CREDENTIALS_B64.

Запуск (из папки promobot):   python ytbot\\push_secret.py
Требует авторизованный gh и запущенный ранее login_capture.py.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "sessions" / "token.json"
SECRET_NAME = "YT_CREDENTIALS_B64"


def main() -> int:
    if not TOKEN_FILE.exists():
        print(f"!! нет {TOKEN_FILE}. Сначала python ytbot\\login_capture.py")
        return 1
    token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    if not token.get("refresh_token"):
        print("!! в token.json нет refresh_token")
        return 1
    b64 = base64.b64encode(json.dumps(token, ensure_ascii=False).encode("utf-8")).decode("ascii")

    print(f"Заливаю секрет {SECRET_NAME} в GitHub ...")
    r = subprocess.run(
        ["gh", "secret", "set", SECRET_NAME],
        input=b64, text=True, capture_output=True,
    )
    if r.returncode != 0:
        print("!! не удалось:", r.stderr.strip() or r.stdout.strip())
        return 1
    print("OK: секрет обновлён.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
