# -*- coding: utf-8 -*-
"""
ui/theme.py
-----------
Single source of truth for the panel's look: colour tokens (light and
dark variants), the bundled JetBrains Mono font, the Qt stylesheet and
the matplotlib styling. Nothing else in the plugin hard-codes a colour.

Visual language ("technical / HUD minimalism"):
  - monospaced type, uppercase micro-labels, tabular figures;
  - 1 px hairlines, no shadows, no gradients, no rounded corners;
  - chamfered (cut) corners on panels instead of radii;
  - one ink colour + one signal colour (data-quality issues only);
  - diagonal stripes only to signal a state (progress, nulls, stale data).

The stylesheet is applied to the dock only, never to the QGIS window.
"""

import os

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtGui import QColor, QFont, QFontDatabase, QPalette
from qgis.PyQt.QtWidgets import QApplication

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(PLUGIN_DIR, 'fonts')
FONT_FILES = ('JetBrainsMono-Regular.ttf', 'JetBrainsMono-Medium.ttf', 'JetBrainsMono-Bold.ttf')
FALLBACK_FAMILIES = ['JetBrains Mono', 'Consolas', 'Menlo', 'DejaVu Sans Mono', 'monospace']

TOKENS = {
    'light': {
        'bg': '#eceee9',
        'surface': '#f7f8f5',
        'surface_alt': '#e3e6e0',
        'hover': '#dfe3dc',
        'ink': '#26303b',
        'ink_soft': '#47525e',
        'muted': '#7b858f',
        'line': '#bcc3c8',
        'line_soft': '#d5dad9',
        'signal': '#d9480f',
        'signal_soft': '#f3d3c2',
        'selection': '#26303b',
        'on_selection': '#f7f8f5',
        'overlay': '#d9480f',
    },
    'dark': {
        'bg': '#13171c',
        'surface': '#191e24',
        'surface_alt': '#20262e',
        'hover': '#242b34',
        'ink': '#d8dee4',
        'ink_soft': '#aeb6be',
        'muted': '#7a848e',
        'line': '#39424c',
        'line_soft': '#262d35',
        'signal': '#ff7a45',
        'signal_soft': '#4a2a1d',
        'selection': '#d8dee4',
        'on_selection': '#13171c',
        'overlay': '#ff7a45',
    },
}

SETTINGS_KEY = 'data_engineering_toolkit/theme'   # 'auto' | 'light' | 'dark'

_FONT_FAMILY = None
_MPL_FONT_REGISTERED = False


def register_fonts():
    """Registers the bundled fonts with Qt (once) and returns the family."""
    global _FONT_FAMILY
    if _FONT_FAMILY is not None:
        return _FONT_FAMILY
    family = None
    for name in FONT_FILES:
        path = os.path.join(FONT_DIR, name)
        if not os.path.exists(path):
            continue
        font_id = QFontDatabase.addApplicationFont(path)
        if font_id >= 0 and family is None:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                family = families[0]
    _FONT_FAMILY = family or 'monospace'
    return _FONT_FAMILY


def register_matplotlib_fonts():
    global _MPL_FONT_REGISTERED
    if _MPL_FONT_REGISTERED:
        return
    try:
        from matplotlib import font_manager
        for name in FONT_FILES:
            path = os.path.join(FONT_DIR, name)
            if os.path.exists(path):
                font_manager.fontManager.addfont(path)
    except Exception:  # pragma: no cover - cosmetic only
        pass
    _MPL_FONT_REGISTERED = True


def preferred_mode():
    return QSettings().value(SETTINGS_KEY, 'auto')


def detect_mode(preference=None):
    """'light' or 'dark'. In 'auto' mode follows the QGIS UI theme by
    looking at the window colour of the current palette."""
    preference = preference or preferred_mode()
    if preference in ('light', 'dark'):
        return preference
    app = QApplication.instance()
    if app is not None:
        window = app.palette().color(QPalette.ColorRole.Window)
        if window.lightness() < 128:
            return 'dark'
    return 'light'


class Theme:
    """Colour tokens + font helpers for one mode."""

    def __init__(self, mode=None):
        self.mode = mode or detect_mode()
        self.tokens = dict(TOKENS[self.mode])
        self.family = register_fonts()

    def __getattr__(self, name):
        tokens = self.__dict__.get('tokens', {})
        if name in tokens:
            return tokens[name]
        raise AttributeError(name)

    @property
    def is_dark(self):
        return self.mode == 'dark'

    def color(self, name, alpha=None):
        c = QColor(self.tokens[name])
        if alpha is not None:
            c.setAlphaF(alpha)
        return c

    def font(self, size=8.0, weight='regular', spacing=0.0):
        f = QFont(self.family)
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSizeF(size)
        f.setWeight({'regular': QFont.Weight.Normal,
                     'medium': QFont.Weight.Medium,
                     'bold': QFont.Weight.Bold}[weight])
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        return f

    # ------------------------------------------------------------------
    def stylesheet(self):
        t = self.tokens
        fam = ', '.join('"{}"'.format(f) for f in [self.family] + FALLBACK_FAMILIES)
        return QSS_TEMPLATE.format(fam=fam, **t)

    def mpl_rc(self):
        register_matplotlib_fonts()
        t = self.tokens
        return {
            # Only fonts matplotlib is sure to find (DejaVu ships with it):
            # unknown names in the list make it log a warning per draw.
            'font.family': [self.family, 'DejaVu Sans Mono'],
            'font.size': 7.5,
            'axes.edgecolor': t['line'],
            'axes.labelcolor': t['muted'],
            'axes.facecolor': t['surface'],
            'axes.titlecolor': t['ink'],
            'figure.facecolor': t['surface'],
            'xtick.color': t['muted'],
            'ytick.color': t['muted'],
            'xtick.labelsize': 7,
            'ytick.labelsize': 7,
            'text.color': t['ink'],
            'grid.color': t['line_soft'],
            'grid.linewidth': 0.6,
            'hatch.color': t['overlay'],
            'hatch.linewidth': 1.0,
        }


# Notes on the stylesheet:
#  - every rule is scoped under #DETRoot (the dock's root widget), so the
#    QGIS main window is never affected;
#  - custom widgets opt in through the dynamic property `det`
#    (e.g. det="tool"), see ui/widgets.py.
QSS_TEMPLATE = """
#DETRoot {{
    background: {bg};
    color: {ink};
    font-family: {fam};
    font-size: 8pt;
}}
#DETRoot QWidget {{
    font-family: {fam};
    font-size: 8pt;
}}
#DETRoot QLabel {{
    color: {ink};
    background: transparent;
}}
#DETRoot QLabel[det="muted"] {{
    color: {muted};
}}
#DETRoot QToolButton[det="tool"] {{
    color: {ink};
    background: transparent;
    border: 1px solid {line};
    padding: 3px 7px;
    font-size: 7pt;
    font-weight: 600;
    min-height: 16px;
}}
#DETRoot QToolButton[det="tool"]:hover {{
    border-color: {ink};
    background: {hover};
}}
#DETRoot QToolButton[det="tool"]:checked {{
    background: {ink};
    color: {surface};
    border-color: {ink};
}}
#DETRoot QToolButton[det="tool"]:disabled {{
    color: {line};
    border-color: {line_soft};
}}
#DETRoot QToolButton[det="tool"]::menu-indicator {{
    image: none;
    width: 0px;
}}
#DETRoot QToolButton[det="signal"] {{
    color: {surface};
    background: {signal};
    border: 1px solid {signal};
    padding: 3px 7px;
    font-size: 7pt;
    font-weight: 600;
    min-height: 16px;
}}
#DETRoot QLineEdit {{
    color: {ink};
    background: transparent;
    border: none;
    border-bottom: 1px solid {line};
    padding: 3px 2px;
    font-size: 8pt;
    selection-background-color: {selection};
    selection-color: {on_selection};
}}
#DETRoot QLineEdit:focus {{
    border-bottom: 1px solid {ink};
}}
#DETRoot QComboBox {{
    color: {ink};
    background: {surface};
    border: 1px solid {line};
    padding: 2px 6px;
    min-height: 18px;
    font-size: 8pt;
}}
#DETRoot QComboBox:hover {{
    border-color: {ink};
}}
#DETRoot QComboBox::drop-down {{
    border: none;
    width: 16px;
}}
#DETRoot QComboBox QAbstractItemView {{
    background: {surface};
    color: {ink};
    border: 1px solid {ink};
    selection-background-color: {selection};
    selection-color: {on_selection};
    outline: 0;
}}
#DETRoot QTableView {{
    background: {surface};
    color: {ink};
    border: 1px solid {line};
    gridline-color: transparent;
    outline: 0;
    selection-background-color: {selection};
    selection-color: {on_selection};
}}
#DETRoot QHeaderView {{
    background: {surface};
}}
#DETRoot QHeaderView::section {{
    background: {surface};
    color: {muted};
    border: none;
    border-bottom: 1px solid {ink};
    padding: 5px 8px 4px 8px;
    font-size: 7pt;
    font-weight: 600;
}}
#DETRoot QHeaderView::section:hover {{
    color: {ink};
}}
#DETRoot QTableCornerButton::section {{
    background: {surface};
    border: none;
}}
#DETRoot QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
#DETRoot QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
#DETRoot QScrollBar::handle {{
    background: {line};
    min-height: 24px;
    min-width: 24px;
}}
#DETRoot QScrollBar::handle:hover {{
    background: {muted};
}}
#DETRoot QScrollBar::add-line, #DETRoot QScrollBar::sub-line {{
    width: 0; height: 0; border: none; background: none;
}}
#DETRoot QScrollBar::add-page, #DETRoot QScrollBar::sub-page {{
    background: none;
}}
#DETRoot QSplitter::handle {{
    background: {bg};
}}
#DETRoot QSplitter::handle:hover {{
    background: {line};
}}
#DETRoot QToolTip {{
    color: {ink};
    background: {surface};
    border: 1px solid {ink};
    padding: 4px;
}}
QMenu[det="menu"] {{
    background: {surface};
    color: {ink};
    border: 1px solid {ink};
    padding: 4px 0;
    font-family: {fam};
    font-size: 8pt;
}}
QMenu[det="menu"]::item {{
    padding: 5px 18px 5px 14px;
    background: transparent;
}}
QMenu[det="menu"]::item:selected {{
    background: {selection};
    color: {on_selection};
}}
QMenu[det="menu"]::item:disabled {{
    color: {muted};
}}
QMenu[det="menu"]::separator {{
    height: 1px;
    background: {line};
    margin: 4px 8px;
}}
QMenu[det="menu"]::section {{
    color: {muted};
    padding: 6px 14px 2px 14px;
    font-size: 7pt;
}}
QMenu[det="menu"]::indicator {{
    width: 10px;
    height: 10px;
    margin-left: 4px;
}}
QMenu[det="menu"]::indicator:checked {{
    background: {ink};
    border: 1px solid {ink};
}}
QMenu[det="menu"]::indicator:unchecked {{
    background: transparent;
    border: 1px solid {line};
}}
"""
