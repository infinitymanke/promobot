#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: warmup.py — разогрев алгоритма: ставим лайки и подписки на майнкрафт-контент
в реальном chromium (Actions, xvfb), чтобы лента вернула свежие майнкрафт-видео.
Цели берутся из поиска (старые видео узки — для лайков норм) + активные аккаунты ниши.
"""

import base64
import datetime
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
VIDEO_QUERIES = ["#майнкрафт", "#майнкрафтсервер", "#майншилд", "#майнкрафтвыживание"]
MAX_LIKES = 18
MAX_FOLLOWS = 5


def get_cookies() -> dict:
    """Возвращает storage_state: {"cookies": [...]} (из секрета или файла)."""
    b64 = os.environ.get("TIKTOK_COOKIES_B64", "")
    if b64:
        try:
            return json.loads(base64.b64decode(b64))
        except Exception as exc:
            print("!! bad TIKTOK_COOKIES_B64:", exc)
    p = Path("sessions/acc1.json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"cookies": []}


def get_cookie_file() -> Path:
    state = get_cookies()
    state_path = Path("sessions/_env.json")
    state_path.parent.mkdir(exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state_path


def collect_targets() -> tuple:
    """(video_ids, author_handles) — для лайков и подписок."""
    ids = []
    handles = set()
    try:
        from tiktok import TikTokClient
        c = TikTokClient(region="RU", language="ru", impersonate="chrome131")
        c.load_cookies(str(get_cookie_file()))
        for q in VIDEO_QUERIES:
            try:
                for it in c.search_videos(q, count=8)[:8]:
                    aid = c.aweme_id(it)
                    if aid and aid not in ids:
                        ids.append(aid)
                    au = it.get("author") or {}
                    h = au.get("uniqueId")
                    if h:
                        handles.add(h)
            except Exception:
                continue
        for u in c.search_users("майнкрафт", count=6):
            h = u.get("unique_id")
            if h:
                handles.add(h)
        c.close()
    except Exception as exc:
        print("!! сбор целей:", exc)
    return ids[:MAX_LIKES], list(handles)[:MAX_FOLLOWS]


def main() -> int:
    cookies = get_cookies()
    if not cookies.get("cookies"):
        print("!! нет кук")
        return 1
    video_ids, follow_handles = collect_targets()
    print("цели: лайков видео =", len(video_ids), "| подписок =", len(follow_handles))

    liked = followed = already = failed = 0
    detail = []

    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = b.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        ctx.add_cookies(cookies.get("cookies", []))
        pg = ctx.new_page()

        def click_like():
            try:
                like = pg.query_selector('[data-e2e="like-icon"], [data-e2e="like-button"]')
                if not like:
                    return "no-btn"
                aria = (like.get_attribute("aria-label") or "").lower()
                if "unlike" in aria or "не нравится" in aria:
                    return "already"
                like.click()
                time.sleep(1.5)
                return "liked"
            except Exception:
                return "err"

        # 1) лайки на видео ниши
        for idx, vid in enumerate(video_ids):
            url = f"https://www.tiktok.com/video/{vid}"
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                detail.append({"id": vid, "like": "goto-err", "why": str(exc)[:40]})
                continue
            pg.wait_for_timeout(3500)
            st = click_like()
            if st == "liked":
                liked += 1
            elif st == "already":
                already += 1
            else:
                failed += 1
            detail.append({"id": vid, "like": st})
            time.sleep(2 if st == "liked" else 0.5)
            if liked >= MAX_LIKES:
                break

        # 2) подписки на каналы ниши
        for h in follow_handles:
            try:
                pg.goto(f"https://www.tiktok.com/@{h}", wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3000)
                btn = pg.query_selector('[data-e2e="follow-button"]')
                if btn:
                    txt = (btn.inner_text() or "").strip().lower()
                    if "follow" in txt or "подпис" in txt or "відписа" in txt:
                        btn.click()
                        followed += 1
                        time.sleep(2)
            except Exception:
                continue
            if followed >= MAX_FOLLOWS:
                break

        ctx.close()
        b.close()

    print(f"WARM_RESULT likes={liked} already={already} follow={followed} failed={failed}")
    print(json.dumps(detail[:20], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())