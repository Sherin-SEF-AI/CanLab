"""Import AUTOSAR ARXML signal definitions.

This used to hand-parse a private dialect — the one CanLab's own exporter
produced — so a real AUTOSAR file from a supplier imported as nothing. cantools
already implements the AUTOSAR 3 and 4 system loaders, so use them and convert
the result to CanLab's signal dicts, exactly as the DBC importer does.
"""
from __future__ import annotations

import logging

import cantools

from canlab.core.dbc_manager import _message_to_signal_dicts

log = logging.getLogger(__name__)


def parse_arxml(filepath: str) -> list[dict]:
    """Load an ARXML file and return signal dicts.

    Raises ValueError with the underlying reason when the file is not a
    loadable AUTOSAR system description.
    """
    try:
        db = cantools.database.load_file(filepath, database_format="arxml")
    except Exception as e:
        raise ValueError(f"Could not read ARXML {filepath}: {e}") from e
    signals = _message_to_signal_dicts(db)
    if not signals:
        log.info("ARXML %s contained no CAN messages", filepath)
    return signals


def parse_arxml_string(text: str) -> list[dict]:
    """Same as :func:`parse_arxml` for an in-memory document."""
    try:
        db = cantools.database.load_string(text, database_format="arxml")
    except Exception as e:
        raise ValueError(f"Could not read ARXML: {e}") from e
    return _message_to_signal_dicts(db)
