# -*- coding: utf-8 -*-
"""
ui/delegates.py
---------------
Custom painting for the statistics table:

  FIELD         name (+ alias in grey) and, on hover, a symbology button
  TYPE          outlined type badge (INT, DEC, TXT, DATE...) + filled KEY
                badge when every value is unique
  DISTRIBUTION  sparkline: histogram or top categories
  NULLS         hairline bar with diagonal stripes for the null share
  numbers       right-aligned tabular figures; cells that select features
                on click carry a small corner tick and underline on hover

Painting with QPainter (instead of rendering a matplotlib figure per
field like before) is what keeps the table instant even with 100+ fields.
"""

from qgis.PyQt.QtCore import QEvent, QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFontMetrics, QPainter, QPen, QPolygonF
from qgis.PyQt.QtWidgets import QStyle, QStyledItemDelegate, QTableView

from . import formatting as fmt
from . import icons
from .stats_model import CLICK_ROLE, KEY_ROLE, PENDING, PROFILE_ROLE, ROW_ROLE
from .widgets import draw_stripes

ROW_HEIGHT = 26
PAD = 8


class StatsView(QTableView):
    """QTableView that tracks the hovered cell (for hover styling)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_index = None
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
        index = self.indexAt(pos)
        old = self.hover_index
        self.hover_index = index if index.isValid() else None
        if (old is None) != (self.hover_index is None) or (
                old is not None and self.hover_index is not None and
                (old.row() != self.hover_index.row() or old.column() != self.hover_index.column())):
            self.viewport().update()
        clickable = self.hover_index is not None and (
            self.hover_index.data(CLICK_ROLE) or self.hover_index.data(KEY_ROLE) == 'dist')
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if clickable
                                  else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_index = None
        self.viewport().update()
        super().leaveEvent(event)


class StatsDelegate(QStyledItemDelegate):

    symbology_requested = pyqtSignal(str)   # field name

    def __init__(self, theme, view, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.view = view
        self._sym_rects = {}
        self._build_cache()

    def set_theme(self, theme):
        self.theme = theme
        self._build_cache()

    def _build_cache(self):
        t = self.theme
        self.f_text = t.font(8.0)
        self.f_name = t.font(8.0, 'medium')
        self.f_badge = t.font(6.6, 'bold', 0.5)
        self.f_small = t.font(7.2)
        self.sym_px = icons.pixmap('symbology', t.ink, 14)
        self.sym_px_sel = icons.pixmap('symbology', t.on_selection, 14)

    # ------------------------------------------------------------------
    def sizeHint(self, option, index):
        key = index.data(KEY_ROLE)
        if key == 'dist':
            return QSize(96, ROW_HEIGHT)
        if key == 'nulls':
            return QSize(124, ROW_HEIGHT)
        if key == 'type':
            return QSize(78, ROW_HEIGHT)
        text = index.data(Qt.ItemDataRole.DisplayRole) or ''
        fm = QFontMetrics(self.f_name if key == 'field' else self.f_text)
        extra = 30 if key == 'field' else 0
        return QSize(fm.horizontalAdvance(str(text)) + 2 * PAD + extra + 6, ROW_HEIGHT)

    def _row_state(self, option, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hi = self.view.hover_index
        hover_row = hi is not None and hi.row() == index.row()
        hover_cell = hover_row and hi.column() == index.column()
        return selected, hover_row, hover_cell

    def paint(self, painter, option, index):
        t = self.theme
        key = index.data(KEY_ROLE)
        selected, hover_row, hover_cell = self._row_state(option, index)
        r = QRectF(option.rect)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg = t.color('selection') if selected else (t.color('hover') if hover_row else t.color('surface'))
        painter.fillRect(r, bg)
        painter.setPen(QPen(t.color('line_soft'), 1.0))
        painter.drawLine(QPointF(r.left(), r.bottom() - 0.5), QPointF(r.right(), r.bottom() - 0.5))

        ink = t.color('on_selection') if selected else t.color('ink')
        muted = t.color('on_selection', 0.6) if selected else t.color('muted')
        prof = index.data(PROFILE_ROLE)

        if key == 'field':
            self._paint_field(painter, r, index, ink, muted, selected, hover_row)
        elif key == 'type':
            self._paint_type(painter, r, index, prof, ink, selected)
        elif key == 'dist':
            self._paint_spark(painter, r, prof, ink, muted)
        elif key == 'nulls':
            self._paint_nulls(painter, r, index, prof, ink, muted, selected)
        else:
            self._paint_number(painter, r, index, ink, muted, hover_cell, selected)
        painter.restore()

    # ------------------------------------------------------------------
    def _paint_field(self, p, r, index, ink, muted, selected, hover_row):
        row = index.data(ROW_ROLE)
        text_rect = r.adjusted(PAD, 0, -PAD - (22 if hover_row else 0), 0)
        p.setFont(self.f_name)
        p.setPen(ink)
        fm = QFontMetrics(self.f_name)
        name = fm.elidedText(row.name, Qt.TextElideMode.ElideRight, int(text_rect.width()))
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), name)
        used = fm.horizontalAdvance(name)
        if row.alias and row.alias != row.name and used + 30 < text_rect.width():
            p.setFont(self.f_small)
            p.setPen(muted)
            alias_rect = text_rect.adjusted(used + 8, 0, 0, 0)
            alias = QFontMetrics(self.f_small).elidedText(row.alias, Qt.TextElideMode.ElideRight,
                                                          int(alias_rect.width()))
            p.drawText(alias_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), alias)
        if hover_row:
            ir = QRectF(r.right() - PAD - 16, r.center().y() - 8, 16, 16)
            p.drawPixmap(ir.topLeft(), self.sym_px_sel if selected else self.sym_px)
            self._sym_rects[row.name] = ir.adjusted(-3, -3, 3, 3)

    def _badge(self, p, rect, text, filled, ink, bg):
        p.setFont(self.f_badge)
        fm = QFontMetrics(self.f_badge)
        w = fm.horizontalAdvance(text) + 10
        b = QRectF(rect.left(), rect.center().y() - 7, w, 14)
        if filled:
            p.fillRect(b, ink)
            p.setPen(bg)
        else:
            p.setPen(QPen(ink, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(b.adjusted(0.5, 0.5, -0.5, -0.5))
        p.drawText(b, int(Qt.AlignmentFlag.AlignCenter), text)
        return w

    def _paint_type(self, p, r, index, prof, ink, selected):
        t = self.theme
        bg = t.color('selection') if selected else t.color('surface')
        x = r.left() + PAD
        w = self._badge(p, QRectF(x, r.top(), 0, r.height()), index.data(Qt.ItemDataRole.DisplayRole),
                        False, ink, bg)
        if prof is not None and prof.is_unique:
            self._badge(p, QRectF(x + w + 4, r.top(), 0, r.height()), 'KEY', True, ink, bg)

    def _paint_spark(self, p, r, prof, ink, muted):
        area = r.adjusted(PAD, 6, -PAD, -6)
        if prof is None:
            draw_stripes(p, area, self.theme.color('line_soft'), spacing=5, width=1.5)
            return
        counts = None
        tails = (None, None)
        if prof.is_continuous and prof.histogram:
            counts = prof.histogram['counts']
            tails = (prof.histogram.get('under'), prof.histogram.get('over'))
        elif prof.is_categorical and prof.top:
            counts = [c for _k, _r, c in prof.top[:12]]
        if not counts:
            p.setPen(muted)
            p.setFont(self.f_text)
            p.drawText(area, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), fmt.DASH)
            return
        # Overflow bins (heavy tails) as thin muted bars at both ends.
        under, over = tails
        if under is not None:
            area.setLeft(area.left() + 5)
        if over is not None:
            area.setRight(area.right() - 5)
        top = max(list(counts) + [v for v in tails if v is not None]) or 1.0
        n = len(counts)
        gap = 1.0 if n < 30 else 0.0
        bw = (area.width() - gap * (n - 1)) / n
        color = ink if prof.is_continuous else muted
        for i, c in enumerate(counts):
            h = max(1.0 if c > 0 else 0.0, area.height() * c / top)
            p.fillRect(QRectF(area.left() + i * (bw + gap), area.bottom() - h, max(bw, 0.8), h), color)
        for value, x in ((under, area.left() - 5), (over, area.right() + 2)):
            if value:
                h = max(1.0, area.height() * value / top)
                p.fillRect(QRectF(x, area.bottom() - h, 3, h), muted)
        p.setPen(QPen(self.theme.color('line'), 1.0))
        p.drawLine(QPointF(area.left(), area.bottom() + 1.5), QPointF(area.right(), area.bottom() + 1.5))

    def _paint_nulls(self, p, r, index, prof, ink, muted, selected):
        t = self.theme
        bar = QRectF(r.left() + PAD, r.center().y() - 4, 46, 8)
        p.setPen(QPen(t.color('line') if not selected else muted, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bar.adjusted(0.5, 0.5, -0.5, -0.5))
        # The cell shows the share; the exact count is in the tooltip and
        # in the detail panel.
        if prof is not None and prof.null_count:
            ratio = prof.null_ratio
            w = max(2.0, (bar.width() - 2) * ratio)
            fill = QRectF(bar.left() + 1, bar.top() + 1, w, bar.height() - 2)
            p.fillRect(fill, t.color('signal_soft') if not selected else t.color('signal', 0.35))
            draw_stripes(p, fill, t.color('signal'), spacing=3.5, width=1.4)
            pct = fmt.fmt_percent(ratio)
            p.setFont(self.f_text)
            p.setPen(t.color('signal') if not selected else ink)
            tr = r.adjusted(bar.width() + PAD + 6, 0, -PAD, 0)
            p.drawText(tr, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), pct)
            self._corner_tick(p, r, selected)
            hi = self.view.hover_index
            if hi is not None and hi.row() == index.row() and hi.column() == index.column():
                self._underline(p, tr, pct, self.f_text, t.color('signal') if not selected else ink)
        else:
            p.setFont(self.f_text)
            p.setPen(muted)
            p.drawText(r.adjusted(bar.width() + PAD + 6, 0, -PAD, 0),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
                       PENDING if prof is None else '0')

    def _paint_number(self, p, r, index, ink, muted, hover_cell, selected):
        text = index.data(Qt.ItemDataRole.DisplayRole) or ''
        clickable = bool(index.data(CLICK_ROLE))
        rect = r.adjusted(PAD, 0, -PAD, 0)
        p.setFont(self.f_text)
        is_muted = text in (fmt.DASH, PENDING)
        if index.data(KEY_ROLE) == 'outliers' and clickable and not selected:
            color = self.theme.color('signal')
        else:
            color = muted if is_muted else (ink if clickable or selected else self.theme.color('ink_soft'))
        p.setPen(color)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), text)
        if clickable:
            self._corner_tick(p, r, selected)
            if hover_cell:
                self._underline(p, rect, text, self.f_text, color)

    def _corner_tick(self, p, r, selected):
        c = self.theme.color('on_selection') if selected else self.theme.color('ink')
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(r.right() - 6, r.top() + 1), QPointF(r.right() - 1, r.top() + 1),
                                 QPointF(r.right() - 1, r.top() + 6)]))

    def _underline(self, p, rect, text, font, color):
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text)
        y = rect.center().y() + fm.ascent() / 2 + 2
        p.setPen(QPen(color, 1.0))
        p.drawLine(QPointF(rect.right() - w, y), QPointF(rect.right(), y))

    # ------------------------------------------------------------------
    def editorEvent(self, event, model, option, index):
        if (event.type() == QEvent.Type.MouseButtonRelease and index.data(KEY_ROLE) == 'field'):
            row = index.data(ROW_ROLE)
            rect = self._sym_rects.get(row.name)
            pos = event.position() if hasattr(event, 'position') else QPointF(event.pos())
            if rect is not None and rect.contains(pos):
                self.symbology_requested.emit(row.name)
                return True
        return super().editorEvent(event, model, option, index)
