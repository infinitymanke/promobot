#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
login_capture.py (ytbot) — разовый вход в YouTube-канал через OAuth 2.0.

Что делает:
  1) открывает браузер на страницу согласия Google;
  2) ловит код на локальном редиректе http://localhost:8765/;
  3) меняет код на refresh-token (access_type=offline + prompt=consent);
  4) пишет sessions/token.json;
  5) печатает base64 этого json — его кладём в секрет YT_CREDENTIALS_B64.

Перед первым запуском: создай OAuth client (Desktop app) в Google Cloud,
скачай его json и положи рядом как client_secret.json.
Скопируй:    python ytbot\\login_capture.py
"""

import base64
import json
import os
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
CLIENT_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "sessions" / "token.json"
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
]

_code_holder = {"code": None, "error": None}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        if "code" in params:
            _code_holder["code"] = params["code"][0]
            body = "Готово! Можно закрыть окно и вернуться в консоль."
        else:
            _code_holder["error"] = params.get("error", ["неизвестно"])[0]
            body = "Ошибка входа: " + str(_code_holder["error"])
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body><h2>{body}</h2></body></html>".encode("utf-8"))

    def log_message(self, *a):
        pass


def load_client() -> tuple:
    if not CLIENT_FILE.exists():
        print(f"!! нет {CLIENT_FILE.name}. Скачай OAuth client (Desktop app) с Google Cloud и положи рядом.")
        sys.exit(1)
    data = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    inst = data.get("installed") or data.get("web") or {}
    if not inst.get("client_id") or not inst.get("client_secret"):
        print("!! в client_secret.json нет client_id/client_secret")
        sys.exit(1)
    return inst["client_id"], inst["client_secret"]


def main() -> int:
    client_id, client_secret = load_client()
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "select_account consent",
    })

    print("=" * 70)
    print("Откроется браузер. Войди в ТОТ аккаунт, где будет YouTube-канал,")
    print("и нажми 'Разрешить'. Если браузер не открылся — скопируй ссылку:")
    print(auth_url)
    print("=" * 70)

    httpd = HTTPServer(("127.0.0.1", PORT), _Handler)
    httpd.timeout = 300
    webbrowser.open(auth_url)
    print("Жду подтверждения в браузере (до 5 минут)...")
    while _code_holder["code"] is None and _code_holder["error"] is None:
        httpd.handle_request()

    if _code_holder["error"]:
        print("!! ошибка авторизации:", _code_holder["error"])
        return 1
    code = _code_holder["code"]
    if not code:
        print("!! код не получен")
        return 1

    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }, timeout=30)
    if resp.status_code != 200:
        print("!! обмен кода не удался:", resp.status_code, resp.text[:300])
        return 1
    tokens = resp.json()
    refresh = data.get("refresh_token") if (data := None) else tokens.get("refresh_token")
    if not refresh:
        print("!! Google не выдал refresh_token. Убедись, что access_type=offline и prompt=consent,")
        print("   и что ты НЕ выдавал доступ ранее (иначе отзови и повтори).")
        return 1

    token = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh,
        "scope": " ".join(SCOPES),
    }
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8")

    raw = json.dumps(token, ensure_ascii=False).encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    print("\nOK: refresh-token получен и сохранён в", TOKEN_FILE)
    print("\nYT_CREDENTIALS_B64 =")
    print(b64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
