#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make.py (vidbot) — генератор Shorts-роликов:
факты/пиар → озвучка edge-tts → пиксельные анимированные фоны (PIL) → ffmpeg.
Каждый ролик: интро-хук + 3 факта + 1 пиар fivasmp + аутро с CTA.
На выход: out/*.mp4 + out/*.json (метаданные для upload.py).
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("!! нет pillow: pip install pillow")
    sys.exit(1)

BASE = Path(__file__).resolve().parent
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
W = int(CFG.get("width", 1080))
H = int(CFG.get("height", 1920))
FPS = int(CFG.get("fps", 24))
PAD = float(CFG.get("pad_s", 0.5))
OUT_DIR = BASE / CFG.get("out_dir", "out")
STATE_FILE = BASE / "state.json"

wg = Path.home() / "AppData/Local/Microsoft/WinGet/Links"
if wg.exists():
    os.environ["PATH"] += os.pathsep + str(wg)


def which_bin(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    for base in (Path("/usr/bin"), Path("/usr/local/bin")):
        cand = base / name
        if cand.exists():
            return str(cand)
    wg = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
    if wg.exists():
        pat = f"*/**/{name}.exe" if os.name == "nt" else f"*/**/{name}"
        hits = sorted(wg.glob(pat))
        if hits:
            return str(hits[0])
    raise RuntimeError(f"не найден {name} — установи ffmpeg")


FFMPEG = which_bin("ffmpeg")
FFPROBE = which_bin("ffprobe")


def find_font(bold: bool = True) -> str:
    if os.name == "nt":
        cands = [
            r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        ]
    else:
        cands = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ]
    for c in cands:
        if Path(c).exists():
            return c
    raise RuntimeError("не найден шрифт с кириллицей")


FONT_BOLD = find_font(True)
FONT_REG = find_font(False)


def load_lines(name: str) -> list:
    p = BASE / name
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"made": 0, "used": {}, "uploaded": {}}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def pick(pool: list, st: dict, rng: random.Random) -> str:
    if not pool:
        return ""
    used = st.setdefault("used", {})
    fresh = [x for x in pool if not used.get(x)]
    src = fresh or pool
    choice = rng.choice(src)
    used[choice] = time.strftime("%Y-%m-%d")
    if len(used) > 400:
        for k in list(used)[:150]:
            used.pop(k, None)
    return choice


def probe_dur(path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def synth(text: str, out: Path) -> float:
    import edge_tts

    async def _run():
        await edge_tts.Communicate(text, CFG.get("voice", "ru-RU-DmitryNeural"),
                                   rate=CFG.get("rate", "+0%")).save(str(out))

    for attempt in range(3):
        try:
            asyncio.run(_run())
            break
        except Exception as exc:
            if attempt == 2:
                raise
            print(f"  tts retry: {exc}")
            time.sleep(2)
    return probe_dur(out)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


SCENES = ("day", "cave", "night", "sunset")

SCENE_SKY = {
    "day": ((118, 195, 240), (191, 231, 255)),
    "sunset": ((255, 140, 80), (255, 214, 160)),
    "night": ((10, 14, 36), (40, 56, 100)),
    "cave": ((36, 38, 44), (20, 21, 26)),
}


class World:
    B = 60

    def __init__(self, scene: str, seed: int):
        self.scene = scene
        rng = random.Random(seed)
        self.rng = rng
        self.base = self._build_static(rng)

    def _build_static(self, rng) -> Image.Image:
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        top, bot = SCENE_SKY[self.scene]
        grad = Image.new("RGB", (1, 256))
        gp = grad.load()
        for y in range(256):
            gp[0, y] = lerp(top, bot, y / 255)
        img.paste(grad.resize((W, H)), (0, 0))
        if self.scene == "night":
            for x, y, s in [(rng.uniform(0, W), rng.uniform(0, H * 0.5), rng.randint(2, 4)) for _ in range(70)]:
                d.rectangle([x, y, x + s, y + s], fill=(210, 220, 255))
            d.rectangle([W - 260, 160, W - 170, 250], fill=(235, 235, 220))
            d.rectangle([W - 240, 190, W - 215, 215], fill=(200, 200, 190))
        elif self.scene == "day":
            d.rectangle([140, 170, 250, 280], fill=(255, 236, 120))
            d.rectangle([155, 185, 235, 265], fill=(255, 246, 170))
        elif self.scene == "sunset":
            d.rectangle([W - 320, 560, W - 190, 690], fill=(255, 170, 70))
            d.rectangle([W - 300, 580, W - 210, 670], fill=(255, 210, 120))
        self._clouds = []
        for _ in range(5):
            self._clouds.append([rng.uniform(-100, W), rng.uniform(140, 520), rng.uniform(20, 55), rng.uniform(14, 40)])
        self._draw_ground(d, rng)
        if self.scene == "night":
            for _ in range(3):
                self._tree(d, rng)
        elif self.scene == "day":
            for _ in range(4):
                self._tree(d, rng)
        if self.scene == "cave":
            for _ in range(46):
                x = rng.uniform(0, W)
                y = rng.uniform(0, H)
                col = rng.choice([(90, 160, 220), (90, 220, 140), (230, 190, 80), (200, 90, 90)])
                d.rectangle([x, y, x + 10, y + 10], fill=col)
                d.rectangle([x + 14, y + 14, x + 22, y + 22], fill=col)
            for x0 in range(0, W, 120):
                hgt = rng.randint(40, 160)
                d.rectangle([x0, 0, x0 + 120, hgt], fill=(48, 50, 58))
        self._parts = []
        for _ in range(30):
            self._parts.append([rng.uniform(0, W), rng.uniform(0, H), rng.uniform(4, 11), rng.randint(3, 8)])
        return img

    def _draw_ground(self, d, rng):
        cols = W // self.B + 1
        h = 6
        heights = []
        for _ in range(cols):
            h += rng.choice((-1, 0, 0, 1))
            h = max(5, min(10, h))
            heights.append(h)
        self.heights = heights
        dirt = (134, 96, 67) if self.scene != "cave" else (70, 66, 62)
        grass = (95, 175, 65) if self.scene == "day" else (
            (120, 150, 70) if self.scene == "sunset" else (
                (52, 90, 60) if self.scene == "night" else (70, 70, 74)))
        stone = (120, 120, 124) if self.scene != "cave" else (78, 80, 88)
        for i, hh in enumerate(heights):
            x0 = i * self.B
            y_top = H - hh * self.B
            for r in range(hh):
                y = y_top + r * self.B
                if r == 0:
                    c = grass
                elif r <= 2:
                    c = dirt
                else:
                    c = stone
                d.rectangle([x0, y, x0 + self.B - 1, y + self.B - 1], fill=c)
                for _ in range(3):
                    sx = x0 + rng.randint(4, self.B - 10)
                    sy = y + rng.randint(4, self.B - 10)
                    shade = tuple(max(0, min(255, ch + rng.randint(-22, 22))) for ch in c)
                    d.rectangle([sx, sy, sx + 6, sy + 6], fill=shade)

    def _tree(self, d, rng):
        cols = len(self.heights)
        i = rng.randint(2, cols - 3)
        y = H - self.heights[i] * self.B
        d.rectangle([i * self.B + 22, y - 120, i * self.B + 38, y], fill=(101, 67, 33))
        leaf = (58, 140, 50) if self.scene == "day" else ((80, 110, 55) if self.scene == "sunset" else (30, 70, 45))
        x0 = i * self.B - 60
        d.rectangle([x0, y - 220, x0 + 180, y - 100], fill=leaf)
        d.rectangle([x0 + 30, y - 260, x0 + 150, y - 220], fill=leaf)

    def frame(self, t: float) -> Image.Image:
        img = self.base.copy()
        d = ImageDraw.Draw(img)
        if self.scene != "cave":
            for cx, cy, sw, sh in self._clouds:
                x = (cx + t * 22 * (sw / 40)) % (W + 260) - 130
                col = (255, 255, 255) if self.scene == "day" else (
                    (255, 200, 180) if self.scene == "sunset" else (70, 80, 110))
                d.rectangle([x, cy, x + sw * 3, cy + sh], fill=col)
                d.rectangle([x + sw, cy - sh * 0.6, x + sw * 2, cy], fill=col)
        for p in self._parts:
            px, py, speed, size = p
            if self.scene == "sunset":
                y = (py - t * 26 * speed) % H
            else:
                y = (py + t * 26 * speed) % H
            x = px + math.sin(t * 1.3 + px) * 12
            if self.scene == "night":
                col = (225, 235, 255)
            elif self.scene == "sunset":
                col = (255, 175, 90)
            elif self.scene == "cave":
                col = (110, 110, 120)
            else:
                col = (255, 255, 240)
            d.rectangle([x, y, x + size, y + size], fill=col)
        return img


def fit_text(d, text, font_path, size, max_w, fill, stroke, y_center, line_gap=1.18):
    font = ImageFont.truetype(font_path, size)
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    lh = int(size * line_gap)
    y0 = y_center - (len(lines) * lh) // 2
    for i, ln in enumerate(lines):
        d.text((W // 2, y0 + i * lh), ln, font=font, fill=fill,
               anchor="mm", stroke_width=max(4, size // 11), stroke_fill=stroke)
    return len(lines)


def draw_badge(d, text, fill=(20, 20, 24), txt=(255, 255, 255)):
    font = ImageFont.truetype(FONT_BOLD, 44)
    tw = d.textlength(text, font=font)
    x0 = W // 2 - tw // 2 - 36
    x1 = W // 2 + tw // 2 + 36
    d.rounded_rectangle([x0, 150, x1, 240], radius=24, fill=fill)
    d.text((W // 2, 195), text, font=font, fill=txt, anchor="mm")


class CardWorld:
    def __init__(self, kind: str):
        self.kind = kind
        self.img = Image.new("RGB", (W, H), (18, 20, 27))
        d = ImageDraw.Draw(self.img)
        for y in range(H):
            pass
        g = Image.new("RGB", (1, 2))
        g.putpixel((0, 0), (24, 26, 34))
        g.putpixel((0, 1), (12, 13, 17))
        self.img.paste(g.resize((W, H)), (0, 0))
        d = ImageDraw.Draw(self.img)
        for _ in range(24):
            x = random.uniform(0, W)
            y = random.uniform(0, H)
            s = random.randint(2, 4)
            d.rectangle([x, y, x + s, y + s], fill=(60, 66, 84))
        if kind == "intro":
            cx, cy = W // 2, 620
            d.rounded_rectangle([cx - 130, cy - 150, cx + 130, cy + 150], radius=40, fill=(168, 170, 178))
            d.rectangle([cx - 70, cy - 50, cx - 30, cy + 10], fill=(28, 28, 32))
            d.rectangle([cx + 30, cy - 50, cx + 70, cy + 10], fill=(28, 28, 32))
            d.rectangle([cx - 20, cy + 60, cx + 20, cy + 90], fill=(90, 92, 100))
            f1 = ImageFont.truetype(FONT_BOLD, 110)
            d.text((W // 2, 960), "АЛЬФРЕДО", font=f1, fill=(255, 255, 255), anchor="mm",
                   stroke_width=6, stroke_fill=(0, 0, 0))
            f2 = ImageFont.truetype(FONT_REG, 48)
            d.text((W // 2, 1060), "редкие факты о майне", font=f2, fill=(170, 178, 200), anchor="mm")
        else:
            f1 = ImageFont.truetype(FONT_BOLD, 112)
            d.text((W // 2, 760), "ПОДПИСЫВАЙСЯ", font=f1, fill=(255, 230, 90), anchor="mm",
                   stroke_width=7, stroke_fill=(0, 0, 0))
            f2 = ImageFont.truetype(FONT_REG, 52)
            d.text((W // 2, 880), "каждый день по ролику", font=f2, fill=(220, 224, 235), anchor="mm")
            f3 = ImageFont.truetype(FONT_BOLD, 60)
            d.text((W // 2, 1040), "СЕРВЕР: " + CFG.get("link", "t.me/fivasmp"), font=f3,
                   fill=(120, 230, 160), anchor="mm", stroke_width=5, stroke_fill=(0, 0, 0))

    def frame(self, t: float) -> Image.Image:
        return self.img.copy()


def render_segment(world, audio: Path, out_mp4: Path, text: str = "", badge: str = "",
                   scene_seed: int = 0) -> float:
    audio_dur = probe_dur(audio)
    total = audio_dur + PAD
    n_frames = int(math.ceil(total * FPS))
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-i", str(audio),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-af", f"apad=pad_dur={PAD:.2f}", "-t", f"{total:.3f}",
        "-movflags", "+faststart",
        str(out_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n_frames):
            t = i / FPS
            img = world.frame(t)
            d = ImageDraw.Draw(img)
            if text:
                fit_text(d, text, FONT_BOLD, 72, W - 140, (255, 255, 255), (10, 10, 12), int(H * 0.42))
            if badge:
                draw_badge(d, badge)
            proc.stdin.write(img.tobytes())
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg упал с кодом {rc} на {out_mp4.name}")
    return total


def make_video(idx: int, st: dict, rng: random.Random, work: Path) -> dict:
    mc = load_lines("mc_facts.txt")
    gen = load_lines("facts.txt")
    ads = load_lines("ads.txt")
    n_facts = int(CFG.get("facts_per_video", 3))
    facts = []
    if rng.random() < 0.5 and gen:
        facts.append(pick(gen, st, rng))
    while len(facts) < n_facts:
        f = pick(mc, st, rng)
        if f and f not in facts:
            facts.append(f)
    ad = pick(ads, st, rng) or ""
    hook = CFG.get("hook", "Эти факты о Minecraft знают немногие")
    cta = "Подписывайся, Альфредо здесь каждый день! Сервер fivasmp — в описании."

    segs = []
    print(f"[видео {idx}] хук, {len(facts)} факт(ов), пиар")
    audio = work / "hook.mp3"
    dur = synth(hook, audio)
    seg = work / "seg_00_intro.mp4"
    render_segment(CardWorld("intro"), audio, seg, text=hook, badge="АЛЬФРЕДО")
    segs.append(seg)

    scenes = list(SCENES)
    rng.shuffle(scenes)
    for i, fact in enumerate(facts, 1):
        audio = work / f"fact_{i}.mp3"
        dur = synth(fact, audio)
        scene = scenes[(i - 1) % len(scenes)]
        world = World(scene, seed=int(hashlib.md5(fact.encode()).hexdigest()[:8], 16))
        seg = work / f"seg_{i:02d}_fact.mp4"
        print(f"  факт {i}/{len(facts)}: {fact[:46]}... ({scene}, {dur:.1f}с)")
        render_segment(world, audio, seg, text=fact, badge=f"ФАКТ {i}/{len(facts)}")
        segs.append(seg)

    if ad:
        audio = work / "ad.mp3"
        dur = synth(ad, audio)
        world = World("sunset", seed=rng.randint(0, 10**6))
        seg = work / "seg_90_ad.mp4"
        print(f"  пиар: {ad[:46]}...")
        render_segment(world, audio, seg, text=ad, badge="БЕСПЛАТНЫЙ СЕРВЕР")
        segs.append(seg)

    audio = work / "cta.mp3"
    dur = synth(cta, audio)
    seg = work / "seg_99_outro.mp4"
    render_segment(CardWorld("outro"), audio, seg, text=cta)
    segs.append(seg)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    final = OUT_DIR / f"vid_{stamp}_{idx}.mp4"
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    r = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", "-r", str(FPS), str(final)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"concat: {r.stderr[:200]}")
    vdur = probe_dur(final)
    size_mb = final.stat().st_size / 1e6
    hashtags = " ".join("#" + h for h in CFG.get("hashtags", []))
    title = f"{hook} #shorts"
    desc = "\n".join(f"{i}. {f}" for i, f in enumerate(facts, 1))
    desc = f"{desc}\n\nСервер: {CFG.get('link', 't.me/fivasmp')}\n\n{hashtags}"
    meta = {
        "file": str(final),
        "title": title,
        "description": desc,
        "tags": CFG.get("hashtags", []),
        "duration_s": round(vdur, 1),
        "size_mb": round(size_mb, 1),
        "facts": facts,
        "ad": ad,
    }
    (OUT_DIR / (final.stem + ".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[видео {idx}] готово: {final.name} {vdur:.1f}с {size_mb:.1f}МБ")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    st = load_state()
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    work = BASE / "_work" / time.strftime("%Y%m%d_%H%M%S")
    work.mkdir(parents=True, exist_ok=True)
    try:
        for i in range(1, args.count + 1):
            make_video(i, st, rng, work)
        st["made"] = int(st.get("made", 0)) + args.count
        save_state(st)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("статистика:", json.dumps({"made": args.count, "total": st.get("made", 0)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
