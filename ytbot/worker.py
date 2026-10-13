#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
worker.py (ytbot) — состояние, пул текстов, учёт дня и тормоза.
Каркас перенесён из TikTok-бота (promobot/worker.py), лишнее убрано.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("ytbot.worker")


class Worker:
    def __init__(self, config: dict, state_path: str = "state.json"):
        self.cfg = config.get("worker", {})
        self.state_path = state_path
        self.state = self._load_state()
        self.done = set(self.state.get("commented", []))

    def _load_state(self) -> dict:
        try:
            return json.loads(Path(self.state_path).read_text(encoding="utf-8"))
        except Exception:
            return {"commented": [], "days": {}}

    def save(self) -> None:
        self.state["commented"] = list(self.done)
        Path(self.state_path).write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def is_commented(self, vid: str) -> bool:
        return vid in self.done

    def mark_commented(self, vid: str) -> None:
        self.done.add(vid)
        days = self.state.setdefault("days", {})
        today = datetime.now().strftime("%Y-%m-%d")
        days[today] = days.get(today, 0) + 1
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.state["days"] = {k: v for k, v in days.items() if k >= cutoff}
        if len(self.done) > 20000:
            self.done = set(list(self.done)[-15000:])
        self.save()

    def daily_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        return int(self.state.get("days", {}).get(today, 0))

    def comment_texts(self) -> list:
        pool = self.cfg.get("comment_pool_file", "comments_pool.txt")
        try:
            lines = Path(pool).read_text(encoding="utf-8").splitlines()
            texts = [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]
            if texts:
                return texts
        except FileNotFoundError:
            pass
        path = self.cfg.get("comment_file", "comment.txt")
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
            return [text] if text else []
        except FileNotFoundError:
            logger.error("нет файла комментариев: %s", path)
            return []
