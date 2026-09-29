#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: tiktok.py
Клиент TikTok web API: лента, комментарии, публикация комментариев.
Использует подпись X-Gnarly (xgnarly.py) + TLS-имитацию Chrome через curl_cffi.
"""

import json
import logging
import random
import re
import time
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

from xgnarly import XGnarly, pick_ms_token

try:
    from curl_cffi import requests as crequests
except ImportError:  # pragma: no cover
    crequests = None

logger = logging.getLogger("promobot.tiktok")

TIKTOK_DOMAIN = "https://www.tiktok.com"

HOME_RECOMMEND = f"{TIKTOK_DOMAIN}/api/recommend/item_list/"
USER_DETAIL = f"{TIKTOK_DOMAIN}/api/user/detail/"
USER_POST = f"{TIKTOK_DOMAIN}/api/post/item_list/"
POST_COMMENT_LIST = f"{TIKTOK_DOMAIN}/api/comment/list/"
COMMENT_PUBLISH = f"{TIKTOK_DOMAIN}/api/comment/v2/publish/"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class TikTokError(Exception):
    """Базовая ошибка TikTok API."""


class TikTokAuthError(TikTokError):
    """Сессия протухла или заблокирована."""


class TikTokEmptyError(TikTokError):
    """Сервер вернул пустой ответ (фильтр/подпись отклонена)."""


class TikTokClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        region: str = "US",
        language: str = "ru",
        impersonate: str = "chrome131",
        timeout: float = 20.0,
        retries: int = 3,
    ) -> None:
        if crequests is None:
            raise TikTokError("curl_cffi не установлен: pip install curl_cffi")

        self.ua = user_agent
        self.region = region
        self.language = language
        self.session = crequests.Session(impersonate=impersonate, timeout=timeout)
        self.timeout = timeout
        self.retries = retries
        self.cookies: Dict[str, str] = {}
        self.csrf_token: str = ""
        self.signer = XGnarly(user_agent)

    # ---------------------------------------------------------------- куки
    def load_cookies(self, path: str) -> None:
        """Загружает куки в формате Playwright storage_state: {"cookies": [...]}"""
        p = Path(path)
        if not p.exists():
            raise TikTokAuthError(f"файл куки не найден: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))

        ms_to_domain = {}
        for c in data.get("cookies", []):
            name = c.get("name")
            value = c.get("value", "")
            domain = c.get("domain", "")
            # msToken встречается на разных доменах — оставляем самый специфичный
            if name in ("msToken",):
                ms_to_domain.setdefault(domain, value)
                continue
            self.cookies[name] = value

        ms_vals = sorted(ms_to_domain.items(), key=lambda kv: kv[0].count("."), reverse=True)
        if ms_vals:
            self.cookies["msToken"] = ms_vals[0][1]

        self.csrf_token = self.cookies.get("tt_csrf_token", "")
        if not self.sessionid():
            raise TikTokAuthError("в куки нет sessionid — вход не выполнен")

        logger.info("загружено куки: %d (sessionid есть, csrf=%s)", len(self.cookies), bool(self.csrf_token))

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def sessionid(self) -> Optional[str]:
        return self.cookies.get("sessionid") or self.cookies.get("sessionid_ss")

    def ms_token(self) -> str:
        return self.cookies.get("msToken", "")

    # ---------------------------------------------------------------- параметры
    def base_params(self, **extra) -> List[Tuple[str, str]]:
        """Базовый набор параметров веб-запроса (порядок важен для подписи)."""
        bv = "5.0"
        if "(" in self.ua:
            inner = self.ua.split("(", 1)[1].split(")", 1)[0]
            bv = f"5.0 ({inner})"
        pairs = [
            ("aid", "1988"),
            ("app_language", self.language),
            ("app_name", "tiktok_web"),
            ("browser_language", "ru-RU"),
            ("browser_name", "Mozilla"),
            ("browser_online", "true"),
            ("browser_platform", "Win32"),
            ("browser_version", bv),
            ("channel", "tiktok_web"),
            ("cookie_enabled", "true"),
            ("device_id", self.cookies.get("tt_webid", "")),
            ("device_platform", "web_pc"),
            ("focus_state", "true"),
            ("from_page", "user"),
            ("history_len", "3"),
            ("is_fullscreen", "false"),
            ("is_page_visible", "true"),
            ("language", self.language),
            ("os", "windows"),
            ("priority_region", self.region),
            ("region", self.region),
            ("screen_height", "1080"),
            ("screen_width", "1920"),
            ("tz_name", "Europe/Moscow"),
            ("webcast_language", self.language),
        ]
        for k, v in extra.items():
            pairs.append((k, str(v)))
        return pairs

    # ---------------------------------------------------------------- HTTP
    def _request(self, method: str, url: str, body_bytes: bytes = b"", headers: Optional[Dict] = None, allow_empty: bool = False):
        headers = dict(headers or {})
        headers.setdefault("User-Agent", self.ua)
        headers.setdefault("Cookie", self.cookie_header())
        headers.setdefault("Referer", "https://www.tiktok.com/")
        headers.setdefault("Accept", "application/json, text/plain, */*")
        if method.upper() == "POST":
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        last_exc = None
        for attempt in range(self.retries):
            try:
                if method.upper() == "GET":
                    r = self.session.get(url, headers=headers, timeout=self.timeout)
                else:
                    r = self.session.post(url, headers=headers, content=body_bytes, timeout=self.timeout)
                self._merge_set_cookie(r)
                if r.status_code in (401, 403):
                    raise TikTokAuthError(f"HTTP {r.status_code} — вероятно, сессия невалидна")
                if r.status_code >= 500:
                    last_exc = TikTokError(f"HTTP {r.status_code}")
                    time.sleep(2 ** attempt)
                    continue
                body = r.content
                if not body and not allow_empty:
                    raise TikTokEmptyError("пустой ответ от TikTok")
                return r
            except (TikTokAuthError, TikTokEmptyError):
                raise
            except Exception as exc:
                last_exc = exc
                time.sleep(1.5 ** attempt)
        raise TikTokError(f"запрос не выполнен после {self.retries} попыток: {last_exc}")

    def _merge_set_cookie(self, r) -> None:
        try:
            for name, value in (r.cookies.items() if hasattr(r.cookies, "items") else []):
                if name == "msToken" and value:
                    self.cookies["msToken"] = value
        except Exception:
            pass

    def _signed_url(self, endpoint: str, pairs: List[Tuple[str, str]], body_bytes: bytes = b"") -> str:
        signed_query, _params = self.signer.sign(pairs, ms_token=self.ms_token(), body=body_bytes)
        return f"{endpoint}?{signed_query}"

    def _json(self, r) -> dict:
        try:
            return r.json()
        except Exception:
            raise TikTokError("ответ не JSON")

    # ---------------------------------------------------------------- лента
    def get_recommend(self, count: int = 10) -> List[dict]:
        """Возвращает список видео из рекомендаций (лента)."""
        pairs = self.base_params(count=count)
        url = self._signed_url(HOME_RECOMMEND, pairs)
        r = self._request("GET", url)
        data = self._json(r)
        if not data:
            raise TikTokEmptyError("лента пустая (подпись/фильтр)")
        if data.get("status_code", 0) != 0 and not data.get("itemList"):
            if len(data) < 3:
                raise TikTokEmptyError(f"лента заблокирована: {data}")
        items = data.get("itemList") or []
        return items

    def get_own_videos(self, sec_uid: str, count: int = 20) -> List[dict]:
        """Свои видео юзера по sec_user_id (для режима «свои видео»)."""
        pairs = self.base_params(secUid=sec_uid, count=count, coverFormat="2", cursor="0")
        url = self._signed_url(USER_POST, pairs)
        r = self._request("GET", url)
        data = self._json(r)
        return data.get("awemeList") or data.get("itemList") or []

    def aweme_id(self, item: dict) -> Optional[str]:
        v = item.get("aweme_id") or item.get("id") or item.get("awemeId")
        return str(v) if v else None

    def author_uid(self, item: dict) -> Optional[str]:
        author = item.get("author") or {}
        return author.get("secUid") or author.get("sec_uid")

    def video_text(self, item: dict) -> str:
        return (item.get("desc") or item.get("title") or "")[:80]

    # ---------------------------------------------------------------- комментарии
    def list_comments(self, aweme_id: str, count: int = 5) -> List[dict]:
        pairs = self.base_params(aweme_id=aweme_id, count=count, cursor="0")
        url = self._signed_url(POST_COMMENT_LIST, pairs)
        r = self._request("GET", url)
        data = self._json(r)
        return data.get("comments") or []

    def publish_comment(self, aweme_id: str, text: str) -> dict:
        """Отправляет комментарий. Форма: aweme_id, text, text_extra (ради csrf)."""
        body_map = {
            "aweme_id": aweme_id,
            "text": text,
            "text_extra": "[]",
        }
        body_bytes = "&".join(f"{k}={quote(v, safe='')}" for k, v in body_map.items()).encode()

        pairs = self.base_params()
        url = self._signed_url(COMMENT_PUBLISH, pairs, body_bytes=body_bytes)

        headers = {}
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
            headers["X-CSRFToken"] = self.csrf_token
            headers["x-csrf-token"] = self.csrf_token

        r = self._request("POST", url, body_bytes=body_bytes, headers=headers)
        data = self._json(r)
        if data.get("status_code") not in (0, None):
            return {"ok": False, "data": data}
        return {"ok": True, "data": data}

    # ---------------------------------------------------------------- вспомогательное
    @staticmethod
    def extract_aweme_ids_from_urls(text: str) -> List[str]:
        """Вытаскивает id видео из ссылок вида /video/<id> или ?item_id=<id>."""
        ids = []
        for m in re.finditer(r"/video/(\d{5,})", text or ""):
            ids.append(m.group(1))
        for m in re.finditer(r"[?&]item_id=(\d{5,})", text or ""):
            ids.append(m.group(1))
        return ids

    def extract_sec_uid_from_url(self, url: str) -> Optional[str]:
        """Достаёт secUid из ссылки /user/<secUid>. Возвращает и удаляет из кук не нужно."""
        m = re.search(r"/user/(MS4wLjABAAAA[\w-]+)", url or "")
        return m.group(1) if m else None

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    c = TikTokClient()
    c.load_cookies("sessions/acc1.json")
    items = c.get_recommend(count=5)
    print("videos:", len(items))
    for it in items[:5]:
        print(c.aweme_id(it), c.video_text(it))