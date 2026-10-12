#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
probe3.py - анонимный браузер в Actions (без кук): умеет ли датацентр видеть контент?
Открывает поиск-страницы, хэштег-страницы и видео-страницы; печатает число видеоссылок
в DOM и сколько данных отдают API (search/general/full, challenge/item_list, recommend/item_list).
"""

import json
import logging
import sys
import time
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("promobot.probe3")
from playwright.sync_api import sync_playwright

from harvest import UA, BROWSER_QUERIES, age_days, is_minecraft
from probe2 import parse_items

MAX_AGE = 60


def on_resp(resp, hits):
    u = resp.url
    if resp.status != 200:
        return
    if "search/general/full" in u or "challenge/item_list" in u or "recommend/item_list" in u:
        try:
            j = resp.json()
            items = parse_items(j)
            tag = u.split("/api/")[-1].split("?")[0]
            cur = hits.setdefault(tag, {"n": 0, "mc": 0, "fresh": 0})
            cur["n"] += len(items)
            cur["mc"] += sum(1 for i in items if is_minecraft(i))
            cur["fresh"] += sum(1 for i in items
                                if age_days(i) is not None and age_days(i) <= MAX_AGE and is_minecraft(i))
        except Exception:
            pass


def dom(pg):
    try:
        c = pg.content()
        return c.count('href="/@')
    except Exception:
        return -1


def main() -> int:
    state = json.loads(Path("state.json").read_text(encoding="utf-8"))
    vids = [str(v) for v in (state.get("commented") or [])][-3:]
    hits = {}

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
        pg = ctx.new_page()
        pg.on("response", lambda r: on_resp(r, hits))

        for q in BROWSER_QUERIES[:4]:
            try:
                pg.goto("https://www.tiktok.com/search?q=" + urllib.parse.quote_plus(q),
                        wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(4500)
                try:
                    pg.mouse.wheel(0, 6000)
                except Exception:
                    pass
                pg.wait_for_timeout(1500)
                print(f"ANON search {q!r}: dom_links={dom(pg)}")
            except Exception as exc:
                print(f"ANON search {q!r}: err {str(exc)[:60]}")

        for tag in ("майнкрафт", "майнкрафтсервер", "майншилд"):
            try:
                pg.goto("https://www.tiktok.com/tag/" + urllib.parse.quote(tag),
                        wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(4500)
                try:
                    pg.mouse.wheel(0, 6000)
                except Exception:
                    pass
                pg.wait_for_timeout(1500)
                print(f"ANON tag {tag}): dom_links={dom(pg)}")
            except Exception as exc:
                print(f"ANON tag {tag}: err {str(exc)[:60]}")

        for vid in vids:
            try:
                pg.goto(f"https://www.tiktok.com/video/{vid}",
                        wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(4000)
                try:
                    pg.mouse.wheel(0, 5000)
                except Exception:
                    pass
                pg.wait_for_timeout(1500)
                print(f"ANON video {vid}: dom_links={dom(pg)}")
            except Exception as exc:
                print(f"ANON video {vid}: err {str(exc)[:60]}")

        ctx.close()
        b.close()
        p.stop()

    print("ANON HITS:", json.dumps(hits, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())