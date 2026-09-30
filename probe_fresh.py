#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: probe_fresh.py — диагностика свежести контента TikTok для данной сессии.
Запускается НА GITHUB ACTIONS (chromium + xvfb). Открывает ленту и тэг-страницы ниши,
перехватывает API-ответы TikTok и печатает PROBE_RESULT: сколько видео, сколько свежих
(<=60 дней), сколько майнкрафт-тематики, дата самой новой.
"""

import base64
import datetime
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
MAX_AGE_DAYS = 60
MICRO = ("майнкрафт", "minecraft")


def get_cookies() -> list:
    b64 = os.environ.get("TIKTOK_COOKIES_B64", "")
    if b64:
        try:
            return json.loads(base64.b64decode(b64))["cookies"]
        except Exception as exc:
            print("!! не могу распаковать TIKTOK_COOKIES_B64:", exc)
    p = Path("sessions/acc1.json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))["cookies"]
    return []


def summarize(items) -> dict:
    now = datetime.datetime.utcnow()
    cut = now - datetime.timedelta(days=MAX_AGE_DAYS)
    fresh = mine = newest = 0
    rows = []
    for it in items[:150]:
        t = it.get("createTime") or 0
        try:
            t = float(t)
        except (TypeError, ValueError):
            t = 0
        if t:
            dt = datetime.datetime.fromtimestamp(t)
            if dt >= cut:
                fresh += 1
            newest = max(newest, t)
            rd = dt.strftime("%Y-%m-%d")
        else:
            rd = None
        d = (it.get("desc") or "").lower()
        if any(k in d for k in MICRO):
            mine += 1
        rows.append((t, str(it.get("id")), rd, d[:35]))
    rows.sort(key=lambda r: r[0], reverse=True)
    return {
        "count": len(items),
        "fresh_60d": fresh,
        "minecraft_posts": mine,
        "newest": datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d") if newest else None,
        "top": [{"id": r[1], "date": r[2], "desc": r[3]} for r in rows[:6]],
    }


def main() -> int:
    cookies = get_cookies()
    if not cookies:
        print("!! нет кук (TIKTOK_COOKIES_B64 или sessions/acc1.json)")
        return 1

    batches = {}
    api_keys = {}
    dom_links = {}

    def on_response(resp):
        u = resp.url
        try:
            if "tiktok.com/api/" in u:
                api_keys.setdefault(u.split("/api/")[-1][:50], 0)
                api_keys[u.split("/api/")[-1][:50]] += 1
            if "tiktok.com/api/" in u and resp.status == 200:
                ct = resp.headers.get("content-type", "")
                if "json" not in ct.lower():
                    return
                j = resp.json()
                if "search/general/full" in u:
                    s = {}
                    for k, v in (j.items() if isinstance(j, dict) else []):
                        if isinstance(v, list):
                            s[k] = "list:" + str(len(v))
                        elif isinstance(v, dict):
                            s[k] = "dict:" + ",".join(list(v)[:8])
                        else:
                            s[k] = type(v).__name__
                    print("SEARCH_FULL_SHAPE=" + json.dumps(s, ensure_ascii=False))
                    if isinstance(j.get("data"), list) and j["data"]:
                        d0 = j["data"][0]
                        if isinstance(d0, dict):
                            print("SEARCH_FULL_ITEM0=" + json.dumps(
                                {k: (type(v).__name__) for k, v in d0.items()}, ensure_ascii=False))
                items = j.get("itemList") or j.get("item_list") or []
                if items and isinstance(items[0], dict):
                    key = u.split("/api/")[-1][:60]
                    batches.setdefault(key, []).extend(items[:150])
        except Exception:
            pass

    pages = [
        "https://www.tiktok.com/",
        "https://www.tiktok.com/tag/" + urllib.parse.quote("майнкрафт"),
        "https://www.tiktok.com/search?q=" + urllib.parse.quote("майнкрафт"),
        "https://www.tiktok.com/tag/" + urllib.parse.quote("майнкрафтсервер"),
        "https://www.tiktok.com/search?q=" + urllib.parse.quote("майнкрафт сервер"),
    ]
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
        ctx.add_cookies(cookies)
        pg = ctx.new_page()
        pg.on("response", on_response)
        for url in pages:
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                print("goto", url, "EXC:", type(exc).__name__, str(exc)[:80])
            pg.wait_for_timeout(7000)
            try:
                hrefs = pg.eval_on_selector_all(
                    'a[href*="/video/"]',
                    "els => els.map(e => e.getAttribute('href')).slice(0,30)",
                )
                dom_links[url] = [h for h in hrefs if h] 
            except Exception as exc:
                dom_links[url] = "EXC:" + str(exc)[:50]
            for _ in range(3):
                try:
                    pg.mouse.wheel(0, 5000)
                except Exception:
                    pass
                pg.wait_for_timeout(1500)
        ctx.close()
        b.close()

    print("API_KEYS=" + json.dumps(api_keys, ensure_ascii=False))
    print("DOM_LINKS=" + json.dumps(dom_links, ensure_ascii=False))

    if not batches:
        print("!! перехвачено API с видео: 0 (возможно блок/капча)")
        return 0
    res = {}
    for key, items in batches.items():
        res[key] = summarize(items)
    print("PROBE_RESULT=" + json.dumps(res, ensure_ascii=False))
    total_fresh = sum(v.get("fresh_60d", 0) for v in res.values())
    total_mine = sum(v.get("minecraft_posts", 0) for v in res.values())
    print("TOTAL_FRESH_60D =", total_fresh)
    print("TOTAL_MINECRAFT =", total_mine)
    return 0


if __name__ == "__main__":
    sys.exit(main())