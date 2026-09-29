#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
login_capture.py — открывает окно браузера (Edge), ждёт, пока пользователь ВРУЧНУЮ войдёт
в TikTok, автоматически ловит появление sessionid и сохраняет куки в sessions/acc1.json.

Статус пишется в _login_status.json (для контроля извне).
"""

import json
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Нужен playwright: pip install playwright")
    sys.exit(1)

BASE = Path(__file__).resolve().parent
OUT = BASE / "sessions" / "acc1.json"
STATUS = BASE / "_login_status.json"
URL = "https://www.tiktok.com/"
KEY = {"sessionid", "sessionid_ss", "tt_csrf_token", "msToken", "ttwid", "odin_tt"}


def write_status(phase: str, saved: int = 0, has_session: bool = False):
    try:
        STATUS.write_text(
            json.dumps({"phase": phase, "saved_cookies": saved, "has_sessionid": has_session}),
            encoding="utf-8",
        )
    except Exception:
        pass


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    write_status("starting")
    with sync_playwright() as p:
        browser = None
        try:
            try:
                browser = p.chromium.launch(
                    headless=False,
                    channel="msedge",
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                )
                write_status("launched:msedge")
            except Exception:
                browser = p.chromium.launch(
                    headless=False,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                )
                write_status("launched:chromium")
            context = browser.new_context(locale="ru-RU", viewport={"width": 1280, "height": 800})
            page = context.new_page()
            page.goto(URL, wait_until="domcontentloaded")
            write_status("waiting_login")

            seen_session = False
            deadline = time.time() + 1500
            while time.time() < deadline:
                time.sleep(1.5)
                cookies = context.cookies()
                names = {c["name"] for c in cookies}
                has_session = "sessionid" in names
                if has_session != seen_session:
                    write_status("sessionid_seen" if has_session else "waiting_login", len(cookies), has_session)
                    seen_session = has_session
                if has_session:
                    wait_since = time.time()
                    while time.time() - wait_since < 2:  # убеждаемся, что сессия стабильна
                        time.sleep(0.5)
                    if "sessionid" in {c["name"] for c in context.cookies()}:
                        break
            else:
                write_status("timeout_no_login")
                print("Время вышло: вход не выполнен, окно закрывается.")
                return

            state = context.storage_state()
            cleaned = []
            for c in state.get("cookies", []):
                if not c.get("name") or c.get("value") in (None, "undefined"):
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
            OUT.write_text(json.dumps({"cookies": cleaned, "origins": []}, ensure_ascii=False, indent=2), encoding="utf-8")
            ok_names = [n for n in ("sessionid", "sessionid_ss", "tt_csrf_token", "msToken", "ttwid", "odin_tt") if n in {c["name"] for c in cleaned}]
            write_status("saved", len(cleaned), "sessionid" in ok_names)
            print(f"Сохранено кук: {len(cleaned)}. Ключевые: {', '.join(ok_names) or 'НЕТ!'}")
            if "sessionid" not in ok_names:
                print("Нет sessionid — вход не выполнен.")
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()