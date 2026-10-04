#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: harvest.py — production-цикл PromoBot на GitHub Actions.
Реальный chromium (xvfb) открывает поиск-страницы ниши TikTok, перехватывает ответы
search/general/full, собирает ВСЕ майнкрафт-видео (свежие <=60д), комментирует через
SecSDK-протокол (tiktok.publish_comment), шлёт отчёт в Telegram и сохраняет state.
"""

import base64
import datetime
import json
import logging
import os
import random
import sys
import time
import urllib.parse
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("promobot.harvest")
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")
BROWSER_QUERIES = [
    "майнкрафт",
    "майнкрафт сервер",
    "майнкрафт выживание",
    "ищусервер майнкрафт",
    "майншилд",
    "майнкрафт приколы",
]
MICRO_KEYS = ("майнкрафт", "майншилд", "ищусервер", "minecraft", "mcpe")

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "sessions" / "acc1.json"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
COOKIES_B64 = os.environ.get("TIKTOK_COOKIES_B64", "")


def load_config() -> dict:
    try:
        cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    override = os.environ.get("PROMOBOT_CONFIG_JSON")
    if override:
        try:
            cfg.update(json.loads(override))
        except Exception:
            pass
    return cfg


def get_cookies() -> list:
    if COOKIES_B64:
        try:
            return json.loads(base64.b64decode(COOKIES_B64))["cookies"]
        except Exception as exc:
            logger.error("bad TIKTOK_COOKIES_B64: %s", exc)
    p = Path("sessions/acc1.json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))["cookies"]
    return []


def age_days(item: dict):
    ts = item.get("createTime") or item.get("create_time")
    if not ts:
        return None
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        ts /= 1000
    return (time.time() - ts) / 86400


def is_minecraft(item: dict) -> bool:
    text = ((item.get("desc") or item.get("title") or "") + " ").lower()
    for ch in item.get("challenges") or []:
        text += " " + str(ch.get("title", "")).lower()
    return any(k in text for k in MICRO_KEYS)


def collect_browser_candidates(pg, queries: list, max_age_days: int):
    found = {}

    def on_response(resp):
        u = resp.url
        try:
            if "search/general/full" in u and resp.status == 200:
                j = resp.json()
                data = j.get("data") or []
                for e in data:
                    if not isinstance(e, dict):
                        continue
                    item = e.get("item")
                    if not isinstance(item, dict):
                        continue
                    aid = item.get("aweme_id") or item.get("id")
                    if not aid:
                        continue
                    if age_days(item) is None or age_days(item) > max_age_days:
                        continue
                    if is_minecraft(item):
                        found[str(aid)] = item
        except Exception:
            pass

    pg.on("response", on_response)
    for q in queries:
        url = "https://www.tiktok.com/search?q=" + urllib.parse.quote_plus(q)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:
            logger.warning("search %s: %s", q, str(exc)[:60])
            continue
        pg.wait_for_timeout(6000)
        for _ in range(5):
            try:
                pg.mouse.wheel(0, 6000)
            except Exception:
                pass
            pg.wait_for_timeout(1600)
    return list(found.values())


def main() -> int:
    cfg = load_config().get("worker", {})
    cookies = get_cookies()
    if not cookies:
        print("!! нет кук (TIKTOK_COOKIES_B64 или sessions/acc1.json)")
        return 1
    if COOKIES_B64:
        try:
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_bytes(base64.b64decode(COOKIES_B64))
        except Exception as exc:
            logger.error("кэш-файл кук не записан: %s", exc)

    from tiktok import TikTokClient
    from telegram import TelegramBot
    from worker import Worker

    client = TikTokClient(region=cfg.get("region", "RU"), language="ru", impersonate="chrome131")
    try:
        client.load_cookies(str(COOKIES_FILE))
    except Exception as exc:
        print("!!", exc)
        return 1
    worker = Worker(client, cfg)
    from worker import logger as wlog  # noqa: F401  (держим общий логгер стиля)

    max_comments = cfg.get("max_comments_per_run", 3)
    max_age = cfg.get("max_age_days", 60)
    dmin = cfg.get("delay_min_s", 20)
    dmax = cfg.get("delay_max_s", 60)

    brake_fails = cfg.get("brake_fails", 3)
    brake_cap = cfg.get("brake_cap", 6)
    brake = worker.state.setdefault("brake", {"fails": 0, "until": ""})
    capped = bool(brake.get("until")) and brake["until"] >= datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    candidates = []
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
        candidates = collect_browser_candidates(pg, BROWSER_QUERIES, max_age)
        ctx.close()
        b.close()
        p.stop()

    candidates.sort(key=lambda it: (age_days(it) or 1e9))
    logger.info("жнец: собрано свежих майнкрафт-видео: %d", len(candidates))

    texts = worker.comment_texts()
    posted = failed = 0
    limit = (brake_cap if capped else max_comments)
    consec = 0
    for item in candidates:
        if posted >= limit:
            break
        aid = client.aweme_id(item)
        if not aid or worker._is_commented(aid):
            continue
        if not texts:
            break
        text = random.choice(texts)
        try:
            client.publish_comment(aid, text)
            worker._mark_commented(aid)
            sec = client.author_uid(item)
            if sec:
                worker._learn_author(sec)
            posted += 1
            consec = 0
            logger.info("[жнец] %s: комментарий ок", aid)
        except Exception as exc:
            consec += 1
            failed += 1
            brake["fails"] = consec
            logger.warning("[жнец] %s: %s", aid, str(exc)[:120])
            if consec >= brake_fails:
                brake["until"] = (datetime.datetime.now() + datetime.timedelta(hours=12)).strftime("%Y-%m-%d %H:%M")
                capped = True
                limit = brake_cap
                logger.warning("!! авто-тормоз: %d провалов подряд — лимит срезан до %d на 12ч", consec, brake_cap)
                break
        time.sleep(random.uniform(dmin, dmax))

    if failed == 0 and brake.get("fails"):
        brake["fails"] = 0
        brake["until"] = ""
        logger.info("цикл чистый — тормоз снят")

    stats = {
        "posted": posted,
        "failed": failed,
        "search_scanned": len(candidates),
        "bounded": limit < max_comments,
    }
    worker._save_state()

    print("статистика:", json.dumps(stats, ensure_ascii=False))
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    daily = worker.state.get("days", {}).get(today, 0)
    line = (
        f"? PromoBot: прокомментировано {posted}, ошибок {failed}\n"
        f"найдено майнкрафт-видео: {len(candidates)}\n"
        f"за сегодня: {daily}"
    )
    if limit < max_comments:
        line += " [тормоз активен]"
    print(line)

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try:
            bot = TelegramBot(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
            bot.send_message(line)
        except Exception as exc:
            print("!! telegram:", exc)

    client.close()
    return 0 if failed == 0 else 0


if __name__ == "__main__":
    sys.exit(main())