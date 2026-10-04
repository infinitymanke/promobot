#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: worker.py
Рабочий цикл promobot:
  - выбирает текст(-ы) комментариев (comment.txt, по одной строке);
  - комментирует видео из ленты (рекомендации) и/или свои видео (videos.txt);
  - ведёт историю прокомментированных aweme_ids (state.json), чтобы не дублировать;
  - соблюдает расписание (рабочие часы) и ограничение на число комментариев за цикл;
  - собирает статистику и передаёт её наружу (для Telegram-уведомлений).
"""

import json
import logging
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from tiktok import TikTokClient, TikTokAuthError, TikTokEmptyError, TikTokError

logger = logging.getLogger("promobot.worker")


class Worker:
    def __init__(self, client: TikTokClient, config: dict, state_path: str = "state.json"):
        self.client = client
        self.cfg = config.get("worker", {})
        self.state_path = state_path
        self.state = self._load_state()
        self.done_paths = set(self.state.get("commented", []))

    # ---------------------------------------------------------------- обучение пула авторов
    def _learn_author(self, sec: str) -> None:
        """Запоминаем автора, на свежее видео которого успешно оставлен комментарий."""
        if not sec:
            return
        pool = self.state.setdefault("trusted", [])
        if sec not in pool:
            pool.append(sec)
            self.state["trusted"] = pool[-150:]
            self._save_state()

    # ---------------------------------------------------------------- состояние
    def _load_state(self) -> dict:
        try:
            return json.loads(Path(self.state_path).read_text(encoding="utf-8"))
        except Exception:
            return {"commented": [], "days": {}}

    def _save_state(self):
        self.state["commented"] = sorted(self.done_paths)
        Path(self.state_path).write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    def _is_commented(self, aweme_id: str) -> bool:
        return aweme_id in self.done_paths

    def _mark_commented(self, aweme_id: str):
        self.done_paths.add(aweme_id)
        self.state.setdefault("days", {})
        day = datetime.now().strftime("%Y-%m-%d")
        self.state["days"][day] = self.state["days"].get(day, 0) + 1
        # держим историю не больше 30 дней
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.state["days"] = {k: v for k, v in self.state["days"].items() if k >= cutoff}
        if len(self.done_paths) > 20_000:
            self.done_paths = set(list(self.done_paths)[-15_000:])
        self._save_state()

    # ---------------------------------------------------------------- тексты комментариев
    def comment_texts(self) -> List[str]:
        """
        Пулы вариантов: comments_pool.txt — по одной вариации на строку.
        Если пула нет — падаем на comment.txt (один комментарий).
        """
        pool_path = self.cfg.get("comment_pool_file", "comments_pool.txt")
        try:
            lines = Path(pool_path).read_text(encoding="utf-8").splitlines()
            texts = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
            if texts:
                return texts
        except FileNotFoundError:
            pass
        path = self.cfg.get("comment_file", "comment.txt")
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            logger.error("нет файла комментариев: %s", path)
            return []
        return [text] if text else []

    def own_video_ids(self) -> List[str]:
        """Свои видео: из videos.txt вытаскиваем id ссылок."""
        ids = []
        path = self.cfg.get("videos_file", "videos.txt")
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                ids.extend(self.client.extract_aweme_ids_from_urls(line))
        except FileNotFoundError:
            logger.error("нет файла своих видео: %s", path)
        return ids

    def in_window(self) -> bool:
        """True, если сейчас в активном окне расписания."""
        sched = self.cfg.get("schedule", {})
        if not sched.get("enabled", True):
            return True
        now = datetime.now()
        hour = now.hour
        start = sched.get("start_hour", 0)
        end = sched.get("end_hour", 23)
        if start <= end:
            return start <= hour <= end
        return hour >= start or hour <= end  # ночь: 22 -> 06

    def sleep_until_window(self) -> None:
        sched = self.cfg.get("schedule", {})
        now = datetime.now()
        start = sched.get("start_hour", 0)
        target = now.replace(hour=start, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        logger.info("вне рабочего окна — сплю до %s", target.time())
        time.sleep(min(delay, 3600))

    # ---------------------------------------------------------------- подбор целей (поиск/авторы)
    def _age_days(self, item: dict):
        """Возраст видео в днях по createTime/create_time (сек или мс), None если нет даты."""
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

    def _search_candidates(self) -> List[tuple]:
        """Полный обход всех запросов ниши за цикл → активные авторы → их НОВЫЕ видео.
        Авторы, у которых есть свежие посты или на видео пользователи оставили комментарий,
        попадают в state["trusted"] и в следующих циклах дёргаются напрямую."""

        def _fetch(query: str) -> List[tuple]:
            count = self.cfg.get("targets", {}).get("search_count", 10)
            max_age = self.cfg.get("max_age_days", 60)
            items = []
            for sort_type in ("0", "1", "2"):
                try:
                    items = self.client.search_videos(query, count=count, sort_type=sort_type)
                    fresh_direct = sum(
                        1 for it in items
                        if self._age_days(it) is not None and self._age_days(it) <= max_age
                    )
                    if fresh_direct:
                        break
                except TikTokError as exc:
                    logger.warning("поиск «%s» (%s): %s", query, sort_type, exc)
                if items:
                    break

            authors = []
            try:
                users = self.client.search_users(query, count=10)
                authors = [u.get("sec_uid") for u in users if u.get("sec_uid")]
            except TikTokError as exc:
                logger.warning("поиск пользователей «%s»: %s", query, exc)
            if not authors:
                seen = set()
                for it in items:
                    sec = self.client.author_uid(it)
                    if sec and sec not in seen:
                        seen.add(sec)
                        authors.append(sec)

            out = []
            for it in items:
                age = self._age_days(it)
                if age is not None and age <= max_age:
                    out.append((it, self.client.author_uid(it)))

            limit = self.cfg.get("creators_max_authors", 6)
            for sec in authors[:limit]:
                try:
                    posts = self.client.get_own_videos(sec, count=12)
                except TikTokError as exc:
                    logger.warning("автор %s: %s", sec[-8:], exc)
                    continue
                had_fresh = False
                for post in posts:
                    age = self._age_days(post)
                    if age is not None and age <= max_age:
                        out.append((post, sec))
                        had_fresh = True
                if had_fresh:
                    self._learn_author(sec)
            logger.info("поиск «%s»: авторов %d, свежих видео %d", query, len(authors), len(out))
            return out

        queries = self.cfg.get("targets", {}).get("search_queries", [])
        candidates = []
        for query in queries:
            if queries and len(candidates) >= self.cfg.get("max_comments_per_run", 3) * 4:
                break
            try:
                candidates += _fetch(query)
            except Exception as exc:
                logger.warning("запрос «%s»: %s", query, exc)

        def _key(pair):
            item, _ = pair
            age = self._age_days(item)
            return age if age is not None else 1e9
        candidates.sort(key=_key)
        return candidates

    def _creator_candidates(self) -> List[tuple]:
        """Свежие видео из пула доверенных авторов (state["trusted"]) + список в конфиге."""
        pool = list(self.state.get("trusted", []))
        pool += [u for u in self.cfg.get("targets", {}).get("creator_sec_uids", [])]
        seen = set()
        uids = []
        for u in pool:
            if u and u not in seen:
                seen.add(u)
                uids.append(u)
        items = []
        for uid in uids[: self.cfg.get("creators_max_authors", 6)]:
            try:
                posts = self.client.get_own_videos(uid, count=10)
            except TikTokError as exc:
                logger.warning("автор %s: %s", uid[-8:], exc)
                continue
            for post in posts:
                age = self._age_days(post)
                if age is not None and age <= self.cfg.get("max_age_days", 60):
                    items.append((post, uid))
        return items

    # ---------------------------------------------------------------- прогон
    def run_once(self) -> dict:
        """
        Один полный проход: лента + свои видео.
        Возвращает статистику: {posted, failed, errors, feed_videos, own_videos}
        """
        stats = {"posted": 0, "failed": 0, "feed_scanned": 0, "own_scanned": 0}
        texts = self.comment_texts()
        if not texts:
            logger.warning("нет текстов комментариев — пропускаю прогон")
            return stats

        modes = self.cfg.get("modes", {"feed": True, "own": True})
        max_comments = self.cfg.get("max_comments_per_run", 20)
        delay_min = self.cfg.get("delay_min_s", 20)
        delay_max = self.cfg.get("delay_max_s", 90)

        # --- лента (рекомендации)
        if modes.get("feed", True) and stats["posted"] < max_comments:
            try:
                items = self.client.get_recommend(count=self.cfg.get("feed_count", 8))
                logger.info("лента: %d видео", len(items))
                stats["feed_scanned"] = len(items)
                stats = self._comment_items(
                    [(it, None) for it in items], texts, stats,
                    max_comments, delay_min, delay_max, note="лента",
                )
            except (TikTokAuthError, TikTokEmptyError) as exc:
                logger.error("лента недоступна: %s", exc)
                stats["errors"] = stats.get("errors", 0) + 1
                raise
            except TikTokError as exc:
                logger.error("лента: %s", exc)
                stats["errors"] = stats.get("errors", 0) + 1

        # --- свои видео
        if modes.get("own", True) and stats["posted"] < max_comments:
            own_ids = self.own_video_ids()[: self.cfg.get("own_count", 10)]
            stats["own_scanned"] = len(own_ids)
            for aweme_id in own_ids:
                if stats["posted"] >= max_comments:
                    break
                if self._is_commented(f"own:{aweme_id}"):
                    continue
                text = random.choice(texts)
                try:
                    self.client.publish_comment(aweme_id, text)
                    self._mark_commented(f"own:{aweme_id}")
                    stats["posted"] += 1
                    logger.info("своё видео %s: комментарий ок", aweme_id)
                except (TikTokAuthError, TikTokEmptyError):
                    raise
                except TikTokError as exc:
                    logger.warning("своё видео %s: %s", aweme_id, exc)
                    stats["failed"] += 1
                self._sleep(delay_min, delay_max)

        # --- поиск по хэштегам (тематический подбор — не зависит от региона ленты)
        if modes.get("search", True) and stats["posted"] < max_comments:
            found = self._search_candidates()
            stats["search_scanned"] = len(found)
            if found:
                stats = self._comment_items(
                    found, texts, stats,
                    max_comments, delay_min, delay_max,
                    note="поиск",
                )

        # --- авторы (доверенный пул: state["trusted"] + конфиг)
        if modes.get("creators", True) and stats["posted"] < max_comments:
            found = self._creator_candidates()
            stats["creators_scanned"] = len(found)
            if found:
                stats = self._comment_items(
                    found, texts, stats,
                    max_comments, delay_min, delay_max,
                    note="авторы",
                )

        self._save_state()
        return stats

    def _comment_items(self, pairs, texts, stats, max_comments, delay_min, delay_max, note):
        max_age = self.cfg.get("max_age_days", 60)
        for item, sec in pairs:
            if stats["posted"] >= max_comments:
                break
            aweme_id = self.client.aweme_id(item)
            if not aweme_id:
                continue
            if self._is_commented(aweme_id):
                continue
            age = self._age_days(item)
            if age is not None and age > max_age:
                stats["skipped_old"] = stats.get("skipped_old", 0) + 1
                logger.info("[%s] %s: пропуск (видео %d дн.)", note, aweme_id, int(age))
                continue
            text = random.choice(texts)
            try:
                self.client.publish_comment(aweme_id, text)
                self._mark_commented(aweme_id)
                if sec:
                    self._learn_author(sec)
                stats["posted"] += 1
                logger.info("[%s] %s: комментарий ок", note, aweme_id)
            except (TikTokAuthError, TikTokEmptyError) as exc:
                logger.error("[%s] %s: критично: %s", note, aweme_id, exc)
                raise
            except TikTokError as exc:
                logger.warning("[%s] %s: %s", note, aweme_id, exc)
                stats["failed"] += 1
            self._sleep(delay_min, delay_max)
        return stats

    def _sleep(self, delay_min, delay_max):
        delay = random.uniform(delay_min, delay_max)
        time.sleep(delay)

    # ---------------------------------------------------------------- цикл (безопасный)
    def loop(self, stop_event=None, on_cycle=None, on_recovery=None, on_critical=None, critical_threshold=5):
        """
        Бесконечный цикл с автоперезапуском после сбоев.
        on_cycle(stats) — после успешного прогона;
        on_recovery(err_count) — после восстановления после серии ошибок;
        on_critical(streak, last_exc) — когда серия ошибок превысила порог (нужен ручной вход/чистка кук).
        """
        errors_streak = 0
        warned_critical = False
        last_exc = None
        while not (stop_event and stop_event.is_set()):
            try:
                if not self.in_window():
                    errors_streak = 0
                    self.sleep_until_window()
                    continue
                stats = self.run_once()
                if stats.get("posted", 0) == 0:
                    delay = self.cfg.get("idle_sleep_s", 1800)
                    if errors_streak:
                        logger.info("восстановление после %d ошибок подряд", errors_streak)
                        if on_recovery:
                            on_recovery(errors_streak)
                        errors_streak = 0
                    time.sleep(delay)
                    continue
                if on_cycle:
                    on_cycle(stats)
                errors_streak = 0
                time.sleep(self.cfg.get("run_gap_s", 300))
            except (TikTokAuthError, TikTokEmptyError) as exc:
                errors_streak += 1
                last_exc = exc
                logger.error("сбой (крит.): %s", exc)
                if errors_streak >= critical_threshold and not warned_critical:
                    warned_critical = True
                    if on_critical:
                        try:
                            on_critical(errors_streak, exc)
                        except Exception:
                            logger.exception("on_critical")
                time.sleep(300)
            except Exception as exc:
                errors_streak += 1
                last_exc = exc
                logger.exception("непредвиденная ошибка: %s", exc)
                time.sleep(min(600, errors_streak * 60))
            if errors_streak == 0:
                warned_critical = False
        logger.info("цикл остановлен")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import time as _t

    c = TikTokClient()
    c.load_cookies("sessions/acc1.json")
    cfg = {"worker": {"comment_file": "comment.txt", "videos_file": "videos.txt", "modes": {"feed": True, "own": True}}}
    w = Worker(c, cfg)
    print(w.run_once())