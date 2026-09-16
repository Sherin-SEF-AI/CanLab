"""A 90 second reel, cut to the beat of the score that plays under it.

The tour explains. This does not: it is ninety seconds meant to make someone
watch the tour. Every cut lands on a beat, because the picture and the music
come off the same clock in ``music_reel``, and a cut that lands half a beat
late is the difference between a reel and a slideshow.

The moves are the ordinary ones, done deliberately:

- a punch on the first frames of a shot, so each cut has an accent;
- a slow push across the shot underneath it, so nothing is ever still;
- whip transitions with directional blur, for changes of subject;
- a digital tear on the two cuts that are about something being wrong;
- kinetic type that snaps in a beat after the cut, never with it, so the eye
  reads the picture first.

Every frame is a real screenshot of the application from the recorders. No
mockups.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import music_reel as M
import titles as T

HERE = Path(__file__).resolve().parent
W, H = 1920, 1080
FPS = 30
BEAT = M.BEAT
BAR = M.BAR

TOUR = HERE / "build-tour" / "stills"
STRESS = HERE / "build-stress" / "frames"
TIGOR = HERE / "build-tigor" / "frames"


def tour_still(key: str) -> Path:
    shots = json.loads((HERE / "build-tour" / "shots.json").read_text())
    for shot in shots:
        if shot["key"] == key:
            return TOUR / shot["still"]
    raise KeyError(key)


def scene_still(build: Path, key: str, which: int = -1) -> Path:
    """One frame of a recorded scene, or a path that does not exist.

    Returning a missing path rather than raising lets build_shots drop the
    shot: the vehicle capture is private and will not be there for everyone.
    """
    manifest = build.parent / "scenes.json"
    if not manifest.is_file():
        return build / f"{key}-missing.png"
    for scene in json.loads(manifest.read_text()):
        if scene["key"] == key:
            return build / scene["frames"][which]
    return build / f"{key}-missing.png"


# ── the cut list ─────────────────────────────────────────────────────────────

@dataclass
class Shot:
    beats: float                       # how long, in beats
    still: Path | None = None
    crop: tuple | None = None          # (x, y, w, h) in the 1920x1080 still
    line: str = ""                     # the kinetic line, "|" splits it
    accent: str = ""                   # a word or number shown large
    move: str = "push"                 # push | punch | slam
    into: str = "cut"                  # how the shot before ends: cut|whip|tear|flash
    kind: str = "shot"                 # shot | intro | outro
    extra: dict = field(default_factory=dict)


def build_shots() -> list[Shot]:
    """The cut list. Shots whose stills are missing are dropped.

    The shots from a private vehicle capture are the reason: that recording
    belongs to its owner and is not in this repository, so anyone else
    building the reel gets the same cut without them rather than an error.
    """
    shots = [
        Shot(13, kind="intro"),

        Shot(4, tour_still("open"), (150, 60, 1500, 840),
             "A bus you know nothing about", accent="", move="slam", into="flash"),
        Shot(4, tour_still("sniffer"), (231, 90, 900, 560),
             "Which bytes actually move", move="punch", into="cut"),
        Shot(4, tour_still("notch"), (231, 60, 800, 420),
             "Notch|ignore everything already moving", move="punch", into="whip",
             extra={"ring": (231, 87, 58, 20)}),

        Shot(4, tour_still("protocol"), (560, 600, 1120, 320),
             "29-bit IDs|usually mean a truck", move="push", into="whip"),
        Shot(4, tour_still("position"), (1100, 640, 640, 180),
             "This one was a boat", accent="NMEA 2000", move="punch", into="tear"),
        Shot(4, tour_still("position"), (1136, 700, 500, 120),
             "Lake Erie", accent="42.661 N", move="punch", into="cut"),

        Shot(4, tour_still("detect"), (245, 150, 1000, 560),
             "Counters found without the spec", accent="24", move="push", into="whip"),
        Shot(4, tour_still("entropy"), (234, 150, 1200, 600),
             "Field edges from bit entropy", accent="42", move="push", into="cut"),

        Shot(4, tour_still("bitgrid"), (544, 520, 1085, 420),
             "Claim the bits", move="punch", into="whip"),
        Shot(4, tour_still("dbc"), (544, 380, 1085, 260),
             "Decoded against real frames", move="push", into="cut"),

        Shot(6, tour_still("plot"), (440, 80, 1220, 980),
             "Same two bytes|read both ways", accent="0.87 m/s", move="push",
             into="flash"),
        Shot(6, tour_still("plot"), (440, 560, 1220, 500),
             "Wrong byte order", accent="184 m/s", move="punch", into="tear"),

        Shot(6, scene_still(TIGOR, "sniffer"), (231, 60, 1200, 700),
             "A real electric car", accent="460,024", move="push", into="whip"),
        Shot(5, scene_still(TIGOR, "plot"), (440, 90, 1190, 940),
             "Every candidate checked against the frames", move="push", into="cut"),
        Shot(5, scene_still(TIGOR, "findings"), (240, 180, 1440, 760),
             "What it could not establish, it says", move="push", into="whip"),

        Shot(4, scene_still(STRESS, "open"), (150, 60, 1500, 840),
             "Two logs, one capture", accent="300,430", move="slam", into="flash"),
        Shot(4, scene_still(STRESS, "plot"), (440, 80, 1220, 900),
             "Engine speed, checked two ways", accent="1762 rpm", move="push",
             into="cut"),
        Shot(4, scene_still(STRESS, "pgn"), (1180, 620, 740, 300),
             "J1939 and NMEA 2000 decoded", move="punch", into="whip"),
        Shot(4, tour_still("preview"), (240, 200, 900, 300),
             "See the frame before you send it", move="punch", into="cut"),

        Shot(4, tour_still("services"), (1330, 0, 590, 332),
             "Assistants can read it", accent="MCP", move="punch", into="whip",
             extra={"ring": (1782, 25, 64, 23)}),
        Shot(4, tour_still("adapter"), (140, 0, 700, 394),
             "Any python-can adapter", move="punch", into="cut",
             extra={"ring": (227, 25, 150, 22)}),
        Shot(5, tour_still("arm"), (1380, 0, 540, 304),
             "Nothing transmits|until you arm it", move="punch", into="tear",
             extra={"ring": (1623, 25, 85, 23)}),

        Shot(14, kind="outro"),
    ]
    return [shot for shot in shots
            if shot.kind != "shot"
            or (shot.still is not None and shot.still.is_file())]


# ── effects ──────────────────────────────────────────────────────────────────

def ease_out(p: float) -> float:
    return 1 - (1 - max(0.0, min(1.0, p))) ** 3


def framed(still: Path, crop: tuple | None, zoom: float,
           offset: tuple[float, float] = (0.0, 0.0),
           ring: tuple | None = None) -> Image.Image:
    """The still, cropped and zoomed, always filling the frame.

    `ring` marks one control before the crop, so a toolbar button reads as the
    subject of the shot. Zooming far enough for a pill to fill the frame would
    be a five times blow-up of a screenshot, which is mush; a ring says the
    same thing and keeps the surroundings that give it meaning.
    """
    base = Image.open(still).convert("RGB")
    if base.size != (W, H):
        base = base.resize((W, H), Image.LANCZOS)
    if ring is not None:
        from compose import Rect, ring as draw_ring, spotlight
        focus = Rect(*ring)
        base = draw_ring(spotlight(base, focus, 0.7), focus, 1.0)
    x, y, w, h = crop or (0, 0, W, H)
    # widen the crop to 16:9 so nothing is stretched
    if w / h < W / H:
        need = h * W / H
        x -= (need - w) / 2
        w = need
    else:
        need = w * H / W
        y -= (need - h) / 2
        h = need
    w, h = w / zoom, h / zoom
    cx, cy = x + w / 2 + offset[0], y + h / 2 + offset[1]
    left = max(0, min(cx - w / 2, W - w))
    top = max(0, min(cy - h / 2, H - h))
    box = (int(left), int(top), int(left + w), int(top + h))
    return base.crop(box).resize((W, H), Image.LANCZOS)


def directional_blur(img: Image.Image, amount: float, horizontal=True) -> Image.Image:
    """Fake motion blur by averaging shifted copies. Cheap, and it reads."""
    if amount < 1:
        return img
    steps = 6
    acc = np.zeros((H, W, 3), dtype=np.float32)
    for i in range(steps):
        shift = int((i - steps / 2) * amount / steps)
        rolled = np.roll(np.asarray(img, dtype=np.float32),
                         shift, axis=1 if horizontal else 0)
        acc += rolled
    return Image.fromarray((acc / steps).astype(np.uint8))


def tear(img: Image.Image, strength: float, rng: np.random.Generator) -> Image.Image:
    """Channel offset and sliced rows: the look of a signal breaking up."""
    if strength <= 0:
        return img
    a = np.asarray(img).copy()
    shift = int(28 * strength)
    a[:, :, 0] = np.roll(a[:, :, 0], shift, axis=1)
    a[:, :, 2] = np.roll(a[:, :, 2], -shift, axis=1)
    for _ in range(int(9 * strength)):
        y = rng.integers(0, H - 24)
        height = int(rng.integers(6, 26))
        a[y:y + height] = np.roll(a[y:y + height], int(rng.integers(-70, 70)), axis=1)
    return Image.fromarray(a)


def flash(img: Image.Image, strength: float, colour=(255, 255, 255)) -> Image.Image:
    if strength <= 0:
        return img
    return Image.blend(img, Image.new("RGB", img.size, colour), min(0.85, strength))


def vignette(img: Image.Image) -> Image.Image:
    mask = Image.radial_gradient("L").resize((W, H))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(dark, img, mask.point(lambda v: int(v * 0.55)))


# ── type ─────────────────────────────────────────────────────────────────────

def kinetic_line(img: Image.Image, text: str, accent: str, t: float,
                 length: float) -> Image.Image:
    """Words snapping in one after another, a beat after the cut."""
    if not text and not accent:
        return img
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    start = BEAT * 0.5
    # The accent sits in its own band above the line. They used to share one,
    # so "0.87 m/s" landed on top of the words explaining it.
    accent_y = H - 470
    y = H - 250

    if accent:
        p = ease_out((t - start) / 0.35)
        if p > 0:
            font = T.ui(158, 800)
            bbox = d.textbbox((0, 0), accent, font=font)
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (W - width) / 2
            # a slab behind it, growing from the left
            d.rectangle((x - 34, accent_y - 26,
                         x - 34 + (width + 68) * p, accent_y + height + 26),
                        fill=(10, 11, 12, int(215 * p)))
            d.text((x, accent_y - bbox[1]), accent, font=font,
                   fill=(*T.GREEN, int(255 * p)))

    lines = [part.strip() for part in text.split("|") if part.strip()]
    for i, line in enumerate(lines):
        words = line.split()
        font = T.ui(62, 700)
        widths = [d.textlength(w + " ", font=font) for w in words]
        total = sum(widths)
        x = (W - total) / 2
        row_y = y + i * 78
        for w, width in zip(words, widths):
            appear = start + 0.09 * (i * len(words) + words.index(w))
            p = ease_out((t - appear) / 0.22)
            if p <= 0:
                x += width
                continue
            d.text((x, row_y + (1 - p) * 26), w, font=font,
                   fill=(*T.TEXT, int(255 * p)), stroke_width=6,
                   stroke_fill=(0, 0, 0, int(220 * p)))
            x += width

    # a thin progress line along the bottom of the shot
    done = max(0.0, min(1.0, t / length))
    d.rectangle((0, H - 8, W * done, H - 4), fill=(*T.GREEN, 200))
    out = img.convert("RGBA")
    out.alpha_composite(layer)
    return out.convert("RGB")


# ── one frame ────────────────────────────────────────────────────────────────

def render_shot_frame(shot: Shot, i: int, total: int,
                      rng: np.random.Generator) -> Image.Image:
    t = i / FPS
    length = total / FPS
    p = i / max(1, total - 1)

    if shot.move == "punch":
        zoom = 1.0 + 0.07 * (1 - ease_out(t / 0.28)) + 0.03 * p
    elif shot.move == "slam":
        zoom = 1.0 + 0.30 * (1 - ease_out(t / 0.5)) + 0.02 * p
    else:                                    # push
        zoom = 1.02 + 0.06 * p

    drift = (math.sin(p * math.pi) * 18, -math.cos(p * math.pi) * 10)
    img = framed(shot.still, shot.crop, zoom, drift, shot.extra.get("ring"))

    if shot.move == "slam" and t < 0.25:
        img = directional_blur(img, 40 * (1 - t / 0.25))
    img = vignette(img)
    img = kinetic_line(img, shot.line, shot.accent, t, length)

    # the entry effect belongs to the shot that is arriving
    if shot.into == "flash" and t < 0.2:
        img = flash(img, (1 - t / 0.2) * 0.8)
    if shot.into == "tear" and t < 0.3:
        img = tear(img, 1 - t / 0.3, rng)
    if shot.into == "whip" and t < 0.16:
        img = directional_blur(img, 90 * (1 - t / 0.16))
    return img


def render_intro_frame(i: int, total: int) -> Image.Image:
    """Logo draws itself, name lands on the drop."""
    t = i / FPS
    img = T.background(t, strength=T.ease_out(T.phase(t, 0.2, 1.5))).convert("RGBA")
    draw_f = T.ease_in_out(T.phase(t, 0.3, 2.4))
    low = T.ease_in_out(T.phase(t, 1.6, 3.2))
    high = T.ease_in_out(T.phase(t, 1.8, 3.4))
    pulse = T.phase(t, 3.4, 4.6) if 3.4 <= t <= 4.6 else None
    size = int(420 - 120 * T.ease_in_out(T.phase(t, 5.6, 7.2)))
    cy = H / 2 - 120 * T.ease_in_out(T.phase(t, 5.6, 7.2))
    mark = T.logo(size, frame=draw_f, low=low, high=high, pulse=pulse,
                  glow=0.4 + 0.6 * T.phase(t, 3.0, 4.8))
    img.alpha_composite(mark, (int(W / 2 - size / 2), int(cy - size / 2)))

    d = ImageDraw.Draw(img)
    word = T.back_out(T.phase(t, 6.0, 6.6))
    if word > 0:
        font = T.ui(170, 800)
        text = "CanLab"
        bbox = d.textbbox((0, 0), text, font=font)
        x = (W - (bbox[2] - bbox[0])) / 2
        d.text((x, cy + size / 2 + 40 + (1 - word) * 40), "Can", font=font,
               fill=(*T.TEXT, int(255 * min(1, word))))
        d.text((x + d.textlength("Can", font=font),
                cy + size / 2 + 40 + (1 - word) * 40), "Lab", font=font,
               fill=(*T.GREEN, int(255 * min(1, word))))
    tag = T.ease_out(T.phase(t, 6.9, 7.6))
    if tag > 0:
        T.draw_centered(d, W / 2, cy + size / 2 + 230, "Reverse-engineer a CAN bus",
                        T.ui(54, 400), (*T.DIM, int(255 * tag)))
    out = img.convert("RGB")
    out = T.fade_to(out, amount=1 - T.ease_out(T.phase(t, 0.0, 0.6)))
    # the last beat before the drop washes out into the first shot
    return flash(out, T.phase(t, total / FPS - 0.25, total / FPS) * 0.9)


def render_outro_frame(i: int, total: int) -> Image.Image:
    t = i / FPS
    length = total / FPS
    img = T.background(t + 30).convert("RGBA")
    size = 240
    img.alpha_composite(T.logo(size, frame=T.ease_in_out(T.phase(t, 0.1, 0.9)),
                               low=T.ease_in_out(T.phase(t, 0.4, 1.2)),
                               high=T.ease_in_out(T.phase(t, 0.5, 1.3)),
                               glow=0.6 * T.phase(t, 1.0, 1.8)),
                        (W // 2 - size // 2, 190))
    d = ImageDraw.Draw(img)
    word = T.back_out(T.phase(t, 0.7, 1.3))
    T.draw_centered(d, W / 2, 470 + (1 - word) * 30, "CanLab", T.ui(150, 800),
                    (*T.TEXT, int(255 * min(1, T.phase(t, 0.7, 1.1)))))
    sub = T.ease_out(T.phase(t, 1.2, 1.8))
    T.draw_centered(d, W / 2, 640, "Open source. Real captures. No hardware needed.",
                    T.ui(50, 400), (*T.DIM, int(255 * sub)))
    for i_line, text in enumerate(("github.com/Sherin-SEF-AI/CanLab",
                                   "sherin-sef-ai.github.io/CanLab")):
        p = T.back_out(T.phase(t, 1.9 + 0.25 * i_line, 2.5 + 0.25 * i_line))
        a = T.phase(t, 1.9 + 0.25 * i_line, 2.3 + 0.25 * i_line)
        font = T.ui(52, 600)
        width = d.textlength(text, font=font)
        y = 760 + i_line * 104 + (1 - p) * 40
        d.rounded_rectangle((W / 2 - width / 2 - 36, y, W / 2 + width / 2 + 36, y + 80),
                            40, fill=(18, 20, 22, int(230 * a)),
                            outline=(*T.GREEN, int(200 * a)), width=3)
        T.draw_centered(d, W / 2, y + 12, text, font, (*T.TEXT, int(255 * a)))
    out = img.convert("RGB")
    return T.fade_to(out, amount=T.ease_in_out(T.phase(t, length - 1.0, length)))
