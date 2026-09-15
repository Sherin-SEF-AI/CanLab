"""Compact controls, built from what the interface actually repeats.

Each of these replaces a pattern that was copied by hand several times, with
slightly different numbers every time:

``Toolstrip`` was four byte-identical copies of a 28 px bar with an inline
stylesheet, its own margins and its own spacing.

``Section`` replaces QGroupBox used as a static frame. There were 31 of them
and not one could be collapsed, so a tab showed every group whether or not it
had anything in it.

``set_status`` replaces 62 calls that each built a stylesheet string to change
one colour. Beyond the repetition, assigning a stylesheet re-parses it and
invalidates the widget's whole styled subtree; flipping a property and
repolishing does not.

``capped`` replaces 22 calls limiting a widget's height across 13 different
numbers, none of which agreed with any other.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

from canlab.theme import ui_font
from canlab.ui.motion import animate
from canlab.ui.tokens import FONT, HEIGHT, MAX_H, SPACE

# Status names understood by the stylesheet's QLabel[state="..."] rules.
STATUSES = ("ok", "warn", "error", "dim", "info")


def set_status(widget, status: str) -> None:
    """Recolour by state rather than by writing a stylesheet.

    Qt does not re-evaluate property selectors on its own, so the unpolish and
    polish pair is required; polish alone leaves the previous computed style
    cached and nothing changes.
    """
    widget.setProperty("state", status)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class StatusLabel(QLabel):
    """A label that carries a state, for the many status lines in the tabs."""

    def __init__(self, text: str = "", status: str = "dim", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setFont(ui_font(FONT["small"]))
        set_status(self, status)

    def say(self, text: str, status: str = "info") -> None:
        self.setText(text)
        set_status(self, status)


class Toolstrip(QWidget):
    """The thin bar of controls above a table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toolstrip")
        self.setFixedHeight(HEIGHT["bar"])
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(SPACE["sm"], SPACE["xs"], SPACE["sm"], SPACE["xs"])
        self._row.setSpacing(SPACE["md"])

    def add(self, *widgets) -> "Toolstrip":
        for w in widgets:
            if isinstance(w, str):
                label = QLabel(w)
                label.setObjectName("label_dim")
                label.setFont(ui_font(FONT["small"]))
                self._row.addWidget(label)
            else:
                self._row.addWidget(w)
        return self

    def stretch(self) -> "Toolstrip":
        self._row.addStretch(1)
        return self

    def separator(self) -> "Toolstrip":
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setObjectName("ws_sep")
        self._row.addWidget(line)
        return self


class Section(QWidget):
    """A titled group that can be folded away.

    Collapsing is what makes a dense layout survivable: a tab can offer six
    groups without showing six groups at once. The body's maximum height is
    animated rather than its visibility, so the fold reads as a fold.
    """

    def __init__(self, title: str, *, collapsed: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("section")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QToolButton(self)
        self._header.setObjectName("section_header")
        self._header.setText(title)
        self._header.setCheckable(True)
        self._header.setChecked(not collapsed)
        self._header.setFixedHeight(HEIGHT["header"])
        self._header.setFont(ui_font(FONT["small"], bold=True))
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setArrowType(Qt.ArrowType.DownArrow if not collapsed
                                  else Qt.ArrowType.RightArrow)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding,
                                   QSizePolicy.Policy.Fixed)
        self._header.clicked.connect(lambda: self.set_collapsed(not self.collapsed))
        outer.addWidget(self._header)

        self.body = QWidget(self)
        self.body.setObjectName("section_body")
        self._body_lay = QVBoxLayout(self.body)
        self._body_lay.setContentsMargins(SPACE["md"], SPACE["sm"],
                                          SPACE["md"], SPACE["sm"])
        self._body_lay.setSpacing(SPACE["sm"])
        outer.addWidget(self.body)
        if collapsed:
            self.body.setMaximumHeight(0)

    @property
    def collapsed(self) -> bool:
        return not self._header.isChecked()

    def add(self, *widgets) -> "Section":
        for w in widgets:
            self._body_lay.addWidget(w)
        return self

    def add_layout(self, layout) -> "Section":
        self._body_lay.addLayout(layout)
        return self

    def set_collapsed(self, collapsed: bool, *, instant: bool = False) -> None:
        self._header.setChecked(not collapsed)
        self._header.setArrowType(Qt.ArrowType.RightArrow if collapsed
                                  else Qt.ArrowType.DownArrow)
        target = 0 if collapsed else self.body.sizeHint().height()
        animate(self.body, b"maximumHeight", target, dur="base", instant=instant)
        if not collapsed and instant:
            # A fixed cap would stop the body ever growing again.
            self.body.setMaximumHeight(16777215)


def capped(widget, size: str = "md"):
    """Limit a widget's height from the shared scale.

    The heights this replaces were 55, 60, 80, 100, 110, 120, 140, 150, 160,
    180 and 200 px, chosen one at a time.
    """
    widget.setMaximumHeight(MAX_H.get(size, MAX_H["md"]))
    return widget


def compact(widget, width: int | None = None):
    """Stop a control stretching across the window.

    Most of the dead space came from single-line fields given the whole width
    of a 1920 px window because nothing said otherwise.
    """
    widget.setSizePolicy(QSizePolicy.Policy.Fixed if width else QSizePolicy.Policy.Maximum,
                         QSizePolicy.Policy.Fixed)
    if width:
        widget.setFixedWidth(width)
    widget.setFixedHeight(HEIGHT["control"])
    return widget


def primary(button: QPushButton) -> QPushButton:
    button.setObjectName("btn_green")
    return button


def row(*widgets, spacing: int = SPACE["md"], stretch_at_end: bool = True) -> QWidget:
    """A horizontal group that does not stretch its children."""
    holder = QWidget()
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for w in widgets:
        if isinstance(w, str):
            label = QLabel(w)
            label.setObjectName("label_dim")
            label.setFont(ui_font(FONT["small"]))
            lay.addWidget(label)
        else:
            lay.addWidget(w)
    if stretch_at_end:
        lay.addStretch(1)
    return holder
