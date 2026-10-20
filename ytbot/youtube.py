#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
youtube.py — клиент YouTube Data API v3 по OAuth2 (refresh token).

Умеет:
  - обновлять access_token по refresh_token;
  - search_shorts(query) — поиск коротких видео ниши;
  - video_stats(ids) — статистика (просмотры/лайки) батчем;
  - post_comment(video_id, text) — топ-комментарий (commentThreads.insert);
  - вести локальный счётчик квоты за сутки.

Никакой браузерной автоматизации — только официальный API.
"""

import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger("ytbot.youtube")

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"

# Стоимость операций в единицах квоты (грубая оценка, лимит 10000/сутки)
COST_SEARCH = 100
COST_STATS = 1
COST_COMMENT = 50
COST_UPLOAD = 1600

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


class YTError(Exception):
    pass


class YTQuotaError(YTError):
    pass


class YTClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        region: str = "RU",
        lang: str = "ru",
        quota_ceiling: int = 4000,
        state: dict = None,
        timeout: int = 30,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.region = region
        self.lang = lang
        self.timeout = timeout
        self.quota_ceiling = quota_ceiling
        self.state = state if state is not None else {}
        self._token = ""
        self._expires = 0.0
        self.session = requests.Session()

    # ---------------------------------------------------------------- auth
    def _access(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }, timeout=30)
        if resp.status_code != 200:
            raise YTError(f"refresh_token не сработал: {resp.status_code} {resp.text[:160]}")
        data = resp.json()
        self._token = data["access_token"]
        self._expires = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _quota_left(self) -> int:
        q = self.state.setdefault("quota", {})
        today = time.strftime("%Y-%m-%d")
        if q.get("day") != today:
            q["day"] = today
            q["used"] = 0
        return self.cfg_quota() - int(q.get("used", 0))

    def cfg_quota(self) -> int:
        return self.quota_ceiling

    def _charge(self, units: int) -> None:
        q = self.state.setdefault("quota", {})
        q["used"] = int(q.get("used", 0)) + units

    def _get(self, path: str, params: dict):
        if self._quota_left() <= 0:
            raise YTQuotaError("суточная квота исчерпана")
        headers = {"Authorization": "Bearer " + self._access()}
        r = requests.get(API + path, params=params, headers=headers, timeout=30)
        return self._handle(r)

    def _post(self, path: str, params: dict, body: dict):
        if self._quota_left() <= 0:
            raise YTQuotaError("суточная квота исчерпана")
        headers = {"Authorization": "Bearer " + self._access()}
        r = requests.post(API + path, params=params, headers=headers, json=body, timeout=30)
        return self._handle(r)

    def _handle(self, r: requests.Response):
        if r.status_code == 200:
            return r.json()
        try:
            err = r.json().get("error", {})
        except Exception:
            err = {}
        reason = ""
        for e in err.get("errors", []) or []:
            reason = e.get("reason", "")
            break
        msg = err.get("message", r.text[:160])
        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            raise YTQuotaError(msg)
        raise YTError(f"{r.status_code} {reason}: {msg}")

    # ---------------------------------------------------------------- поиск
    def search_shorts(self, query: str, max_results: int = 25) -> list:
        """Поиск коротких видео (видео <~4 мин) по запросу."""
        data = self._get(
            "/search",
            {
                "part": "snippet",
                "type": "video",
                "videoDuration": "short",
                "maxResults": min(max_results, 50),
                "q": query,
                "order": "date",
                "relevanceLanguage": self.lang,
                "regionCode": self.region,
            },
        )
        self._charge(COST_SEARCH)
        out = []
        for it in data.get("items", []):
            vid = (it.get("id") or {}).get("videoId")
            sn = it.get("snippet") or {}
            if not vid:
                continue
            out.append(
                {
                    "id": vid,
                    "title": sn.get("title", ""),
                    "description": sn.get("description", ""),
                    "channelId": sn.get("channelId", ""),
                    "publishedAt": sn.get("publishedAt", ""),
                }
            )
        return out

    def video_stats(self, ids: list) -> dict:
        """Статистика для списка id (батч до 50 за вызов)."""
        out = {}
        for i in range(0, len(ids), 50):
            chunk = ids[i : i + 50]
            data = self._get(
                "/videos",
                {"part": "statistics,snippet", "id": ",".join(chunk)},
            )
            self._charge(COST_STATS)
            for it in data.get("items", []):
                vid = it.get("id")
                st = it.get("statistics") or {}
                sn = it.get("snippet") or {}
                views = int(st.get("viewCount", 0) or 0)
                out[vid] = {
                    "views": views,
                    "likes": int(st.get("likeCount", 0) or 0),
                    "comments": int(st.get("commentCount", 0) or 0),
                    "title": sn.get("title", ""),
                }
        return out

    # ---------------------------------------------------------------- загрузка
    def upload_video(
        self,
        path,
        title: str,
        description: str = "",
        tags: list = None,
        category_id: str = "20",
        privacy: str = "public",
    ) -> str:
        """Загрузка файла на канал (resumable upload). Возвращает video_id."""
        p = Path(path)
        size = p.stat().st_size
        token = self._access()
        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": (tags or [])[:30],
                "categoryId": category_id,
                "defaultLanguage": self.lang,
                "defaultAudioLanguage": self.lang,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        head = {
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        }
        if self._quota_left() < COST_UPLOAD:
            raise YTQuotaError(f"нет {COST_UPLOAD} единиц квоты на загрузку")
        r = requests.post(
            UPLOAD_URL + "?uploadType=resumable&part=snippet,status",
            headers=head, json=body, timeout=60,
        )
        if r.status_code != 200:
            self._handle(r)
        location = r.headers.get("Location", "")
        if not location:
            raise YTError("upload: нет Location-заголовка")
        chunk = 8 * 1024 * 1024
        offset = 0
        video_id = ""
        with p.open("rb") as fh:
            while offset < size:
                data = fh.read(chunk)
                if not data:
                    break
                end = offset + len(data) - 1
                rr = requests.put(
                    location,
                    data=data,
                    headers={
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                    timeout=600,
                )
                if rr.status_code in (200, 201):
                    video_id = (rr.json() or {}).get("id", "")
                    break
                if rr.status_code != 308:
                    self._handle(rr)
                offset = end + 1
        if not video_id:
            raise YTError("upload: не получили video_id")
        self._charge(COST_UPLOAD)
        logger.info("загружено: %s -> %s", p.name, video_id)
        return video_id

    def my_channel(self) -> dict:
        """Канал владельца токена: {id, title, subs}."""
        data = self._get("/channels", {"part": "snippet,statistics", "mine": "true"})
        self._charge(COST_STATS)
        items = data.get("items") or []
        if not items:
            return {}
        it = items[0]
        st = it.get("statistics") or {}
        sn = it.get("snippet") or {}
        return {
            "id": it.get("id", ""),
            "title": sn.get("title", ""),
            "subs": st.get("subscriberCount", "?"),
        }

    # ---------------------------------------------------------------- коммент
    def post_comment(self, video_id: str, text: str) -> str:
        """Оставить топ-комментарий под видео. Возвращает id комментария."""
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        }
        data = self._post("/commentThreads", {"part": "snippet"}, body)
        self._charge(COST_COMMENT)
        return (data.get("snippet") or {}).get("topLevelComment", {}).get("id", "")

    def comment_visible(self, video_id: str, comment_id: str) -> bool:
        """Виден ли наш комментарий публично (не ушёл ли на модерацию)."""
        try:
            data = self._get("/commentThreads", {
                "part": "snippet", "videoId": video_id,
                "maxResults": 100, "order": "time", "textFormat": "plainText",
            })
        except Exception:
            return True
        for it in data.get("items", []):
            if (it.get("snippet") or {}).get("topLevelComment", {}).get("id") == comment_id:
                return True
        return False

    def close(self):
        try:
            requests.session()
        except Exception:
            pass
