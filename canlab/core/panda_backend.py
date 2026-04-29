"""
comma.ai Panda hardware backend.

Wraps the `panda` library to expose the same recv/send/shutdown API that
python-can's Bus provides, so LiveCANWorker can use it transparently.

Gracefully disabled if panda is not installed.
"""
import time
from typing import Optional

_PANDA_AVAILABLE = False
try:
    import panda as _panda_lib  # noqa: F401
    _PANDA_AVAILABLE = True
except ImportError:
    pass


def is_available() -> bool:
    return _PANDA_AVAILABLE


class _FakeMessage:
    """Minimal stand-in for can.Message so existing frame-routing code works."""
    def __init__(self, arbitration_id: int, data: bytes, timestamp: float,
                 is_extended_id: bool = False):
        self.arbitration_id = arbitration_id
        self.data           = data
        self.dlc            = len(data)
        self.timestamp      = timestamp
        self.is_extended_id = is_extended_id
        self.is_fd          = False


class PandaBus:
    """
    Wraps a comma.ai Panda device to mimic the python-can Bus API.

    Usage::
        bus = PandaBus(bus_index=0, bitrate=500000)
        msg = bus.recv(timeout=0.1)   # returns _FakeMessage or None
        bus.send(can_msg)
        bus.shutdown()
    """

    def __init__(self, bus_index: int = 0, bitrate: int = 500_000,
                 safety_model: str = "SAFETY_NOOUTPUT"):
        if not _PANDA_AVAILABLE:
            raise RuntimeError(
                "panda library not installed. "
                "Run: pip install panda --break-system-packages"
            )
        import panda
        self._p         = panda.Panda()
        self._bus_index = bus_index

        # Set bitrate
        self._p.set_can_speed_kbps(bus_index, bitrate // 1000)

        # Safety
        safety_map = {
            "SAFETY_NOOUTPUT":   panda.Panda.SAFETY_NOOUTPUT,
            "SAFETY_ALLOUTPUT":  panda.Panda.SAFETY_ALLOUTPUT,
            "SAFETY_ELM327":     panda.Panda.SAFETY_ELM327,
        }
        sm = safety_map.get(safety_model, panda.Panda.SAFETY_NOOUTPUT)
        self._p.set_safety_mode(sm)

        self._running = True
        self._rx_buf  = []

    def recv(self, timeout: float = 0.1) -> Optional[_FakeMessage]:
        """Block up to `timeout` seconds and return first available message."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msgs = self._p.can_recv()
            for addr, _, dat, src in msgs:
                if src == self._bus_index:
                    return _FakeMessage(
                        arbitration_id=addr,
                        data=bytes(dat),
                        timestamp=time.time(),
                    )
            time.sleep(0.005)
        return None

    def send(self, msg) -> None:
        """Send a message (accepts python-can Message or _FakeMessage)."""
        arb_id = msg.arbitration_id
        data   = bytes(msg.data)
        self._p.can_send(arb_id, data, self._bus_index)

    def shutdown(self):
        try:
            self._p.close()
        except Exception:
            pass

    # ── python-can compatibility shims ────────────────────────────────────────

    def set_filters(self, filters):
        pass

    def flush_tx_buffer(self):
        pass
