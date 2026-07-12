"""Vision-OCR reference extraction.

Read a numeric value (speed, RPM, SoC, …) shown on a dashboard/gauge in a
**video** by OCR-ing a user-defined region across frames, producing a
(timestamps, values) reference series. Fed into core.reference_calibrate, this
lets you calibrate a CAN byte to the physical value read off a dashcam — closing
the loop with no bench reference hardware.

opencv + rapidocr are OPTIONAL heavy dependencies (no PyTorch): the module
imports fine without them; `is_available()` reports readiness and
`extract_reference()` raises a clear message if they're missing.
"""
from __future__ import annotations

import re

# One number, optionally decimal, optionally sign — the first match in OCR text.
_NUM_RE = re.compile(r"[-+]?\d{1,4}(?:[.,]\d{1,3})?")


def _parse_number(text: str) -> float | None:
    """Extract the first numeric reading from OCR text (handles ',' decimals)."""
    if not text:
        return None
    m = _NUM_RE.search(text.replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "."))
    except ValueError:
        return None


def is_available() -> tuple[bool, str]:
    """Return (ok, message) — whether opencv + rapidocr are importable."""
    try:
        import cv2  # noqa: F401
    except Exception:
        return False, "opencv not installed (pip install opencv-python-headless)"
    try:
        from rapidocr import RapidOCR  # noqa: F401
    except Exception:
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: F401
        except Exception:
            return False, "rapidocr not installed (pip install rapidocr onnxruntime)"
    return True, "ready"


def _make_ocr():
    try:
        from rapidocr import RapidOCR
    except Exception:
        from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def _run_ocr(engine, img):
    """Return the concatenated recognized text for an image (version-agnostic)."""
    result = engine(img)
    # rapidocr 2.x returns an object with .txts; onnxruntime variant returns
    # (list_of_[box,text,score], elapse).
    txts = getattr(result, "txts", None)
    if txts is not None:
        return " ".join(txts or [])
    if isinstance(result, tuple):
        dets = result[0] or []
        return " ".join(d[1] for d in dets)
    return ""


def extract_reference(video_path: str, roi: tuple[int, int, int, int],
                      sample_hz: float = 2.0,
                      t_start: float = 0.0, t_end: float | None = None,
                      time_offset: float = 0.0,
                      progress=None) -> tuple[list, list]:
    """OCR a numeric value from ``roi`` (x, y, w, h) across the video.

    Returns (timestamps, values). ``time_offset`` is added to each video
    timestamp to align it to the CAN log's time base. ``progress`` is an optional
    callable(fraction_0_to_1).
    """
    ok, msg = is_available()
    if not ok:
        raise RuntimeError(f"Vision OCR unavailable: {msg}")
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = total / fps if fps else 0.0
    if t_end is None:
        t_end = duration
    step = max(1.0 / max(sample_hz, 0.1), 1.0 / fps)

    x, y, w, h = roi
    engine = _make_ocr()
    ts_out, val_out = [], []
    t = t_start
    while t <= t_end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok_frame, frame = cap.read()
        if not ok_frame:
            break
        crop = frame[y:y + h, x:x + w]
        if crop.size:
            val = _parse_number(_run_ocr(engine, crop))
            if val is not None:
                ts_out.append(t + time_offset)
                val_out.append(val)
        if progress and duration:
            progress(min(1.0, t / duration))
        t += step
    cap.release()
    return ts_out, val_out
