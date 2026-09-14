"""Workspaces: the sixteen tabs grouped by what you are doing.

Sixteen labels in one row do not fit, so the bar scrolled and you navigated a
tool with 49 panes through two arrows. Blender's answer is workspaces, and
this is that: the five groups, then the tabs of the active group, in one row.

One row rather than two because the height is not free. Two rows put the
window's minimum at 919 px against a 900 px ceiling; measured, the pages alone
need 748 and the surrounding chrome 71, so the bar's whole budget is about
thirty pixels. It is also what Blender does, whose workspaces live in the top
bar beside everything else.

The tab widget itself is untouched. This drives ``QTabWidget.setCurrentIndex``
from outside and listens to ``currentChanged`` to follow, so Alt+N, Ctrl+Tab,
Ctrl+F, the View menu, the demo recorders and the acceptance scripts all keep
working exactly as before and the bar simply reflects them. Deliberately not a
QTabWidget subclass: the smoke test reaches the DIAGNOSTICS sub-tabs with
``findChild(type(window.tabs))``, which would stop matching a plain
``QTabWidget`` if this were one.

Selecting a tab in another workspace switches workspace to follow it. That is
the normal case rather than an edge case, because Alt+5 is AI ENGINE in DETECT
while Alt+1 is FRAMES in CAPTURE.
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)

from canlab.theme import COLORS, ui_font
from canlab.ui.motion import animate
from canlab.ui.tokens import FONT, HEIGHT, SPACE

# Grouped by the first word of the tab label, the same key the demo recorders
# use, so the two can never disagree about what a tab is called.
WORKSPACES: list[tuple[str, tuple[str, ...]]] = [
    ("CAPTURE", ("FRAMES", "SNIFFER", "SIGNALS")),
    ("EXPLORE", ("PLOT", "DASHBOARD", "TIMELINE", "INTELLIGENCE")),
    ("DETECT",  ("AUTO-RE", "ML", "AI")),
    ("DEFINE",  ("DBC", "CODE")),
    ("BUS",     ("INJECTION", "DIAGNOSTICS", "OBD-II", "GATEWAY")),
]


def _first_word(label: str) -> str:
    return label.split()[0] if label.split() else label


class _Underline(QWidget):
    """The moving mark under the active button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(2)
        # A plain QWidget subclass ignores a stylesheet background unless it
        # is told to draw one. Without this the mark has correct geometry,
        # reports itself visible, and paints nothing at all.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"background:{COLORS['active']}; border:none;")
        self.hide()

    def move_to(self, button: QWidget, *, instant: bool = False) -> None:
        target = QRect(button.x(), button.parent().height() - 2,
                       button.width(), 2)
        if not self.isVisible():
            self.setGeometry(target)
            self.show()
            self.raise_()
            return
        animate(self, b"geometry", target, dur="base", instant=instant)
        self.raise_()


class _Strip(QScrollArea):
    """A row of buttons that scrolls rather than widening the window.

    Without this the BUS row alone would add its full button width to the
    window's minimum, which is the floor the whole layout is measured against.
    """

    def __init__(self, height: int, parent=None):
        super().__init__(parent)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setFixedHeight(height)
        self.setMinimumWidth(0)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.inner = QWidget()
        self.inner.setObjectName("strip_inner")
        self.row = QHBoxLayout(self.inner)
        self.row.setContentsMargins(SPACE["sm"], 0, SPACE["sm"], 0)
        self.row.setSpacing(SPACE["xs"])
        self.setWidget(self.inner)
        self.underline = _Underline(self.inner)

    def clear(self) -> None:
        while self.row.count():
            item = self.row.takeAt(0)
            w = item.widget()
            if w is not None and w is not self.underline:
                w.setParent(None)
                w.deleteLater()

    def minimumSizeHint(self) -> QSize:      # noqa: N802 - Qt override
        return QSize(0, self.height())


class WorkspaceBar(QWidget):
    """One row: the workspaces, then the tabs of the active one."""

    workspace_changed = pyqtSignal(str)

    def __init__(self, tabs, parent=None):
        super().__init__(parent)
        self._tabs = tabs
        self._syncing = False
        self._active_ws = 0
        self._ws_buttons: list[QToolButton] = []
        self._tab_buttons: dict[int, QToolButton] = {}
        # Explicit groups, not setAutoExclusive: auto-exclusion groups by
        # parent widget, and now that both kinds of button share this bar as
        # their parent it would make a workspace and a tab mutually exclusive,
        # so choosing a tab would clear its own workspace.
        self._ws_group = QButtonGroup(self)
        self._ws_group.setExclusive(True)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)

        self.setObjectName("workspace_bar")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._strip = _Strip(HEIGHT["bar"], self)
        lay.addWidget(self._strip)
        self.setFixedHeight(HEIGHT["bar"])
        # Two marks in one row: one under the active workspace, one under the
        # active tab. They are siblings so each animates independently.
        self._ws_mark = self._strip.underline
        self._tab_mark = _Underline(self._strip.inner)

        self._index_of_ws = self._map_tabs()
        self._build_workspace_row()
        self._show_workspace(0, instant=True)

    # -- mapping ---------------------------------------------------------------
    def _map_tabs(self) -> dict[int, int]:
        """{tab index: workspace index}. Raises if a tab has no home, because
        a silently unmapped tab would be unreachable from the bar."""
        by_word: dict[str, int] = {}
        for ws_index, (_name, words) in enumerate(WORKSPACES):
            for word in words:
                by_word[word] = ws_index
        mapping: dict[int, int] = {}
        orphans = []
        for i in range(self._tabs.count()):
            word = _first_word(self._tabs.tabText(i))
            if word in by_word:
                mapping[i] = by_word[word]
            else:
                orphans.append(self._tabs.tabText(i))
        if orphans:
            raise ValueError(f"tabs with no workspace: {orphans}")
        return mapping

    def tabs_in(self, ws_index: int) -> list[int]:
        return [i for i, ws in sorted(self._index_of_ws.items()) if ws == ws_index]

    # -- building --------------------------------------------------------------
    def _make_button(self, text: str, *, strong: bool) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setFont(ui_font(FONT["small"], bold=strong))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        b.setObjectName("ws_button" if strong else "ws_tab_button")
        return b

    def _build_workspace_row(self) -> None:
        for ws_index, (name, _words) in enumerate(WORKSPACES):
            b = self._make_button(name, strong=True)
            b.clicked.connect(lambda _c=False, w=ws_index: self._on_workspace_clicked(w))
            self._ws_buttons.append(b)
            self._ws_group.addButton(b)
            self._strip.row.addWidget(b)
        self._sep = QFrame(self._strip.inner)
        self._sep.setFrameShape(QFrame.Shape.VLine)
        self._sep.setObjectName("ws_sep")
        self._strip.row.addWidget(self._sep)
        # Tabs are appended after the separator and rebuilt per workspace, so
        # the trailing stretch has to be added after them, not now.
        self._tail = self._strip.row.count()

    def _build_tab_row(self, ws_index: int) -> None:
        for _i, button in list(self._tab_buttons.items()):
            self._tab_group.removeButton(button)
            self._strip.row.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        self._tab_buttons.clear()
        if self._strip.row.count() > self._tail:
            item = self._strip.row.takeAt(self._strip.row.count() - 1)
            del item                       # the old trailing stretch
        for i in self.tabs_in(ws_index):
            label = self._tabs.tabText(i).replace("★", "").strip()
            b = self._make_button(label, strong=False)
            b.setToolTip(self._tabs.tabToolTip(i) or label)
            b.clicked.connect(lambda _c=False, index=i: self._tabs.setCurrentIndex(index))
            self._tab_buttons[i] = b
            self._tab_group.addButton(b)
            self._strip.row.addWidget(b)
        self._strip.row.addStretch(1)
        self._strip.row.activate()

    # -- selection -------------------------------------------------------------
    def _on_workspace_clicked(self, ws_index: int) -> None:
        """Clicking a workspace opens its first tab; everything else follows
        from currentChanged."""
        wanted = self.tabs_in(ws_index)
        if wanted:
            self._tabs.setCurrentIndex(wanted[0])

    def _show_workspace(self, ws_index: int, *, instant: bool = False) -> None:
        self._active_ws = ws_index
        self._ws_buttons[ws_index].setChecked(True)
        self._build_tab_row(ws_index)
        self._ws_mark.move_to(self._ws_buttons[ws_index], instant=instant)
        self.workspace_changed.emit(WORKSPACES[ws_index][0])

    def select_index(self, index: int, *, instant: bool = False) -> None:
        """Follow the tab widget. Switches workspace when the tab lives in
        another one, which is the ordinary case for Alt+N and Ctrl+Tab."""
        if self._syncing or index < 0:
            return
        ws = self._index_of_ws.get(index)
        if ws is None:
            return
        self._syncing = True
        rebuilt = ws != self._active_ws or not self._tab_buttons
        try:
            if rebuilt:
                self._show_workspace(ws, instant=instant)
            else:
                # Reposition even when the workspace is unchanged: the first
                # placement happens during construction, before the strip has
                # been laid out, so without this the mark stays where it was
                # put when every button still measured zero.
                self._ws_mark.move_to(self._ws_buttons[ws], instant=instant)
            button = self._tab_buttons.get(index)
            if button is not None:
                button.setChecked(True)
                self._strip.ensureWidgetVisible(button)
            if rebuilt:
                # The row was just recreated. Even after activating the
                # layout, buttons created inside this call still report the
                # default 100x30 geometry, so measuring them now parks the
                # mark at the far left. One turn of the event loop is what it
                # takes for the real geometry to exist.
                QTimer.singleShot(0, lambda i=index: self._place_tab_mark(i, True))
            else:
                self._place_tab_mark(index, instant)
        finally:
            self._syncing = False

    def _place_tab_mark(self, index: int, instant: bool) -> None:
        button = self._tab_buttons.get(index)
        if button is not None and button.width() > 1:
            self._tab_mark.move_to(button, instant=instant)

    # -- workspace cycling, for the View menu ----------------------------------
    def step_workspace(self, delta: int) -> None:
        target = (self._active_ws + delta) % len(WORKSPACES)
        self._on_workspace_clicked(target)

    def showEvent(self, event):               # noqa: N802 - Qt override
        super().showEvent(event)
        # Geometry is only real once shown; place the marks without animating.
        self.select_index(self._tabs.currentIndex(), instant=True)

    def resizeEvent(self, event):             # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.select_index(self._tabs.currentIndex(), instant=True)
