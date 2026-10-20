#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
upload.py (vidbot) — загрузка готовых роликов из out/ на YouTube.

Читает out/*.json (метаданные рядом с .mp4), тянет токен из ytbot/sessions/token.json
или секрета YT_CREDENTIALS_B64, загружает видео через youtube.upload и отмечает
state.json, чтобы не залить повторно.

Флаги:
  --probe   только показать канал владельца токена и выйти
  --limit N максимум загрузок за запуск (по умолчанию 1)
"""

import argparse
import base64
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
YT_DIR = BASE.parent / "ytbot"
OUT_DIR = BASE / json.loads((BASE / "config.json").read_text(encoding="utf-8")).get("out_dir", "out")
STATE_FILE = BASE / "state.json"

sys.path.insert(0, str(YT_DIR))
from youtube import YTClient, YTError  # noqa: E402


def load_creds() -> dict:
    raw = os.environ.get("YT_CREDENTIALS_B64", "").strip()
    if raw:
        try:
            data = json.loads(base64.b64decode(raw).decode("utf-8"))
            if data.get("client_id") and data.get("refresh_token"):
                return data
            inst = data.get("installed") or data.get("web") or {}
            cid = data.get("client_id") or inst.get("client_id", "")
            if cid and data.get("refresh_token"):
                return {
                    "client_id": cid,
                    "client_secret": data.get("client_secret") or inst.get("client_secret", ""),
                    "refresh_token": data["refresh_token"],
                }
        except Exception as exc:
            print("YT_CREDENTIALS_B64: ", exc)
    tok = YT_DIR / "sessions" / "token.json"
    if tok.exists():
        data = json.loads(tok.read_text(encoding="utf-8"))
        if data.get("client_id") and data.get("refresh_token"):
            return data
        inst = data.get("installed") or data.get("web") or {}
        return {
            "client_id": data.get("client_id") or inst.get("client_id", ""),
            "client_secret": data.get("client_secret") or inst.get("client_secret", ""),
            "refresh_token": data.get("refresh_token", ""),
        }
    return {}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"made": 0, "used": {}, "uploaded": {}}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--limit", type=int, default=1)
    args = ap.parse_args()

    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    creds = load_creds()
    if not creds.get("client_id") or not creds.get("refresh_token"):
        print("!! нет кред YouTube (YT_CREDENTIALS_B64 или ytbot/sessions/token.json)")
        return 1

    st = load_state()
    client = YTClient(
        client_id=creds["client_id"],
        client_secret=creds.get("client_secret", ""),
        refresh_token=creds["refresh_token"],
        region="RU", lang="ru",
        quota_ceiling=int(cfg.get("quota_ceiling", 8000)),
        state=json.loads((YT_DIR / "state.json").read_text(encoding="utf-8"))
        if (YT_DIR / "state.json").exists() else {},
    )

    if args.probe:
        ch = client.my_channel()
        print("канал:", json.dumps(ch, ensure_ascii=False))
        return 0

    metas = sorted(OUT_DIR.glob("*.json")) if OUT_DIR.exists() else []
    queue = []
    for m in metas:
        try:
            meta = json.loads(m.read_text(encoding="utf-8"))
        except Exception:
            continue
        vid_file = Path(meta.get("file", m.with_suffix(".mp4")))
        if not vid_file.exists():
            continue
        stem = vid_file.stem
        if stem in st.get("uploaded", {}):
            continue
        queue.append((vid_file, meta, stem))

    if not queue:
        print("нет новых видео в out/ — генерируй: python vidbot\\make.py")
        return 0

    uploaded = 0
    for vid_file, meta, stem in queue[: max(1, args.limit)]:
        try:
            vid = client.upload_video(
                vid_file,
                title=meta.get("title", "Факты о Minecraft #shorts"),
                description=meta.get("description", ""),
                tags=meta.get("tags", []),
                category_id=str(cfg.get("category_id", "20")),
            )
        except YTError as exc:
            print(f"!! {vid_file.name}: {exc}")
            break
        st.setdefault("uploaded", {})[stem] = vid
        save_state(st)
        try:
            (YT_DIR / "state.json").write_text(
                json.dumps(client.state, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        uploaded += 1
        print(f"загружено: {vid_file.name} -> https://youtu.be/{vid}")

    q = client.state.get("quota", {})
    print("статистика:", json.dumps(
        {"uploaded": uploaded, "quota_used": q.get("used", 0), "quota_left": client._quota_left()},
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
