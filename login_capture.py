#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: login_capture.py
Локальная утилита: открывает браузер, ждёт, пока пользователь ВРУЧНУЮ войдёт в TikTok,
и сохраняет свежие куки в sessions/acc1.json (совместимо с promobot).

Запуск:  pip install playwright pyperclip  &&  playwright install chromium
         python login_capture.py
Нужен только когда куки протухают (бот предупреждает в Telegram).
"""

import json
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Нужен playwright: pip install playwright && playwright install chromium")
    sys.exit(1)

OUT = Path(__file__).resolve().parent / "sessions" / "acc1.json"
URL = "https://www.tiktok.com/"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized", "--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            locale="ru-RU",
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded")

        print("=" * 64)
        print("Войдите в свой аккаунт TikTok в открывшемся окне.")
        print("Когда будете на главной ленте (видно видео) — нажмите Enter здесь.")
        print("Куки будут сохранены в sessions/acc1.json")
        print("=" * 64)
        input()

        state = context.storage_state()
        # прибираем мусорные куки, оставляем только httponly/важные поля
        cleaned = []
        for c in state.get("cookies", []):
            if not c.get("name") or c.get("value") == "undefined":
                continue
            cleaned.append(
                {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                    "expires": c.get("expires", -1),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False),
                }
            )
        payload = {"cookies": cleaned, "origins": []}
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        ok_names = [n for n in ("sessionid", "sessionid_ss", "tt_csrf_token", "msToken", "ttwid", "odin_tt") if n in {c["name"] for c in cleaned}]
        print(f"Сохранено кук: {len(cleaned)}. Ключевые: {', '.join(ok_names) or 'НЕТ!'}")
        if "sessionid" not in ok_names:
            print("⚠️ Нет sessionid — кажется, вход не выполнен. Повторите логин.")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()