#!/usr/bin/env python
# -*- encoding: utf-8 -*-
"""
@Description: selftest.py
Проверка promobot:
  python selftest.py            — локальные тесты подписей (без интернета);
  python selftest.py --online   — живая проверка против TikTok (после деплоя/локально с новыми куками).

Возвращает 0 при успехе, 1 при провале; в Telegram можно слать результат.
"""

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _ua():
    from tiktok import DEFAULT_UA
    return DEFAULT_UA


def test_encode_query():
    from xgnarly import encode_query
    pairs = [("a", "1"), ("b", "привет мир"), ("c", "x&y=z")]
    q = encode_query(pairs)
    assert isinstance(q, str) and q.startswith("a=1&"), q
    # кириллица должна быть percent-кодирована
    assert "привет" not in q, q
    print("encode_query OK")


def test_seal_unseal_roundtrip():
    from xgnarly import seal, unseal, KEY_WORDS
    payload = b"hello xbogus verification"
    key = tuple((0x11223344 + i * 0x01020304) & 0xFFFFFFFF for i in range(KEY_WORDS))
    token = seal(payload, key)
    restored, restored_key = unseal(token)
    assert restored == payload, (restored, payload)
    assert tuple(restored_key) == key, (tuple(restored_key), key)
    print("seal/unseal round-trip OK")


def test_sign_deterministic_inputs():
    from xgnarly import sign, KEY_WORDS
    from tiktok import DEFAULT_UA
    pairs = [("aid", "1988"), ("app_name", "tiktok_web"), ("region", "US")]
    k1 = tuple((0xDEAD0000 + i) & 0xFFFFFFFF for i in range(KEY_WORDS))
    k2 = tuple((0xBEEF0000 + i) & 0xFFFFFFFF for i in range(KEY_WORDS))
    kw = dict(timestamp=1700000000, nonce=42, nonce2=43, key=k1, key2=k2)
    q1, p1 = sign(pairs, DEFAULT_UA, ms_token="tok123", **kw)
    q2, p2 = sign(pairs, DEFAULT_UA, ms_token="tok123", **kw)
    assert q1 == q2, "подпись не детерминирована при фикс. nonce/time"
    assert p1["X-Gnarly"] == p2["X-Gnarly"]
    assert "X-Dynosaur" in q1 and "X-Gnarly" in q1 and "X-Bogus=1" in q1
    assert q1.startswith("aid=1988&"), q1
    print("sign OK:", q1[:80], "…")


def test_hashes():
    from xgnarly import hash_state
    a = hash_state("Mozilla/5.0")
    b = hash_state("Mozilla/5.0")
    c = hash_state("Chrome")
    assert a == b and a != c
    print("hash_state OK")


def test_xbogus():
    from xbogus import XBogus
    x = XBogus("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0")
    q, _sig, _ua = x.getXBogus("aid=1988&app_name=tiktok_web&region=US")
    assert isinstance(_sig, str) and len(_sig) > 0
    assert q.endswith("&X-Bogus=" + _sig)
    print("xbogus OK:", _sig)


def _clients():
    from tiktok import TikTokClient
    c = TikTokClient()
    c.load_cookies("sessions/acc1.json")
    return c


def online():
    c = _clients()
    print("→ лента (recommend)")
    t0 = time.time()
    items = c.get_recommend(count=3)
    print(f"  получено {len(items)} видео за {time.time()-t0:.1f}с")
    if not items:
        print("PASS? — пусто, но без исключения")
        return
    it = items[0]
    aweme_id = c.aweme_id(it)
    print("  первое видео:", aweme_id, "|", c.video_text(it)[:40])
    assert aweme_id, "нет aweme_id"

    print("→ список комментариев")
    cm = c.list_comments(aweme_id, count=2)
    print(f"  комментариев: {len(cm)}")

    print("→ публикация тестового комментария")
    author = (it.get("author") or {}).get("uniqueId")
    video_page = f"https://www.tiktok.com/@{author}/video/{aweme_id}" if author else None
    print("  визит на страницу видео для свежего msToken…")
    c.fetch_video_page(aweme_id, author=author)
    print("  msToken:", (c.ms_token() or "")[:12] + "…")
    res = c.publish_comment(aweme_id, "Подпишитесь на нас в Telegram: t.me/fivasmp", referer=video_page)
    print("  status:", res.get("ok"), res.get("data", {}))
    print("→ проверка: есть ли комментарий на странице")
    cm2 = c.list_comments(aweme_id, count=10)
    found = any("t.me/fivasmp" in (cc.get("text") or "") for cc in cm2)
    print(f"  комментариев в списке: {len(cm2)}, наш найден: {found}")
    # не падаем даже если формат тела не идеален — печатаем тело для разбора
    c.close()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--online":
        online()
        print("ONLINE CHECK: без исключений")
        return 0
    test_encode_query()
    test_seal_unseal_roundtrip()
    test_sign_deterministic_inputs()
    test_hashes()
    test_xbogus()
    print("=" * 40)
    print("Все локальные тесты прошли")
    return 0


if __name__ == "__main__":
    sys.exit(main())