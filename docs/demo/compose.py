"""Turn captured stills into shots: zoom, spotlight, and point at things.

A screencast made of full-window stills asks the viewer to find the thing
being talked about, in a 1920x1080 interface, while the narrator moves on.
A demo instead moves the frame: it pushes in on the control under discussion,
dims everything else, and draws a ring around it.

The callouts here are placed from the widget's real geometry, captured at
record time with ``widget.mapTo(window, ...)``. Nothing is positioned by eye,
so a control that moves in a later build takes its label with it instead of
leaving an arrow pointing at empty panel.

Frames are generated rather than handed to ffmpeg's zoompan, because the ring
and the label have to be drawn in full-frame coordinates *before* the crop, so
that they scale with the push instead of sliding over it.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

FPS = 30
W, H = 1920, 1080

# Blender-ish accent, matching the interface being demonstrated.
RING = (86, 128, 194)
RING_GLOW = (71, 114, 179)
LABEL_BG = (18, 18, 20)
LABEL_FG = (232, 232, 232)
DIM = 0.42                       # how far the surroundings fall back


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def padded(self, pad: int, aspect: float = W / H,
               min_width: int = 880) -> "Rect":
        """Grow the rect, then widen it to the video's aspect ratio.

        `min_width` caps the magnification. A toolbar button is 58 px wide, and
        pushing in until it fills the frame is a five-times blow-up of a
        screenshot: unreadably soft, and it loses the surroundings that tell
        you where the control is. Roughly two times is the useful limit.
        """
        x, y = self.x - pad, self.y - pad
        w, h = self.w + pad * 2, self.h + pad * 2
        if w < min_width:
            grow = (min_width - w) / 2
            x -= grow
            w = min_width
        if w / h < aspect:
            need = h * aspect
            x -= (need - w) / 2
            w = need
        else:
            need = w / aspect
            y -= (need - h) / 2
            h = need
        # Keep the crop inside the frame.
        x = max(0, min(x, W - w)) if w <= W else 0
        y = max(0, min(y, H - h)) if h <= H else 0
        w, h = min(w, W), min(h, H)
        return Rect(int(x), int(y), int(w), int(h))

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def as_box(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.w, self.y + self.h


FULL = Rect(0, 0, W, H)


def ease(t: float) -> float:
    """Ease in and out, so a push never starts or stops abruptly."""
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def lerp_rect(a: Rect, b: Rect, t: float) -> Rect:
    e = ease(t)
    return Rect(
        int(a.x + (b.x - a.x) * e),
        int(a.y + (b.y - a.y) * e),
        int(a.w + (b.w - a.w) * e),
        int(a.h + (b.h - a.h) * e),
    )


def _rounded(draw, box, radius, **kw):
    draw.rounded_rectangle(box, radius=radius, **kw)


def spotlight(base: Image.Image, focus: Rect, strength: float) -> Image.Image:
    """Darken and soften everything except the focus, by `strength` (0..1)."""
    if strength <= 0.01:
        return base
    dimmed = base.point(lambda v: int(v * (1 - DIM * strength)))
    dimmed = dimmed.filter(ImageFilter.GaussianBlur(1.2 * strength))
    mask = Image.new("L", base.size, 0)
    md = ImageDraw.Draw(mask)
    pad = 10
    _rounded(md, (focus.x - pad, focus.y - pad,
                  focus.x + focus.w + pad, focus.y + focus.h + pad),
             12, fill=int(255 * strength))
    mask = mask.filter(ImageFilter.GaussianBlur(18))
    return Image.composite(base, dimmed, mask)


def ring(img: Image.Image, focus: Rect, strength: float) -> Image.Image:
    """Draw the highlight ring around the focus."""
    if strength <= 0.01:
        return img
    layer = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    pad = 6
    box = (focus.x - pad, focus.y - pad,
           focus.x + focus.w + pad, focus.y + focus.h + pad)
    glow = int(70 * strength)
    for grow, alpha in ((6, glow // 3), (3, glow // 2)):
        _rounded(d, (box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow),
                 12 + grow, outline=(*RING_GLOW, alpha), width=3)
    _rounded(d, box, 10, outline=(*RING, int(235 * strength)), width=3)
    return Image.alpha_composite(layer, overlay).convert("RGB")


def caption(img: Image.Image, text: str, font, strength: float) -> Image.Image:
    """A caption in the lower third, drawn on the finished frame.

    Deliberately in screen space rather than beside the control. Drawn into
    the scene before the crop, a caption is magnified by the push and clipped
    by its edges: at a two-times zoom half the words leave the frame. Here it
    is always the same size, always in the same place, and never cut.
    """
    if not text or strength <= 0.01:
        return img
    layer = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    pad_x, pad_y = 26, 16
    bbox = d.multiline_textbbox((0, 0), text, font=font, spacing=6)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    bw, bh = tw + pad_x * 2, th + pad_y * 2
    bx = (W - bw) // 2
    by = int(H * 0.80)
    # Rise into place as the push settles.
    by += int((1 - strength) * 26)

    alpha = int(235 * strength)
    _rounded(d, (bx, by, bx + bw, by + bh), 10,
             fill=(*LABEL_BG, alpha), outline=(*RING, int(200 * strength)), width=2)
    d.multiline_text((bx + pad_x, by + pad_y - bbox[1]), text,
                     font=font, fill=(*LABEL_FG, alpha), spacing=6, align="center")
    return Image.alpha_composite(layer, overlay).convert("RGB")


def render_shot(still: Path, out_dir: Path, index: int, *, seconds: float,
                focus: Rect | None, text: str, font,
                hold_ratio: float = 0.62, overlay=None,
                overlay_frames: int = 0) -> int:
    """Write the frames for one scene. Returns how many were written.

    With a focus: push in, hold with the ring and caption up, pull back out.
    Without one: a slow drift across the whole window, so a static shot still
    has life in it.

    `overlay(frame, i)` is drawn on top of the first `overlay_frames` frames,
    for things that animate over the beat such as a chapter title. Those
    frames are always rendered individually, never linked to the hold frame.
    """
    base = Image.open(still).convert("RGB")
    if base.size != (W, H):
        base = base.resize((W, H), Image.LANCZOS)
    total = frame_count(seconds)
    out_dir.mkdir(parents=True, exist_ok=True)

    if focus is None:
        # A 4% drift: enough that the shot is not frozen, not enough to notice.
        start = Rect(0, 0, W, H)
        end = Rect(int(W * 0.04), int(H * 0.04), int(W * 0.92), int(H * 0.92))
        for i in range(total):
            crop = lerp_rect(start, end, i / max(1, total - 1))
            frame = base.crop(crop.as_box()).resize((W, H), Image.LANCZOS)
            if overlay is not None and i < overlay_frames:
                frame = overlay(frame, i)
            frame.save(out_dir / f"{index + i:06d}.jpg", quality=92)
        return total

    target = focus.padded(120)
    push = max(6, int(total * (1 - hold_ratio) / 2))
    hold = total - push * 2

    held: Path | None = None
    for i in range(total):
        path = out_dir / f"{index + i:06d}.jpg"
        if i < push:                       # push in
            t = i / max(1, push - 1)
            crop = lerp_rect(FULL, target, t)
            strength = ease(t)
        elif i < push + hold:              # hold
            # Every hold frame is identical, and holds are most of a beat.
            # Render the first and hard-link the rest to it.
            if held is not None and i >= overlay_frames:
                os.link(held, path)
                continue
            crop = target
            strength = 1.0
        else:                              # pull out
            t = (i - push - hold) / max(1, push - 1)
            crop = lerp_rect(target, FULL, t)
            strength = 1.0 - ease(t)

        frame = spotlight(base, focus, strength)
        frame = ring(frame, focus, strength)
        frame = frame.crop(crop.as_box()).resize((W, H), Image.LANCZOS)
        frame = caption(frame, text, font, strength)
        if overlay is not None and i < overlay_frames:
            frame = overlay(frame, i)
        frame.save(path, quality=92)
        if push <= i < push + hold and i >= overlay_frames:
            held = path
    return total


def frame_count(seconds: float) -> int:
    """How many frames render_shot writes for a beat of this length."""
    return max(2, int(round(seconds * FPS)))


def load_font(size: int = 30):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()
