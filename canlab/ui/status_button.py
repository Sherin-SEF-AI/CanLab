"""One button that says what is connected, and what it is connected to.

Before this, the answer was spread across the window: a dot and a label in the
status bar for the bus, two tinted toolbar buttons for the servers, and the
port only in a status message that had already scrolled away. Someone who has
just plugged in an adapter and started an MCP server wants one place that says
both are up, and says it plainly enough to be believed.

The button shows a dot per service and the count that are up. Clicking it
opens a panel with a line for each: what it is, whether it is connected, and
the detail that matters (the channel and bitrate, the URL to paste into an
assistant, how many frames a second are arriving). The URL can be copied from
there, which is the thing people actually need next.

A service is green only when it is genuinely up. "Connected" here means the
bus is open and the hub is running, or the server is bound and listening, not
that a button was pressed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout,
    QWidgetAction,
)

from canlab.theme import COLORS, dot_icon, mono_font
from canlab.ui.tokens import FONT, RADIUS, SPACE


@dataclass
class Service:
    """One thing that can be connected, as the button understands it."""

    key: str
    label: str                      # what it is called on screen
    up: bool = False
    detail: str = ""                # channel and bitrate, or a URL
    extra: dict = field(default_factory=dict)
    copyable: str = ""              # a URL worth putting on the clipboard


class ConnectionStatus(QToolButton):
    """A toolbar button summarising every connection, with a panel behind it."""

    ORDER = ("bus", "mcp", "rest")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("connection_status")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setFont(mono_font(FONT["small"]))
        self._services: dict[str, Service] = {
            "bus": Service("bus", "CAN bus"),
            "mcp": Service("mcp", "MCP server"),
            "rest": Service("rest", "REST API"),
        }
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._menu.aboutToShow.connect(self._rebuild)
        self._refresh()

    # ── what the window tells it ─────────────────────────────────────────────

    def set_service(self, key: str, up: bool, detail: str = "", *,
                    copyable: str = "", **extra) -> None:
        service = self._services.get(key)
        if service is None:
            service = self._services[key] = Service(key, key.upper())
        service.up, service.detail, service.copyable = bool(up), detail, copyable
        service.extra.update(extra)
        self._refresh()

    def service(self, key: str) -> Service:
        return self._services[key]

    def connected(self) -> list[str]:
        return [k for k in self.ORDER if self._services[k].up]

    # ── the button itself ────────────────────────────────────────────────────

    def _refresh(self) -> None:
        up = self.connected()
        # The dot is the state at a glance; the text says how many, because a
        # row of dots alone reads as decoration.
        if up:
            self.setIcon(dot_icon(COLORS["green"]))
            names = " ".join(self._short(k) for k in up)
            self.setText(f" {names}")
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        else:
            self.setIcon(dot_icon(COLORS["dim"]))
            self.setText(" nothing connected")
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setToolTip(self._tooltip())
        self._tint(bool(up))

    @staticmethod
    def _short(key: str) -> str:
        return {"bus": "BUS", "mcp": "MCP", "rest": "REST"}.get(key, key.upper())

    def _tooltip(self) -> str:
        lines = []
        for key in self.ORDER:
            s = self._services[key]
            mark = "connected" if s.up else "not connected"
            lines.append(f"{s.label}: {mark}" + (f"  {s.detail}" if s.detail else ""))
        lines.append("")
        lines.append("Click for details.")
        return "\n".join(lines)

    def _tint(self, on: bool) -> None:
        if not on:
            self.setStyleSheet("")
            return
        green = COLORS["green"]
        self.setStyleSheet(
            f"QToolButton {{ color:{green}; background:rgba(0,255,136,28);"
            f" border:1px solid rgba(0,255,136,110);"
            f" border-radius:{RADIUS['sm']}px; padding:2px 8px; }}"
            f"QToolButton::menu-indicator {{ width:0px; }}"
            f"QToolButton:hover {{ background:rgba(0,255,136,55); }}")

    # ── the panel ────────────────────────────────────────────────────────────

    def _rebuild(self) -> None:
        self._menu.clear()
        panel = QFrame()
        panel.setObjectName("status_panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(SPACE["lg"], SPACE["md"], SPACE["lg"], SPACE["md"])
        lay.setSpacing(SPACE["sm"])

        title = QLabel("CONNECTIONS")
        title.setFont(mono_font(FONT["small"]))
        title.setStyleSheet(f"color:{COLORS['dim']};")
        lay.addWidget(title)

        for key in self.ORDER:
            lay.addWidget(self._row(self._services[key]))

        action = QWidgetAction(self._menu)
        action.setDefaultWidget(panel)
        self._menu.addAction(action)

    def _row(self, service: Service) -> QFrame:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.NoFrame)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE["md"])

        dot = QLabel()
        dot.setPixmap(dot_icon(COLORS["green"] if service.up
                               else COLORS["dim"]).pixmap(10, 10))
        lay.addWidget(dot)

        name = QLabel(service.label)
        name.setFont(mono_font(FONT["body"]))
        name.setFixedWidth(110)
        lay.addWidget(name)

        state = QLabel("connected" if service.up else "not connected")
        state.setFont(mono_font(FONT["body"]))
        state.setStyleSheet(
            f"color:{COLORS['green'] if service.up else COLORS['dim']};")
        state.setFixedWidth(110)
        lay.addWidget(state)

        detail = QLabel(service.detail or "not applicable")
        detail.setFont(mono_font(FONT["small"]))
        detail.setStyleSheet(f"color:{COLORS['text']};")
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(detail, 1)

        if service.copyable:
            copy = QPushButton("Copy")
            copy.setFont(mono_font(FONT["small"]))
            copy.setFixedHeight(20)
            copy.setToolTip(f"Copy {service.copyable}")
            copy.clicked.connect(
                lambda _=False, text=service.copyable: self._copy(text))
            lay.addWidget(copy)
        return row

    def _copy(self, text: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self._menu.close()
        window = self.window()
        if hasattr(window, "statusBar"):
            window.statusBar().showMessage(f"Copied {text}", 4000)
