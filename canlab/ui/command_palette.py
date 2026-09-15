"""Type what you want instead of finding where it lives.

The application has 16 tabs holding 33 sub-tabs, 49 panes in all, plus five
menus. Even grouped into workspaces that is a lot of places for a thing to be,
and the honest answer for a tool this size is Blender's: a search box.

It indexes two things, both discovered at open time rather than registered by
hand, so nothing has to be kept in step as tabs and actions change:

- every QAction reachable from the window or its menus, with its shortcut;
- every tab and sub-tab, by the path you would read aloud.

Matching is subsequence-based, so "trim" finds "Tools > Trim capture" and
"sniff" finds "Go to SNIFFER". Results are ranked so that a run of consecutive
characters and a match at the start of a word beat the same letters scattered
through the middle of a longer sentence.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QLineEdit, QListWidget, QListWidgetItem, QMenu, QTabWidget, QVBoxLayout,
)

from canlab.theme import ui_font
from canlab.ui.tokens import FONT, SPACE


def score(query: str, text: str) -> int | None:
    """How well `text` matches `query`, or None if it does not.

    Higher is better. Consecutive characters and matches at a word boundary
    score above the same letters scattered through the string, so "export"
    ranks "Export DBC" above "Reference-driven export of a signal".
    """
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    total = 0
    pos = 0
    streak = 0
    for ch in q:
        found = t.find(ch, pos)
        if found < 0:
            return None
        if found == pos and pos > 0:
            streak += 1
            total += 6 + streak          # consecutive characters
        else:
            streak = 0
            total += 1
        if found == 0 or t[found - 1] in " >_-/":
            total += 8                    # start of a word
        pos = found + 1
    # Prefer shorter targets: an exact-ish hit should beat a long sentence
    # that happens to contain the same letters.
    return total * 100 - len(text)


class Command:
    __slots__ = ("label", "hint", "run")

    def __init__(self, label: str, hint: str, run):
        self.label = label
        self.hint = hint
        self.run = run


def collect(window) -> list[Command]:
    """Everything the palette can reach, found by walking the real window."""
    commands: list[Command] = []
    seen_actions = set()

    for holder in [window] + window.menuBar().findChildren(QMenu):
        menu_name = ""
        if isinstance(holder, QMenu):
            menu_name = holder.title().replace("&", "")
        for action in holder.actions():
            if action.isSeparator() or action.menu() is not None:
                continue
            text = action.text().replace("&", "").strip()
            if not text or id(action) in seen_actions:
                continue
            seen_actions.add(id(action))
            label = f"{menu_name} > {text}" if menu_name else text
            shortcut = action.shortcut().toString()
            commands.append(Command(label, shortcut, action.trigger))

    tabs = window.tabs
    for i in range(tabs.count()):
        name = tabs.tabText(i).strip()
        commands.append(Command(
            f"Go to {name}", "tab",
            lambda index=i: tabs.setCurrentIndex(index)))
        page = tabs.widget(i)
        inner = page.findChild(QTabWidget) if page is not None else None
        if inner is None:
            continue
        for j in range(inner.count()):
            sub = inner.tabText(j).strip()
            commands.append(Command(
                f"Go to {name} > {sub}", "pane",
                lambda index=i, jndex=j, box=inner: (
                    tabs.setCurrentIndex(index), box.setCurrentIndex(jndex))))
    return commands


class CommandPalette(QDialog):
    """A search box over every action and pane in the window."""

    MAX_RESULTS = 40

    def __init__(self, window):
        super().__init__(window)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setModal(True)
        self.setObjectName("command_palette")
        self.setMinimumWidth(560)

        self._commands = collect(window)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        lay.setSpacing(SPACE["sm"])

        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Go to a tab, or run a command…")
        self.query.setFont(ui_font(FONT["head"]))
        self.query.textChanged.connect(self._refilter)
        lay.addWidget(self.query)

        self.results = QListWidget(self)
        self.results.setFont(ui_font(FONT["body"]))
        self.results.setUniformItemSizes(True)
        self.results.itemActivated.connect(lambda _i: self._accept())
        lay.addWidget(self.results)

        self._refilter("")
        # Typing goes to the box; Up/Down and Enter reach the list through
        # keyPressEvent below, so the hands never leave the keyboard.
        self.query.setFocus()

    def _refilter(self, text: str) -> None:
        text = text.strip()
        scored = []
        for command in self._commands:
            value = score(text, command.label)
            if value is not None:
                scored.append((value, command))
        scored.sort(key=lambda pair: -pair[0])
        self.results.clear()
        for _value, command in scored[:self.MAX_RESULTS]:
            item = QListWidgetItem(
                f"{command.label}    {command.hint}" if command.hint else command.label)
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _accept(self) -> None:
        item = self.results.currentItem()
        self.accept()
        if item is not None:
            command = item.data(Qt.ItemDataRole.UserRole)
            if command is not None:
                command.run()

    def keyPressEvent(self, event):        # noqa: N802 - Qt override
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept()
            return
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            row = self.results.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
            if 0 <= row < self.results.count():
                self.results.setCurrentRow(row)
            return
        super().keyPressEvent(event)


def open_palette(window) -> None:
    palette = CommandPalette(window)
    palette.exec()
    palette.deleteLater()
