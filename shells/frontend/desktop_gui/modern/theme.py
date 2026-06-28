"""Theme constants, color palette, and QSS for the modern Partner GUI.

Light theme with blue accent colors.
"""

import sys
from PySide6.QtGui import QFont


class ThemeColors:
    """Light theme color palette."""

    # Backgrounds
    bg = "#F5F7FA"          # Main background (light gray)
    bg2 = "#FFFFFF"         # Card background
    bg3 = "#F0F2F5"         # Slightly darker for secondary areas
    card = "#FFFFFF"        # Card/panel
    card_hl = "#F0F4F8"     # Card hover

    # Accent colors
    accent = "#4A90D9"      # Primary accent (blue)
    accent2 = "#6BA5E7"     # Lighter blue
    accent3 = "#3A7BD5"     # Darker blue
    accent_h = "#357ABD"    # Accent hover

    # Status colors
    green = "#4CAF50"       # Success / running
    yellow = "#F5A623"      # Warning / idle
    red = "#E53935"         # Error / stopped
    blue = "#4A90D9"        # Info
    pink = "#E91E63"        # Special

    # Text colors
    txt = "#2C3E50"         # Primary text
    txt2 = "#7F8C8D"        # Secondary text
    txt3 = "#BDC3C7"        # Muted text

    # UI colors
    border = "#E1E5EB"      # Borders
    input_bg = "#FFFFFF"    # Input background
    sidebar_bg = "#FFFFFF"  # Left sidebar background

    # Chat colors
    chat_user = "#D6E8FF"   # User message bubble (light blue)
    chat_bot = "#F0F2F5"    # Partner message bubble (light gray)
    chat_user_hl = "#C4DFFF"  # User bubble hover
    chat_bot_hl = "#E5E8EB"   # Partner bubble hover

    # Sidebar
    nav_active = "#4A90D9"  # Active nav item accent
    nav_hover = "#F0F4F8"   # Nav item hover
    nav_text = "#5A6D80"    # Nav item text
    nav_text_active = "#4A90D9"  # Active nav text
    sidebar_width = 220     # Expanded sidebar width
    sidebar_collapsed = 64  # Collapsed sidebar width

    # Title sizing
    title_size = 20
    title_weight = "bold"


THEME = ThemeColors()


def get_default_font() -> QFont:
    """Return the appropriate default font for the platform."""
    if sys.platform == "win32":
        return QFont("Segoe UI", 10)
    elif sys.platform == "darwin":
        return QFont("SF Pro Text", 13)
    else:
        return QFont("Noto Sans CJK SC", 10)


def get_mono_font() -> QFont:
    """Return monospace font for log/console views."""
    if sys.platform == "win32":
        return QFont("Consolas", 10)
    elif sys.platform == "darwin":
        return QFont("SF Mono", 12)
    else:
        return QFont("Noto Sans Mono CJK SC", 10)


def generate_stylesheet() -> str:
    """Generate the complete QSS stylesheet for the application."""
    T = THEME
    return f"""
    /* ── Global ── */
    QMainWindow {{
        background-color: {T.bg};
        color: {T.txt};
    }}
    QWidget {{
        background-color: {T.bg};
        color: {T.txt};
        font-size: 13px;
    }}
    QLabel {{
        background: transparent;
        color: {T.txt};
        border: none;
    }}
    QLabel#title {{
        font-size: 20px;
        font-weight: bold;
        color: {T.txt};
    }}
    QLabel#subtitle {{
        font-size: 13px;
        color: {T.txt2};
    }}
    QLabel#section {{
        font-size: 15px;
        font-weight: bold;
        color: {T.accent};
        padding: 8px 0px 4px 0px;
    }}
    QLabel#status_ok {{
        color: {T.green};
    }}
    QLabel#status_warn {{
        color: {T.yellow};
    }}
    QLabel#status_error {{
        color: {T.red};
    }}

    /* ── Buttons ── */
    QPushButton {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.bg2}, stop:1 {T.bg3});
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 10px;
        padding: 8px 20px;
        min-height: 42px;
        font-size: 13px;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.card_hl}, stop:1 {T.bg2});
        border-color: {T.accent};
        color: {T.accent};
    }}
    QPushButton:pressed {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.bg3}, stop:1 {T.border});
        border-color: {T.accent_h};
    }}
    QPushButton:disabled {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.bg3}, stop:1 #E0E0E0);
        color: {T.txt3};
        border-color: {T.border};
    }}
    QPushButton#accent {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.accent}, stop:1 {T.accent3});
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 10px;
        padding: 8px 20px;
        min-height: 42px;
    }}
    QPushButton#accent:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.accent2}, stop:1 {T.accent_h});
    }}
    QPushButton#accent:pressed {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.accent3}, stop:1 #2A5F8A);
    }}
    QPushButton#danger {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #E53935, stop:1 #C62828);
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 10px;
        padding: 8px 20px;
        min-height: 42px;
    }}
    QPushButton#danger:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #EF5350, stop:1 #D32F2F);
    }}
    QPushButton#danger:pressed {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #C62828, stop:1 #B71C1C);
    }}
    QPushButton#success {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #43A047, stop:1 #2E7D32);
        color: white;
        font-weight: bold;
        border: none;
        border-radius: 10px;
        padding: 8px 20px;
        min-height: 42px;
    }}
    QPushButton#success:hover {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #66BB6A, stop:1 #43A047);
    }}
    QPushButton#success:pressed {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #2E7D32, stop:1 #1B5E20);
    }}

    /* ── Sidebar ── */
    QPushButton#nav_btn {{
        background: transparent;
        color: {T.nav_text};
        border: none;
        border-radius: 10px;
        padding: 6px 16px;
        text-align: left;
        font-size: 13px;
        min-height: 42px;
    }}
    QPushButton#nav_btn:hover {{
        background-color: {T.nav_hover};
        color: {T.txt};
    }}
    QPushButton#nav_btn:checked {{
        background-color: rgba(74, 144, 217, 0.15);
        color: {T.nav_text_active};
        border-left: 3px solid {T.nav_active};
        font-weight: bold;
    }}

    /* ── Inputs ── */
    QLineEdit {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.input_bg}, stop:1 {T.bg3});
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 10px;
        padding: 8px 14px;
        min-height: 42px;
        font-size: 13px;
        font-weight: bold;
    }}
    QLineEdit:focus {{
        border-color: {T.accent};
    }}
    QLineEdit#error {{
        border-color: {T.red};
    }}

    QComboBox {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.input_bg}, stop:1 {T.bg3});
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 10px;
        padding: 8px 36px 8px 14px;
        min-height: 42px;
        font-size: 13px;
        font-weight: bold;
    }}
    QComboBox:hover {{
        border-color: {T.accent};
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.card_hl}, stop:1 {T.input_bg});
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 32px;
        border: none;
        border-top-right-radius: 10px;
        border-bottom-right-radius: 10px;
    }}
    QComboBox::down-arrow {{
        image: none;
        border-left: 7px solid transparent;
        border-right: 7px solid transparent;
        border-top: 9px solid {T.txt2};
        margin-right: 4px;
    }}
    QComboBox::down-arrow:hover {{
        border-top-color: {T.accent};
    }}
    QComboBox QAbstractItemView {{
        background-color: {T.bg};
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 8px;
        padding: 4px;
        outline: none;
        selection-background-color: {T.bg3};
    }}
    QComboBox QAbstractItemView::item {{
        padding: 8px 14px;
        border-radius: 6px;
        min-height: 34px;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background-color: rgba(74, 144, 217, 0.10);
        color: {T.accent};
    }}
    QComboBox QAbstractItemView::item:selected {{
        background-color: rgba(74, 144, 217, 0.15);
        color: {T.accent};
        font-weight: bold;
    }}

    QTextEdit, QPlainTextEdit {{
        background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 {T.input_bg}, stop:1 {T.bg3});
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 10px;
        padding: 8px 14px;
        font-size: 13px;
    }}
    QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {T.accent};
    }}

    /* ── Lists / Trees / Tables ── */
    QListWidget {{
        background-color: {T.input_bg};
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 6px;
        outline: none;
    }}
    QListWidget::item {{
        padding: 10px 14px;
        border-radius: 4px;
    }}
    QListWidget::item:selected {{
        background-color: {T.bg3};
        color: {T.accent};
    }}
    QListWidget::item:hover {{
        background-color: {T.card_hl};
    }}

    QTreeWidget {{
        background-color: {T.input_bg};
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 6px;
        outline: none;
    }}
    QTreeWidget::item {{
        padding: 8px 10px;
    }}
    QTreeWidget::item:selected {{
        background-color: {T.bg3};
        color: {T.accent};
    }}

    QTableWidget {{
        background-color: {T.input_bg};
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 6px;
        gridline-color: {T.border};
    }}
    QTableWidget::item {{
        padding: 8px 10px;
    }}
    QTableWidget::item:selected {{
        background-color: {T.bg3};
        color: {T.accent};
    }}
    QHeaderView::section {{
        background-color: {T.bg2};
        color: {T.txt2};
        border: none;
        border-bottom: 1px solid {T.border};
        padding: 10px;
        font-weight: bold;
    }}

    /* ── Progress Bar ── */
    QProgressBar {{
        background-color: {T.bg3};
        border: 1px solid {T.border};
        border-radius: 4px;
        text-align: center;
        color: {T.txt};
        height: 10px;
    }}
    QProgressBar::chunk {{
        background-color: {T.accent};
        border-radius: 3px;
    }}

    /* ── Tabs ── */
    QTabWidget::pane {{
        background-color: {T.bg};
        border: 1px solid {T.border};
        border-radius: 6px;
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: {T.bg2};
        color: {T.txt2};
        border: 1px solid {T.border};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 10px 22px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background-color: {T.bg};
        color: {T.accent};
        border-bottom: 2px solid {T.accent};
    }}
    QTabBar::tab:hover {{
        background-color: {T.card_hl};
    }}

    /* ── Scrollbars ── */
    QScrollBar:vertical {{
        background: {T.bg2};
        width: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: {T.border};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {T.txt3};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: {T.bg2};
        height: 8px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {T.border};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {T.txt3};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* ── Splitter ── */
    QSplitter::handle {{
        background-color: {T.border};
        width: 1px;
    }}
    QSplitter::handle:hover {{
        background-color: {T.accent};
    }}

    /* ── Menu ── */
    QMenuBar {{
        background-color: {T.sidebar_bg};
        color: {T.txt};
        border-bottom: 1px solid {T.border};
        padding: 2px;
    }}
    QMenuBar::item {{
        padding: 6px 12px;
        border-radius: 4px;
    }}
    QMenuBar::item:selected {{
        background-color: {T.nav_hover};
    }}
    QMenu {{
        background-color: {T.bg2};
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 8px 24px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {T.bg3};
        color: {T.accent};
    }}
    QMenu::separator {{
        height: 1px;
        background: {T.border};
        margin: 4px 8px;
    }}

    /* ── Status Bar ── */
    QStatusBar {{
        background-color: {T.sidebar_bg};
        color: {T.txt2};
        border-top: 1px solid {T.border};
        padding: 4px 12px;
    }}
    QStatusBar::item {{
        border: none;
    }}

    /* ── Tooltips ── */
    QToolTip {{
        background-color: {T.bg2};
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 4px;
        padding: 6px 10px;
        font-size: 12px;
    }}

    /* ── Dialogs ── */
    QDialog {{
        background-color: {T.bg};
        color: {T.txt};
    }}

    /* ── Group box ── */
    QGroupBox {{
        background-color: {T.card};
        border: 1px solid {T.border};
        border-radius: 8px;
        margin-top: 12px;
        padding: 16px;
        font-weight: bold;
        color: {T.txt};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 8px;
        color: {T.accent};
    }}

    /* ── Frame cards ── */
    QFrame#card {{
        background-color: {T.card};
        border: 1px solid {T.border};
        border-radius: 10px;
        padding: 20px;
    }}
    QFrame#card:hover {{
        border-color: {T.accent};
    }}

    /* ── Checkbox / Radio ── */
    QCheckBox {{
        spacing: 8px;
        color: {T.txt};
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 4px;
        border: 2px solid {T.border};
        background: {T.input_bg};
    }}
    QCheckBox::indicator:checked {{
        background-color: {T.accent};
        border-color: {T.accent};
    }}
    QRadioButton {{
        spacing: 8px;
        color: {T.txt};
    }}
    QRadioButton::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 10px;
        border: 2px solid {T.border};
        background: {T.input_bg};
    }}
    QRadioButton::indicator:checked {{
        background-color: {T.accent};
        border-color: {T.accent};
    }}

    /* ── Scrollbars ── */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {T.border};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {T.txt3};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {T.border};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {T.txt3};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: transparent;
    }}

    /* ── Table ── */
    QTableWidget {{
        background-color: {T.bg};
        color: {T.txt};
        border: 1px solid {T.border};
        border-radius: 8px;
        gridline-color: {T.bg3};
        outline: none;
    }}
    QTableWidget::item {{
        padding: 4px 8px;
    }}
    QTableWidget::item:selected {{
        background-color: transparent;
        color: {T.accent};
    }}
    QTableWidget::item:selected:active {{
        background-color: transparent;
        color: {T.accent};
    }}
    QHeaderView::section {{
        background-color: {T.card};
        color: {T.txt2};
        border: none;
        border-bottom: 1px solid {T.border};
        border-right: 1px solid {T.bg3};
        padding: 8px 12px;
        font-weight: bold;
        font-size: 12px;
    }}

    /* ── System Tray ── */
    QSystemTrayIcon {{
        color: {T.txt};
    }}
    """
