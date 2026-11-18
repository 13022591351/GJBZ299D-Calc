"""Shared desktop palette, font fallback, and dark/light theme switching."""

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPalette
from PyQt6.QtWidgets import QLabel, QTextBrowser

FONT_CANDIDATES = (
    'Noto Sans CJK SC',
    'Source Han Sans SC',
    'Microsoft YaHei UI',
    'Microsoft YaHei',
    '微软雅黑',
    'PingFang SC',
    'WenQuanYi Micro Hei',
    'SimSun',
    'Sans Serif',
)
LATIN_UI_CANDIDATES = (
    'Ubuntu Sans',
    'Inter',
    'Segoe UI',
    'Noto Sans',
    'Liberation Sans',
    'DejaVu Sans',
)
FONT_FAMILY = FONT_CANDIDATES[0]
BASE_FONT_PX = 14

DARK_COLORS = {
    'background': '#181818',
    'surface': '#1f1f1f',
    'raised': '#252526',
    'header': '#282c34',
    'input': '#313131',
    'border': '#505050',
    'input_border': '#7c7c7c',
    'grid': '#414141',
    'text': '#e4e7ec',
    'muted': '#a9b1bd',
    'heading': '#ffffff',
    'selection': '#264f78',
    'selection_text': '#ffffff',
    'hover': '#333333',
    'button': '#2b2b2b',
    'button_hover': '#383838',
    'button_pressed': '#454545',
    'accent': '#54b5ff',
    'primary': '#0069ad',
    'primary_hover': '#0075b5',
    'primary_border': '#3794ff',
    'formula_bg': '#252526',
    'formula_text': '#dceeff',
    'warning': '#e8bc72',
    'error': '#f28b82',
    'success': '#73c991',
    'disabled': '#9a9a9a',
    'disabled_bg': '#222222',
    'highlight_cell': '#5c4314',
    'share_bar': '#1d4d72',
    'status_bg': '#005a9e',
    'handle': '#707070',
    'handle_hover': '#929292',
}

LIGHT_COLORS = {
    'background': '#f4f6f8',
    'surface': '#ffffff',
    'raised': '#eaeff5',
    'header': '#e2e8f0',
    'input': '#ffffff',
    'border': '#cbd5e1',
    'input_border': '#94a3b8',
    'grid': '#dce3eb',
    'text': '#1f2937',
    'muted': '#52606d',
    'heading': '#102a43',
    'selection': '#cfe3fa',
    'selection_text': '#0b2545',
    'hover': '#e6eef7',
    'button': '#ffffff',
    'button_hover': '#edf2f7',
    'button_pressed': '#dce6f2',
    'accent': '#0969da',
    'primary': '#0069ad',
    'primary_hover': '#00568f',
    'primary_border': '#0969da',
    'formula_bg': '#edf4fc',
    'formula_text': '#102a43',
    'warning': '#b45309',
    'error': '#c92a2a',
    'success': '#1b7f4b',
    'disabled': '#829ab1',
    'disabled_bg': '#eef2f6',
    'highlight_cell': '#fff3bf',
    'share_bar': '#cde3f7',
    'status_bg': '#005a9e',
    'handle': '#94a3b8',
    'handle_hover': '#64748b',
}

def _asset_path(filename):
    """Resolve packaged asset path without Windows extended-length prefixes."""
    bundle_dir = getattr(sys, '_MEIPASS', None)
    base = Path(bundle_dir) / 'src' / 'assets' if bundle_dir else Path(os.path.abspath(__file__)).parent / 'assets'
    return os.path.normpath(str(base / filename))


COLORS = dict(DARK_COLORS)
_ACTIVE_THEME = None
_CHECK_ICON = _asset_path('check.svg').replace('\\', '/')
_MIXED_ICON = _asset_path('mixed.svg').replace('\\', '/')


class _ThemeBus(QObject):
    changed = pyqtSignal(str)


THEME_BUS = _ThemeBus()


def resolve_font_families():
    """Return an ordered UI sans + CJK font stack installed on the host."""
    if QGuiApplication.instance() is not None:
        available = set(QFontDatabase.families())
        cjk = [name for name in FONT_CANDIDATES if name in available]
        latin = [name for name in LATIN_UI_CANDIDATES if name in available]
        stack = []
        if latin:
            stack.append(latin[0])
        if cjk:
            stack.extend(cjk)
        for name in latin[1:]:
            if name not in stack:
                stack.append(name)
        if stack:
            return stack
    return list(FONT_CANDIDATES)


def resolve_font_family():
    return resolve_font_families()[0]


def current_theme():
    global _ACTIVE_THEME
    if _ACTIVE_THEME is None:
        saved = QSettings('DuoYuWang', 'GJB299D').value('ui/theme', 'dark')
        _ACTIVE_THEME = 'light' if saved == 'light' else 'dark'
        COLORS.clear()
        COLORS.update(LIGHT_COLORS if _ACTIVE_THEME == 'light' else DARK_COLORS)
    return _ACTIVE_THEME


def current_colors():
    current_theme()
    return COLORS


def palette(mode=None):
    if mode is None:
        mode = current_theme()
    c = LIGHT_COLORS if mode == 'light' else DARK_COLORS
    result = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: c['background'],
        QPalette.ColorRole.WindowText: c['text'],
        QPalette.ColorRole.Base: c['surface'],
        QPalette.ColorRole.AlternateBase: c['raised'],
        QPalette.ColorRole.Text: c['text'],
        QPalette.ColorRole.Button: c['raised'],
        QPalette.ColorRole.ButtonText: c['text'],
        QPalette.ColorRole.Highlight: c['selection'],
        QPalette.ColorRole.HighlightedText: c['selection_text'],
        QPalette.ColorRole.ToolTipBase: c['raised'],
        QPalette.ColorRole.ToolTipText: c['text'],
        QPalette.ColorRole.Link: c['accent'],
        QPalette.ColorRole.PlaceholderText: c['muted'],
        QPalette.ColorRole.Mid: c['border'],
        QPalette.ColorRole.Light: c['handle'],
        QPalette.ColorRole.Dark: c['background'],
    }.items():
        result.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(c['disabled']))
    return result


def build_stylesheet(mode=None, font_family=None):
    if mode is None:
        mode = current_theme()
    c = LIGHT_COLORS if mode == 'light' else DARK_COLORS
    if isinstance(font_family, (list, tuple)):
        families = list(font_family)
    elif isinstance(font_family, str) and font_family:
        families = [font_family] + [f for f in resolve_font_families() if f != font_family]
    else:
        families = resolve_font_families()
    family_css = ', '.join(f"'{name}'" for name in families) + ', sans-serif'
    return f"""
QWidget {{ background:{c['background']}; color:{c['text']}; font-family:{family_css}; font-size:{BASE_FONT_PX}px; }}
QMainWindow, QDialog {{ background:{c['background']}; }}
QFrame#Navigation {{ background:{c['raised']}; border-bottom:1px solid {c['border']}; }}
QLabel {{ background:transparent; }}
QLabel#Brand {{ font-size:16px; font-weight:600; color:{c['heading']}; }}
QLabel#Muted {{ color:{c['muted']}; }}
QLabel#Title {{ font-size:18px; font-weight:600; color:{c['heading']}; }}
QLabel#Section {{ font-weight:600; color:{c['heading']}; }}
QLabel#Value {{ font-size:18px; font-weight:600; color:{c['accent']}; }}
QLabel#Formula {{ padding:6px 8px; background:{c['formula_bg']}; color:{c['formula_text']}; border-left:3px solid {c['primary_border']}; }}
QFrame#Card, QFrame#Inspector {{ background:{c['surface']}; border:1px solid {c['border']}; border-radius:3px; }}
QFrame#Card QLabel, QFrame#Inspector QLabel {{ background:transparent; }}
QFrame#ToolbarCard {{ background:{c['surface']}; border:1px solid {c['border']}; border-radius:3px; }}
QFrame#ToolbarCard QLabel {{ background:transparent; }}
QFrame#VLine {{ background:{c['border']}; border:0; max-width:1px; min-width:1px; margin:3px 2px; }}
QPushButton {{ background:{c['button']}; border:1px solid {c['input_border']}; border-radius:2px;
    padding:4px 10px; color:{c['text']}; min-height:22px; }}
QPushButton:hover {{ background:{c['button_hover']}; border-color:{c['accent']}; }}
QPushButton:pressed {{ background:{c['button_pressed']}; }}
QPushButton:focus {{ border-color:{c['accent']}; }}
QPushButton:disabled {{ color:{c['disabled']}; border-color:{c['border']}; background:{c['disabled_bg']}; }}
QPushButton#Subtle {{ background:transparent; border:1px solid transparent; border-radius:2px;
    padding:3px 8px; color:{c['heading']}; font-weight:600; min-height:20px; text-align:left; }}
QPushButton#Subtle:hover {{ background:{c['hover']}; border-color:{c['border']}; }}
QPushButton#Primary {{ background:{c['primary']}; color:#ffffff; border:1px solid {c['primary_border']}; }}
QPushButton#Primary:hover {{ background:{c['primary_hover']}; }}
QPushButton#Primary:disabled {{ color:{c['disabled']}; background:{c['disabled_bg']}; border-color:{c['border']}; }}
QDialogButtonBox QPushButton {{ min-width:76px; padding:5px 14px; }}
QPushButton#Nav {{ border:0; border-radius:0; background:transparent; padding:8px 14px; border-bottom:2px solid transparent; }}
QPushButton#Nav:checked {{ background:{c['surface']}; color:{c['heading']}; border-bottom:2px solid {c['accent']}; }}
QPushButton#Nav:hover {{ background:{c['hover']}; }}
QPushButton#Chip {{ border:1px solid {c['border']}; border-radius:11px; padding:2px 10px; min-height:20px; background:{c['surface']}; }}
QPushButton#Chip:checked {{ background:{c['selection']}; color:{c['selection_text']}; border-color:{c['accent']}; font-weight:600; }}
QPushButton#Chip:hover {{ border-color:{c['accent']}; }}
QLineEdit, QTextEdit {{ background:{c['input']}; color:{c['text']};
    border:1px solid {c['input_border']}; border-radius:3px; padding:4px 8px;
    selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}
QLineEdit {{ min-height:24px; }}
QSpinBox, QDoubleSpinBox {{ background:{c['input']}; color:{c['text']}; border:1px solid {c['input_border']};
    border-radius:3px; padding:4px 6px; min-height:24px;
    selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}
QComboBox {{ background:{c['input']}; color:{c['text']}; border:1px solid {c['input_border']};
    border-radius:3px; padding:4px 10px; padding-right:24px; min-height:24px; combobox-popup:0;
    selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}
QComboBox:hover {{ border-color:{c['accent']}; }}
QComboBox QLineEdit {{ border:0; background:transparent; padding:0 2px; min-height:0; }}
QTableWidget QComboBox {{ border:1px solid {c['border']}; padding:2px 8px; margin:2px; min-height:24px; }}
QTableWidget QLineEdit, QTableWidget QSpinBox, QTableWidget QDoubleSpinBox {{ border:1px solid {c['border']}; padding:2px 6px; margin:2px; min-height:22px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {{ border-color:{c['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ background:{c['raised']}; color:{c['disabled']}; border-color:{c['border']}; }}
QComboBox QAbstractItemView {{ background:{c['raised']}; color:{c['text']}; border:1px solid {c['input_border']};
    selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; outline:0; }}
QComboBox QAbstractItemView::item {{ min-height:30px; padding:4px 10px; }}
QTableView, QTreeView, QListWidget, QTextBrowser {{ background:{c['surface']}; border:1px solid {c['border']};
    gridline-color:{c['grid']}; alternate-background-color:{c['raised']}; selection-background-color:{c['selection']}; selection-color:{c['selection_text']}; }}
QTableView::item, QTreeView::item {{ padding:4px 6px; border:0; }}
QTreeView::item {{ min-height:28px; padding:4px 6px; border-right:1px solid {c['grid']}; }}
QTableView::item:selected, QTreeView::item:selected, QListWidget::item:selected {{ background:{c['selection']}; color:{c['selection_text']}; }}
QHeaderView::section {{ background:{c['header']}; color:{c['heading']}; font-weight:600; border:0; border-right:1px solid {c['border']}; border-bottom:1px solid {c['border']}; padding:6px 6px; }}
QTableCornerButton::section {{ background:{c['header']}; border:1px solid {c['border']}; }}
QListWidget::item {{ padding:7px; }}
QListWidget::item:hover {{ background:{c['hover']}; }}
QTabWidget::pane {{ border:1px solid {c['border']}; background:{c['surface']}; }}
QTabBar::tab {{ background:{c['raised']}; color:{c['muted']}; border:1px solid {c['border']}; padding:6px 14px; margin-right:2px; }}
QTabBar::tab:selected {{ background:{c['surface']}; color:{c['heading']}; border-bottom:2px solid {c['accent']}; }}
QTabBar::tab:hover {{ background:{c['hover']}; color:{c['text']}; }}
QScrollArea {{ border:1px solid {c['border']}; }}
QScrollBar:vertical {{ background:{c['surface']}; width:12px; margin:0; }}
QScrollBar:horizontal {{ background:{c['surface']}; height:12px; margin:0; }}
QScrollBar::handle {{ background:{c['handle']}; min-height:24px; min-width:24px; }}
QScrollBar::handle:hover {{ background:{c['handle_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
QSplitter::handle {{ background:{c['border']}; }}
QSplitter::handle:hover {{ background:{c['primary_border']}; }}
QStatusBar {{ color:#ffffff; background:{c['status_bg']}; padding:2px 8px; }}
QToolTip {{ background:{c['raised']}; color:{c['text']}; border:1px solid {c['input_border']}; padding:5px; }}
QMenu {{ background:{c['raised']}; color:{c['text']}; border:1px solid {c['input_border']}; }}
QMenu::item {{ padding:5px 18px; }}
QMenu::item:selected {{ background:{c['selection']}; color:{c['selection_text']}; }}
QCheckBox, QRadioButton {{ spacing:7px; background:transparent; }}
QTreeView::indicator, QTableView::indicator, QCheckBox::indicator {{ width:15px; height:15px; border:1px solid {c['input_border']}; border-radius:2px; background:{c['raised']}; }}
QTreeView::indicator:checked, QTableView::indicator:checked, QCheckBox::indicator:checked {{ background:{c['primary']}; border-color:{c['primary_border']}; image:url("{_CHECK_ICON}"); }}
QTreeView::indicator:indeterminate, QTableView::indicator:indeterminate {{ background:{c['primary']}; border-color:{c['primary_border']}; image:url("{_MIXED_ICON}"); }}
QCheckBox::indicator:disabled {{ border-color:{c['border']}; background:{c['disabled_bg']}; }}
QRadioButton::indicator {{ width:13px; height:13px; border:1px solid {c['input_border']}; border-radius:7px; background:{c['raised']}; }}
QRadioButton::indicator:checked {{ background:{c['accent']}; border-color:{c['heading']}; }}
"""


STYLE = build_stylesheet('dark', FONT_FAMILY)


def apply_theme(app, mode='dark', *, persist=True):
    global _ACTIVE_THEME, STYLE
    mode = 'light' if mode == 'light' else 'dark'
    _ACTIVE_THEME = mode
    COLORS.clear()
    COLORS.update(LIGHT_COLORS if mode == 'light' else DARK_COLORS)
    if persist:
        QSettings('DuoYuWang', 'GJB299D').setValue('ui/theme', mode)
    families = resolve_font_families()
    font = QFont(families[0])
    font.setFamilies(families)
    font.setPixelSize(BASE_FONT_PX)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferVerticalHinting)
    app.setFont(font)
    app.setPalette(palette(mode))
    STYLE = build_stylesheet(mode, families)
    app.setStyleSheet(STYLE)
    THEME_BUS.changed.emit(mode)
    return mode


def section_label(text, name='Muted', *, wrap=True):
    """Create a themed label, word-wrapped by default unless disabled for single-line toolbars."""
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def app_icon():
    """Return the application icon, preferring crisp pre-rendered PNG with SVG fallback."""
    for filename in ('gjb299d-calc.png', 'gjb299d-calc.svg'):
        candidate = Path(_asset_path(filename))
        if candidate.is_file():
            return QIcon(str(candidate))
    return QIcon()


def fit_window(widget, width, height, *, min_width=None, min_height=None):
    """Size and optionally clamp a window/dialog to stay within desktop work area."""
    target_w, target_h = width, height
    screen = widget.screen() or QGuiApplication.primaryScreen()
    if screen is not None and QGuiApplication.platformName() not in ('offscreen', 'minimal'):
        avail = screen.availableGeometry()
        if avail.width() > 200 and avail.height() > 200:
            max_w = max(480, avail.width() - 24)
            max_h = max(360, avail.height() - 36)
            target_w = min(target_w, max_w)
            target_h = min(target_h, max_h)
            if min_width is not None:
                min_width = min(min_width, max_w)
            if min_height is not None:
                min_height = min(min_height, max_h)
    if min_width is not None and min_height is not None:
        widget.setMinimumSize(min_width, min_height)
    widget.resize(target_w, target_h)


def text_browser():
    browser = QTextBrowser()
    browser.setAcceptDrops(False)
    browser.document().setDefaultStyleSheet(
        'h1, h2 { font-size:18px; font-weight:600; margin:8px 0; } '
        'h3 { font-size:15px; font-weight:600; margin:8px 0 4px; } '
        'p { margin:4px 0; }')
    return browser
