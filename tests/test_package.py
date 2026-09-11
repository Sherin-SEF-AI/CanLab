"""Package layout: importable, versioned, entry points registered."""
import importlib.metadata as md

import canlab


def test_version_present():
    assert canlab.__version__


def test_main_importable():
    from canlab.main import main
    assert callable(main)


def test_console_scripts_registered():
    names = {e.name for e in md.entry_points(group="console_scripts")}
    assert {"canlab", "canlab-mcp"} <= names
