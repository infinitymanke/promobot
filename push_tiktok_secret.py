#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
push_tiktok_secret.py — заливает sessions/acc1.json в секрет GitHub TIKTOK_COOKIES_B64.

Запуск (из папки promobot):   python push_tiktok_secret.py
Требует авторизованный gh и запущенный ранее login_capture.py.
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "sessions" / "acc1.json"
SECRET_NAME = "TIKTOK_COOKIES_B64"


def main() -> int:
    if not COOKIES_FILE.exists():
        print(f"!! нет {COOKIES_FILE}. Сначала python login_capture.py")
        return 1
    data = json.loads(COOKIES_FILE.read_text(encoding="utf-8"))
    names = {c.get("name") for c in data.get("cookies", [])}
    if "sessionid" not in names:
        print("!! в acc1.json нет sessionid — вход не выполнен")
        return 1
    b64 = base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode("ascii")

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
