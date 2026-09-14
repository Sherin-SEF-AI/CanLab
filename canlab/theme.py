"""The application's appearance, generated from the scale in ``ui.tokens``.

The look is Blender's: layered greys where nesting reads as depth instead of
as more borders, blue for selection, compact controls, small radii. The status
colours stay what they were, because green means connected and red means armed
to transmit, and those must not dissolve into the chrome.

Nothing here picks a number. Every size, gap and radius comes from
``canlab.ui.tokens`` so the whole interface can be made denser or looser in one
place. The public surface is unchanged: ``COLORS``, ``QSS``, ``mono_font``,
``ui_font``, ``dot_icon`` and ``desc_label``.
"""
from pathlib import Path

from PyQt6.QtGui import QFont

from canlab.ui.tokens import (
    CHROME, FAMILY_MONO, FAMILY_MONO_FIRST, FAMILY_UI, FAMILY_UI_FIRST,
    FONT, HEIGHT, RADIUS, SPACE, STATUS, SYNTAX,
)

# The flat palette the rest of the application reads. Chrome, status and
# syntax are separate concerns in tokens.py and merged here for the call
# sites, which have always used one dict.
COLORS = {
    # chrome, under the names the codebase already uses
    "bg":           CHROME["window"],
    "panel_bg":     CHROME["panel"],
    "border":       CHROME["border"],
    "text":         CHROME["text"],
    "dim":          CHROME["dim"],
    "row_alt":      CHROME["editor"],
    "selection_bg": CHROME["select"],
    # chrome, new names
    "editor":       CHROME["editor"],
    "raised":       CHROME["raised"],
    "input":        CHROME["input"],
    "hover":        CHROME["hover"],
    "select":       CHROME["select"],
    "active":       CHROME["active"],
    "text_dim":     CHROME["text_dim"],
    # status: unchanged meanings
    "green":        STATUS["green"],
    "amber":        STATUS["amber"],
    "error":        STATUS["error"],
    "accent":       STATUS["accent"],
    # syntax highlighting for the generated code view
    "keyword":      SYNTAX["keyword"],
    "string":       SYNTAX["string"],
    "comment":      SYNTAX["comment"],
    "function":     SYNTAX["function"],
}

# Qt's stylesheet engine draws subcontrol arrows from an image; the CSS
# border-triangle trick renders as a filled square, which is what the combo
# boxes used to show. These ship with the package.
_ASSETS = Path(__file__).resolve().parent / "assets"
_ARROW = (_ASSETS / "arrow-down.svg").as_posix()
_ARROW_DIM = (_ASSETS / "arrow-down-dim.svg").as_posix()

# Kept for compatibility: callers pass no arguments to mono_font in 97 places.
FONT_FAMILY = FAMILY_MONO_FIRST
FONT_SIZE = FONT["body"]


def mono_font(size=FONT_SIZE, bold=False) -> QFont:
    """The data face: hex, tables, code, anything that has to line up."""
    f = QFont(FAMILY_MONO_FIRST, size)
    f.setBold(bold)
    return f


def ui_font(size=FONT_SIZE, bold=False) -> QFont:
    """The chrome face: menus, buttons, labels, anything that is prose."""
    f = QFont(FAMILY_UI_FIRST, size)
    f.setBold(bold)
    return f


def dot_icon(color: str, size: int = 10):
    """A filled circle, for showing whether something is on at a glance.

    A toolbar of identical grey words makes the reader parse every one of
    them to find the state. A coloured dot is read without reading.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(px)


# Shorthands so the stylesheet below reads as design rather than arithmetic.
_C = CHROME
_S = STATUS
_XS, _SM, _MD, _LG = SPACE["xs"], SPACE["sm"], SPACE["md"], SPACE["lg"]
_R, _RM = RADIUS["sm"], RADIUS["md"]
_ROW, _CTL, _BAR = HEIGHT["row"], HEIGHT["control"], HEIGHT["bar"]
_F_MICRO, _F_SMALL, _F_BODY = FONT["micro"], FONT["small"], FONT["body"]

QSS = f"""
/* ── roots ──────────────────────────────────────────────────────────────── */
QMainWindow, QDialog {{
    background: {_C['window']};
    color: {_C['text']};
}}

/* The default face is the proportional one, so chrome reads as Blender does.
   Data widgets are switched back to monospace further down, and the 250-odd
   explicit mono_font() calls in the widget layer still win over this.

   Deliberately no background here. Painting every QWidget flattens the whole
   interface to one tone, which is what made the first attempt at this palette
   look like the old near-black theme with different numbers: a tab page, a
   group inside it and the window behind it were all the same grey. Left
   unset, a plain child widget is transparent and shows whatever surface it
   sits on, so the layering below actually reads as depth. */
QWidget {{
    color: {_C['text']};
    font-family: {FAMILY_UI};
    font-size: {_F_BODY}pt;
}}

QWidget:disabled {{
    color: {_C['dim']};
}}

/* ── menu bar and menus ─────────────────────────────────────────────────── */
QMenuBar {{
    background: {_C['window']};
    color: {_C['text']};
    border-bottom: 1px solid {_C['border']};
    padding: 0px {_SM}px;
}}

QMenuBar::item {{
    background: transparent;
    padding: {_XS}px {_MD}px;
    border-radius: {_R}px;
}}

QMenuBar::item:selected {{
    background: {_C['raised']};
}}

QMenuBar::item:pressed {{
    background: {_C['select']};
    color: #ffffff;
}}

QMenu {{
    background: {_C['panel']};
    color: {_C['text']};
    border: 1px solid {_C['border_strong']};
    border-radius: {_RM}px;
    padding: {_SM}px;
}}

QMenu::item {{
    padding: {_SM}px {_LG}px {_SM}px {_MD}px;
    border-radius: {_R}px;
}}

QMenu::item:selected {{
    background: {_C['select']};
    color: #ffffff;
}}

QMenu::item:disabled {{
    color: {_C['dim']};
}}

QMenu::separator {{
    height: 1px;
    background: {_C['border']};
    margin: {_SM}px {_MD}px;
}}

/* ── toolbar ────────────────────────────────────────────────────────────── */
QToolBar {{
    background: {_C['window']};
    border-bottom: 1px solid {_C['border']};
    spacing: {_XS}px;
    padding: {_XS}px {_SM}px;
    min-height: {_BAR + 6}px;
    max-height: {_BAR + 6}px;
}}

QToolBar QToolButton {{
    background: transparent;
    color: {_C['text']};
    border: 1px solid transparent;
    border-radius: {_R}px;
    padding: {_XS}px {_MD}px;
}}

QToolBar QToolButton:hover {{
    background: {_C['raised']};
    border-color: {_C['border_strong']};
}}

QToolBar QToolButton:pressed,
QToolBar QToolButton:checked {{
    background: {_C['select']};
    border-color: {_C['active']};
    color: #ffffff;
}}

QToolBar QToolButton:disabled {{
    color: {_C['dim']};
}}

QToolButton::menu-indicator {{
    image: url({_ARROW});
    width: 8px;
    height: 5px;
    subcontrol-position: right center;
    right: {_SM}px;
}}

QToolBar::separator {{
    background: {_C['border']};
    width: 1px;
    margin: {_SM}px {_XS}px;
}}

/* ── workspace bar ──────────────────────────────────────────────────────── */
#workspace_bar {{
    background: {_C['window']};
    border-bottom: 1px solid {_C['border']};
}}

#strip_inner {{
    background: transparent;
}}

#ws_button, #ws_tab_button {{
    background: transparent;
    border: none;
    border-radius: {_R}px;
    padding: {_XS}px {_MD}px;
    margin: 0px;
    color: {_C['dim']};
}}

#ws_button {{
    text-transform: uppercase;
    letter-spacing: 1px;
    color: {_C['text_dim']};
}}

#ws_button:hover, #ws_tab_button:hover {{
    background: {_C['panel']};
    color: {_C['text']};
}}

#ws_button:checked {{
    color: {_C['text']};
    background: transparent;
}}

#ws_tab_button:checked {{
    color: {_C['active']};
    background: {_C['editor']};
}}

#ws_sep {{
    color: {_C['border']};
    max-width: 1px;
    margin: {_SM}px {_MD}px;
}}

/* ── tabs ───────────────────────────────────────────────────────────────── */
/* The layering is the whole point of this palette: the window is the darkest
   tone, a tab page sits one step above it, and a panel one step above that.
   Setting QWidget's background alone makes every surface the same darkest
   grey, which reads as the old near-black theme with different numbers. The
   QStackedWidget rule is what actually lifts the pages, since a tab page is a
   plain QWidget and would otherwise inherit the base. */
QTabWidget::pane {{
    border: 1px solid {_C['border']};
    background: {_C['editor']};
    top: -1px;
}}

QStackedWidget > QWidget {{
    background: {_C['editor']};
}}

QTabBar {{
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background: {_C['window']};
    color: {_C['text_dim']};
    padding: {_SM}px {_LG}px;
    margin-right: 1px;
    border: 1px solid transparent;
    border-top-left-radius: {_R}px;
    border-top-right-radius: {_R}px;
    min-width: 54px;
}}

QTabBar::tab:hover:!selected {{
    background: {_C['panel']};
    color: {_C['text']};
}}

QTabBar::tab:selected {{
    background: {_C['editor']};
    color: {_C['text']};
    border-color: {_C['border']};
    border-bottom-color: {_C['editor']};
}}

QTabBar QToolButton {{
    background: {_C['panel']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
}}

/* ── data surfaces: monospace ───────────────────────────────────────────── */
QTableWidget, QTreeWidget, QListWidget, QTextEdit, QPlainTextEdit, QLineEdit {{
    font-family: {FAMILY_MONO};
}}

QTableWidget, QTableView {{
    background: {_C['editor']};
    alternate-background-color: {_C['window']};
    color: {_C['text']};
    gridline-color: {_C['border']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
    selection-background-color: {_C['select']};
    selection-color: #ffffff;
}}

QTableWidget::item {{
    padding: 0px {_SM}px;
    border: none;
    height: {_ROW}px;
}}

QTableWidget::item:selected {{
    background: {_C['select']};
    color: #ffffff;
}}

QHeaderView {{
    background: {_C['panel']};
}}

QHeaderView::section {{
    background: {_C['panel']};
    color: {_C['text_dim']};
    padding: {_XS}px {_SM}px;
    font-family: {FAMILY_UI};
    font-size: {_F_SMALL}pt;
    text-transform: uppercase;
    border: none;
    border-right: 1px solid {_C['window']};
    border-bottom: 1px solid {_C['border']};
}}

QHeaderView::section:hover {{
    background: {_C['raised']};
    color: {_C['text']};
}}

QTableCornerButton::section {{
    background: {_C['panel']};
    border: none;
    border-bottom: 1px solid {_C['border']};
}}

QTreeWidget, QTreeView {{
    background: {_C['editor']};
    color: {_C['text']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
    selection-background-color: {_C['select']};
    selection-color: #ffffff;
}}

QTreeWidget::item, QTreeView::item {{
    padding: 1px {_XS}px;
    height: {_ROW}px;
    border: none;
}}

QTreeWidget::item:hover, QTreeView::item:hover {{
    background: {_C['panel']};
}}

QTreeWidget::item:selected, QTreeView::item:selected {{
    background: {_C['select']};
    color: #ffffff;
}}

QListWidget, QListView {{
    background: {_C['editor']};
    color: {_C['text']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
    selection-background-color: {_C['select']};
    selection-color: #ffffff;
}}

QListWidget::item {{
    padding: {_XS}px {_SM}px;
    height: {_ROW}px;
    border-radius: {_R}px;
}}

QListWidget::item:hover {{
    background: {_C['panel']};
}}

QListWidget::item:selected {{
    background: {_C['select']};
    color: #ffffff;
}}

QTextEdit, QPlainTextEdit {{
    background: {_C['input']};
    color: {_C['text']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
    selection-background-color: {_C['select']};
    selection-color: #ffffff;
    padding: {_XS}px;
}}

/* ── inputs ─────────────────────────────────────────────────────────────── */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {_C['input']};
    color: {_C['text']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
    padding: {_XS}px {_SM}px;
    min-height: {_CTL - 6}px;
    selection-background-color: {_C['select']};
    selection-color: #ffffff;
}}

QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {_C['border_strong']};
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {_C['active']};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {_C['window']};
    color: {_C['dim']};
}}

QComboBox {{
    font-family: {FAMILY_UI};
}}

QComboBox::drop-down {{
    border: none;
    background: transparent;
    width: 16px;
}}

QComboBox::down-arrow {{
    image: url({_ARROW});
    width: 10px;
    height: 6px;
    margin-right: {_SM}px;
}}

QComboBox::down-arrow:disabled {{
    image: url({_ARROW_DIM});
}}

QComboBox QAbstractItemView {{
    background: {_C['panel']};
    color: {_C['text']};
    border: 1px solid {_C['border_strong']};
    border-radius: {_R}px;
    selection-background-color: {_C['select']};
    selection-color: #ffffff;
    outline: none;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: {_C['raised']};
    border: none;
    width: 14px;
}}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {_C['hover']};
}}

/* ── buttons ────────────────────────────────────────────────────────────── */
QPushButton {{
    background: {_C['raised']};
    color: {_C['text']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
    padding: {_XS}px {_LG}px;
    min-height: {_CTL - 8}px;
}}

QPushButton:hover {{
    background: {_C['hover']};
    border-color: {_C['border_strong']};
}}

QPushButton:pressed {{
    background: {_C['select']};
    border-color: {_C['active']};
    color: #ffffff;
}}

QPushButton:checked {{
    background: {_C['select']};
    border-color: {_C['active']};
    color: #ffffff;
}}

QPushButton:disabled {{
    background: {_C['panel']};
    color: {_C['dim']};
    border-color: {_C['border']};
}}

QPushButton:default {{
    border-color: {_C['active']};
}}

QPushButton#btn_green {{
    background: rgba(0, 255, 136, 0.10);
    color: {_S['green']};
    border-color: rgba(0, 255, 136, 0.45);
}}

QPushButton#btn_green:hover {{
    background: rgba(0, 255, 136, 0.20);
}}

QPushButton#btn_amber {{
    background: rgba(255, 179, 0, 0.10);
    color: {_S['amber']};
    border-color: rgba(255, 179, 0, 0.45);
}}

QPushButton#btn_amber:hover {{
    background: rgba(255, 179, 0, 0.20);
}}

/* ── choice controls ────────────────────────────────────────────────────── */
QCheckBox, QRadioButton {{
    spacing: {_MD}px;
    background: transparent;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 13px;
    height: 13px;
    background: {_C['input']};
    border: 1px solid {_C['border_strong']};
}}

QCheckBox::indicator {{
    border-radius: {_R}px;
}}

QRadioButton::indicator {{
    border-radius: 7px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {_C['active']};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {_C['select']};
    border-color: {_C['active']};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: {_C['panel']};
    border-color: {_C['border']};
}}

/* ── slider ─────────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {{
    background: {_C['input']};
    height: 4px;
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: {_C['select']};
    height: 4px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {_C['text']};
    border: none;
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}

QSlider::handle:horizontal:hover {{
    background: #ffffff;
}}

/* ── group box ──────────────────────────────────────────────────────────── */
QGroupBox {{
    background: {_C['panel']};
    border: 1px solid {_C['border']};
    border-radius: {_RM}px;
    margin-top: {_MD + 2}px;
    padding-top: {_XS}px;
    font-size: {_F_SMALL}pt;
    color: {_C['text_dim']};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {_LG}px;
    padding: 0 {_SM}px;
    text-transform: uppercase;
    color: {_C['text_dim']};
}}

/* ── splitter ───────────────────────────────────────────────────────────── */
QSplitter::handle {{
    background: {_C['window']};
}}

QSplitter::handle:horizontal {{
    width: {_SM}px;
}}

QSplitter::handle:vertical {{
    height: {_SM}px;
}}

QSplitter::handle:hover {{
    background: {_C['select']};
}}

/* ── scroll ─────────────────────────────────────────────────────────────── */
QScrollArea {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {_C['raised']};
    min-height: 24px;
    border-radius: 5px;
    margin: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: {_C['hover']};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {_C['raised']};
    min-width: 24px;
    border-radius: 5px;
    margin: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {_C['hover']};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ── status bar ─────────────────────────────────────────────────────────── */
QStatusBar {{
    background: {_C['window']};
    color: {_C['text_dim']};
    border-top: 1px solid {_C['border']};
    font-size: {_F_SMALL}pt;
}}

QStatusBar::item {{
    border: none;
}}

/* ── labels ─────────────────────────────────────────────────────────────── */
QLabel {{
    background: transparent;
    color: {_C['text']};
}}

QLabel#label_dim {{
    color: {_C['text_dim']};
    font-size: {_F_SMALL}pt;
}}

QLabel#label_green {{
    color: {_S['green']};
}}

QLabel#label_amber {{
    color: {_S['amber']};
}}

/* State as a property rather than an inline stylesheet. Set it with
   widget.setProperty("state", "ok") then re-polish; see ui.widgets. */
QLabel[state="ok"]    {{ color: {_S['green']}; }}
QLabel[state="warn"]  {{ color: {_S['amber']}; }}
QLabel[state="error"] {{ color: {_S['error']}; }}
QLabel[state="dim"]   {{ color: {_C['dim']}; }}
QLabel[state="info"]  {{ color: {_C['text']}; }}

/* ── sections and toolstrips ────────────────────────────────────────────── */
#toolstrip {{
    background: {_C['panel']};
    border-bottom: 1px solid {_C['border']};
}}

#section {{
    background: transparent;
}}

#section_header {{
    background: {_C['panel']};
    color: {_C['text_dim']};
    border: none;
    border-radius: {_R}px;
    padding: 0px {_SM}px;
    text-align: left;
    text-transform: uppercase;
}}

#section_header:hover {{
    background: {_C['raised']};
    color: {_C['text']};
}}

#section_header:checked {{
    color: {_C['text']};
}}

#section_body {{
    background: {_C['panel']};
    border: 1px solid {_C['border']};
    border-top: none;
    border-bottom-left-radius: {_RM}px;
    border-bottom-right-radius: {_RM}px;
}}

/* ── progress ───────────────────────────────────────────────────────────── */
QProgressBar {{
    background: {_C['input']};
    border: 1px solid {_C['border']};
    border-radius: {_R}px;
    text-align: center;
    font-size: {_F_MICRO}pt;
    color: {_C['text']};
}}

QProgressBar::chunk {{
    background: {_C['select']};
    border-radius: {_R - 1}px;
}}

#command_palette {{
    background: {_C['panel']};
    border: 1px solid {_C['border_strong']};
    border-radius: {_RM}px;
}}

#command_palette QLineEdit {{
    background: {_C['input']};
    border: 1px solid {_C['border_strong']};
    padding: {_MD}px {_SM}px;
    font-family: {FAMILY_UI};
}}

#command_palette QListWidget {{
    background: transparent;
    border: none;
    font-family: {FAMILY_UI};
}}

/* ── tooltip ────────────────────────────────────────────────────────────── */
QToolTip {{
    background: {_C['panel']};
    color: {_C['text']};
    border: 1px solid {_C['border_strong']};
    border-radius: {_R}px;
    padding: {_SM}px {_MD}px;
    font-family: {FAMILY_UI};
}}
"""


def desc_label(text: str, size: int = FONT["small"]):
    """A wrapping label for the help text above a panel.

    Long help in a non-wrapping label sets a minimum width on its whole tab,
    and the widest of them decided the minimum width of the application: the
    window could not be made narrower than 1662 px, so it did not fit a
    1366x768 laptop screen. Wrapping costs nothing and removes that floor.
    """
    from PyQt6.QtWidgets import QLabel

    label = QLabel(text)
    label.setFont(ui_font(size))
    label.setWordWrap(True)
    label.setObjectName("label_dim")
    return label
