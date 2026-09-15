#!/usr/bin/env python3
"""Build a vertical YouTube Short from the tour's real stills.

One finding, told in under a minute: the capture turns out to be a boat, the
decoded fields agree, the detector finds the protocol's counter unaided, and
reading two bytes in the wrong order gives a wind speed nobody could believe.

1080x1920 at 30 fps. Most Shorts are watched muted, so the narration is shown
as large captions that highlight word by word. The timing comes from the
speech engine's own word boundaries rather than being estimated from
character counts, and there is only ever one block of text on screen.

Every frame shows the real application, from ``record_tour.py``'s stills.

    python docs/demo/record_tour.py     # if build-tour/stills is missing
    python docs/demo/build_short.py
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import titles as T  # noqa: E402
from compose import Rect, ring, spotlight  # noqa: E402

STILLS = HERE / "build-tour" / "stills"
BUILD = HERE / "build-short"
FRAMES = BUILD / "frames"
AUDIO = BUILD / "audio"
VIDEO = BUILD / "canlab-short.mp4"

W, H = 1080, 1920
FPS = 30
SR = 48_000
VOICE = "en-GB-RyanNeural"
RATE = "+0%"

#: Where the screenshot card sits. The right-hand column and the bottom of a
#: Short are covered by YouTube's own buttons and title, so nothing that has
#: to be read goes there.
CARD_TOP = 300
CARD_W = 1040
CAPTION_Y = 1330
CAPTION_MAX_W = 860

SCENES = [
    # Regions are tight on purpose: at phone size a whole panel is unreadable,
    # so each scene shows only the cells being talked about, at 2 to 3x.
    {"key": "hook", "still": "0005.png", "region": (455, 113, 797, 317),
     "ring": (459, 154, 334, 100), "lead": 0.12,
     "text": "This CAN bus recording turned out to be a boat."},
    {"key": "protocol", "still": "0003.png", "region": (667, 662, 990, 786),
     "ring": (913, 684, 74, 100),
     "text": ("Twenty-nine bit IDs usually mean a truck on J1939. But the data "
              "page and PGN numbers say NMEA 2000, the marine standard.")},
    {"key": "position", "still": "0004.png", "region": (1136, 722, 1486, 790),
     "ring": (1140, 764, 330, 20),
     "text": "Decode the fields, and the position lands on Lake Erie."},
    {"key": "detect", "still": "0008.png", "region": (718, 158, 1174, 382),
     "ring": (948, 182, 222, 78),
     "text": ("The counter detector was never told any of that. It still found "
              "the sequence byte, wrapping at two hundred and fifty one.")},
    {"key": "plot", "still": "0012.png", "region": (440, 80, 1660, 1060),
     "ring": None, "labels": True,
     "text": ("Now the real test. The same two bytes, read both ways. One says "
              "the wind was under one metre per second. The other says over a "
              "hundred and eighty. Only one of those is weather.")},
    {"key": "end", "still": None, "min": 4.8,
     "text": "That's CanLab. It's free and open source, and the full tour is on my channel."},
]

LEAD, TAIL = 0.22, 0.38
XFADE_HALF = 4


# ── narration with word timings ──────────────────────────────────────────────

async def _voice(scene: dict) -> None:
    import edge_tts
    AUDIO.mkdir(parents=True, exist_ok=True)
    mp3 = AUDIO / f"{scene['key']}.mp3"
    words = AUDIO / f"{scene['key']}.json"
    digest = hashlib.sha256(f"{VOICE}|{RATE}|{scene['text']}".encode()).hexdigest()
    stamp = AUDIO / f"{scene['key']}.sha"
    scene["audio"], scene["words_file"] = mp3, words
    if mp3.exists() and words.exists() and stamp.exists() and stamp.read_text() == digest:
        return
    comm = edge_tts.Communicate(scene["text"], VOICE, rate=RATE, boundary="WordBoundary")
    marks = []
    with mp3.open("wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                marks.append({"t": chunk["offset"] / 1e7,
                              "d": chunk["duration"] / 1e7,
                              "w": chunk["text"]})
    words.write_text(json.dumps(marks))
    stamp.write_text(digest)
    print(f"  voiced {scene['key']}", flush=True)


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


def caption_groups(text: str, marks: list[dict], max_words: int = 3) -> list[list[dict]]:
    """Group spoken words into short caption lines, breaking at punctuation.

    Boundary events carry the word without punctuation, so each one is found
    in the script in order to see what follows it.
    """
    lower = text.lower()
    cursor = 0
    groups, current = [], []
    for m in marks:
        word = m["w"]
        at = lower.find(word.lower(), cursor)
        end = at + len(word) if at >= 0 else cursor
        cursor = max(cursor, end)
        after = text[end:end + 1] if at >= 0 else ""
        current.append(m)
        if (after and after in ".,!?;:") or len(current) >= max_words:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


# ── drawing ──────────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def _prepared(still: str, ring_box: tuple | None):
    base = Image.open(STILLS / still).convert("RGB")
    if ring_box:
        focus = Rect(*ring_box)
        base = ring(spotlight(base, focus, 0.85), focus, 1.0)
    return base


@lru_cache(maxsize=8)
def _backdrop(still: str | None) -> Image.Image:
    if still is None:
        return _title_backdrop()
    src = Image.open(STILLS / still).convert("RGB")
    scale = H / src.height
    cover = src.resize((int(src.width * scale), H), Image.LANCZOS)
    left = (cover.width - W) // 2
    cover = cover.crop((left, 0, left + W, H)).filter(ImageFilter.GaussianBlur(28))
    return Image.blend(cover, Image.new("RGB", (W, H), (8, 9, 10)), 0.72)


@lru_cache(maxsize=1)
def _title_backdrop() -> Image.Image:
    wide = T.background(1.0)
    left = (wide.width - W * wide.height // H) // 2
    return wide.crop((left, 0, left + W * wide.height // H, wide.height)).resize((W, H))


@lru_cache(maxsize=1)
def _card_mask(w: int, h: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), 30, fill=255)
    return mask


def _card(scene: dict, p: float) -> Image.Image:
    x0, y0, x1, y1 = scene["region"]
    rw, rh = x1 - x0, y1 - y0
    # a slow push in: 10% wider at the start of the scene, the region at the end
    grow = 0.10 * (1 - T.ease_in_out(p))
    cx, cy = x0 + rw / 2, y0 + rh / 2
    w, h = rw * (1 + grow), rh * (1 + grow)
    base = _prepared(scene["still"], scene.get("ring"))
    # Keep the box inside the image by moving it, not by clipping it: a
    # clipped box has the wrong aspect and the resize squashes the picture.
    left = min(max(0, cx - w / 2), base.width - w)
    top = min(max(0, cy - h / 2), base.height - h)
    box = (left, top, left + w, top + h)
    ch = int(CARD_W * rh / rw)
    return base.crop(tuple(int(v) for v in box)).resize((CARD_W, ch), Image.LANCZOS)


def _header(img: Image.Image) -> None:
    mark = T.logo(78)
    img.alpha_composite(mark, (W // 2 - 150, 132))
    d = ImageDraw.Draw(img)
    f = T.ui(58, 700)
    d.text((W // 2 - 58, 138), "Can", font=f, fill=T.TEXT)
    d.text((W // 2 - 58 + d.textlength("Can", font=f), 138), "Lab", font=f, fill=T.GREEN)


def _captions(img: Image.Image, groups: list, t: float) -> None:
    """The line being spoken, with the current word lit."""
    active = None
    for g in groups:
        if g[0]["t"] - 0.05 <= t <= g[-1]["t"] + g[-1]["d"] + 0.25:
            active = g
    if active is None:
        return
    d = ImageDraw.Draw(img)
    font = T.ui(84, 800)
    words = [m["w"] for m in active]
    widths = [d.textlength(w, font=font) for w in words]
    space = d.textlength(" ", font=font)
    lines, line, lw = [], [], 0.0
    for i, w in enumerate(words):
        add = widths[i] + (space if line else 0)
        if line and lw + add > CAPTION_MAX_W:
            lines.append(line)
            line, lw = [], 0.0
            add = widths[i]
        line.append(i)
        lw += add
    lines.append(line)
    pop = T.back_out(T.phase(t, active[0]["t"] - 0.05, active[0]["t"] + 0.12))
    y = CAPTION_Y - (len(lines) - 1) * 50 + (1 - pop) * 18
    for line in lines:
        total = sum(widths[i] for i in line) + space * (len(line) - 1)
        x = W / 2 - total / 2
        for i in line:
            m = active[i]
            lit = m["t"] - 0.02 <= t
            colour = T.GREEN if (lit and t <= m["t"] + m["d"] + 0.08) else T.TEXT
            d.text((x, y), m["w"], font=font, fill=colour,
                   stroke_width=7, stroke_fill=(0, 0, 0))
            x += widths[i] + space
        y += 100


def _plot_labels(img: Image.Image, top: int, card_h: int, t: float, total: float) -> None:
    """Name the two traces once the voice reaches them."""
    d = ImageDraw.Draw(img)
    font = T.ui(38, 700)
    for i, (label, colour, frac) in enumerate((
            ("little-endian: 0.72 to 0.87 m/s", T.GREEN, 0.06),
            ("big-endian: 184 to 223 m/s", (255, 179, 0), 0.52))):
        p = T.ease_out(T.phase(t, total * (0.28 + 0.17 * i), total * (0.28 + 0.17 * i) + 0.45))
        if p <= 0:
            continue
        tw = d.textlength(label, font=font)
        x = 60 + (1 - p) * -30
        y = top + card_h * frac
        d.rounded_rectangle((x, y, x + tw + 40, y + 62), 16,
                            fill=(10, 11, 12, int(235 * p)),
                            outline=(*colour, int(255 * p)), width=3)
        d.text((x + 20, y + 9), label, font=font, fill=(*colour, int(255 * p)))


def _end(t: float, total: float) -> Image.Image:
    img = _title_backdrop().convert("RGBA")
    draw = T.ease_in_out(T.phase(t, 0.1, 1.0))
    lines = T.ease_in_out(T.phase(t, 0.4, 1.2))
    size = 300
    img.alpha_composite(T.logo(size, frame=draw, low=lines, high=lines,
                               glow=0.6 * T.phase(t, 1.0, 1.8)),
                        (W // 2 - size // 2, 430))
    d = ImageDraw.Draw(img)
    word = T.ease_out(T.phase(t, 0.8, 1.4))
    T.draw_centered(d, W / 2, 790 + (1 - word) * 30, "CanLab", T.ui(150, 800),
                    T.with_alpha(T.TEXT, word))
    sub = T.ease_out(T.phase(t, 1.2, 1.8))
    T.draw_centered(d, W / 2, 985, "Reverse-engineer a CAN bus", T.ui(52, 400),
                    T.with_alpha(T.DIM, sub))
    for i, (text, colour) in enumerate((("Free and open source", T.GREEN),
                                        ("Full tour on the channel", T.TEXT))):
        p = T.back_out(T.phase(t, 1.7 + i * 0.3, 2.3 + i * 0.3))
        a = T.phase(t, 1.7 + i * 0.3, 2.0 + i * 0.3)
        f = T.ui(54, 700)
        tw = d.textlength(text, font=f)
        y = 1120 + i * 120 + (1 - p) * 40
        d.rounded_rectangle((W / 2 - tw / 2 - 40, y, W / 2 + tw / 2 + 40, y + 92), 46,
                            fill=(18, 20, 22, int(235 * a)),
                            outline=(*colour, int(220 * a)), width=3)
        T.draw_centered(d, W / 2, y + 16, text, f, T.with_alpha(colour, a))
    out = img.convert("RGB")
    return T.fade_to(out, amount=T.ease_in_out(T.phase(t, total - 0.6, total)))


def _frame(job: tuple) -> int:
    index, scene, local, total, groups = job
    t = local / FPS
    if scene["still"] is None:
        _end(t, total).save(FRAMES / f"{index:05d}.jpg", quality=93)
        return 1
    img = _backdrop(scene["still"]).convert("RGBA")
    card = _card(scene, local / max(1, total * FPS - 1))
    ch = card.height
    top = 290 + max(0, (900 - ch) // 2)
    left = (W - CARD_W) // 2
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((left + 8, top + 22, left + CARD_W + 8, top + ch + 22),
                                             30, fill=(0, 0, 0, 170))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(24)))
    img.paste(card, (left, top), _card_mask(CARD_W, ch))
    ImageDraw.Draw(img).rounded_rectangle((left, top, left + CARD_W, top + ch), 30,
                                          outline=(*T.GREEN, 150), width=3)
    _header(img)
    if scene.get("labels"):
        _plot_labels(img, top, ch, t, total)
    _captions(img, groups, t - scene["voice_at"])
    img.convert("RGB").save(FRAMES / f"{index:05d}.jpg", quality=93)
    return 1


# ── assembly ─────────────────────────────────────────────────────────────────

def main() -> int:
    if not STILLS.is_dir():
        raise SystemExit("run docs/demo/record_tour.py first")
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required")

    print("narrating…", flush=True)
    async def voice_all():
        for s in SCENES:
            await _voice(s)
    asyncio.run(voice_all())

    start = 0
    for s in SCENES:
        s["speech"] = duration(s["audio"])
        s["voice_at"] = s.get("lead", LEAD)
        s["duration"] = max(s.get("min", 0), s["voice_at"] + s["speech"] + TAIL)
        s["frames"] = int(round(s["duration"] * FPS))
        s["first"] = start
        s["marks"] = json.loads(Path(s["words_file"]).read_text())
        start += s["frames"]
    total_frames = start
    seconds = total_frames / FPS
    print(f"  {seconds:.1f}s", flush=True)
    if seconds > 60:
        print("  warning: over 60 seconds", flush=True)

    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    jobs = []
    for s in SCENES:
        plain = {k: s[k] for k in ("key", "still", "voice_at") if k in s}
        plain.update({k: s[k] for k in ("region", "ring", "labels") if k in s})
        groups = [] if s["key"] == "end" else caption_groups(s["text"], s["marks"])
        jobs += [(s["first"] + i, plain, i, s["duration"], groups) for i in range(s["frames"])]
    print(f"rendering {total_frames} frames…", flush=True)
    with ProcessPoolExecutor() as pool:
        sum(pool.map(_frame, jobs, chunksize=6))

    # dissolves at the cuts, in place so the timeline does not move
    for s in SCENES[1:]:
        cut = s["first"]
        paths = [FRAMES / f"{cut + k:05d}.jpg" for k in range(-XFADE_HALF, XFADE_HALF)]
        frames = [Image.open(p).convert("RGB") for p in paths]
        before, after = frames[XFADE_HALF - 1], frames[XFADE_HALF]
        for k, path in enumerate(paths):
            a = frames[k] if k < XFADE_HALF else before
            b = after if k < XFADE_HALF else frames[k]
            mixed = Image.blend(a, b, (k + 0.5) / (2 * XFADE_HALF))
            os.unlink(path)
            mixed.save(path, quality=93)

    print("mixing…", flush=True)
    import music
    n = int(seconds * SR)
    voice = np.zeros(n, dtype=np.float32)
    for s in SCENES:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(s["audio"]), "-f", "f32le",
                              "-ac", "1", "-ar", str(SR), "-"], capture_output=True, check=True).stdout
        clip = np.frombuffer(raw, dtype=np.float32)
        at = int((s["first"] / FPS + s["voice_at"]) * SR)
        voice[at:at + len(clip)] += clip[:max(0, n - at)]
    voice *= 0.92 / (float(np.abs(voice).max()) or 1.0)
    end_start = SCENES[-1]["first"] / FPS
    keys = [(0, 0.10), (end_start - 0.3, 0.10), (end_start + 0.8, 0.30),
            (seconds - 0.7, 0.25), (seconds, 0.0)]
    bed = music.bed(seconds, seed=11) * music.envelope(seconds, keys, ramp=0.8)[:, None]
    stereo = bed[:n] + voice[:, None]
    stereo *= min(1.0, 0.98 / float(np.abs(stereo).max()))
    wav = BUILD / "mix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                    "-i", "-", str(wav)], input=stereo.astype(np.float32).tobytes(), check=True)

    print("encoding…", flush=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS),
                    "-i", str(FRAMES / "%05d.jpg"), "-i", str(wav),
                    "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "slow",
                    "-crf", "20", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", str(VIDEO)], check=True)
    print(f"\n{VIDEO}  {seconds:.1f}s  {VIDEO.stat().st_size / 1e6:.1f} MB  {W}x{H}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
