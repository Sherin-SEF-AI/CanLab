"""Title cards and motion graphics for the tour: intro, outro, chapter chips.

Everything is drawn from vectors at render time rather than from a video
template. The logo is the same geometry as ``docs/assets/logo.svg`` (a rounded
frame and the CAN-H and CAN-L traces of a differential pair), so it can draw
itself stroke by stroke instead of fading in as a flat picture.

Frames are rendered at 2x and downsampled, which is what keeps the strokes and
the type from stairstepping.

The scrolling hex in the background is real traffic from the bundled sample
capture, not random digits: a demo about reading a bus should not decorate
itself with a bus that does not exist.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
FPS = 30

REPO = Path(__file__).resolve().parents[2]
UI_FONT = "/usr/share/fonts/truetype/ubuntu/UbuntuSans[wdth,wght].ttf"
MONO_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

BG = (11, 12, 13)
BG_CENTRE = (24, 27, 29)
GREY = (187, 188, 190)            # the logo's CAN-L and frame
GREEN = (59, 205, 117)            # the logo's CAN-H
TEXT = (236, 237, 238)
DIM = (140, 144, 148)
PANEL = (20, 22, 24)

VERSION = "v2.0.0"


# ── easing ───────────────────────────────────────────────────────────────────

def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def phase(t: float, start: float, end: float) -> float:
    """0 before `start`, 1 after `end`, linear in between."""
    return clamp((t - start) / max(1e-6, end - start))


def ease_out(p: float) -> float:
    return 1 - (1 - p) ** 3


def ease_in_out(p: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * clamp(p))


def back_out(p: float) -> float:
    """Overshoots slightly and settles, for things that should feel placed."""
    c = 1.4
    p = clamp(p) - 1
    return 1 + (c + 1) * p ** 3 + c * p ** 2


# ── fonts ────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def ui(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    try:
        font = ImageFont.truetype(UI_FONT, size)
        font.set_variation_by_axes([100, weight])
        return font
    except OSError:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if weight >= 600 else
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)


@lru_cache(maxsize=16)
def mono(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(MONO_BOLD if bold else MONO_FONT, size)


# ── the logo, as geometry ────────────────────────────────────────────────────

STROKE = 28.1
_FRAME = (110.9, 124.0, 779.2, 752.1, 127.1)      # x, y, w, h, corner radius
CAN_L = [(153.1, 538.5), (413.5, 538.5), (413.5, 655.7),
         (587.0, 655.7), (587.0, 538.5), (847.9, 538.5)]
CAN_H = [(153.1, 455.7), (413.5, 455.7), (413.5, 334.9),
         (587.0, 334.9), (587.0, 455.7), (847.9, 455.7)]


def _frame_points() -> list[tuple[float, float]]:
    """The rounded frame as one closed polyline, starting top centre, clockwise."""
    x, y, w, h, r = _FRAME
    pts = [(x + w / 2, y)]
    corners = [((x + w - r, y + r), -90), ((x + w - r, y + h - r), 0),
               ((x + r, y + h - r), 90), ((x + r, y + r), 180)]
    for (cx, cy), start in corners:
        for k in range(25):
            a = math.radians(start + 90 * k / 24)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    pts.append((x + w / 2, y))
    return pts


FRAME = _frame_points()


def partial(points: list, frac: float) -> list:
    """The first `frac` of a polyline, by length."""
    if frac <= 0:
        return []
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    target = sum(lengths) * clamp(frac)
    out = [points[0]]
    run = 0.0
    for (a, b), seg in zip(zip(points, points[1:]), lengths):
        if run + seg >= target:
            k = (target - run) / seg if seg else 0
            out.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
            return out
        out.append(b)
        run += seg
    return out


def point_at(points: list, frac: float) -> tuple[float, float]:
    return partial(points, max(1e-4, frac))[-1]


def _h_gradient(width: int, left: float, right: float) -> Image.Image:
    """The CAN-H stroke colour: grey at the ends, green through the middle."""
    row = Image.new("RGB", (width, 1))
    px = row.load()
    stops = [(0.0, GREY), (0.12, GREY), (0.38, GREEN), (0.62, GREEN),
             (0.88, GREY), (1.0, GREY)]
    for x in range(width):
        f = clamp((x - left) / max(1, right - left))
        for (a, ca), (b, cb) in zip(stops, stops[1:]):
            if a <= f <= b:
                k = (f - a) / max(1e-6, b - a)
                px[x, 0] = tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
                break
    return row


def logo(size: int, *, frame: float = 1.0, low: float = 1.0, high: float = 1.0,
         pulse: float | None = None, glow: float = 0.0) -> Image.Image:
    """The mark at `size` px, each stroke drawn to the given fraction."""
    ss = 2
    px = size * ss
    s = px / 1000.0
    width = max(1, int(round(STROKE * s)))

    def scaled(points):
        return [(x * s, y * s) for x, y in points]

    grey = Image.new("L", (px, px), 0)
    d = ImageDraw.Draw(grey)
    for pts, frac in ((FRAME, frame), (CAN_L, low)):
        part = scaled(partial(pts, frac))
        if len(part) >= 2:
            d.line(part, fill=255, width=width, joint="curve")

    hmask = Image.new("L", (px, px), 0)
    part = scaled(partial(CAN_H, high))
    if len(part) >= 2:
        ImageDraw.Draw(hmask).line(part, fill=255, width=width, joint="curve")

    out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    out.paste(Image.new("RGB", (px, px), GREY), (0, 0), grey)
    gradient = _h_gradient(px, 153.1 * s, 847.9 * s).resize((px, px))
    out.paste(gradient, (0, 0), hmask)

    if glow > 0 or pulse is not None:
        halo = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        if glow > 0:
            g = Image.new("RGBA", (px, px), (*GREEN, 0))
            g.putalpha(hmask.point(lambda v: int(v * 0.8 * glow)))
            halo = Image.alpha_composite(halo, g)
        if pulse is not None and 0 <= pulse <= 1:
            cx, cy = point_at(scaled(CAN_H), pulse)
            dot = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            dd = ImageDraw.Draw(dot)
            for r, a in ((42 * s, 70), (26 * s, 150), (13 * s, 255)):
                dd.ellipse((cx - r, cy - r, cx + r, cy + r),
                           fill=(210, 255, 225, a))
            halo = Image.alpha_composite(halo, dot)
        halo = halo.filter(ImageFilter.GaussianBlur(16 * s * 2))
        out = Image.alpha_composite(halo, out)
        if pulse is not None and 0 <= pulse <= 1:
            cx, cy = point_at(scaled(CAN_H), pulse)
            core = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            r = 9 * s
            ImageDraw.Draw(core).ellipse((cx - r, cy - r, cx + r, cy + r),
                                         fill=(240, 255, 245, 255))
            out = Image.alpha_composite(out, core)

    return out.resize((size, size), Image.LANCZOS)


# ── background ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _base() -> Image.Image:
    """Radial falloff from a slightly lifted centre to near black."""
    small = Image.new("RGB", (192, 108))
    px = small.load()
    for y in range(108):
        for x in range(192):
            d = math.hypot((x - 96) / 96, (y - 54) / 54 * 0.8)
            k = clamp(1 - d) ** 1.6
            px[x, y] = tuple(int(BG[i] + (BG_CENTRE[i] - BG[i]) * k) for i in range(3))
    return small.resize((W, H), Image.BICUBIC)


@lru_cache(maxsize=1)
def _dots() -> Image.Image:
    """A dot grid, two cells taller than the frame so it can drift."""
    step = 48
    tile = Image.new("L", (W, H + step * 2), 0)
    d = ImageDraw.Draw(tile)
    for y in range(0, H + step * 2, step):
        for x in range(step // 2, W, step):
            d.ellipse((x - 1.4, y - 1.4, x + 1.4, y + 1.4), fill=255)
    return tile


@lru_cache(maxsize=1)
def _hex_column() -> Image.Image:
    """Real frames from the bundled sample, as a tall strip of hex."""
    lines = []
    sample = REPO / "canlab" / "sample_data" / "sample_kona_drive.csv"
    try:
        with sample.open() as fh:
            next(fh)
            for i, row in enumerate(fh):
                if i % 7:
                    continue
                cols = row.strip().split(",")
                lines.append(f"{cols[1].lstrip('0') or '0':>3}  {' '.join(cols[6:14])}")
                if len(lines) >= 90:
                    break
    except OSError:
        lines = ["0A6  06 E0 06 E0 06 E0 06 E0"] * 90
    font = mono(19)
    height = 30 * len(lines)
    strip = Image.new("L", (380, height), 0)
    d = ImageDraw.Draw(strip)
    for i, line in enumerate(lines):
        d.text((0, i * 30), line, font=font, fill=255)
    return strip


def background(t: float, *, strength: float = 1.0) -> Image.Image:
    img = _base().copy()
    if strength <= 0:
        return img
    step = 48
    drift = int((t * 9) % step)
    dots = _dots().crop((0, step * 2 - drift, W, step * 2 - drift + H))
    img.paste(Image.new("RGB", (W, H), (44, 48, 51)), (0, 0),
              dots.point(lambda v: int(v * 0.55 * strength)))

    strip = _hex_column()
    offset = int(t * 22) % strip.height
    tall = Image.new("L", (strip.width, strip.height * 2))
    tall.paste(strip, (0, 0))
    tall.paste(strip, (0, strip.height))
    col = tall.crop((0, offset, strip.width, offset + H))
    fade = Image.linear_gradient("L").resize((1, H))
    fade = Image.eval(fade, lambda v: int(255 * math.sin(math.pi * v / 255)))
    fade = fade.resize((strip.width, H))
    from PIL import ImageChops
    col = ImageChops.multiply(col, fade).point(lambda v: int(v * 0.10 * strength))
    ink = Image.new("RGB", (strip.width, H), GREEN)
    img.paste(ink, (70, 0), col)
    img.paste(ink, (W - strip.width - 20, 0),
              col.transpose(Image.FLIP_TOP_BOTTOM))
    return img


def fade_to(img: Image.Image, colour=(0, 0, 0), amount: float = 0.0) -> Image.Image:
    if amount <= 0:
        return img
    return Image.blend(img, Image.new("RGB", img.size, colour), clamp(amount))


# ── type helpers ─────────────────────────────────────────────────────────────

def text_layer(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def draw_centered(d, cx, y, text, font, fill):
    box = d.textbbox((0, 0), text, font=font)
    d.text((cx - (box[2] - box[0]) / 2 - box[0], y), text, font=font, fill=fill)


def with_alpha(colour, a: float):
    return (*colour, int(255 * clamp(a)))


# ── intro ────────────────────────────────────────────────────────────────────

#: When the voice starts in the intro, so the logo has finished drawing first.
INTRO_VOICE_AT = 3.3


def intro_frame(t: float, total: float) -> Image.Image:
    img = background(t, strength=ease_out(phase(t, 0.0, 1.2))).convert("RGBA")

    # 1. the mark draws itself, then a pulse runs along CAN-H
    draw_f = ease_in_out(phase(t, 0.15, 1.35))
    low_f = ease_in_out(phase(t, 0.85, 1.9))
    high_f = ease_in_out(phase(t, 0.95, 2.0))
    pulse = phase(t, 2.0, 2.9) if 2.0 <= t <= 2.9 else None
    glow = 0.35 + 0.65 * math.sin(math.pi * phase(t, 1.9, 3.1))

    # 2. it settles to the left and the wordmark arrives beside it
    move = ease_in_out(phase(t, 2.8, 3.7))
    size = int(380 - 150 * move)
    cx = W / 2 - 340 * move
    cy = H / 2 - 40 * move
    mark = logo(size, frame=draw_f, low=low_f, high=high_f, pulse=pulse,
                glow=glow if t > 1.0 else 0)
    img.alpha_composite(mark, (int(cx - size / 2), int(cy - size / 2)))

    layer, d = text_layer((W, H))
    word = "CanLab"
    font = ui(150, 700)
    x = cx + size / 2 + 50
    base_y = cy - 110
    for i, ch in enumerate(word):
        p = back_out(phase(t, 3.15 + i * 0.06, 3.75 + i * 0.06))
        a = phase(t, 3.15 + i * 0.06, 3.5 + i * 0.06)
        colour = GREEN if ch in "Lab" and i >= 3 else TEXT
        d.text((x, base_y + (1 - p) * 60), ch, font=font, fill=with_alpha(colour, a))
        x += d.textlength(ch, font=font)

    tag = ease_out(phase(t, 3.9, 4.6))
    d.text((cx + size / 2 + 56, cy + 62 + (1 - tag) * 24),
           "Reverse-engineer a CAN bus.", font=ui(46, 400),
           fill=with_alpha(DIM, tag))

    chip = ease_out(phase(t, 4.5, 5.1))
    if chip > 0:
        cx0 = cx + size / 2 + 58
        cy0 = cy + 140
        label = f"GUIDED TOUR   {VERSION}"
        f = mono(24, bold=True)
        tw = d.textlength(label, font=f)
        reveal = tw + 44
        d.rounded_rectangle((cx0, cy0, cx0 + reveal * chip, cy0 + 48), 24,
                            fill=(*PANEL, int(230 * chip)),
                            outline=(*GREEN, int(200 * chip)), width=2)
        if chip > 0.6:
            d.text((cx0 + 22, cy0 + 10), label, font=f,
                   fill=with_alpha(GREEN, phase(chip, 0.6, 1.0)))
    img.alpha_composite(layer)

    out = img.convert("RGB")
    return fade_to(out, amount=1 - ease_out(phase(t, 0.0, 0.5)))


# ── outro ────────────────────────────────────────────────────────────────────

OUTRO_VOICE_AT = 1.0

LINKS = [
    ("SOURCE AND RELEASES", "github.com/Sherin-SEF-AI/CanLab"),
    ("DOCUMENTATION", "sherin-sef-ai.github.io/CanLab"),
]
FOOTER = "MIT licence    ·    Linux x86_64 build    ·    540 tests"


def outro_frame(t: float, total: float) -> Image.Image:
    img = background(t + 40).convert("RGBA")

    draw_f = ease_in_out(phase(t, 0.1, 1.1))
    lines_f = ease_in_out(phase(t, 0.5, 1.4))
    size = 170
    mark = logo(size, frame=draw_f, low=lines_f, high=lines_f,
                glow=0.5 * phase(t, 1.2, 2.0))
    img.alpha_composite(mark, (W // 2 - size // 2, 150))

    layer, d = text_layer((W, H))
    word = ease_out(phase(t, 0.9, 1.6))
    draw_centered(d, W / 2, 345 + (1 - word) * 30, "CanLab", ui(96, 700),
                  with_alpha(TEXT, word))
    sub = ease_out(phase(t, 1.3, 1.9))
    draw_centered(d, W / 2, 470, "Reverse-engineer a CAN bus.", ui(38, 400),
                  with_alpha(DIM, sub))

    card_w, card_h = 720, 132
    gap = 40
    left = W / 2 - card_w - gap / 2
    for i, (label, url) in enumerate(LINKS):
        p = back_out(phase(t, 1.9 + i * 0.25, 2.6 + i * 0.25))
        a = phase(t, 1.9 + i * 0.25, 2.3 + i * 0.25)
        x = left + i * (card_w + gap)
        y = 590 + (1 - p) * 50
        d.rounded_rectangle((x, y, x + card_w, y + card_h), 18,
                            fill=(*PANEL, int(235 * a)),
                            outline=(60, 66, 70, int(255 * a)), width=2)
        bar = ease_out(phase(t, 2.3 + i * 0.25, 3.0 + i * 0.25))
        d.rounded_rectangle((x, y, x + 8, y + card_h * bar), 4,
                            fill=(*GREEN, int(255 * a)))
        d.text((x + 40, y + 26), label, font=mono(22, bold=True),
               fill=with_alpha(GREEN, a))
        d.text((x + 40, y + 62), url, font=ui(40, 500), fill=with_alpha(TEXT, a))

    foot = ease_out(phase(t, 2.8, 3.4))
    draw_centered(d, W / 2, 800, FOOTER, ui(32, 400), with_alpha(DIM, foot))
    img.alpha_composite(layer)

    # No fade in: the cut from the last beat is a crossfade, applied later.
    return fade_to(img.convert("RGB"), amount=ease_in_out(phase(t, total - 1.2, total)))


# ── chapter chips ────────────────────────────────────────────────────────────

#: Beat key -> chapter title. A chip appears at the start of each chapter.
CHAPTERS = {
    "open": "Overview",
    "protocol": "Identify the bus",
    "sniffer": "Watch it move",
    "detect": "Automatic detection",
    "dbc": "Define a signal",
    "plot": "Check it against the data",
    "arm": "The transmit gate",
    "services": "Assistants and hardware",
    "scale": "At scale",
}

CHIP_IN = 0.35
CHIP_OUT = 4.2


def chapter_number(key: str) -> int:
    return list(CHAPTERS).index(key) + 1


def chip_overlay(frame: Image.Image, t: float, key: str) -> Image.Image:
    """Draw the chapter chip at time `t` into the beat. No-op outside its window."""
    if key not in CHAPTERS or t > CHIP_OUT + 0.5:
        return frame
    appear = ease_out(phase(t, CHIP_IN, CHIP_IN + 0.5))
    leave = ease_in_out(phase(t, CHIP_OUT, CHIP_OUT + 0.45))
    a = appear * (1 - leave)
    if a <= 0.01:
        return frame

    num = f"{chapter_number(key):02d}"
    title = CHAPTERS[key]
    layer, d = text_layer(frame.size)
    fnum, ftitle = mono(26, bold=True), ui(38, 600)
    tw = d.textlength(title, font=ftitle)
    nw = d.textlength(num, font=fnum)
    x = 64 - (1 - appear) * 40
    y = 56
    width = 36 + nw + 22 + tw + 36
    d.rounded_rectangle((x, y, x + width, y + 76), 16,
                        fill=(12, 13, 14, int(225 * a)),
                        outline=(62, 68, 72, int(255 * a)), width=2)
    d.text((x + 36, y + 22), num, font=fnum, fill=with_alpha(GREEN, a))
    d.line((x + 36 + nw + 11, y + 20, x + 36 + nw + 11, y + 56),
           fill=(70, 76, 80, int(255 * a)), width=2)
    d.text((x + 36 + nw + 22, y + 14), title, font=ftitle, fill=with_alpha(TEXT, a))
    under = ease_out(phase(t, CHIP_IN + 0.3, CHIP_IN + 1.1)) * (1 - leave)
    d.rounded_rectangle((x + 36, y + 76 + 8, x + 36 + (width - 72) * under, y + 76 + 12),
                        2, fill=with_alpha(GREEN, a))
    base = frame.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")
