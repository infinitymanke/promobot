#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
tgbot.py — Альфредо, смотритель музея контента 🗿
Опрашивает команды Telegram и отвечает. Запускается часто (workflow tg.yml),
сам ничего не постит — только пульт управления.

Команды: /start /stats /run /pause /resume /help
Доступ: только с TELEGRAM_CHAT_ID (чужой не пройдёт).
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
from telegram import TelegramBot, HELP_TEXT  # noqa: E402

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

STATE_TT = BASE_DIR / "state.json"
STATE_YT = BASE_DIR / "ytbot" / "state.json"
CONTROL = BASE_DIR / "control.json"
OFFSET = BASE_DIR / "tg_offset.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_paused() -> bool:
    return bool(_read_json(CONTROL, {}).get("paused", False))


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _bot_summary(path: Path) -> dict:
    st = _read_json(path, {})
    days = st.get("days", {})
    return {
        "total": len(st.get("commented", [])),
        "today": int(days.get(_today(), 0)),
        "quota": int(st.get("quota", {}).get("used", 0)),
    }


def status_text() -> str:
    tt = _bot_summary(STATE_TT)
    yt = _bot_summary(STATE_YT)
    zoo = "закрыт на переучёт 🔒" if is_paused() else "открыт 🗝️"
    return (
        "🗿 Альфредо на посту.\n"
        f"Музей: {zoo}\n"
        f"TikTok ✅ — сегодня {tt['today']}, всего {tt['total']}\n"
        f"YouTube ✅ — сегодня {yt['today']}, всего {yt['total']}"
    )


def stats_text() -> str:
    tt = _bot_summary(STATE_TT)
    yt = _bot_summary(STATE_YT)
    zoo = "закрыт на переучёт 🔒" if is_paused() else "открыт 🗝️"
    quota_note = " (свежая)" if yt["quota"] < 8000 else " (на исходе)"
    return (
        "🗿 Доклад из залов музея:\n\n"
        f"TikTok — сегодня {tt['today']}, всего {tt['total']}\n"
        f"YouTube — сегодня {yt['today']}, всего {yt['total']}\n"
        f"Квота YouTube: {yt['quota']}/10000{quota_note}\n"
        f"Музей: {zoo}"
    )


def commit_push(paths, msg: str) -> None:
    subprocess.run(["git", "add", *[str(p) for p in paths]], cwd=BASE_DIR, check=False)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR).returncode == 0:
        return
    subprocess.run(["git", "config", "user.name", "alfredo"], cwd=BASE_DIR, check=False)
    subprocess.run(["git", "config", "user.email", "alfredo@users.noreply.github.com"], cwd=BASE_DIR, check=False)
    subprocess.run(["git", "commit", "-m", msg], cwd=BASE_DIR, check=False)
    for _ in range(5):
        p = subprocess.run("git pull --rebase --autostash origin main && git push",
                           cwd=BASE_DIR, shell=True)
        if p.returncode == 0:
            return
        time.sleep(5)


def dispatch(which: str) -> str:
    name = {"tt": "promobot", "yt": "ytbot"}.get(which)
    env = dict(os.environ)
    env.setdefault("GH_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
    try:
        r = subprocess.run(["gh", "workflow", "run", name], cwd=BASE_DIR, env=env,
                           capture_output=True, text=True)
        if r.returncode == 0:
            return f"🗿 Хранитель отправлен в обход: {name}. Пыль полетит."
        return f"🗿 Двери заклинило ({name}): {r.stderr.strip()[:120]}"
    except Exception as exc:
        return f"🗿 Не смог открыть дверь: {exc}"


def handle(cmd: str, arg: str) -> str:
    if cmd in ("/start", "/help"):
        return status_text() + "\n\n" + HELP_TEXT
    if cmd == "/stats":
        return stats_text()
    if cmd == "/run":
        return dispatch(arg or "yt")
    if cmd == "/pause":
        _write_json(CONTROL, {"paused": True, "by": "alfredo"})
        commit_push([CONTROL], "alfredo: пауза")
        return "🗿 Музей закрыт на переучёт. Комментарии — под замок."
    if cmd == "/resume":
        _write_json(CONTROL, {"paused": False, "by": "alfredo"})
        commit_push([CONTROL], "alfredo: возобновление")
        return "🗿 Залы открыты. Хранитель снова в строю."
    return "🗿 Не знаю такой команды. /help — табличка у входа."


def main() -> int:
    if not TOKEN:
        print("!! нет TELEGRAM_TOKEN")
        return 1
    bot = TelegramBot(TOKEN, CHAT_ID)
    if not bot.chat_id:
        bot.try_resolve_chat_id()
    if not bot.chat_id:
        print("!! нет chat_id (напиши боту /start)")
        return 0
    bot.set_commands()

    offset = int(_read_json(OFFSET, {}).get("offset", 0) or 0)
    debug = {"offset_in": offset, "chat_id": bot.chat_id}
    try:
        r = bot._session.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"timeout": 1, "limit": 20, "offset": offset + 1}, timeout=20,
        )
        data = r.json()
    except Exception as exc:
        print("!! getUpdates:", exc)
        _write_json(BASE_DIR / "tg_debug.json", {"error": str(exc)})
        return 1
    if not data.get("ok"):
        print("!! getUpdates:", data.get("description"))
        _write_json(BASE_DIR / "tg_debug.json", {"error": data.get("description")})
        return 1

    results = data.get("result", [])
    debug["ok"] = True
    debug["updates"] = []
    max_offset = offset
    answered = 0
    for upd in results:
        uid = int(upd.get("update_id", 0))
        max_offset = max(max_offset, uid)
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "")[:60]
        debug["updates"].append({"id": uid, "chat": chat.get("id"), "text": text})
        if str(chat.get("id")) != str(bot.chat_id):
            continue
        parts = (msg.get("text") or "").strip().split()
        if not parts:
            continue
        cmd = parts[0].split("@")[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else ""
        reply = handle(cmd, arg)
        bot.send_message(reply)
        answered += 1

    if max_offset != offset:
        _write_json(OFFSET, {"offset": max_offset})
        commit_push([OFFSET], "alfredo: offset")
    debug["answered"] = answered
    debug["offset_out"] = max_offset
    if results or answered:
        _write_json(BASE_DIR / "tg_debug.json", debug)
        commit_push([BASE_DIR / "tg_debug.json"], "alfredo: debug")
    print(f"обработано команд: {answered}, offset={max_offset}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
