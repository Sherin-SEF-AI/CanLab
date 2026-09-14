"""The design scale: every spacing, radius, height and size in one place.

Before this, the interface was built from numbers chosen one at a time. A
survey of the widget layer found 78 calls setting layout margins across six
different tuples, 73 setting spacing across five values, 22 capping widget
height across thirteen values, and 252 font calls at seven sizes. Nothing was
wrong with any single number; together they meant no two panels agreed on what
a gap was, and there was no way to make the interface denser without editing
several hundred call sites.

A scale fixes that by removing the choice. There is one small gap, one medium
gap, one row height, and if a panel wants something else it is almost always
the panel that is wrong.

The palette is Blender's: layered greys for chrome so that nesting reads as
depth rather than as borders, and blue for selection. The status colours are
deliberately *not* part of that scheme. Green means a bus is connected or a
server is running, red means transmit is armed, and those have to stay legible
as themselves rather than dissolve into the chrome, because they are the
colours that say whether this program can put frames on a wire.
"""
from __future__ import annotations

# ── spacing ──────────────────────────────────────────────────────────────────
# Gaps between things. XS separates items that belong together, SM is the
# default gap inside a panel, MD is between groups, LG between major regions.
SPACE = {"xs": 2, "sm": 4, "md": 6, "lg": 10, "xl": 16}

# ── shape ────────────────────────────────────────────────────────────────────
RADIUS = {"sm": 3, "md": 5, "lg": 8}

# ── vertical rhythm ──────────────────────────────────────────────────────────
# ROW is a table row, CONTROL is an input or button, BAR is a toolstrip.
# Everything vertical should be one of these or a multiple of them.
HEIGHT = {"row": 20, "control": 22, "bar": 26, "header": 18}

# ── type ─────────────────────────────────────────────────────────────────────
# Point sizes. BODY is the default; MICRO is for dense table furniture only.
FONT = {"micro": 7, "small": 8, "body": 9, "head": 10, "title": 12}

# Two families. The proportional one carries chrome (menus, buttons, labels)
# because that is how Blender reads; the monospace one carries data, where
# columns of hex have to line up. Both are stacks: naming a single family is
# what let "Courier New" silently resolve to Liberation Mono on a machine that
# does not have it.
FAMILY_UI = '"DejaVu Sans", "Noto Sans", "Segoe UI", sans-serif'
FAMILY_MONO = '"DejaVu Sans Mono", "Liberation Mono", "Consolas", monospace'

# The first concrete family in each stack, for QFont, which takes one name.
FAMILY_UI_FIRST = "DejaVu Sans"
FAMILY_MONO_FIRST = "DejaVu Sans Mono"

# ── palette ──────────────────────────────────────────────────────────────────
# Chrome: Blender's layered greys. Each step is a surface sitting on the one
# before it, so depth comes from value rather than from drawing more lines.
CHROME = {
    "window":   "#1d1d1d",   # the application behind everything
    "editor":   "#262626",   # a tab page
    "panel":    "#303030",   # a panel or group inside a page
    "raised":   "#3d3d3d",   # a button, a header, anything that lifts
    "input":    "#1a1a1a",   # a field you can type in, recessed
    "border":   "#3a3a3a",
    "border_strong": "#4a4a4a",
    "hover":    "#4a4a4a",
    "select":   "#4772b3",   # Blender's selection blue
    "active":   "#5680c2",   # the brighter step, for focus
    "text":     "#e5e5e5",
    "text_dim": "#9a9a9a",
    "dim":      "#6b6b6b",
}

# Status: these mean something and do not follow the chrome.
STATUS = {
    "green":  "#00ff88",   # connected, running, ok
    "amber":  "#ffb300",   # warning, pending
    "error":  "#ff3333",   # armed to transmit, failed
    "accent": "#4772b3",   # kept as a name: it now points at the selection blue
}

# Syntax highlighting for the generated code view. These are read by
# code_gen_tab.py and are not decoration.
SYNTAX = {
    "keyword":  "#6a9ee8",
    "string":   "#c9a26d",
    "comment":  "#6b6b6b",
    "function": "#5bc8a0",
}


def px(value: int) -> str:
    return f"{value}px"


def pt(value: int) -> str:
    return f"{value}pt"
