# -*- coding: utf-8 -*-
"""
ui/icons.py
-----------
Line icons drawn as tiny SVGs at runtime, so they can take the theme's
ink colour (static SVG files can't be recoloured for dark mode).
16x16 grid, 1.5 px strokes, square caps: same language as the panel.
"""

from qgis.PyQt.QtCore import QByteArray, QSize, Qt
from qgis.PyQt.QtGui import QIcon, QPainter, QPixmap
from qgis.PyQt.QtSvg import QSvgRenderer

_PATHS = {
    # circular arrow
    'refresh': '<path d="M13 8a5 5 0 1 1-1.6-3.7"/><path d="M13 2.5v3.2H9.8"/>',
    # stop square
    'stop': '<rect x="4" y="4" width="8" height="8" fill="{c}"/>',
    # dashed selection box with filled corner
    'selection': '<path d="M2.5 5V2.5H5M11 2.5h2.5V5M13.5 11v2.5H11M5 13.5H2.5V11"/>'
                 '<rect x="6" y="6" width="4" height="4" fill="{c}"/>',
    # loop / auto
    'auto': '<path d="M3 6.5h8.5l-2-2M13 9.5H4.5l2 2"/>',
    # sliders
    'settings': '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>'
                '<rect x="9" y="3" width="2.5" height="3" fill="{c}"/>'
                '<rect x="4.5" y="6.5" width="2.5" height="3" fill="{c}"/>'
                '<rect x="10" y="10" width="2.5" height="3" fill="{c}"/>',
    # palette / symbology: three squares graded
    'symbology': '<rect x="2.5" y="9" width="3" height="4.5" fill="none"/>'
                 '<rect x="6.5" y="6" width="3" height="7.5" fill="{c}" fill-opacity="0.45"/>'
                 '<rect x="10.5" y="2.5" width="3" height="11" fill="{c}"/>',
    'search': '<circle cx="7" cy="7" r="4.2"/><path d="M10.2 10.2l3.3 3.3"/>',
    'table': '<rect x="2.5" y="3" width="11" height="10"/><path d="M2.5 6.5h11M2.5 10h11M6.5 3v10"/>',
    'copy': '<rect x="5" y="5" width="8.5" height="8.5"/><path d="M3 11V2.5h8.5"/>',
    'restore': '<path d="M3 8a5 5 0 1 0 1.6-3.7"/><path d="M3 2.5v3.2h3.2"/>',
    'chevrons': '<path d="M3 3.5l4.5 4.5L3 12.5M8.5 3.5L13 8l-4.5 4.5"/>',
}


def svg(name, color):
    body = _PATHS[name].format(c=color)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" '
            'fill="none" stroke="{c}" stroke-width="1.5" stroke-linecap="square" '
            'stroke-linejoin="miter">{body}</svg>').format(c=color, body=body)


def pixmap(name, color, size=16, ratio=2.0):
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode('utf-8')))
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    renderer.render(p)
    p.end()
    px.setDevicePixelRatio(ratio)
    return px


def icon(name, theme, size=16, color=None):
    ic = QIcon()
    ic.addPixmap(pixmap(name, color or theme.ink, size), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, theme.surface, size), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, theme.line, size), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


ICON_SIZE = QSize(14, 14)
