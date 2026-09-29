#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: main.py
Точка входа promobot:
  - поднимает Flask-сервер с /ping (для keep-alive от cron-job.org, чтобы Render не засыпал);
  - в фоновом потоке крутит Worker-цикл (лента + свои видео) с авто-перезапуском;
  - отправляет Telegram-уведомления: старт, статистика за цикл, переход в ошибки,
    восстановление, нужда в ручном перезапуске (сессия протухла).
"""

import base64
import json
import logging
import os
import threading
from pathlib import Path

from flask import Flask

from telegram import TelegramBot
from tiktok import TikTokClient
from worker import Worker

logger = logging.getLogger("promobot.main")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

BASE_DIR = Path(__file__).resolve().parent

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PORT = int(os.environ.get("PORT", "8000"))

COOKIES_FILE = BASE_DIR / "sessions" / "acc1.json"
COOKIES_B64_ENV = os.environ.get("TIKTOK_COOKIES_B64", "")


def load_config() -> dict:
    cfg_path = BASE_DIR / "config.json"
    if cfg_path.exists():
        try:
            base = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("config.json не удалось прочитать: %s", exc)
            base = {}
    else:
        base = {}
    override = os.environ.get("PROMOBOT_CONFIG_JSON")
    if override:
        sync = json.loads(override)  # те же ключи поверх config.json
        base.update(sync)
    if base.get("worker", {}).get("cookies_file") is None:
        base.setdefault("worker", {})["cookies_file"] = str(COOKIES_FILE)
    return base


def write_cookies_str(cfg: dict):
    """Если задана TIKTOK_COOKIES_B64 — перезаписать куки (без логирования значения)."""
    if COOKIES_B64_ENV:
        try:
            raw = base64.b64decode(COOKIES_B64_ENV)
            data = json.loads(raw)
            COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_FILE.write_bytes(raw)
            logger.info("куки загружены из env (кук: %d)", len(data.get("cookies", [])))
        except Exception as exc:
            logger.error("не удалось распаковать TIKTOK_COOKIES_B64: %s", exc)


class Supervisor:
    """Оборачивает цикл Worker в не убиваемый поток, пересоздавая клиента при падении."""

    def __init__(self, config: dict, bot: TelegramBot):
        self.config = config
        self.bot = bot
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="promobot-worker")

    def start(self):
        self.thread.start()
        logger.info("супервизор запущен")

    def _build(self):
        wcfg = self.config.get("worker", {})
        client = TikTokClient(
            user_agent=wcfg.get("user_agent", ""),
            region=wcfg.get("region", "US"),
            language=wcfg.get("language", "ru"),
            impersonate=wcfg.get("impersonate", "chrome131"),
        )
        client.load_cookies(wcfg.get("cookies_file", str(COOKIES_FILE)))
        return client, Worker(client, self.config)

    def _notify(self, text: str):
        try:
            self.bot.send_message(text)
        except Exception as exc:
            logger.warning("telegram notify: %s", exc)

    def _run(self):
        last_notify = {}
        notify_gap = self.config.get("telegram", {}).get("stats_interval_s", 3600)

        while not self.stop_event.is_set():
            try:
                client, worker = self._build()
            except Exception as exc:
                self._notify(f"⚠️ Не удалось загрузить куки/клиента: {exc}\nНужен вход: login_capture.py")
                logger.exception("build client")
                for _ in range(12):
                    if self.stop_event.is_set():
                        return
                    threading.Event().wait(60)
                continue

            try:
                worker.loop(
                    stop_event=self.stop_event,
                    on_cycle=lambda stats: self._maybe_notify(stats, last_notify, notify_gap),
                    on_recovery=lambda n: self._notify(
                        f"✅ Восстановился после {n} ошибок подряд — продолжаю."
                    ),
                    on_critical=lambda n, exc: self._notify(
                        f"⚠️ {n} ошибок подряд ({type(exc).__name__}). "
                        f"Скорее всего куки протухли → запустите login_capture.py локально."
                    ),
                    critical_threshold=self.config.get("telegram", {}).get("critical_threshold", 5),
                )
            except Exception as exc:
                logger.exception("цикл оборвался, перезапускаю: %s", exc)
                self._notify(f"⚠️ Цикл оборвался: {exc}. Перезапускаю автоматически.")
            finally:
                try:
                    client.close()
                except Exception:
                    pass
            if not self.stop_event.is_set():
                threading.Event().wait(30)

    def _maybe_notify(self, stats, last_notify, gap):
        now = __import__("time").time()
        if now - last_notify.get("t", 0) >= gap:
            last_notify["t"] = now
            self._notify(
                f"📊 Цикл завершён: комментариев {stats.get('posted', 0)}, "
                f"ошибок {stats.get('failed', 0)}\n"
                f"Лента: {stats.get('feed_scanned', 0)} · свои: {stats.get('own_scanned', 0)}"
            )

    def shutdown(self):
        self.stop_event.set()


app = Flask("promobot")


@app.get("/ping")
def ping():
    return "ok", 200


@app.get("/")
def index():
    return "promobot <a href='/ping'>/ping</a>", 200


def main():
    config = load_config()
    write_cookies_str(config)

    bot = TelegramBot(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
    if not bot.chat_id:
        bot.try_resolve_chat_id()
        TELEGRAM_CHAT_ID_after = bot.chat_id
        if TELEGRAM_CHAT_ID_after:
            logger.info("chat_id найдено автоматически: %s", TELEGRAM_CHAT_ID_after)
            os.environ["TELEGRAM_CHAT_ID"] = TELEGRAM_CHAT_ID_after
        bot = TelegramBot(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID_after or "")

    if bot.token and bot.chat_id:
        bot.send_message("🚀 PromoBot запущен. Расписание активно.")

    sup = Supervisor(config, bot)
    sup.start()
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()