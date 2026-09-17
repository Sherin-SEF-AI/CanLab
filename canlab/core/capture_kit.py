"""A headless logger for a small computer left in a vehicle.

The desktop captures well when someone is sitting at it. A day of driving
needs something that starts at boot, writes to disk as it goes, survives the
ignition being turned off, and lets whoever is driving say "this is the
brake" without a screen: a key, an HTTP request from a phone, or a switch on
a GPIO pin. That is the capture kit. It owns a ``BusHub`` for the receive
thread, a ``SegmentWriter`` for the files, a ``MarkFile`` for the marks, and
a small ``FrameStore`` so an HTTP client can see the last few seconds.

When the run ends the segments and the marks are folded into one ``.canlab``
project through ``write_project``, so the desktop opens what the kit
recorded with every mark on the timeline.

Marks are on the wall clock, the same clock python-can stamps frames with,
so a mark made from a phone lines up with the frames around it.

The kit never elevates privileges and never transmits: the bus it is given
is only ever read.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from canlab.core.annotations import AnnotationSet
from canlab.core.bus_hub import BusHub
from canlab.core.capture_writer import SegmentWriter, iter_segments
from canlab.core.frame_store import FrameStore

MARK_ACTIONS = ("begin", "end", "point", "toggle")


# ── marks ────────────────────────────────────────────────────────────────────

class MarkFile:
    """An AnnotationSet that is written to disk on every change, atomically."""

    def __init__(self, path):
        self.path = Path(path)
        self.annotations = AnnotationSet()
        if self.path.is_file():
            try:
                self.annotations = AnnotationSet.from_json(self.path.read_text())
            except (ValueError, TypeError, KeyError):
                self.annotations = AnnotationSet()
        self._lock = threading.Lock()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(self.annotations.to_json())
        os.replace(tmp, self.path)

    def open_labels(self) -> list[str]:
        return [a.label for a in self.annotations.items if not a.closed]

    def begin(self, label: str, at: float) -> str:
        with self._lock:
            self.annotations.begin(label, at)
            self.save()
        return "begin"

    def end(self, label: str, at: float) -> str:
        with self._lock:
            if label not in self.open_labels():
                return "none"
            self.annotations.end(label, at)
            self.save()
        return "end"

    def point(self, label: str, at: float, half: float = 0.5) -> str:
        with self._lock:
            self.annotations.add(label, at - half, at + half)
            self.save()
        return "point"

    def toggle(self, label: str, at: float) -> str:
        if label in self.open_labels():
            return self.end(label, at)
        return self.begin(label, at)

    def apply(self, label: str, action: str, at: float) -> str:
        action = (action or "toggle").lower()
        if action not in MARK_ACTIONS:
            raise ValueError(f"unknown mark action {action!r}")
        return getattr(self, action)(label, at)

    def close_all(self, at: float) -> int:
        with self._lock:
            open_now = self.open_labels()
            for label in open_now:
                self.annotations.end(label, at)
            if open_now:
                self.save()
        return len(open_now)

    def __len__(self) -> int:
        return len(self.annotations.items)


# ── the session ──────────────────────────────────────────────────────────────

@dataclass
class CaptureOptions:
    out_dir: Path
    prefix: str = "capture"
    max_frames: int = 200_000
    max_seconds: float = 600.0
    keep_tail: int = 20_000           # frames the HTTP view can show
    tick_s: float = 0.25
    project_path: Path | None = None  # None: <out_dir>/<prefix>.canlab.zip
    no_project: bool = False
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.out_dir = Path(self.out_dir)
        if self.project_path is not None:
            self.project_path = Path(self.project_path)


class _KitState:
    """What the REST API reads: the recent frames, no signals, connected."""

    def __init__(self, session: "CaptureSession"):
        self._session = session
        self.dbc_signals: list = []
        self.ai_memory: list = []
        self.can_bus = None

    @property
    def frames_df(self):
        return self._session.store.materialize()

    @property
    def is_connected(self) -> bool:
        return self._session.running


class CaptureSession:
    """Read a bus into segments and marks until told to stop."""

    def __init__(self, bus, opts: CaptureOptions, *, name: str = "capture",
                 bitrate: int = 500_000, bus_index: int = 0, log=None):
        self.opts = opts
        self.log = log or (lambda *_: None)
        self.hub = BusHub(bus, name=name, bus_index=bus_index, bitrate=bitrate,
                          on_error=lambda m: self.log(f"bus error: {m}"))
        self.writer = SegmentWriter(opts.out_dir, opts.prefix, max_frames=opts.max_frames,
                                    max_seconds=opts.max_seconds)
        self.marks = MarkFile(opts.out_dir / "marks.json")
        self.store = FrameStore(cap=max(1000, opts.keep_tail))
        self.state = _KitState(self)
        self.running = False
        self.started_at: float | None = None
        self.stopped_at: float | None = None
        self.frames = 0
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        self.opts.out_dir.mkdir(parents=True, exist_ok=True)
        self.marks.save()               # an empty run still leaves marks.json
        self.started_at = time.time()
        self.running = True
        self.hub.start()
        self.log(f"capturing into {self.opts.out_dir}")

    def step(self) -> int:
        """Move what the hub has received into the files and the tail view."""
        rows = self.hub.drain()
        if not rows:
            return 0
        with self._lock:
            self.writer.write_rows(rows)
            self.store.append_batch(rows)
            self.frames += len(rows)
        return len(rows)

    def mark(self, label: str, action: str = "toggle", at: float | None = None) -> str:
        label = (label or "").strip()
        if not label:
            raise ValueError("a mark needs a label")
        when = float(at) if at is not None else time.time()
        performed = self.marks.apply(label, action, when)
        self.log(f"mark {label}: {performed} at {when:.3f}")
        return performed

    def stop(self) -> dict:
        if not self.running:
            return self.summary()
        self.running = False
        self.hub.shutdown()
        self.step()                     # the tail the thread had queued
        self.stopped_at = time.time()
        closed = self.marks.close_all(self.stopped_at)
        if closed:
            self.log(f"closed {closed} open mark(s) at stop")
        self.writer.close()
        return self.summary()

    def run(self, stop_event: threading.Event, tick_s: float | None = None,
            duration_s: float | None = None) -> dict:
        """Start, tick until ``stop_event`` or ``duration_s``, stop."""
        tick = float(tick_s or self.opts.tick_s)
        self.start()
        deadline = time.monotonic() + duration_s if duration_s else None
        try:
            while not stop_event.is_set():
                self.step()
                if deadline is not None and time.monotonic() >= deadline:
                    break
                stop_event.wait(tick)
        finally:
            summary = self.stop()
        return summary

    def summary(self) -> dict:
        seconds = ((self.stopped_at or time.time()) - self.started_at) if self.started_at else 0.0
        return {"frames": self.frames, "segments": [str(p) for p in self.writer.segments],
                "dropped": self.hub.dropped, "error_frames": self.hub.error_frames,
                "marks": len(self.marks), "ids": len(self.store.unique_ids()),
                "seconds": round(seconds, 3), "out_dir": str(self.opts.out_dir)}

    def state_getter(self):
        return self.state


# ── the project ──────────────────────────────────────────────────────────────

def build_project(segments, annotations: AnnotationSet, path, *, meta: dict | None = None) -> int:
    """Fold the segments and the marks into one .canlab project. Returns the
    frame count. Segments are streamed, so a long run never sits in memory."""
    from canlab.core.project import write_project
    full_meta = {"source": "capture kit", "segments": [str(p) for p in segments]}
    full_meta.update(meta or {})
    return write_project(str(path), frames_chunks=iter_segments(segments),
                         annotations_json=annotations.to_json(), meta=full_meta)


# ── mark inputs ──────────────────────────────────────────────────────────────

def parse_mark_line(line: str) -> tuple[str, str] | None:
    """``"brake"`` toggles; ``"brake on"`` / ``"brake off"`` begin and end;
    ``"brake point"`` marks an instant. ``None`` for a blank line."""
    parts = line.strip().split()
    if not parts:
        return None
    label = parts[0]
    if len(parts) == 1:
        return label, "toggle"
    word = parts[1].lower()
    action = {"on": "begin", "begin": "begin", "start": "begin",
              "off": "end", "end": "end", "stop": "end",
              "point": "point", "now": "point"}.get(word, "toggle")
    return label, action


def stdin_marker(session: CaptureSession, stream, stop_event: threading.Event) -> None:
    """Read lines until EOF or ``q``. Meant to run on its own thread."""
    for raw in stream:
        if stop_event.is_set():
            break
        text = raw.strip()
        if text.lower() in ("q", "quit", "exit"):
            stop_event.set()
            break
        parsed = parse_mark_line(text)
        if parsed is None:
            continue
        label, action = parsed
        try:
            session.mark(label, action)
        except ValueError as e:
            session.log(str(e))


def parse_pin_map(text: str) -> dict[int, str]:
    """``"17=brake,27=horn"`` -> ``{17: "brake", 27: "horn"}``."""
    out: dict[int, str] = {}
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"expected PIN=label, got {item!r}")
        pin, label = item.split("=", 1)
        pin_no = int(pin.strip())
        label = label.strip()
        if not label:
            raise ValueError(f"pin {pin_no} has no label")
        if pin_no in out:
            raise ValueError(f"pin {pin_no} given twice")
        out[pin_no] = label
    return out


def gpio_marker(session: CaptureSession, pins: dict[int, str]) -> list[str]:
    """Wire GPIO buttons to marks: pressed begins, released ends.

    Uses gpiozero when it is importable and reports, never raises, when it is
    not: a kit built without it still records, just without the switches.
    Returns the lines to print. The button objects are kept on the session so
    they are not collected while the run lasts.
    """
    notes: list[str] = []
    if not pins:
        return notes
    try:
        from gpiozero import Button
    except Exception as e:                       # ImportError, or no pin factory
        notes.append(f"GPIO marks unavailable ({type(e).__name__}: {e}); "
                     f"pins {sorted(pins)} ignored")
        return notes
    buttons = []
    for pin, label in pins.items():
        try:
            button = Button(pin)
        except Exception as e:
            notes.append(f"GPIO {pin} ({label}): {e}")
            continue
        button.when_pressed = (lambda lab=label: session.mark(lab, "begin"))
        button.when_released = (lambda lab=label: session.mark(lab, "end"))
        buttons.append(button)
        notes.append(f"GPIO {pin} marks {label!r} while pressed")
    session._gpio_buttons = buttons
    return notes


def write_token_file(path, token: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token + "\n")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def summary_text(summary: dict) -> str:
    kinds = (f"{summary['frames']} frames from {summary['ids']} IDs in "
             f"{summary['seconds']:.1f} s, {len(summary['segments'])} segment(s), "
             f"{summary['marks']} mark(s)")
    if summary.get("dropped"):
        kinds += f", {summary['dropped']} dropped before they could be written"
    if summary.get("error_frames"):
        kinds += f", {summary['error_frames']} error frames"
    return kinds


def dump_json(path, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, indent=1, default=str))
