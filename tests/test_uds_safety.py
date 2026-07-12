"""Tests that the UDS service scan classifies destructive services (C3)."""
import pytest

pytest.importorskip("PyQt6")   # core.uds imports QThread at module load

from core.uds import DESTRUCTIVE_SERVICES


def test_dangerous_services_are_flagged_destructive():
    # These must be skipped unless the user explicitly opts into an unsafe scan.
    for sid in (0x11, 0x14, 0x27, 0x28, 0x2E, 0x2F, 0x31, 0x34, 0x35, 0x36):
        assert sid in DESTRUCTIVE_SERVICES


def test_readonly_services_are_not_flagged():
    for sid in (0x22, 0x19, 0x3E):   # ReadDataByIdentifier, ReadDTC, TesterPresent
        assert sid not in DESTRUCTIVE_SERVICES
