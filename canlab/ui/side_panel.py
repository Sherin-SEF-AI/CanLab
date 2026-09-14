"""The container that makes a side panel resizable and collapsible.

The two side panels were never draggable. They sat in a plain QHBoxLayout
with a minimum and a maximum width, and the ``resize(220, ...)`` calls in
their constructors did nothing at all, because a widget in a layout takes its
width from the layout rather than from a prior resize. The comment in
``id_panel.py`` saying "resizable rather than fixed" described an intention
the layout never delivered.

``PanelHost`` wraps a panel so a QSplitter can own its width. It exists for
one specific reason: a splitter clamps a pane to its child's ``minimumWidth``,
so animating a 220 px panel down to zero would jam at 150 and then snap. The
host carries no minimum of its own, which lets the width pass smoothly through
to zero, while the panel keeps its maximum so dragging still has a sane limit.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QVBoxLayout, QWidget


class PanelHost(QWidget):
    """Holds one panel and lets the splitter take it to any width."""

    def __init__(self, panel: QWidget, parent=None):
        super().__init__(parent)
        self.panel = panel
        self.setMinimumWidth(0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(panel)
        # The panel's own floor would otherwise become the host's floor and
        # the collapse would stop short; its ceiling is kept so a drag still
        # has a limit.
        panel.setMinimumWidth(0)

    @property
    def preferred(self) -> int:
        hint = self.panel.sizeHint().width()
        return max(160, min(hint, self.panel.maximumWidth()))
