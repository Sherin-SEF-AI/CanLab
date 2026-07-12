"""Tests for vision-OCR reference extraction (#2).

The number parser is always tested; the OCR path runs only when opencv +
rapidocr are installed (optional heavy deps)."""
import pytest

from core.vision_reference import _parse_number, is_available


@pytest.mark.parametrize("text,expected", [
    ("88", 88.0),
    ("SPD 62.5 km/h", 62.5),
    ("1,234", 1.234),
    ("RPM: 3000", 3000.0),
    ("-12", -12.0),
    ("no digits here", None),
    ("", None),
])
def test_parse_number(text, expected):
    assert _parse_number(text) == expected


def test_ocr_reads_rendered_number():
    ok, _ = is_available()
    if not ok:
        pytest.skip("opencv/rapidocr not installed")
    import numpy as np, cv2
    from core.vision_reference import _make_ocr, _run_ocr
    img = np.full((120, 240, 3), 255, np.uint8)
    cv2.putText(img, "137", (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (0, 0, 0), 6)
    val = _parse_number(_run_ocr(_make_ocr(), img))
    assert val == 137.0
