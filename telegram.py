#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: telegram.py
Отправка уведомлений в Telegram через Bot API (long polling не нужен — только исходящие).
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger("promobot.telegram")

API_BASE = "https://api.telegram.org"

COMMANDS = [
    ("start", "Запустить / статус бота"),
    ("stats", "Статистика работы"),
    ("help", "Справка и команды"),
]

HELP_TEXT = (
    "🤖 PromoBot — твой автокомментатор TikTok\n\n"
    "Команды:\n"
    "/start — статус и работа бота\n"
    "/stats — отчёт по последнему циклу\n"
    "/help — эта справка\n\n"
    "Бот запускается каждый час (GitHub Actions) и сам постит комментарии "
    "под новые майнкрафт-видео с твоего аккаунта."
)


class TelegramBot:
    def __init__(self, token: str, chat_id: Optional[str] = None, timeout: float = 15.0):
        self.token = str(token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.timeout = timeout
        self._session = requests.Session()

    def _post(self, method: str, payload: dict):
        if not self.token:
            return None
        url = f"{API_BASE}/bot{self.token}/{method}"
        try:
            r = self._session.post(url, json=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                logger.warning("telegram %s: %s", method, data.get("description"))
                return None
            return data["result"]
        except Exception as exc:
            logger.warning("telegram %s error: %s", method, exc)
            return None

    def send_message(self, text: str) -> bool:
        if not self.chat_id:
            return False
        payload = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        result = self._post("sendMessage", payload)
        return bool(result)

    def set_commands(self) -> bool:
        """Регистрирует меню команд (видно при вводе '/')."""
        if not self.token:
            return False
        payload = {"commands": [{"command": c, "description": d} for c, d in COMMANDS]}
        return bool(self._post("setMyCommands", payload))

    def answer_updates(self, report: str, state: dict) -> Optional[str]:
        """
        Забирает накопленные команды (раз в час) и отвечает на них.
        Обработанный update_id сохраняется в state (переживает запуски Actions).
        """
        if not self.token or not self.chat_id:
            return None
        offset = int(state.get("tg_update_offset", 0) or 0)
        try:
            r = self._session.get(
                f"{API_BASE}/bot{self.token}/getUpdates",
                params={"timeout": 1, "limit": 10, "offset": offset + 1},
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("getUpdates error: %s", exc)
            return None
        if not data.get("ok"):
            logger.warning("getUpdates: %s", data.get("description"))
            return None

        answered = 0
        for upd in data.get("result", []):
            uid = int(upd.get("update_id", 0))
            state["tg_update_offset"] = max(int(state.get("tg_update_offset", 0) or 0), uid)
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            if str(chat.get("id")) != str(self.chat_id):
                continue
            cmd = ((msg.get("text") or "").strip().split(" ")[0].split("@")[0]).lower()
            if cmd in ("/start", "/help"):
                reply = HELP_TEXT
            elif cmd == "/stats":
                reply = report or "Статистики пока нет."
            else:
                continue
            self.send_message(reply)
            answered += 1
        return f"обработано команд: {answered}" if answered else None

    def try_resolve_chat_id(self) -> Optional[str]:
        """
        Если chat_id не задан — пробует найти последнее обновление и взять оттуда chat.id.
        Чтобы не наплодить диалог, перед вызовом пользователь должен написать боту /start.
        """
        if self.chat_id or not self.token:
            return self.chat_id
        try:
            r = self._session.get(
                f"{API_BASE}/bot{self.token}/getUpdates",
                params={"timeout": 1, "limit": 1},
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("ok") and data.get("result"):
                update = data["result"][-1]
                msg = update.get("message") or update.get("edited_message") or {}
                chat = msg.get("chat") or {}
                cid = chat.get("id")
                if cid is not None:
                    self.chat_id = str(cid)
                    logger.info("chat_id определён автоматически: %s", self.chat_id)
            if not data.get("ok"):
                logger.warning("getUpdates: %s", data.get("description"))
        except Exception as exc:
            logger.warning("getUpdates error: %s", exc)
        return self.chat_id

    def retry(self, fn, attempts: int = 3, delay: float = 2.0):
        last = None
        for i in range(attempts):
            try:
                return fn()
            except Exception as exc:
                last = exc
                time.sleep(delay * (i + 1))
        raise last


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    tok = input("token: ").strip()
    bot = TelegramBot(tok)
    bot.try_resolve_chat_id()
    if bot.chat_id:
        bot.send_message("PromoBot: тест уведомления OK")
        print("chat_id =", bot.chat_id)
    else:
        print("нет chat_id (напишите боту /start и повторите)")