"""Global bus-transmit safety gate.

Nothing leaves CanLab until the user explicitly arms transmit (the ARM TX
toolbar toggle); transmit is *disarmed by default*. Every path that puts a
frame on a CAN bus or a byte on a DoIP socket goes through :func:`gated_send`
or :func:`require_armed`, which raise unless TX is armed. This is the
last-line guard against flooding a live vehicle bus.

Two more mechanisms build on the flag:

* an **observer** list (:func:`add_observer`) so the UI can react to arm/disarm;
* a **TX-worker registry** so disarming actively *stops* any running fuzz /
  replay / gateway / scan loop instead of merely blocking its next send.

A per-session **blocked-ID** set lets the user mark arbitration IDs that must
never be transmitted (e.g. known braking/steering IDs); :func:`require_tx_allowed`
rejects them even while armed.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_lock = threading.RLock()
_armed = False
_blocked_ids: set[int] = set()
_observers: list = []
_workers: list = []


class BusNotArmedError(RuntimeError):
    """Raised when a transmit is attempted while the bus TX gate is disarmed."""


class BlockedIdError(RuntimeError):
    """Raised when a transmit targets an arbitration ID on the blocked list."""


# ── arm state ────────────────────────────────────────────────────────────────

def is_armed() -> bool:
    with _lock:
        return _armed


def set_armed(value: bool) -> None:
    """Arm or disarm transmit. Disarming stops every registered TX worker first,
    then notifies observers. Observers run outside the lock so a worker that is
    blocked in :func:`is_armed` cannot deadlock the caller."""
    global _armed
    value = bool(value)
    with _lock:
        changed = value != _armed
        _armed = value
    if not value:
        stop_all_tx_workers()
    if changed:
        for cb in list(_observers):
            try:
                cb(value)
            except Exception:
                log.exception("arm-state observer failed")


def require_armed() -> None:
    """Raise :class:`BusNotArmedError` unless TX has been explicitly armed."""
    if not is_armed():
        raise BusNotArmedError(
            "Bus transmit is disarmed. Enable 'ARM TX' before injecting, "
            "replaying, fuzzing, bridging, or sending diagnostic requests."
        )


# ── blocked IDs ──────────────────────────────────────────────────────────────

def set_blocked_ids(ids) -> None:
    global _blocked_ids
    with _lock:
        _blocked_ids = {int(i) for i in ids}


def blocked_ids() -> set[int]:
    with _lock:
        return set(_blocked_ids)


def require_tx_allowed(arb_id=None) -> None:
    """Gate a single transmit: armed, and (if ``arb_id`` given) not blocked."""
    require_armed()
    if arb_id is not None:
        with _lock:
            blocked = int(arb_id) in _blocked_ids
        if blocked:
            raise BlockedIdError(
                f"Arbitration ID 0x{int(arb_id):X} is on the blocked-ID list; "
                "remove it in Settings before transmitting to it."
            )


def gated_send(bus, msg) -> None:
    """The single choke point for CAN transmit: check the gate, then send."""
    require_tx_allowed(getattr(msg, "arbitration_id", None))
    bus.send(msg)


# ── observers ────────────────────────────────────────────────────────────────

def add_observer(callback) -> None:
    """Register ``callback(armed: bool)``, called after each arm-state change."""
    if callback not in _observers:
        _observers.append(callback)


def remove_observer(callback) -> None:
    if callback in _observers:
        _observers.remove(callback)


# ── TX-worker registry ───────────────────────────────────────────────────────

def register_tx_worker(worker) -> None:
    """Track a running transmit worker so disarm can stop it."""
    with _lock:
        if worker not in _workers:
            _workers.append(worker)


def unregister_tx_worker(worker) -> None:
    with _lock:
        if worker in _workers:
            _workers.remove(worker)


def stop_all_tx_workers() -> None:
    """Stop and join every registered TX worker (best-effort)."""
    with _lock:
        workers = list(_workers)
        _workers.clear()
    for w in workers:
        try:
            w.stop()
        except Exception:
            log.exception("failed to stop TX worker %r", w)
