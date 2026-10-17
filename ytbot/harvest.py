#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
harvest.py (ytbot) — один цикл YouTube-бота.

Логика:
  1) поиск коротких видео по нишевым запросам (search.list);
  2) дедуп + удаление уже прокомментированных;
  3) батч-статистика (videos.list) → сортировка по просмотрам (топ охвата вперёд);
  4) постинг топ-комментариев через commentThreads.insert с паузами;
  5) авто-тормоз при серии ошибок, суточный потолок и dry-счётчик;
  6) отчёт в Telegram + сохранение state.

Креды берём из YT_CREDENTIALS_B64 (base64 json) или sessions/token.json.
"""

import base64
import datetime
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("ytbot.harvest")

BASE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = BASE_DIR / "sessions" / "token.json"

sys.path.insert(0, str(BASE_DIR))
from youtube import YTClient, YTQuotaError  # noqa: E402
from worker import Worker  # noqa: E402


def load_creds() -> dict:
    """Креды: env YT_CREDENTIALS_B64 -> sessions/token.json."""
    raw = os.environ.get("YT_CREDENTIALS_B64", "").strip()
    if raw:
        try:
            data = json.loads(base64.b64decode(raw).decode("utf-8"))
            creds = normalize_creds(data)
            if creds.get("client_id") and creds.get("refresh_token"):
                return creds
            logger.error("YT_CREDENTIALS_B64 разобран, но нет client_id/refresh_token")
        except Exception as exc:
            logger.error("YT_CREDENTIALS_B64 не разобран: %s", exc)
    if TOKEN_FILE.exists():
        try:
            return normalize_creds(json.loads(TOKEN_FILE.read_text(encoding="utf-8")))
        except Exception as exc:
            logger.error("token.json не разобран: %s", exc)
    return {}


def normalize_creds(data: dict) -> dict:
    """Поддерживаем наш token.json и client_secret.json от Google."""
    if data.get("client_id") and data.get("refresh_token"):
        return {
            "client_id": data["client_id"],
            "client_secret": data.get("client_secret", ""),
            "refresh_token": data["refresh_token"],
        }
    inst = data.get("installed") or data.get("web") or {}
    return {
        "client_id": data.get("client_id") or inst.get("client_id", ""),
        "client_secret": data.get("client_secret") or inst.get("client_secret", ""),
        "refresh_token": data.get("refresh_token", ""),
    }


def _notify(text: str) -> None:
    tok = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat):
        return
    try:
        sys.path.insert(0, str(BASE_DIR.parent))
        from telegram import TelegramBot

        TelegramBot(tok, chat).send_message(text)
    except Exception as exc:
        logger.warning("telegram: %s", exc)


def main() -> int:
    cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8")).get("worker", {})
    try:
        ctrl = json.loads((BASE_DIR.parent / "control.json").read_text(encoding="utf-8"))
        if ctrl.get("paused"):
            print("🗿 пауза (Альфредо закрыл музей) — цикл пропущен")
            return 0
    except Exception:
        pass
    creds = load_creds()
    if not creds.get("client_id") or not creds.get("refresh_token"):
        print("!! нет кред (YT_CREDENTIALS_B64 или sessions/token.json). Сначала login_capture.py")
        return 1

    worker = Worker(cfg, str(BASE_DIR / "state.json"))
    client = YTClient(
        client_id=creds["client_id"],
        client_secret=creds.get("client_secret", ""),
        refresh_token=creds["refresh_token"],
        region=cfg.get("region", "RU"),
        lang=cfg.get("language", "ru"),
        quota_ceiling=cfg.get("quota_ceiling", 4000),
        state=worker.state,
    )

    per_query = cfg.get("search_count", 25)
    max_comments = cfg.get("max_comments_per_run", 15)
    max_age_days = cfg.get("max_age_days", 60)
    day_cap = cfg.get("day_cap", 20)
    dmin = cfg.get("delay_min_s", 20)
    dmax = cfg.get("delay_max_s", 60)
    brake_fails = cfg.get("brake_fails", 3)

    # 1) поиск (ротация запросов, чтобы не жечь квоту всеми сразу)
    all_queries = cfg.get("search_queries", [])
    qpr = max(1, int(cfg.get("queries_per_run", 2)))
    if all_queries:
        offset = datetime.datetime.now().hour % len(all_queries)
        queries = [all_queries[(offset + i) % len(all_queries)] for i in range(min(qpr, len(all_queries)))]
    else:
        queries = []
    found = {}
    auth_failed = False
    for q in queries:
        try:
            for it in client.search_shorts(q, max_results=per_query):
                found.setdefault(it["id"], it)
        except YTQuotaError as exc:
            logger.warning("поиск «%s»: квота — %s", q, str(exc)[:120])
        except Exception as exc:
            msg = str(exc)
            if "refresh_token" in msg or "invalid_grant" in msg or "invalid_client" in msg:
                auth_failed = True
            logger.warning("поиск «%s»: %s", q, msg[:120])
    if auth_failed and not found:
        line = ("🗿 Альфредо: ключ YouTube недействителен (истёк или отозван).\n"
                "Перезайди: python ytbot\\login_capture.py, затем python ytbot\\push_secret.py")
        print(line)
        _notify(line)
        return 1
    logger.info("поиск: собрано уникальных видео: %d", len(found))

    candidates = [v for v in found.values() if not worker.is_commented(v["id"])]

    # 2) статистика батчем + фильтр свежести + сортировка по просмотрам
    if candidates:
        try:
            stats = client.video_stats([c["id"] for c in candidates])
        except Exception as exc:
            stats = {}
            logger.warning("статистика: %s", str(exc)[:120])
        now = datetime.datetime.now(datetime.timezone.utc)
        fresh = []
        for c in candidates:
            c["views"] = stats.get(c["id"], {}).get("views", 0)
            pub = c.get("publishedAt") or ""
            age = None
            if pub:
                try:
                    dt = datetime.datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    age = (now - dt).days
                except Exception:
                    age = None
            c["age"] = age
            if age is None or age <= max_age_days:
                fresh.append(c)
        fresh.sort(key=lambda x: x.get("views", 0), reverse=True)
        candidates = fresh

    logger.info("жнец: к постингу %d (свежих, по просмотрам)", len(candidates))

    texts = worker.comment_texts()
    posted = failed = consec = 0
    posted_pairs = []
    for c in candidates:
        if posted >= max_comments or not texts:
            break
        if worker.daily_count() >= day_cap:
            logger.info("дневной потолок %d достигнут — стоп", day_cap)
            break
        aid = c["id"]
        try:
            cid = client.post_comment(aid, random.choice(texts))
            worker.mark_commented(aid)
            posted += 1
            posted_pairs.append((aid, cid))
            consec = 0
            logger.info("[жнец] %s (views=%s): комментарий ок", aid, c.get("views"))
        except Exception as exc:
            consec += 1
            failed += 1
            logger.warning("[жнец] %s: %s", aid, str(exc)[:140])
            if consec >= brake_fails:
                logger.warning("!! авто-тормоз: %d провалов подряд — стоп до следующего цикла", consec)
                break
        time.sleep(random.uniform(dmin, dmax))

    held = 0
    if posted_pairs:
        for vid, cid in posted_pairs[:2]:
            if not client.comment_visible(vid, cid):
                held += 1
        if held:
            logger.warning("!! %d из %d проверенных комментариев ушли на модерацию YouTube", held, min(2, len(posted_pairs)))

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    dry = worker.state.setdefault("dry", {"day": "", "streak": 0})
    if posted or failed or candidates:
        dry["streak"] = 0
    elif dry.get("day") != today:
        dry["streak"] = dry.get("streak", 0) + 1
    dry["day"] = today
    worker.save()

    line = (
        f"🗿 YouTube: украшено {posted}, сбоев {failed}\n"
        f"найдено свежих: {len(candidates)}, за сегодня: {worker.daily_count()}"
    )
    if not candidates:
        line += "\nисточник пуст — повезёт в след. час"
    if held:
        line += f"\n[!] {held} коммент(а) ушли на модерацию YouTube — возможен флаг текста/канала"
    if dry.get("streak", 0) >= 3:
        line += "\n[!] 3 дня подряд нет новых видео — проверь токен/канал"
    print("статистика:", json.dumps(
        {"posted": posted, "failed": failed, "found": len(candidates), "dry_days": dry.get("streak", 0)},
        ensure_ascii=False,
    ))
    print(line)
    _notify(line)

    return 0


if __name__ == "__main__":
    sys.exit(main())
