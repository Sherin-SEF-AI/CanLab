"""Global bus-transmit safety gate.

Every worker that writes frames onto the bus (injection, fuzzing, replay,
gateway) must call :func:`require_armed` before each send. Transmit is
*disarmed by default*; the user has to explicitly arm it (the ARM TX toolbar
toggle) before any frame can leave the tool. This is a last-line guard against
accidentally flooding a live vehicle bus.
"""
import threading

_lock = threading.Lock()
_armed = False


class BusNotArmedError(RuntimeError):
    """Raised when a transmit is attempted while the bus TX gate is disarmed."""


def is_armed() -> bool:
    with _lock:
        return _armed


def set_armed(value: bool) -> None:
    global _armed
    with _lock:
        _armed = bool(value)


def require_armed() -> None:
    """Raise BusNotArmedError unless TX has been explicitly armed."""
    if not is_armed():
        raise BusNotArmedError(
            "Bus transmit is disarmed. Enable 'ARM TX' before injecting, "
            "replaying, fuzzing, or bridging frames."
        )
