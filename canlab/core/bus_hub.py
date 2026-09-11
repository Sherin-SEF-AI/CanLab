"""Single receive dispatcher for one CAN bus.

Exactly one thread calls ``bus.recv``; everything else subscribes. Before this
existed the live-capture worker and each diagnostic worker called ``recv`` on
the same bus object concurrently, so responses were delivered to whichever
thread happened to win the race and ISO-TP transfers dropped frames at random.

A :class:`Subscription` duck-types the python-can bus API (``send`` / ``recv``),
so existing workers take one in place of a raw bus with no other change. Its
``send`` routes through :func:`canlab.core.safety.gated_send`, which makes the
hub the single CAN transmit choke point as well.

The hub is Qt-free: it reports load, errors and trigger hits through plain
callables (the GUI passes callbacks that emit Qt signals, which is safe from
the receive thread because Qt queues cross-thread emissions).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque

from canlab.core.bus_health import BusHealthMeter
from canlab.core.bus_load import BusLoadMeter
from canlab.core.canid import normalize_id
from canlab.core.log_parser import make_row
from canlab.core.safety import gated_send

log = logging.getLogger(__name__)

DEFAULT_QUEUE = 2000
PENDING_LIMIT = 200_000     # rows held for the GUI if it stalls


def _make_filter(id_filter):
    """Accept None (everything), an int, a container of ints, or a predicate."""
    if id_filter is None:
        return lambda _arb: True
    if callable(id_filter):
        return id_filter
    if isinstance(id_filter, int):
        return lambda arb, _w=id_filter: arb == _w
    wanted = set(int(i) for i in id_filter)
    return lambda arb, _w=wanted: arb in _w


class Subscription:
    """A per-consumer view of the bus: its own receive queue, shared transmit."""

    def __init__(self, hub: "BusHub", id_filter=None, maxsize: int = DEFAULT_QUEUE):
        self._hub = hub
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._matches = _make_filter(id_filter)
        self.dropped = 0
        self.closed = False

    # -- receive side (called from the hub's rx thread) ------------------
    def matches(self, arb_id: int) -> bool:
        try:
            return bool(self._matches(arb_id))
        except Exception:
            log.debug("subscription filter raised", exc_info=True)
            return False

    def offer(self, msg) -> None:
        try:
            self._queue.put_nowait(msg)
        except queue.Full:
            # Drop the oldest frame so a stalled consumer never blocks capture.
            try:
                self._queue.get_nowait()
                self.dropped += 1
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(msg)
            except queue.Full:
                self.dropped += 1

    # -- bus-like API ----------------------------------------------------
    def recv(self, timeout: float = 0.1):
        if self.closed:
            return None
        try:
            return self._queue.get(timeout=max(0.001, timeout or 0.001))
        except queue.Empty:
            return None

    def send(self, msg) -> None:
        self._hub.send(msg)

    def close(self) -> None:
        self.closed = True
        self._hub.unsubscribe(self)

    # python-can compatibility: a worker handed a Subscription must never
    # shut down the shared hardware bus.
    def shutdown(self) -> None:
        self.close()


class BusHub:
    """Owns one bus, its receive thread, its meters and its subscriptions."""

    def __init__(self, bus, *, name: str = "live", bus_index: int = 0,
                 bitrate: int = 500_000, on_error=None, on_load=None,
                 on_trigger=None, triggers_getter=None):
        self._bus = bus
        self.name = name
        self.bus_index = bus_index
        self._on_error = on_error
        self._on_load = on_load
        self._on_trigger = on_trigger
        self._triggers_getter = triggers_getter

        self.load_meter = BusLoadMeter(bitrate=bitrate)
        self.health_meter = BusHealthMeter(bitrate=bitrate)

        self._subs: list[Subscription] = []
        self._pending: deque = deque(maxlen=PENDING_LIMIT)
        self._lock = threading.Lock()
        self._tx_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.rx_count = 0
        self.error_frames = 0

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._rx_loop, daemon=True,
                                        name=f"canlab-rx-{self.name}")
        self._thread.start()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the receive thread, close subscriptions, then close the bus."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        with self._lock:
            subs, self._subs = list(self._subs), []
        for s in subs:
            s.closed = True
        try:
            self._bus.shutdown()
        except Exception:
            log.debug("bus shutdown failed", exc_info=True)

    # -- subscriptions ---------------------------------------------------
    def subscribe(self, id_filter=None, maxsize: int = DEFAULT_QUEUE) -> Subscription:
        sub = Subscription(self, id_filter, maxsize)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    # -- transmit --------------------------------------------------------
    def send(self, msg) -> None:
        """Gated transmit, serialised so two workers cannot interleave frames."""
        with self._tx_lock:
            gated_send(self._bus, msg)

    # -- GUI hand-off ----------------------------------------------------
    def drain(self) -> list[dict]:
        """Take the rows captured since the last call (GUI thread)."""
        with self._lock:
            rows = list(self._pending)
            self._pending.clear()
        return rows

    def health_snapshot(self) -> dict:
        return self.health_meter.snapshot()

    # -- receive thread --------------------------------------------------
    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                msg = self._bus.recv(timeout=0.1)
            except Exception as e:
                if not self._stop.is_set():
                    log.exception("bus receive failed")
                    self._report_error(str(e))
                return
            if msg is not None:
                try:
                    self._handle(msg)
                except Exception:
                    log.exception("frame dispatch failed")

    def _handle(self, msg) -> None:
        ts = getattr(msg, "timestamp", None) or time.time()
        data = bytes(msg.data or b"")
        dlc = len(data)
        arb = int(msg.arbitration_id)

        if getattr(msg, "is_error_frame", False):
            self.error_frames += 1
            self.health_meter.add_frame(dlc, ts, "", is_error=True, error_class=arb)
            return

        self.health_meter.add_frame(dlc, ts, normalize_id(arb))
        load = self.load_meter.add_frame(dlc, ts)
        if load is not None and self._on_load is not None:
            self._on_load(load)

        if getattr(msg, "is_remote_frame", False):
            return

        # Triggers are evaluated here, once per frame — the only trigger path.
        if self._on_trigger is not None and self._triggers_getter is not None:
            rules = self._triggers_getter()
            if rules:
                from canlab.core.trigger import check_triggers
                for rule in check_triggers(rules, arb, data):
                    self._on_trigger(rule, msg)

        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            if not sub.closed and sub.matches(arb):
                sub.offer(msg)

        row = make_row(ts, arb, bool(getattr(msg, "is_extended_id", False)),
                       self.bus_index, data[:64])
        with self._lock:
            self._pending.append(row)
            self.rx_count += 1

    def _report_error(self, message: str) -> None:
        if self._on_error is not None:
            try:
                self._on_error(message)
            except Exception:
                log.exception("hub error callback failed")
