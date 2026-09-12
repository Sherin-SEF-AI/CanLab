"""Process-wide logging setup and the unhandled-exception hook (Qt-free).

``configure_logging`` writes INFO+ to a rotating file under ``~/.canlab/logs``
and WARNING+ to stderr. ``install_excepthook`` routes uncaught exceptions on
any thread through the log; without it PyQt6 aborts the whole process when a
slot raises. GUI code can register a listener (see ``canlab.ui.error_dialog``)
to surface those exceptions to the user.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
from pathlib import Path

LOG_DIR = Path.home() / ".canlab" / "logs"
LOG_FILE = LOG_DIR / "canlab.log"

_configured = False
_listeners: list = []


def configure_logging(level: str | int | None = None) -> Path:
    """Attach the file + stderr handlers once. Returns the log file path."""
    global _configured
    if _configured:
        return LOG_FILE
    if level is None:
        level = os.environ.get("CANLAB_LOG_LEVEL", "INFO")
    root = logging.getLogger()
    root.setLevel(level.upper() if isinstance(level, str) else level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:  # unwritable home: stderr only
        sys.stderr.write(f"canlab: cannot open log file {LOG_FILE}: {e}\n")
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    _configured = True
    return LOG_FILE


def add_exception_listener(callback) -> None:
    """Register ``callback(exc_type, exc, tb)`` to be told about uncaught exceptions."""
    _listeners.append(callback)


def remove_exception_listener(callback) -> None:
    if callback in _listeners:
        _listeners.remove(callback)


def _handle(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger("canlab").critical("Unhandled exception", exc_info=(exc_type, exc, tb))
    for cb in list(_listeners):
        try:
            cb(exc_type, exc, tb)
        except Exception:
            logging.getLogger("canlab").exception("exception listener failed")


def install_excepthook() -> None:
    """Route uncaught exceptions from the main thread and worker threads to the log."""
    sys.excepthook = _handle

    def _thread_hook(args):
        _handle(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_hook
