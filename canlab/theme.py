from PyQt6.QtGui import QFont, QColor

COLORS = {
    "bg":           "#0a0a0a",
    "panel_bg":     "#111111",
    "border":       "#1e1e1e",
    "text":         "#e0e0e0",
    "green":        "#00ff88",
    "amber":        "#ffb300",
    "error":        "#ff3333",
    "accent":       "#00aaff",
    "dim":          "#555555",
    "row_alt":      "#141414",
    "selection_bg": "#1a2a3a",
    "keyword":      "#00aaff",
    "string":       "#ffb300",
    "comment":      "#555555",
    "function":     "#00ff88",
}

FONT_FAMILY = "Courier New"
FONT_SIZE   = 9

def mono_font(size=FONT_SIZE, bold=False) -> QFont:
    f = QFont(FONT_FAMILY, size)
    f.setBold(bold)
    return f

QSS = f"""
QMainWindow, QDialog {{
    background: {COLORS['bg']};
    color: {COLORS['text']};
}}

QWidget {{
    background: {COLORS['bg']};
    color: {COLORS['text']};
    font-family: "Courier New";
    font-size: 9pt;
}}

QToolBar {{
    background: {COLORS['panel_bg']};
    border-bottom: 1px solid {COLORS['border']};
    spacing: 2px;
    padding: 2px 4px;
    min-height: 32px;
    max-height: 32px;
}}

QToolBar QToolButton {{
    background: transparent;
    color: {COLORS['text']};
    border: 1px solid transparent;
    border-radius: 2px;
    padding: 2px 6px;
    font-family: "Courier New";
    font-size: 9pt;
}}

QToolBar QToolButton:hover {{
    background: {COLORS['border']};
    border-color: {COLORS['accent']};
    color: {COLORS['accent']};
}}

QToolBar QToolButton:pressed {{
    background: {COLORS['selection_bg']};
}}

QToolBar::separator {{
    background: {COLORS['border']};
    width: 1px;
    margin: 4px 2px;
}}

QTabWidget::pane {{
    border: 1px solid {COLORS['border']};
    background: {COLORS['bg']};
}}

QTabBar::tab {{
    background: {COLORS['panel_bg']};
    color: {COLORS['dim']};
    border: 1px solid {COLORS['border']};
    border-bottom: none;
    padding: 3px 10px;
    font-family: "Courier New";
    font-size: 9pt;
    text-transform: uppercase;
    min-width: 60px;
}}

QTabBar::tab:selected {{
    background: {COLORS['bg']};
    color: {COLORS['green']};
    border-bottom: 1px solid {COLORS['bg']};
}}

QTabBar::tab:hover:!selected {{
    color: {COLORS['text']};
    background: #181818;
}}

QTableWidget {{
    background: {COLORS['bg']};
    color: {COLORS['text']};
    gridline-color: transparent;
    border: 1px solid {COLORS['border']};
    font-family: "Courier New";
    font-size: 9pt;
    selection-background-color: {COLORS['selection_bg']};
    selection-color: {COLORS['accent']};
}}

QTableWidget::item {{
    padding: 0px 4px;
    border-bottom: 1px solid {COLORS['border']};
    height: 20px;
}}

QHeaderView::section {{
    background: {COLORS['panel_bg']};
    color: {COLORS['dim']};
    border: none;
    border-right: 1px solid {COLORS['border']};
    border-bottom: 1px solid {COLORS['border']};
    padding: 2px 4px;
    font-family: "Courier New";
    font-size: 8pt;
    text-transform: uppercase;
}}

QTreeWidget {{
    background: {COLORS['bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    font-family: "Courier New";
    font-size: 9pt;
    selection-background-color: {COLORS['selection_bg']};
    selection-color: {COLORS['accent']};
}}

QTreeWidget::item {{
    padding: 1px 2px;
    height: 20px;
}}

QTreeWidget::item:selected {{
    background: {COLORS['selection_bg']};
    color: {COLORS['accent']};
}}

QListWidget {{
    background: {COLORS['bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    font-family: "Courier New";
    font-size: 9pt;
}}

QListWidget::item {{
    padding: 2px 4px;
    height: 20px;
}}

QListWidget::item:selected {{
    background: {COLORS['selection_bg']};
    color: {COLORS['accent']};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {COLORS['panel_bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 2px;
    padding: 2px 4px;
    font-family: "Courier New";
    font-size: 9pt;
    selection-background-color: {COLORS['selection_bg']};
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {COLORS['accent']};
}}

QComboBox::drop-down {{
    border: none;
    background: {COLORS['border']};
    width: 16px;
}}

QComboBox::down-arrow {{
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {COLORS['text']};
    margin-right: 4px;
}}

QComboBox QAbstractItemView {{
    background: {COLORS['panel_bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['selection_bg']};
}}

QTextEdit, QPlainTextEdit {{
    background: {COLORS['panel_bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    font-family: "Courier New";
    font-size: 9pt;
}}

QPushButton {{
    background: {COLORS['panel_bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 2px;
    padding: 3px 10px;
    font-family: "Courier New";
    font-size: 9pt;
}}

QPushButton:hover {{
    background: {COLORS['border']};
    border-color: {COLORS['accent']};
    color: {COLORS['accent']};
}}

QPushButton:pressed {{
    background: {COLORS['selection_bg']};
}}

QPushButton:disabled {{
    color: {COLORS['dim']};
    border-color: {COLORS['border']};
}}

QPushButton#btn_amber {{
    background: #2a1f00;
    color: {COLORS['amber']};
    border-color: {COLORS['amber']};
}}

QPushButton#btn_amber:hover {{
    background: #3a2a00;
}}

QPushButton#btn_green {{
    background: #002a15;
    color: {COLORS['green']};
    border-color: {COLORS['green']};
}}

QPushButton#btn_green:hover {{
    background: #003a1f;
}}

QSplitter::handle {{
    background: {COLORS['border']};
}}

QSplitter::handle:horizontal {{
    width: 2px;
}}

QSplitter::handle:vertical {{
    height: 2px;
}}

QScrollBar:vertical {{
    background: {COLORS['panel_bg']};
    width: 8px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background: {COLORS['border']};
    min-height: 20px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background: {COLORS['dim']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: {COLORS['panel_bg']};
    height: 8px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background: {COLORS['border']};
    min-width: 20px;
    border-radius: 4px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QStatusBar {{
    background: {COLORS['panel_bg']};
    color: {COLORS['dim']};
    border-top: 1px solid {COLORS['border']};
    font-family: "Courier New";
    font-size: 9pt;
}}

QStatusBar::item {{
    border: none;
}}

QLabel {{
    color: {COLORS['text']};
    font-family: "Courier New";
    font-size: 9pt;
}}

QLabel#label_green {{
    color: {COLORS['green']};
}}

QLabel#label_amber {{
    color: {COLORS['amber']};
}}

QLabel#label_dim {{
    color: {COLORS['dim']};
}}

QGroupBox {{
    border: 1px solid {COLORS['border']};
    margin-top: 8px;
    font-family: "Courier New";
    font-size: 9pt;
    color: {COLORS['dim']};
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {COLORS['dim']};
    text-transform: uppercase;
}}

QCheckBox {{
    color: {COLORS['text']};
    font-family: "Courier New";
    font-size: 9pt;
    spacing: 4px;
}}

QCheckBox::indicator {{
    width: 12px;
    height: 12px;
    border: 1px solid {COLORS['border']};
    background: {COLORS['panel_bg']};
}}

QCheckBox::indicator:checked {{
    background: {COLORS['accent']};
    border-color: {COLORS['accent']};
}}

QProgressBar {{
    background: {COLORS['panel_bg']};
    border: 1px solid {COLORS['border']};
    border-radius: 2px;
    text-align: center;
    color: {COLORS['text']};
    font-size: 8pt;
}}

QProgressBar::chunk {{
    background: {COLORS['green']};
}}

QMenu {{
    background: {COLORS['panel_bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    font-family: "Courier New";
    font-size: 9pt;
}}

QMenu::item {{
    padding: 4px 20px;
}}

QMenu::item:selected {{
    background: {COLORS['selection_bg']};
    color: {COLORS['accent']};
}}

QMenu::separator {{
    height: 1px;
    background: {COLORS['border']};
    margin: 2px 0;
}}

QToolTip {{
    background: {COLORS['panel_bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    font-family: "Courier New";
    font-size: 9pt;
    padding: 2px 6px;
}}
"""
