# -*- coding: utf-8 -*-
"""
ui/widgets.py
-------------
Small custom-painted widgets that carry the panel's visual language,
things a stylesheet can't express:

  ChamferFrame    panel with cut corners and an optional filled title tab
  BrandMark       the double-chevron mark next to the panel title
  StripedProgress hairline progress bar with moving diagonal stripes
  Banner          clickable state strip (e.g. "data changed, refresh")

All colours come from ui/theme.py.
"""

from qgis.PyQt.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QFrame, QLabel, QSizePolicy, QToolButton, QWidget

from . import icons


def tool_button(text, icon_name, theme, tooltip='', checkable=False, parent=None):
    b = QToolButton(parent)
    b.setProperty('det', 'tool')
    b.setText(text)
    if icon_name:
        b.setIcon(icons.icon(icon_name, theme))
        b.setIconSize(icons.ICON_SIZE)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                             else Qt.ToolButtonStyle.ToolButtonIconOnly)
    b.setCheckable(checkable)
    b.setToolTip(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


def menu_section(menu, title):
    """Section header for a QMenu. QMenu.addSection() only draws a plain
    separator with most Qt styles (Fusion, Windows), so the title would be
    lost; a disabled item styled by the theme shows it everywhere."""
    if not menu.isEmpty():
        menu.addSeparator()
    action = menu.addAction(title.upper())
    action.setEnabled(False)
    font = action.font()
    font.setBold(True)
    font.setPointSizeF(7.0)
    action.setFont(font)
    return action


def muted_label(text='', parent=None):
    lab = QLabel(text, parent)
    lab.setProperty('det', 'muted')
    return lab


def readout_html(theme, pairs, sep='&nbsp;&nbsp; '):
    """'[KEY] value' pairs as rich text: muted key, strong value.
    Each pair is unbreakable; the separator ends with a normal space so
    the line can wrap BETWEEN pairs in narrow panels."""
    parts = []
    for key, value in pairs:
        parts.append(
            '<span style="color:{m}">{k}</span>&nbsp;<span style="color:{i};font-weight:600">{v}</span>'
            .format(m=theme.muted, i=theme.ink, k=key, v=str(value).replace(' ', '&nbsp;')))
    return sep.join(parts)


def chamfer_path(rect, c, cut=('tl', 'br')):
    """Rectangle path with 45° cuts of size c on the given corners."""
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    pts = []
    pts += [QPointF(x0, y0 + c), QPointF(x0 + c, y0)] if 'tl' in cut else [QPointF(x0, y0)]
    pts += [QPointF(x1 - c, y0), QPointF(x1, y0 + c)] if 'tr' in cut else [QPointF(x1, y0)]
    pts += [QPointF(x1, y1 - c), QPointF(x1 - c, y1)] if 'br' in cut else [QPointF(x1, y1)]
    pts += [QPointF(x0 + c, y1), QPointF(x0, y1 - c)] if 'bl' in cut else [QPointF(x0, y1)]
    path = QPainterPath()
    path.addPolygon(QPolygonF(pts))
    path.closeSubpath()
    return path


class ChamferFrame(QFrame):
    """Panel with chamfered corners and an optional title tab."""

    TAB_H = 18

    def __init__(self, theme, title='', chamfer=9, dots=True, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._title = title
        self._chamfer = chamfer
        self._dots = dots
        self._update_margins()

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def set_title(self, title):
        self._title = title
        self._update_margins()
        self.update()

    def _update_margins(self):
        top = self.TAB_H + 8 if self._title else 10
        self.setContentsMargins(10, top, 10, 10)

    def paintEvent(self, event):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = chamfer_path(rect, self._chamfer)
        p.fillPath(path, t.color('surface'))
        p.setPen(QPen(t.color('line'), 1.0))
        p.drawPath(path)

        if self._title:
            font = t.font(7.2, 'bold', 0.8)
            fm = QFontMetrics(font)
            text = self._title.upper()
            tw = fm.horizontalAdvance(text) + self._chamfer + 14
            h = self.TAB_H
            c = self._chamfer
            tab = QPolygonF([QPointF(rect.left(), rect.top() + c), QPointF(rect.left() + c, rect.top()),
                             QPointF(rect.left() + tw, rect.top()), QPointF(rect.left() + tw + h, rect.top() + h),
                             QPointF(rect.left(), rect.top() + h)])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color('ink'))
            p.drawPolygon(tab)
            p.setFont(font)
            p.setPen(t.color('surface'))
            p.drawText(QRectF(rect.left() + c + 4, rect.top(), tw, h),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)

        if self._dots:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color('muted'))
            x = rect.right() - self._chamfer - 26
            for i in range(3):
                p.drawRect(QRectF(x + i * 6, rect.top() + 6, 2.5, 2.5))
        p.end()


class BrandMark(QWidget):
    """Two filled chevrons (the panel's mark)."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setFixedSize(22, 16)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.theme.color('ink'))
        for x0 in (1, 10):
            p.drawPolygon(QPolygonF([
                QPointF(x0, 1), QPointF(x0 + 4, 1), QPointF(x0 + 10, 8),
                QPointF(x0 + 4, 15), QPointF(x0, 15), QPointF(x0 + 6, 8)]))
        p.end()


def draw_stripes(p, rect, color, spacing=6.0, width=2.0, offset=0.0):
    """Diagonal (///) stripes clipped to rect."""
    p.save()
    p.setClipRect(rect)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    h = rect.height()
    x = rect.left() - h + (offset % spacing)
    while x < rect.right() + h:
        p.drawLine(QPointF(x, rect.bottom()), QPointF(x + h, rect.top()))
        x += spacing
    p.restore()


class StripedProgress(QWidget):
    """Hairline progress bar. value=None -> indeterminate (moving stripes)."""

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self._value = None
        self._offset = 0.0
        self.setFixedHeight(10)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def setValue(self, value):
        self._value = value
        self.update()

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self):
        self._offset += 1.0
        self.update()

    def paintEvent(self, event):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(t.color('line'), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        inner = rect.adjusted(2, 2, -2, -2)
        if self._value is None:
            draw_stripes(p, inner, t.color('ink', 0.55), offset=self._offset)
        else:
            w = inner.width() * max(0.0, min(100.0, self._value)) / 100.0
            filled = QRectF(inner.left(), inner.top(), w, inner.height())
            p.fillRect(filled, t.color('ink'))
            rest = QRectF(inner.left() + w, inner.top(), inner.width() - w, inner.height())
            draw_stripes(p, rest, t.color('line'), offset=self._offset)
        p.end()


class Banner(QWidget):
    """Thin clickable strip: striped block + uppercase message."""

    clicked = pyqtSignal()

    def __init__(self, theme, tone='signal', parent=None):
        super().__init__(parent)
        self.theme = theme
        self.tone = tone
        self._text = ''
        self.setFixedHeight(22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def setText(self, text):
        self._text = text
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, event):
        t = self.theme
        color = t.color(self.tone)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(color, 1.0))
        p.setBrush(QBrush(t.color('surface')))
        p.drawRect(rect)
        block = QRectF(rect.left(), rect.top(), 34, rect.height())
        draw_stripes(p, block.adjusted(2, 2, -2, -2), color, spacing=5, width=2)
        p.setPen(color)
        p.setFont(t.font(7.2, 'bold', 0.6))
        p.drawText(rect.adjusted(44, 0, -8, 0),
                   int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self._text.upper())
        p.end()
