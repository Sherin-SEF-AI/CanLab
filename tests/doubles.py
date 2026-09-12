"""Shared test doubles: a recording CAN bus with a scripted receive queue."""
from __future__ import annotations

import queue


class FakeMsg:
    def __init__(self, arbitration_id, data, is_extended_id=False, timestamp=0.0):
        self.arbitration_id = arbitration_id
        self.data = bytes(data)
        self.dlc = len(self.data)
        self.is_extended_id = is_extended_id
        self.timestamp = timestamp
        self.is_error_frame = False
        self.is_remote_frame = False
        self.channel = "fake0"


class RecordingBus:
    """Records every ``send`` and serves ``recv`` from a queue (None on timeout).

    ``on_send`` (optional) is called with each sent message and may return a
    reply message (or a list of them) to queue for the next ``recv``.
    """

    def __init__(self, on_send=None):
        self.sent: list = []
        self._rx: "queue.Queue" = queue.Queue()
        self._on_send = on_send
        self.shutdown_calls = 0

    def send(self, msg):
        self.sent.append(msg)
        if self._on_send is not None:
            reply = self._on_send(msg)
            if reply is not None:
                for m in (reply if isinstance(reply, (list, tuple)) else [reply]):
                    self._rx.put(m)

    def feed(self, *msgs):
        for m in msgs:
            self._rx.put(m)

    def recv(self, timeout=0.1):
        try:
            return self._rx.get(timeout=timeout if timeout else 0.001)
        except queue.Empty:
            return None

    def shutdown(self):
        self.shutdown_calls += 1
