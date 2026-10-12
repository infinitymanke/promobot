#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
probe2.py - диагностика каналов добычи в headed-chromium (xvfb).
Проверяет, что реально отдаёт свежие майнкрафт-видео:
  1) search (контроль, текущий канал);
  2) challenge/item_list (страницы хэштегов);
  3) post/item_list (профили доверенных авторов);
  4) recommend/item_list (related под прокомментированными видео).
Ничего не постит и не пишет.
"""

import json
import logging
import sys
import time
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("promobot.probe2")
from playwright.sync_api import sync_playwright

from harvest import (
    UA,
    BROWSER_QUERIES,
    age_days,
    collect_browser_candidates,
    get_cookies,
    is_minecraft,
)

MAX_AGE = 60
TAGS = ["майнкрафт", "майнкрафтсервер", "майншилд", "ищусервер", "майнкрафтвыживание"]


def parse_items(payload: dict):
    out = []
    if not isinstance(payload, dict):
        return out
    for key in ("itemList", "item_list", "aweme_list", "awemeList"):
        v = payload.get(key)
        if isinstance(v, list):
            for e in v:
                if isinstance(e, dict) and e.get("aweme_id"):
                    out.append(e)
    return out


def fresh_minecraft(items):
    return [it for it in items if age_days(it) is not None
            and age_days(it) <= MAX_AGE and is_minecraft(it)]


def walk(pg, resp_stats, dom_links=None):
    def on_resp(resp):
        u = resp.url
        try:
            if resp.status != 200:
                resp_stats[u.split("/api/")[-1].split("/")[0]] = (resp.status, "status")
                return
            for tag in ("challenge/item_list", "search/general/full",
                        "post/item_list", "recommend/item_list"):
                if tag in u:
                    j = resp.json()
                    items = parse_items(j)
                    fresh = len(fresh_minecraft(items))
                    mc = sum(1 for it in items if is_minecraft(it))
                    cur = resp_stats.setdefault(tag, {"hits": 0, "items": 0, "mc": 0, "fresh": 0})
                    cur["hits"] += 1
                    cur["items"] += len(items)
                    cur["mc"] += mc
                    cur["fresh"] += fresh
                    break
        except Exception:
            pass

    pg.on("response", on_resp)
    return resp_stats


def count_dom_video_links(pg) -> int:
    try:
        return pg.content().count('href="/@')
    except Exception:
        return -1


def main() -> int:
    cfg_path = Path("config.json")
    state = json.loads(Path("state.json").read_text(encoding="utf-8"))
    cookies = get_cookies()
    if not cookies:
        print("!! нет кук")
        return 1

    resp_stats = {}
    trusted = [s for s in (state.get("trusted") or []) if s][:6]
    commented = [str(v) for v in (state.get("commented") or [])][-4:]

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
        walk(pg, resp_stats)

        # 1) search (контроль)
        found = collect_browser_candidates(pg, BROWSER_QUERIES[:4], MAX_AGE)
        fresh_s = len(fresh_minecraft(found))
        print(f"SEARCH: got={len(found)} fresh_mc={fresh_s}")

        # 2) challenge/item_list
        dom_links_total = 0
        api_fresh = 0
        for tag in TAGS:
            try:
                pg.goto("https://www.tiktok.com/tag/" + urllib.parse.quote(tag),
                        wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                print(f"  tag {tag}: goto err {str(exc)[:60]}")
                continue
            pg.wait_for_timeout(4000)
            try:
                pg.mouse.wheel(0, 5000)
            except Exception:
                pass
            pg.wait_for_timeout(1500)
            dom_links_total += count_dom_video_links(pg)
        ch = resp_stats.get("challenge/item_list", {})
        print(f"CHALLENGE: tags={len(TAGS)} api_hits={ch.get('hits',0)} items={ch.get('items',0)} "
              f"mc={ch.get('mc',0)} fresh={ch.get('fresh',0)} dom_links={dom_links_total}")

        # 3) post/item_list for authors
        for sec in trusted[:6]:
            url = ("https://www.tiktok.com/api/post/item_list/?secUid=" + sec +
                   "&count=18&coverFormat=2&cursor=0")
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                print(f"  author {sec[-8:]}: goto err {str(exc)[:60]}")
                continue
            pg.wait_for_timeout(1500)
        au = resp_stats.get("post/item_list", {})
        print(f"AUTHORS: checked={len(trusted[:6])} api_hits={au.get('hits',0)} "
              f"items={au.get('items',0)} mc={au.get('mc',0)} fresh={au.get('fresh',0)}")

        # 4) recommend/item_list (related)
        for vid in commented[:4]:
            try:
                pg.goto(f"https://www.tiktok.com/video/{vid}",
                        wait_until="domcontentloaded", timeout=45000)
            except Exception as exc:
                print(f"  video {vid}: goto err {str(exc)[:60]}")
                continue
            pg.wait_for_timeout(3500)
            try:
                pg.mouse.wheel(0, 4000)
            except Exception:
                pass
            pg.wait_for_timeout(1200)
        rec = resp_stats.get("recommend/item_list", {})
        print(f"RELATED: videos={len(commented[:4])} api_hits={rec.get('hits',0)} "
              f"items={rec.get('items',0)} mc={rec.get('mc',0)} fresh={rec.get('fresh',0)}")

        ctx.close()
        b.close()
        p.stop()

    print("RAW_HITS:", json.dumps(resp_stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())