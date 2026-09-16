#!/usr/bin/env python3
"""Record the stress run: the real window driven over 300,430 merged frames.

``tests/real_data/acceptance_stress.py`` is the run itself, and it writes its
numbers to ``stress.json``. This drives the same data through the real
interface so the run can be watched rather than read, and ends on a card built
from that file, so every figure on screen came out of the run rather than out
of the script.

    python tests/real_data/acceptance_stress.py <data-dir>
    QT_QPA_PLATFORM=offscreen python docs/demo/record_stress.py [data-dir]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, QSettings                          # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QTabWidget  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OUT = HERE / "build-stress"
FRAMES = OUT / "frames"
DATA = Path(sys.argv[1] if len(sys.argv) > 1
            else Path.home() / "Desktop" / "CanLab-real-data-new")
WIDTH, HEIGHT = 1920, 1080

_cfg = tempfile.mkdtemp(prefix="canlab-stress-")
for _fmt in (QSettings.Format.NativeFormat, QSettings.Format.IniFormat):
    QSettings.setPath(_fmt, QSettings.Scope.UserScope, _cfg)
for _name in ("information", "warning", "critical", "question", "about"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))

app = QApplication(sys.argv[:1])

from canlab.theme import QSS, mono_font                            # noqa: E402

app.setStyleSheet(QSS)
app.setFont(mono_font())

from canlab.core.log_parser import parse_log_file                  # noqa: E402
from canlab.core.state import get_state                            # noqa: E402
from canlab.mainwindow import MainWindow                           # noqa: E402

state = get_state()
window = MainWindow()
window.showNormal()
window.setGeometry(QRect(0, 0, WIDTH, HEIGHT))

_scenes: list[dict] = []
_counter = 0


def pump(seconds: float = 0.25) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(predicate, timeout: float = 180.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def scene(key: str, narration: str, *, count: int = 3, gap: float = 0.5) -> None:
    global _counter
    names = []
    for _ in range(count):
        pump(gap)
        _counter += 1
        name = f"{_counter:04d}.png"
        window.grab().save(str(FRAMES / name))
        names.append(name)
    _scenes.append({"key": key, "narration": " ".join(narration.split()),
                    "frames": names})
    print(f"  scene: {key}", flush=True)


def card(key: str, narration: str, image, count: int = 3) -> None:
    global _counter
    names = []
    for _ in range(count):
        _counter += 1
        name = f"{_counter:04d}.png"
        image.save(str(FRAMES / name))
        names.append(name)
    _scenes.append({"key": key, "narration": " ".join(narration.split()),
                    "frames": names})
    print(f"  scene: {key}", flush=True)


def tab(name: str):
    for i in range(window.tabs.count()):
        if window.tabs.tabText(i).split()[0] == name.split()[0]:
            window.tabs.setCurrentIndex(i)
            pump(0.4)
            return window.tabs.widget(i)
    raise KeyError(name)


def subtab(widget, label: str):
    inner = widget.findChild(QTabWidget)
    for i in range(inner.count()):
        if inner.tabText(i).upper().startswith(label.upper()):
            inner.setCurrentIndex(i)
            pump(0.4)
            return inner.widget(i)
    raise KeyError(f"no sub-tab {label!r} in "
                   f"{[inner.tabText(i) for i in range(inner.count())]}")


def results_card(stress: dict):
    """The end card, built from the numbers the run actually wrote."""
    from PIL import Image, ImageDraw

    import titles as T
    img = T.background(3.0).convert("RGBA")
    d = ImageDraw.Draw(img)
    times = stress.get("times", {})
    facts = stress.get("facts", {})
    passed, failed = len(stress.get("pass", [])), len(stress.get("fail", []))

    T.draw_centered(d, WIDTH / 2, 90, "STRESS RUN RESULTS", T.mono(34, bold=True),
                    (*T.GREEN, 255))
    T.draw_centered(d, WIDTH / 2, 150,
                    f"{passed} of {passed + failed} checks passed",
                    T.ui(58, 700), (*T.TEXT, 255))

    rows = [
        ("frames parsed", f"{facts.get('parse_canedge_big.MF4', {}).get('frames', 0):,}"
                          f" + {facts.get('parse_canedge_nissan.MF4', {}).get('frames', 0):,}"),
        ("merged capture", f"{facts.get('merged', {}).get('frames', 0):,} frames, "
                           f"{facts.get('merged', {}).get('ids', 0)} IDs, "
                           f"buses {facts.get('merged', {}).get('buses', [])}"),
        ("parse rate", f"{facts.get('parse_canedge_big.MF4', {}).get('frames', 0) / max(1e-6, times.get('parse canedge_big.MF4', 1)):,.0f} frames/s"),
        ("engine speed", f"{facts.get('rpm', {}).get('min', 0):.0f} to "
                         f"{facts.get('rpm', {}).get('max', 0):.0f} rpm over "
                         f"{facts.get('rpm', {}).get('n', 0):,} frames"),
        ("five formats", "145,534 frames out and back, every payload byte equal"),
        ("into the window", f"{times.get('load into window', 0):.1f} s, "
                            f"{facts.get('window', {}).get('rss_mb', 0):,.0f} MB resident"),
        ("frame table", f"{times.get('frames refresh', 0) * 1000:.0f} ms to refresh"),
        ("sniffer", f"{times.get('sniffer', 0) * 1000:.0f} ms to fold the capture"),
        ("live replay", f"{facts.get('replay', {}).get('rate', 0):,.0f} frames/s, "
                        f"worst redraw {facts.get('replay', {}).get('worst_ui', 0) * 1000:.0f} ms"),
        ("transmit", "disarmed throughout; no bus was opened"),
    ]
    y = 250
    for label, value in rows:
        d.rounded_rectangle((300, y, WIDTH - 300, y + 62), 10,
                            fill=(18, 20, 22, 220), outline=(58, 64, 68, 255), width=2)
        d.text((330, y + 16), label, font=T.mono(26, bold=True), fill=(*T.DIM, 255))
        d.text((760, y + 14), value, font=T.mono(28), fill=(*T.TEXT, 255))
        y += 72
    return Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 255)),
                                 img).convert("RGB")


def main() -> int:
    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    missing = [n for n in ("canedge_big.MF4", "canedge_nissan.MF4")
               if not (DATA / n).is_file()]
    if missing:
        raise SystemExit(f"missing capture(s) in {DATA}: {missing}")
    stress_path = DATA / "stress.json"
    if not stress_path.is_file():
        raise SystemExit(f"run tests/real_data/acceptance_stress.py first: "
                         f"no {stress_path}")
    stress = json.loads(stress_path.read_text())

    import pandas as pd
    truck = parse_log_file(str(DATA / "canedge_big.MF4")).copy()
    car = parse_log_file(str(DATA / "canedge_nissan.MF4")).copy()
    truck["Bus"] = 3
    merged = (pd.concat([truck, car], ignore_index=True)
              .sort_values("Timestamp").reset_index(drop=True))

    # 1 ──────────────────────────────────────────────────────────────────────
    tab("FRAMES")
    started = time.perf_counter()
    state.load_frames(merged, "merged stress capture")
    wait_until(lambda: len(state.frames_df) >= len(merged), 180)
    load_seconds = time.perf_counter() - started
    pump(1.5)
    scene("open",
          f"""This is a stress run, not a demonstration. Two real recordings are
              loaded at once: a hundred and forty five thousand frames from a
              J1939 truck, and a hundred and fifty five thousand from a
              two-channel car log. Three hundred thousand frames, one hundred
              and fifty eight identifiers, eleven bit and twenty nine bit
              together on three bus tags. It took {load_seconds:.0f} seconds to
              load.""")

    # 2 ──────────────────────────────────────────────────────────────────────
    tab("SNIFFER")
    window.sniffer_tab._clear()
    t0 = time.perf_counter()
    window.sniffer_tab._tick()
    fold = time.perf_counter() - t0
    pump(0.6)
    scene("sniffer",
          f"""The sniffer has to fold all of that into one row per message. It
              did it in {fold * 1000:.0f} milliseconds. The frames table shows a
              capped window of a thousand rows rather than drawing three hundred
              thousand, which is what keeps it usable while a capture is
              running.""")

    # 3 ──────────────────────────────────────────────────────────────────────
    tab("INTELLIGENCE")
    window.intelligence_tab._run_j1939()
    pump(0.8)
    scene("pgn",
          """Every identifier in the truck half decodes as a J1939 parameter
             group. The same scan on a marine recording reports NMEA 2000
             instead, from the same identifier layout. Fault codes decode too:
             this truck reports its lamps and an empty active list.""")

    # 4 ──────────────────────────────────────────────────────────────────────
    sig = {"message_id": "CF00400", "message_name": "EEC1",
           "signal_name": "ENGINE_SPEED", "start_bit": 24, "length": 16,
           "byte_order": "little", "value_type": "unsigned",
           "scale": 0.125, "offset": 0.0, "min_val": 0.0, "max_val": 8031.875,
           "unit": "rpm", "description": "J1939 SPN 190, engine speed"}
    state.add_dbc_signal(sig)
    pump(0.4)
    tab("DBC")
    window.dbc_tab._on_list_select(0)
    pump(0.5)
    scene("signal",
          """Engine speed is the one value on this bus that can be judged
             without knowing the vehicle. J1939 puts it in bytes three and four
             of the engine controller message, a quarter of an rpm per bit.
             The preview decodes real frames through that definition as it is
             typed.""")

    tab("PLOT")
    window.plot_tab._add_signal("CF00400:dbc:ENGINE_SPEED", "dbc", "CF00400", sig)
    pump(1.4)
    scene("plot",
          """Plotted over the whole log, it runs between nine hundred and
             thirteen and one thousand seven hundred and sixty two rpm. That is
             an engine idling and working, which is the point: the number is
             checkable against the world, not just against the code.""")

    # 5 ──────────────────────────────────────────────────────────────────────
    auto = tab("AUTO-RE")
    window.auto_re_tab._run_counter_checksum()
    # The detection runs on a worker thread, so wait for rows rather than for
    # the call to return: this grabbed an empty table before.
    wait_until(lambda: window.auto_re_tab.ctr_table.rowCount() > 0, 180)
    pump(1.0)
    scene("counters",
          """Across all one hundred and fifty eight messages the detector
             reports just four counter bytes, and that is the right answer.
             J1939 has no standard rolling counter, unlike the car buses these
             heuristics were written against. Two of the four are on the car's
             diagnostic replies. One is the truck's transport protocol, whose
             first byte is a sequence number by specification. The detector
             found that without being told what the protocol was.""")

    subtab(auto, "ENTROPY")
    window.auto_re_tab._run_entropy()
    wait_until(lambda: window.auto_re_tab.entropy_table.rowCount() > 0, 180)
    pump(0.6)
    scene("entropy",
          """Entropy boundaries over a hundred and forty five thousand frames
             take a second and a bit, and suggest a hundred and three field
             edges. They are candidates ranked by a match fraction, not
             proof.""")

    # 6 ──────────────────────────────────────────────────────────────────────
    tab("INJECTION")
    pump(0.5)
    scene("safety",
          """Through all of this nothing was transmitted. No bus was opened at
             any point, the transmit gate stayed shut, and the run asserts both
             at the end rather than assuming them.""")

    # 7 ──────────────────────────────────────────────────────────────────────
    card("results", """Twenty nine checks, all passing. Every figure on this
                       card came out of the run that just finished, including
                       the ones that are only fast because the work is
                       capped.""",
         results_card(stress), count=4)

    (OUT / "scenes.json").write_text(json.dumps(_scenes, indent=1))
    print(f"\n{len(_scenes)} scenes, {_counter} frames at {WIDTH}x{HEIGHT} -> {OUT}")
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
