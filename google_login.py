#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
google_login.py — повторный вход в TikTok через Google OAuth (если TikTok снова разлогинил).
Открывает окно браузера, жмёт «Продолжить с Google», вводит почту; пароль/2FA
пользователь вводит сам в видимом окне. Удачный вход → sessions/acc1.json.
Дальше: python push_tiktok_secret.py
Почта: env TIKTOK_EMAIL или файл sessions/.email, либо спросит в консоли.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
TMP = BASE
OUT = BASE / "sessions" / "acc1.json"
STATUS = TMP / "login_status.json"

EMAIL = os.environ.get("TIKTOK_EMAIL", "")
if not EMAIL:
    f = BASE / "sessions" / ".email"
    if f.exists():
        EMAIL = f.read_text(encoding="utf-8").strip()
if not EMAIL:
    EMAIL = input("Почта TikTok/Google: ").strip()

DEADLINE = time.time() + 720


def write_status(phase: str, detail=""):
    try:
        STATUS.write_text(
            json.dumps({"phase": phase, "detail": str(detail)[:900], "ts": time.time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    print(f"[status] {phase} {str(detail)[:150]}", flush=True)


def save_cookies(context) -> set:
    state = context.storage_state()
    cleaned = []
    for c in state.get("cookies", []):
        if not c.get("name") or c.get("value") in (None, "undefined"):
            continue
        cleaned.append({
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1),
            "httpOnly": c.get("httpOnly", False),
            "secure": c.get("secure", False),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cookies": cleaned, "origs": []}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {c["name"] for c in cleaned}


def has_session(context) -> bool:
    return any(c["name"] == "sessionid" for c in context.cookies())


def body_text(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def google_window(context):
    for pg in context.pages:
        try:
            if "accounts.google.com" in pg.url or "google.com/o/oauth" in pg.url or "gsi" in pg.url:
                return pg
        except Exception:
            continue
    return None


def fill_google_email(pg) -> bool:
    for sel in ("input#identifierId", "input[type='email']", "input[name='identifier']"):
        try:
            loc = pg.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.fill(EMAIL)
                pg.wait_for_timeout(800)
                return True
        except Exception:
            continue
    return False


def click_google_next(pg) -> bool:
    for sel in ("#identifierNext button", "#passwordNext button",
                "button:has-text('Далее')", "button:has-text('Next')",
                "div[role='button']:has-text('Далее')", "div[role='button']:has-text('Next')"):
        try:
            loc = pg.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=4000)
                return True
        except Exception:
            continue
    return False


def click_google_consent(pg) -> bool:
    for sel in ("button:has-text('Продолжить')", "div[role='button']:has-text('Продолжить')",
                "button:has-text('Continue')", "div[role='button']:has-text('Continue')",
                "button:has-text('Разрешить')", "button:has-text('Allow')"):
        try:
            loc = pg.locator(sel)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                try:
                    if el.is_visible():
                        el.click(timeout=4000)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def choose_google_account(pg) -> bool:
    try:
        loc = pg.get_by_text("alfredofivasikov", exact=False).first
        if loc.count() and loc.is_visible():
            loc.click(timeout=4000)
            return True
    except Exception:
        pass
    return False


def tiktok_logged_in(context) -> bool:
    for pg in context.pages:
        try:
            if "tiktok.com" in pg.url:
                txt = body_text(pg)
                if "Войти" not in txt and ("Профиль" in txt or "Выйти" in txt or "Загрузить" in txt):
                    return True
        except Exception:
            continue
    return False


def main() -> int:
    write_status("starting")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = None
        try:
            try:
                browser = p.chromium.launch(
                    headless=False, channel="msedge",
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                )
                write_status("launched:msedge")
            except Exception:
                browser = p.chromium.launch(
                    headless=False,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                )
                write_status("launched:chromium")

            ctx = browser.new_context(
                locale="ru-RU",
                viewport={"width": 1280, "height": 800},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            )
            page = ctx.new_page()
            page.goto("https://www.tiktok.com/login/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(6000)
            write_status("login_page_loaded", page.title())

            if has_session(ctx):
                names = save_cookies(ctx)
                write_status("saved", f"already_logged_in cookies={len(names)}")
                return 0

            clicked = False
            for sel in ("div[role='link']:has-text('Google')", "div:has-text('Продолжить с Google')"):
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
            write_status("google_clicked", f"ok={clicked}")
            if not clicked:
                return 1

            gwin = None
            for _ in range(20):
                page.wait_for_timeout(1500)
                gwin = google_window(ctx)
                if gwin:
                    break
            if not gwin:
                write_status("no_google_window", json.dumps([pg.url[:80] for pg in ctx.pages], ensure_ascii=False))
                return 1
            write_status("google_window", gwin.url[:80])
            try:
                gwin.bring_to_front()
            except Exception:
                pass

            chosen = choose_google_account(gwin)
            if chosen:
                write_status("account_chosen")
                page.wait_for_timeout(2500)

            email_done = fill_google_email(gwin)
            write_status("google_email", f"filled={chosen or email_done}")
            if email_done:
                page.wait_for_timeout(800)
                click_google_next(gwin)
                write_status("google_next_clicked")

            wrote_manual = False
            while time.time() < DEADLINE:
                page.wait_for_timeout(2500)

                if has_session(ctx):
                    names = save_cookies(ctx)
                    if tiktok_logged_in(ctx) or True:
                        write_status("saved", f"cookies={len(names)}")
                        return 0

                gwin = google_window(ctx)
                if gwin is None:
                    closed = [pg2.url[:60] for pg2 in ctx.pages]
                    if len(ctx.pages) == 1 and "tiktok.com" in ctx.pages[0].url:
                        page.wait_for_timeout(4000)
                        if has_session(ctx):
                            names = save_cookies(ctx)
                            write_status("saved", f"cookies={len(names)}")
                            return 0
                    write_status("google_closed_no_session", json.dumps(closed, ensure_ascii=False))
                    page.wait_for_timeout(3000)
                    continue

                txt = ""
                try:
                    txt = body_text(gwin)
                except Exception:
                    pass

                if "o/oauth" in gwin.url or "consent" in gwin.url or "Выберите аккаунт" in txt or "Choose an account" in txt:
                    if choose_google_account(gwin):
                        write_status("account_chosen")
                        page.wait_for_timeout(2000)
                        continue
                    if click_google_consent(gwin):
                        write_status("consent_clicked")
                        page.wait_for_timeout(3000)
                        continue

                try:
                    pw = gwin.locator("input[type='password']").first
                    if pw.count() and pw.is_visible():
                        if not wrote_manual:
                            write_status("google_manual", "введи пароль и 2FA в окне Google сам(а)")
                            wrote_manual = True
                        continue
                except Exception:
                    pass

                try:
                    ident = gwin.locator("input#identifierId, input[type='email']").first
                    if ident.count() and ident.is_visible():
                        cur = ""
                        try:
                            cur = ident.input_value()
                        except Exception:
                            pass
                        if EMAIL not in cur:
                            fill_google_email(gwin)
                            page.wait_for_timeout(800)
                            click_google_next(gwin)
                            write_status("google_refilled")
                        continue
                except Exception:
                    pass

                if click_google_consent(gwin):
                    write_status("consent_clicked")
                    page.wait_for_timeout(3000)
                    continue

            if has_session(ctx):
                names = save_cookies(ctx)
                write_status("saved", f"cookies={len(names)}")
                return 0
            write_status("timeout")
            return 1
        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
