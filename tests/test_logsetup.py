"""Logging setup and the unhandled-exception hook."""
import logging
import sys

import pytest

from canlab import logging_config as lc


@pytest.fixture
def fresh_logging(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(lc, "LOG_FILE", tmp_path / "logs" / "canlab.log")
    monkeypatch.setattr(lc, "_configured", False)
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for h in root.handlers[:]:
        if h not in before:
            root.removeHandler(h)
            h.close()


def test_configure_creates_file_and_is_idempotent(fresh_logging):
    path = lc.configure_logging("INFO")
    assert lc.configure_logging("INFO") == path
    logging.getLogger("canlab.test").info("hello file")
    for h in logging.getLogger().handlers:
        h.flush()
    assert path.exists()
    assert "hello file" in path.read_text()


def test_excepthook_logs_and_notifies(fresh_logging, monkeypatch, caplog):
    seen = []
    monkeypatch.setattr(lc, "_listeners", [])
    lc.add_exception_listener(lambda t, e, tb: seen.append((t, str(e))))
    old = sys.excepthook
    try:
        lc.install_excepthook()
        with caplog.at_level(logging.CRITICAL, logger="canlab"):
            try:
                raise ValueError("boom")
            except ValueError:
                sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = old
    assert seen == [(ValueError, "boom")]
    assert "Unhandled exception" in caplog.text
