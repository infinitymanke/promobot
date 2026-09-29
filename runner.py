#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: runner.py — один автономный прогон promobot (для GitHub Actions).
  - читает куки из секрета TIKTOK_COOKIES_B64 (или локального sessions/acc1.json);
  - выполняет один цикл Worker (свои видео + поиск по теме);
  - шлёт короткий отчёт в Telegram;
  - код возврата 0/1 для статуса Actions.
"""

import base64
import json
import logging
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
COOKIES_FILE = BASE_DIR / "sessions" / "acc1.json"

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
COOKIES_B64 = os.environ.get("TIKTOK_COOKIES_B64", "")


def load_config() -> dict:
    cfg_path = BASE_DIR / "config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    override = os.environ.get("PROMOBOT_CONFIG_JSON")
    if override:
        try:
            cfg.update(json.loads(override))
        except Exception:
            pass
    return cfg


def main() -> int:
    from tiktok import TikTokClient, TikTokAuthError
    from telegram import TelegramBot
    from worker import Worker

    bot = TelegramBot(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    cfg = load_config()

    source = "секрет"
    if COOKIES_B64:
        try:
            raw = base64.b64decode(COOKIES_B64)
            json.loads(raw)
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_bytes(raw)
        except Exception as exc:
            print("!! не могу распаковать TIKTOK_COOKIES_B64:", exc)
            return 1
    elif COOKIES_FILE.exists():
        source = "файл"
    else:
        print("!! нет кук: задайте TIKTOK_COOKIES_B64 или положите sessions/acc1.json")
        return 1

    client = TikTokClient(
        user_agent=cfg.get("worker", {}).get("user_agent", ""),
        region=cfg.get("worker", {}).get("region", "US"),
        language=cfg.get("worker", {}).get("language", "ru"),
        impersonate=cfg.get("worker", {}).get("impersonate", "chrome131"),
    )
    try:
        client.load_cookies(str(COOKIES_FILE))
    except TikTokAuthError as exc:
        print("!!", exc)
        return 1

    worker = Worker(client, cfg)
    try:
        stats = worker.run_once()
    except Exception as exc:
        print("!! прогон оборвался:", exc)
        try:
            bot.send_message(f"🚨 PromoBot: сбой прогона: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return 1
    finally:
        client.close()

    print("статистика:", json.dumps(stats, ensure_ascii=False))
    line = (
        f"✅ PromoBot: прокомментировано {stats.get('posted', 0)}, "
        f"ошибок {stats.get('failed', 0)}\n"
        f"Поиск: {stats.get('search_scanned', 0)} · авторы: {stats.get('creators_scanned', 0)} · "
        f"свои: {stats.get('own_scanned', 0)}"
    )
    print(line)
    sent = bot.send_message(line)
    if not sent and (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("!! не удалось отправить уведомление в Telegram")

    try:
        bot.set_commands()
        handled = bot.answer_updates(line, worker.state)
        worker._save_state()
        if handled:
            print("telegram:", handled)
    except Exception as exc:
        print("!! telegram команды:", exc)
    return 0 if stats.get("failed") == 0 else 0


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)