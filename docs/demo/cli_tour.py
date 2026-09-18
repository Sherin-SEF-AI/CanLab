"""Draw the command line tour: a terminal that types, and output that arrives.

The transcript from ``record_cli.py`` holds what really happened. This turns
it into pictures: a terminal panel on the tour's own background, a command
typed a character at a time, then the bytes that command actually wrote,
arriving in the order and roughly the rhythm they arrived in.

Two things keep it honest. The text is the recorded output, never a retyped
approximation, and long waits are compressed rather than faked: a command that
took twelve seconds is shown taking a few, with the elapsed clock in the
window's title bar counting real seconds so the compression is visible.

The typography, colours, logo and chapter chips come from ``titles`` so the
tour looks like the rest of the demos.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

import titles as T

W, H = T.W, T.H
FPS = T.FPS

# ── the terminal ─────────────────────────────────────────────────────────────

TERM_X, TERM_Y = 96, 138
TERM_W, TERM_H = W - 2 * TERM_X, 684
PAD = 34
BAR_H = 52
FONT_SIZE = 21
LINE_H = 27
COLS = 104
ROWS = (TERM_H - BAR_H - 2 * PAD) // LINE_H

TERM_BG = (13, 15, 16)
TERM_BAR = (26, 29, 31)
TERM_EDGE = (48, 53, 56)
PROMPT_GREEN = T.GREEN
PROMPT_PATH = (110, 168, 235)
OUT_TEXT = (214, 218, 221)
OUT_DIM = (132, 138, 143)
ACCENT = (240, 190, 90)

#: Words worth colouring in the output. A demo that highlights everything
#: highlights nothing, so this is short: the numbers people came to see.
NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
HEXID = re.compile(r"\b[0-9A-F]{3,8}\b")

CHAPTERS = {
    "overview": "The command line",
    "ids": "What is on this bus",
    "formats": "Every format, one reader",
    "detect": "Find the structure",
    "decode": "Decode it",
    "convert": "Convert and prove it",
    "capture": "The capture kit",
    "assistants": "Assistants over MCP",
}

#: Real seconds of waiting are compressed to this fraction, with a floor and a
#: ceiling, so a 13 second capture does not cost 13 seconds of video.
TIME_SCALE = 0.34
MAX_WAIT = 2.6
MIN_GAP = 0.05
TYPE_CPS = 43.0          # characters a second while the command is typed
LINE_FADE = 0.16         # how long a new line takes to arrive


def wrap(text: str, cols: int = COLS) -> list[str]:
    """Break a recorded line the way a terminal of this width would."""
    text = text.replace("\t", "    ").rstrip("\n")
    if not text:
        return [""]
    out = []
    while len(text) > cols:
        out.append(text[:cols])
        text = text[cols:]
    out.append(text)
    return out


@dataclass
class Line:
    """One rendered line, and when it appeared."""
    text: str
    at: float
    kind: str = "out"          # out | prompt | typing


@dataclass
class Shot:
    """One command: its typing, its output, and how long the whole thing runs."""
    cmd: str
    chapter: str
    caption: str
    note: str
    cwd: str = "captures"
    lines: list[Line] = field(default_factory=list)
    type_for: float = 0.0
    duration: float = 0.0
    real_seconds: float = 0.0
    exit: int = 0


def build_shots(transcript: dict) -> list[Shot]:
    """Lay the recorded steps out on the video clock."""
    shots = []
    for step in transcript["steps"]:
        cmd = step["cmd"]
        shot = Shot(cmd=cmd, chapter=step["chapter"], caption=step["caption"],
                    note=step.get("note", ""), cwd=step.get("cwd") or "captures",
                    real_seconds=step["seconds"], exit=step["exit"])
        shot.type_for = max(0.7, len(cmd) / TYPE_CPS)
        t = shot.type_for + 0.35                     # a beat before it answers
        seen = ""
        for when, chunk in step["chunks"]:
            seen += chunk
        # Attribute each finished line to the moment its chunk arrived.
        consumed = 0
        clock = t
        for when, chunk in step["chunks"]:
            gap = min(MAX_WAIT, max(MIN_GAP, when * TIME_SCALE - (clock - t)))
            clock = t + max(clock - t, when * TIME_SCALE)
            piece = seen[consumed:consumed + len(chunk)]
            consumed += len(chunk)
            for raw in piece.split("\n"):
                if raw == "" and piece.endswith("\n"):
                    continue
                for part in wrap(raw):
                    shot.lines.append(Line(part, clock))
                    clock += 0.012
            del gap
        tail = step.get("pause", 1.2)
        shot.duration = max(clock - 0 + tail, shot.type_for + 1.0 + tail)
        shots.append(shot)
    return shots


# ── drawing ──────────────────────────────────────────────────────────────────

@lru_cache(maxsize=4)
def _panel_shadow() -> Image.Image:
    shadow = Image.new("L", (TERM_W + 120, TERM_H + 120), 0)
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((60, 66, TERM_W + 60, TERM_H + 62), 22, fill=150)
    return shadow.filter(ImageFilter.GaussianBlur(26))


def _dot(d, x, y, colour):
    d.ellipse((x, y, x + 12, y + 12), fill=colour)


def terminal_panel(elapsed: float, title: str, exit_code: int | None) -> Image.Image:
    """The window itself: chrome, title, and an elapsed clock."""
    panel = Image.new("RGBA", (TERM_W, TERM_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((0, 0, TERM_W - 1, TERM_H - 1), 18, fill=(*TERM_BG, 252),
                        outline=(*TERM_EDGE, 255), width=2)
    d.rounded_rectangle((0, 0, TERM_W - 1, BAR_H), 18, fill=(*TERM_BAR, 255))
    d.rectangle((0, BAR_H - 18, TERM_W - 1, BAR_H), fill=(*TERM_BAR, 255))
    d.line((1, BAR_H, TERM_W - 2, BAR_H), fill=(*TERM_EDGE, 255), width=2)

    for i, colour in enumerate(((92, 96, 99), (92, 96, 99), (92, 96, 99))):
        _dot(d, 22 + i * 22, BAR_H // 2 - 6, colour)

    f = T.mono(19)
    tw = d.textlength(title, font=f)
    d.text(((TERM_W - tw) / 2, BAR_H // 2 - 12), title, font=f, fill=(158, 164, 169))

    clock = f"{elapsed:5.2f}s"
    if exit_code is not None:
        ok = exit_code == 0
        badge = "exit 0" if ok else f"exit {exit_code}"
        colour = T.GREEN if ok else (232, 100, 100)
        bw = d.textlength(badge, font=T.mono(17, bold=True))
        d.rounded_rectangle((TERM_W - 40 - bw - 20, BAR_H // 2 - 14,
                             TERM_W - 40 + 4, BAR_H // 2 + 14), 12,
                            fill=(*colour, 34), outline=(*colour, 150), width=1)
        d.text((TERM_W - 40 - bw - 8, BAR_H // 2 - 9), badge,
               font=T.mono(17, bold=True), fill=(*colour, 255))
        d.text((TERM_W - 40 - bw - 20 - 78, BAR_H // 2 - 9), clock,
               font=T.mono(17), fill=(120, 126, 131, 255))
    else:
        d.text((TERM_W - 108, BAR_H // 2 - 9), clock, font=T.mono(17),
               fill=(120, 126, 131, 255))
    return panel


def _colour_for(text: str, kind: str) -> tuple:
    if kind == "prompt":
        return OUT_TEXT
    low = text.strip().lower()
    if low.startswith(("usage:", "positional arguments", "options:", "bus:",
                       "files:", "marks:")):
        return (150, 200, 255)
    if "error" in low or "traceback" in low or low.startswith("no such"):
        return (232, 120, 120)
    return OUT_TEXT


def draw_body(panel: Image.Image, shot: Shot, t: float, scroll_px: float) -> None:
    """Command line and output, clipped to the window."""
    view = Image.new("RGBA", (TERM_W - 2 * PAD, TERM_H - BAR_H - 2 * PAD + 8),
                     (0, 0, 0, 0))
    d = ImageDraw.Draw(view)
    f = T.mono(FONT_SIZE)
    fb = T.mono(FONT_SIZE, bold=True)

    y = -scroll_px
    # the prompt, typed
    shown = int(min(len(shot.cmd), max(0.0, t) * TYPE_CPS))
    prompt_x = 0
    d.text((prompt_x, y), "canlab", font=fb, fill=(*PROMPT_GREEN, 255))
    prompt_x += d.textlength("canlab ", font=fb)
    d.text((prompt_x, y), shot.cwd, font=f, fill=(*PROMPT_PATH, 255))
    prompt_x += d.textlength(shot.cwd + " ", font=f)
    d.text((prompt_x, y), "$", font=fb, fill=(*OUT_DIM, 255))
    prompt_x += d.textlength("$ ", font=fb)

    typed = shot.cmd[:shown]
    # The first line carries the prompt, so it is narrower than the rest. An
    # earlier version compared parts with `is not`, which is string identity:
    # a wrapped command gained a blank line and left its cursor adrift.
    parts = wrap(typed, COLS - 20)
    for i, part in enumerate(parts):
        d.text((prompt_x, y), part, font=f, fill=(*OUT_TEXT, 255))
        if i < len(parts) - 1:
            y += LINE_H
            prompt_x = 0
    cursor_x = prompt_x + d.textlength(parts[-1], font=f)
    if shown < len(shot.cmd) or math.sin(t * 6.0) > -0.2:
        d.rectangle((cursor_x + 2, y + 3, cursor_x + 12, y + LINE_H - 4),
                    fill=(*T.GREEN, 210 if shown < len(shot.cmd) else 150))
    y += LINE_H + 6

    for line in shot.lines:
        if t < line.at:
            break
        a = T.clamp((t - line.at) / LINE_FADE)
        colour = _colour_for(line.text, line.kind)
        d.text((0, y), line.text, font=f, fill=(*colour, int(255 * a)))
        y += LINE_H

    panel.alpha_composite(view, (PAD, BAR_H + PAD - 4))


def scroll_for(shot: Shot, t: float) -> float:
    """How far the view has scrolled, so the newest line stays visible."""
    visible = 1 + sum(1 for line in shot.lines if t >= line.at)
    over = visible - ROWS
    if over <= 0:
        return 0.0
    # ease the scroll rather than jumping a whole line each time
    return over * LINE_H


def caption_bar(img: Image.Image, shot: Shot, t: float, total: float) -> Image.Image:
    """The line that says what is happening, under the terminal."""
    if not shot.caption:
        return img
    appear = T.ease_out(T.phase(t, 0.15, 0.75))
    leave = T.ease_in_out(T.phase(t, total - 0.45, total))
    a = appear * (1 - leave)
    if a <= 0.01:
        return img
    layer, d = T.text_layer(img.size)
    y = TERM_Y + TERM_H + 40
    f = T.ui(38, 500)
    tw = d.textlength(shot.caption, font=f)
    x = (W - tw) / 2
    d.rounded_rectangle((x - 30, y - 14 + (1 - appear) * 12, x + tw + 30,
                         y + 52 + (1 - appear) * 12), 14,
                        fill=(16, 18, 20, int(225 * a)),
                        outline=(58, 64, 68, int(200 * a)), width=2)
    d.text((x, y + (1 - appear) * 12), shot.caption, font=f,
           fill=T.with_alpha(T.TEXT, a))
    if shot.note:
        na = T.ease_out(T.phase(t, 1.1, 1.7)) * (1 - leave)
        fn = T.ui(29, 400)
        nw = d.textlength(shot.note, font=fn)
        d.text(((W - nw) / 2, y + 74), shot.note, font=fn,
               fill=T.with_alpha(T.DIM, na))
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


def render_shot_frame(shot: Shot, index: int, count: int, first_of_chapter: bool,
                      chapter_t: float) -> Image.Image:
    """One frame of one command."""
    t = index / FPS
    img = T.background(t + 7.0, strength=0.75).convert("RGBA")

    shadow = _panel_shadow()
    dark = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dark.paste(Image.new("RGBA", shadow.size, (0, 0, 0, 255)),
               (TERM_X - 60, TERM_Y - 60), shadow)
    img.alpha_composite(dark)

    elapsed = min(shot.real_seconds, (t / max(shot.duration, 1e-6)) * shot.real_seconds)
    done = t >= (shot.lines[-1].at if shot.lines else shot.type_for + 0.4)
    panel = terminal_panel(elapsed if not done else shot.real_seconds,
                           "canlab-cli", shot.exit if done else None)
    draw_body(panel, shot, t, scroll_for(shot, t))

    # a settle-in on the first frames of a command
    rise = T.ease_out(T.phase(t, 0.0, 0.45))
    img.alpha_composite(panel, (TERM_X, int(TERM_Y + (1 - rise) * 18)))
    out = img.convert("RGB")
    out = caption_bar(out, shot, t, shot.duration)
    if first_of_chapter:
        out = chip(out, chapter_t, shot.chapter)
    return out


# ── chapter chips ────────────────────────────────────────────────────────────

def chip(frame: Image.Image, t: float, key: str) -> Image.Image:
    """The tour's chapter chip, with this tour's chapter list."""
    if key not in CHAPTERS:
        return frame
    appear = T.ease_out(T.phase(t, 0.2, 0.7))
    leave = T.ease_in_out(T.phase(t, 3.6, 4.1))
    a = appear * (1 - leave)
    if a <= 0.01:
        return frame
    num = f"{list(CHAPTERS).index(key) + 1:02d}"
    title = CHAPTERS[key]
    layer, d = T.text_layer(frame.size)
    fnum, ftitle = T.mono(24, bold=True), T.ui(34, 600)
    tw = d.textlength(title, font=ftitle)
    nw = d.textlength(num, font=fnum)
    x = 64 - (1 - appear) * 40
    y = 58
    width = 32 + nw + 20 + tw + 32
    d.rounded_rectangle((x, y, x + width, y + 68), 14,
                        fill=(12, 13, 14, int(228 * a)),
                        outline=(62, 68, 72, int(255 * a)), width=2)
    d.text((x + 32, y + 21), num, font=fnum, fill=T.with_alpha(T.GREEN, a))
    d.line((x + 32 + nw + 10, y + 18, x + 32 + nw + 10, y + 50),
           fill=(70, 76, 80, int(255 * a)), width=2)
    d.text((x + 32 + nw + 20, y + 14), title, font=ftitle,
           fill=T.with_alpha(T.TEXT, a))
    base = frame.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


# ── intro and outro ──────────────────────────────────────────────────────────

INTRO_S = 6.0
OUTRO_S = 6.5


def render_intro_frame(index: int, count: int) -> Image.Image:
    t = index / FPS
    img = T.intro_frame(t, count / FPS)
    layer, d = T.text_layer(img.size)
    # the tour's own subtitle, under the wordmark
    a = T.ease_out(T.phase(t, 4.9, 5.5))
    if a > 0:
        line = "canlab-cli:  the whole analysis, without the window"
        f = T.mono(34, bold=True)
        tw = d.textlength(line, font=f)
        d.text(((W - tw) / 2, H - 190), line, font=f, fill=T.with_alpha(T.DIM, a))
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


def render_outro_frame(index: int, count: int) -> Image.Image:
    return T.outro_frame(index / FPS, count / FPS)
