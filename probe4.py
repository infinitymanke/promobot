#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
probe4.py - структура challenge/item_list у анонима + item/detail анонимом из браузера.
Определяет, можно ли из анонимного браузера (Actions) получить createTime для видео.
"""

import json
import logging
import re
import sys
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
from playwright.sync_api import sync_playwright

from harvest import UA

challenge_payloads = []


def dump_keys(o, depth=3):
    out = []
    if isinstance(o, dict):
        for k in list(o.keys())[:8]:
            v = o[k]
            if isinstance(v, (dict, list)):
                out.append(f"{k}:<{type(v).__name__}>")
            else:
                out.append(f"{k!r}={str(v)[:30]!r}")
    return out


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = b.new_context(user_agent=UA, viewport={"width": 1280, "height": 800},
                            locale="ru-RU", timezone_id="Europe/Moscow")
        pg = ctx.new_page()

        def on_resp(resp):
            u = resp.url
            if "challenge/item_list" in u and resp.status == 200:
                try:
                    j = resp.json()
                    challenge_payloads.append(j)
                    data = j.get("data") or j
                    n = len(data) if isinstance(data, list) else 0
                    top = dump_keys(j)
                    print("CHALLENGE PAYLOAD: n(data)=", n, "top_keys=", top)
                    if isinstance(data, list) and data:
                        e0 = data[0]
                        print("  elem0 type:", type(e0).__name__, "keys:", list(e0.keys())[:8] if isinstance(e0, dict) else str(e0)[:80])
                        item = e0.get("item") if isinstance(e0, dict) else None
                        if isinstance(item, dict):
                            print("  item keys:", list(item.keys())[:12])
                            print("  createTime:", item.get("createTime"), "desc:", (item.get("desc") or "")[:40])
                except Exception as ex:
                    print("challenge parse err:", str(ex)[:80])

        pg.on("response", on_resp)
        pg.goto("https://www.tiktok.com/tag/%D0%BC%D0%B0%D0%B9%D0%BD%D0%BA%D1%80%D0%B0%D1%84%D1%82%D1%81%D0%B5%D1%80%D0%B2%D0%B5%D1%80", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(4500)
        for _ in range(2):
            try:
                pg.mouse.wheel(0, 7000)
            except Exception:
                pass
            pg.wait_for_timeout(1600)

        html = pg.content()
        ids = re.findall(r'/video/(\d{8,})', html)
        ids = list(dict.fromkeys(ids))
        print("DOM video ids:", len(ids), ids[:3])

        for vid in ids[:3]:
            try:
                pg.goto("https://www.tiktok.com/api/item/detail/?aweme_id=" + vid + "&aid=1988",
                        wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(2500)
                t = pg.content()
                if t.startswith("{"):
                    j = json.loads(t)
                    it = j.get("item") or {}
                    print("ITEMDETAIL", vid, "status=", j.get("status_code"),
                          "createTime=", it.get("createTime"), "desc=", (it.get("desc") or "")[:30])
                else:
                    print("ITEMDETAIL", vid, "не JSON, len=", len(t), "->", t[:40].replace(chr(10), " "))
            except Exception as ex:
                print("ITEMDETAIL", vid, "err:", str(ex)[:60])

        ctx.close()
        b.close()
        p.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())