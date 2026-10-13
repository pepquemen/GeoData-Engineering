# -*- coding: utf-8 -*-
"""
ui/chart_panel.py
-----------------
Detail panel (right side of the dock): the selected field's key figures
and a large interactive chart, shown NEXT TO the table (master-detail)
instead of replacing it.

Chart behaviour:
  - numbers / dates / times: histogram over the true [min, max], with the
    median and the outlier fences marked;
  - text / booleans: horizontal bars of the top categories + "OTHER";
  - the current map selection is overlaid as hatched bars (brushing &
    linking: select on the map, see where it falls in the distribution);
  - click a bar: select (Ctrl: add, Ctrl+Shift: remove);
    right click: menu with select / add / remove / attribute table;
  - hovering a bar shows its range and count.
"""

import bisect
from collections import Counter

import matplotlib
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.ticker import FixedLocator, FuncFormatter, MaxNLocator

try:  # matplotlib >= 3.5: one backend for PyQt5 / PyQt6
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except ImportError:  # pragma: no cover - older matplotlib bundled with QGIS
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

import numpy as np
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QSizePolicy, QVBoxLayout

from .. import profiling_engine as pe
from .. import selection_helper as sh
from .. import stats_core
from ..translation import tr
from . import formatting as fmt
from .widgets import ChamferFrame, muted_label, readout_html


class _ThemedCanvas(FigureCanvas):
    """Applies the theme's rcParams on EVERY draw (resizes and repaints
    included), without touching matplotlib's global settings."""

    rc = {}

    def draw(self):
        with matplotlib.rc_context(self.rc):
            super().draw()


def modifiers_behavior():
    mods = QApplication.keyboardModifiers()
    ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
    shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
    if ctrl and shift:
        return sh.REMOVE
    if ctrl:
        return sh.ADD
    return sh.SET


class ChartPanel(ChamferFrame):

    select_requested = pyqtSignal(str, str)        # expression, behavior
    table_requested = pyqtSignal(str)              # expression

    def __init__(self, theme, parent=None):
        super().__init__(theme, title=tr('FIELD'), parent=parent)
        self.profile = None
        self.overlay = None          # (values, scale, selected_count)
        self._bars = []              # (patch, expression, tooltip)
        self._hover = None

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.readout = QLabel()
        self.readout.setTextFormat(Qt.TextFormat.RichText)
        self.readout.setWordWrap(True)
        top.addWidget(self.readout, 1)
        layout.addLayout(top)

        self.figure = Figure(figsize=(4, 2.4), dpi=96)
        self._enable_tight_layout()
        self.canvas = _ThemedCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.setMinimumHeight(120)
        self.canvas.mpl_connect('button_press_event', self._on_press)
        self.canvas.mpl_connect('motion_notify_event', self._on_motion)
        self.canvas.mpl_connect('figure_leave_event', self._on_leave)
        self.ax = self.figure.add_subplot(111)
        layout.addWidget(self.canvas, 1)

        bottom = QHBoxLayout()
        self.legend = QLabel()
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.hint = muted_label(tr('CLICK BAR: SELECT · CTRL: ADD · CTRL+SHIFT: REMOVE · RIGHT CLICK: MORE'))
        self.hint.setStyleSheet('font-size: 6.5pt;')
        # Never let the hint impose a minimum width: it is clipped instead.
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.hint.setToolTip(self.hint.text())
        bottom.addWidget(self.legend)
        bottom.addWidget(self.hint, 1)
        layout.addLayout(bottom)

        self.set_theme(theme)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_hint()

    def _fit_hint(self):
        # Show the usage hint only when it fits whole (never cut mid-sentence).
        available = self.contentsRect().width() - self.legend.sizeHint().width() - 12
        self.hint.setVisible(self.hint.sizeHint().width() <= available)

    def _enable_tight_layout(self):
        if hasattr(self.figure, 'set_layout_engine'):
            self.figure.set_layout_engine('tight', pad=0.4)
        else:  # pragma: no cover - matplotlib < 3.6
            self.figure.set_tight_layout({'pad': 0.4})

    # ------------------------------------------------------------------
    def set_theme(self, theme):
        super().set_theme(theme)
        self.theme = theme
        _ThemedCanvas.rc = theme.mpl_rc()
        self.canvas.rc = _ThemedCanvas.rc
        self.figure.set_facecolor(theme.surface)
        self.redraw()

    def set_profile(self, profile, placeholder=None):
        self.profile = profile
        self.overlay = None
        self._placeholder = placeholder
        self.redraw()

    def set_overlay(self, values, scale, selected_count):
        self.overlay = (values, scale, selected_count) if values is not None else None
        self.redraw()

    # ------------------------------------------------------------------
    def redraw(self):
        t = self.theme
        prof = self.profile
        self._bars = []
        self._hover = None
        with matplotlib.rc_context(self.canvas.rc):
            self.ax.clear()
            self.ax.set_facecolor(t.surface)
            if prof is None:
                self.set_title(tr('FIELD'))
                self.readout.setText('')
                self.legend.setText('')
                self._empty(getattr(self, '_placeholder', None) or tr('SELECT A FIELD IN THE TABLE'))
            else:
                self.set_title('{}  //  {}'.format(tr('FIELD'), prof.name))
                self.readout.setText(self._readout_text(prof))
                if prof.is_continuous and prof.histogram:
                    self._draw_histogram(prof)
                elif prof.is_categorical and prof.top:
                    self._draw_categories(prof)
                else:
                    self._empty(tr('NO VALUES TO CHART'))
                self._update_legend()
        self.canvas.draw_idle()

    def _empty(self, text):
        t = self.theme
        self.ax.set_axis_off()
        self.ax.text(0.5, 0.5, text, transform=self.ax.transAxes, ha='center', va='center',
                     color=t.muted, fontsize=7.5, fontweight='bold')

    def _style_axes(self):
        t = self.theme
        ax = self.ax
        for side in ('top', 'right', 'left'):
            ax.spines[side].set_visible(False)
        ax.spines['bottom'].set_color(t.ink)
        ax.spines['bottom'].set_linewidth(1.0)
        ax.tick_params(axis='both', length=3, width=0.8, colors=t.muted)
        ax.grid(axis='y', color=t.line_soft, linewidth=0.6)
        ax.set_axisbelow(True)

    def _readout_text(self, prof):
        pairs = [(tr('NULLS'), '{} ({})'.format(fmt.fmt_count(prof.null_count), fmt.fmt_percent(prof.null_ratio))),
                 (tr('DISTINCT'), ('>' + fmt.fmt_int(stats_core.DISTINCT_LIMIT)) if prof.distinct_capped
                  else fmt.fmt_count(prof.distinct))]
        if prof.is_continuous:
            pairs += [(tr('MIN'), fmt.fmt_value(prof, prof.min)),
                      (tr('MAX'), fmt.fmt_value(prof, prof.max)),
                      (tr('MEAN'), fmt.fmt_value(prof, prof.mean) if prof.is_temporal else fmt.fmt_decimal(prof.mean)),
                      (tr('MEDIAN'), ('~' if prof.sampled else '') + fmt.fmt_value(prof, prof.median))]
        elif prof.top:
            label, _raw, count = prof.top[0]
            pairs.append((tr('MODE'), '{} ({})'.format(_short(label, 24), fmt.fmt_count(count))))
        if prof.sampled:
            pairs.append((tr('SAMPLE'), fmt.fmt_int(prof.sample_size)))
        return readout_html(self.theme, pairs)

    def _update_legend(self):
        t = self.theme
        if self.overlay is None:
            self.legend.setText('')
            self._fit_hint()
            return
        _values, _scale, count = self.overlay
        self.legend.setText(
            '<span style="color:{c};font-weight:700">▨ {lab}</span>&nbsp;'
            '<span style="color:{i};font-weight:600">{n}</span>'.format(
                c=t.overlay, i=t.ink, lab=tr('MAP SELECTION'), n=fmt.fmt_count(count)))
        self._fit_hint()

    # ------------------------------------------------------------------
    def _draw_histogram(self, prof):
        t = self.theme
        ax = self.ax
        self._style_axes()
        hist = prof.histogram
        edges = hist['edges']
        counts = hist['counts']
        widths = np.diff(edges)
        est = '~' if hist.get('estimated') else ''
        patches = ax.bar(edges[:-1], counts, width=widths, align='edge', color=t.ink,
                         edgecolor=t.surface, linewidth=0.6)
        last = len(edges) - 2
        for i, patch in enumerate(patches):
            expr = sh.expression_for_range(prof.name, prof.kind, edges[i], edges[i + 1],
                                           upper_inclusive=(i == last))
            tip = '[{} – {}{}\n{}: {}{}'.format(
                fmt.fmt_value(prof, edges[i]), fmt.fmt_value(prof, edges[i + 1]), ']' if i == last else ')',
                tr('features'), est, fmt.fmt_count(round(counts[i])))
            self._bars.append((patch, expr, tip))

        # Overflow bins for heavy tails, drawn apart from the core range.
        lo, hi = edges[0], edges[-1]
        w = float(np.mean(widths)) if len(widths) else 1.0
        x_min, x_max = lo, hi
        under, over = hist.get('under'), hist.get('over')
        if under is not None:
            x = lo - 1.6 * w
            patch = ax.bar([x], [under], width=w, align='edge', color=t.muted, edgecolor=t.surface,
                           linewidth=0.6, hatch='///')[0]
            ax.text(x + w / 2, -0.04, '<', transform=ax.get_xaxis_transform(), ha='center', va='top',
                    color=t.muted, fontsize=7, fontweight='bold')
            tip = '< {}\n{}: {}{}'.format(fmt.fmt_value(prof, lo), tr('features'), est, fmt.fmt_count(round(under)))
            self._bars.append((patch, sh.expression_for_below(prof.name, prof.kind, lo), tip))
            x_min = x - 0.3 * w
        if over is not None:
            x = hi + 0.6 * w
            patch = ax.bar([x], [over], width=w, align='edge', color=t.muted, edgecolor=t.surface,
                           linewidth=0.6, hatch='///')[0]
            ax.text(x + w / 2, -0.04, '>', transform=ax.get_xaxis_transform(), ha='center', va='top',
                    color=t.muted, fontsize=7, fontweight='bold')
            tip = '> {}\n{}: {}{}'.format(fmt.fmt_value(prof, hi), tr('features'), est, fmt.fmt_count(round(over)))
            self._bars.append((patch, sh.expression_for_above(prof.name, prof.kind, hi), tip))
            x_max = x + 1.3 * w

        if self.overlay is not None:
            values, scale, _n = self.overlay
            if values:
                arr = np.asarray(values, dtype=float)
                sel_counts, _ = np.histogram(arr, bins=edges)
                ax.bar(edges[:-1], sel_counts * scale, width=widths, align='edge',
                       facecolor=to_rgba(t.overlay, 0.2), edgecolor=t.overlay, hatch='////', linewidth=1.0)
                if under is not None:
                    ax.bar([lo - 1.6 * w], [np.count_nonzero(arr < lo) * scale], width=w, align='edge',
                           facecolor=to_rgba(t.overlay, 0.2), edgecolor=t.overlay, hatch='////', linewidth=1.0)
                if over is not None:
                    ax.bar([hi + 0.6 * w], [np.count_nonzero(arr > hi) * scale], width=w, align='edge',
                           facecolor=to_rgba(t.overlay, 0.2), edgecolor=t.overlay, hatch='////', linewidth=1.0)

        if prof.median is not None and lo <= prof.median <= hi:
            ax.axvline(prof.median, color=t.ink_soft, linewidth=0.8, linestyle=(0, (2, 2)))
            ax.text(prof.median, 1.0, ' MED', transform=ax.get_xaxis_transform(), color=t.ink_soft,
                    fontsize=6.5, va='top', ha='left')
        for bound in (prof.outlier_lower, prof.outlier_upper):
            if bound is not None and lo < bound < hi:
                ax.axvline(bound, color=t.signal, linewidth=0.9, linestyle=(0, (4, 2)))
        ax.set_xlim(x_min, x_max)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: fmt.fmt_int(v) if v >= 0 else ''))
        ticks = [v for v in MaxNLocator(nbins=5).tick_values(lo, hi) if lo <= v <= hi]
        ax.xaxis.set_major_locator(FixedLocator(ticks))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: fmt.fmt_value(prof, v)))

    def _draw_categories(self, prof):
        t = self.theme
        ax = self.ax
        self._style_axes()
        ax.grid(axis='y', visible=False)
        ax.grid(axis='x', color=t.line_soft, linewidth=0.6)
        ax.spines['bottom'].set_visible(False)
        ax.spines['left'].set_visible(True)
        ax.spines['left'].set_color(t.ink)

        top = list(prof.top)
        labels = [_short(k, 22) for k, _r, _c in top]
        counts = [c for _k, _r, c in top]
        exprs = [sh.expression_for_category(prof.name, prof.kind, r) for _k, r, _c in top]
        colors = [t.ink] * len(top)
        if prof.other_count > 0:
            labels.append(tr('OTHER'))
            counts.append(prof.other_count)
            exprs.append(sh.expression_for_other_categories(prof.name, prof.kind, [r for _k, r, _c in top])
                         if prof.kind != pe.OTHER else None)
            colors.append(t.muted)
        y = np.arange(len(labels))[::-1]
        patches = ax.barh(y, counts, height=0.72, color=colors, edgecolor=t.surface, linewidth=0.6)
        approx = '' if prof.top_exact else '≥'
        for i, patch in enumerate(patches):
            is_other = i >= len(top)
            tip = '{}\n{}: {}{}'.format(top[i][0] if not is_other else tr('All other values'), tr('features'),
                                        ('≤' if not prof.top_exact else '') if is_other else approx,
                                        fmt.fmt_count(counts[i]))
            self._bars.append((patch, exprs[i], tip))
        cmax = max(counts) if counts else 1
        for yi, c in zip(y, counts):
            ax.text(c + cmax * 0.01, yi, fmt.fmt_count(c), va='center', ha='left', fontsize=6.5, color=t.muted)

        if self.overlay is not None:
            values, scale, _n = self.overlay
            if values:
                vc = Counter(values)
                keys = [k for k, _r, _c in top]
                sel = [vc.get(k, 0) * scale for k in keys]
                if prof.other_count > 0:
                    sel.append((len(values) - sum(vc.get(k, 0) for k in keys)) * scale)
                ax.barh(y, sel, height=0.72, facecolor=to_rgba(t.overlay, 0.2), edgecolor=t.overlay, hatch='////', linewidth=1.0)

        ax.set_yticks(y)
        ax.set_yticklabels(labels)
        ax.set_xlim(0, cmax * 1.12)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: fmt.fmt_int(v) if v >= 0 else ''))
        ax.tick_params(axis='y', length=0, colors=t.ink)

    # ------------------------------------------------------------------
    def _bar_at(self, event):
        if event.inaxes is not self.ax or not self._bars:
            return None
        for i, (patch, _expr, _tip) in enumerate(self._bars):
            contains, _ = patch.contains(event)
            if contains:
                return i
        # Histogram: also accept clicks above a short bar.
        if self.profile is not None and self.profile.is_continuous and event.xdata is not None:
            edges = self.profile.histogram['edges']
            if edges[0] <= event.xdata <= edges[-1]:
                i = min(bisect.bisect_right(edges, event.xdata) - 1, len(edges) - 2)
                if 0 <= i < len(self._bars):
                    return i
        if self.profile is not None and self.profile.is_categorical and event.ydata is not None:
            n = len(self._bars)
            i = n - 1 - int(round(event.ydata))
            if 0 <= i < n:
                return i
        return None

    def _on_motion(self, event):
        i = self._bar_at(event)
        if i == self._hover:
            return
        t = self.theme
        if self._hover is not None and self._hover < len(self._bars):
            patch = self._bars[self._hover][0]
            patch.set_facecolor(patch._det_color)
        self._hover = i
        if i is not None:
            patch, _expr, tip = self._bars[i]
            if not hasattr(patch, '_det_color'):
                patch._det_color = patch.get_facecolor()
            patch.set_facecolor(t.signal)
            self.canvas.setToolTip(tip)
            self.canvas.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.canvas.setToolTip('')
            self.canvas.setCursor(Qt.CursorShape.ArrowCursor)
        self.canvas.draw_idle()

    def _on_leave(self, _event):
        if self._hover is not None and self._hover < len(self._bars):
            patch = self._bars[self._hover][0]
            if hasattr(patch, '_det_color'):
                patch.set_facecolor(patch._det_color)
            self._hover = None
            self.canvas.draw_idle()

    def _on_press(self, event):
        i = self._bar_at(event)
        if i is None:
            return
        expr = self._bars[i][1]
        if not expr:
            return
        if event.button == 3:
            self._context_menu(expr)
        elif event.button == 1:
            self.select_requested.emit(expr, modifiers_behavior())

    def _context_menu(self, expr):
        menu = QMenu(self)
        menu.setProperty('det', 'menu')
        menu.setStyleSheet(self.theme.stylesheet())
        menu.addAction(tr('Select'), lambda: self.select_requested.emit(expr, sh.SET))
        menu.addAction(tr('Add to selection'), lambda: self.select_requested.emit(expr, sh.ADD))
        menu.addAction(tr('Remove from selection'), lambda: self.select_requested.emit(expr, sh.REMOVE))
        menu.addSeparator()
        menu.addAction(tr('Open in attribute table'), lambda: self.table_requested.emit(expr))
        menu.exec(QCursor.pos())


def _short(text, n):
    text = str(text)
    return text if len(text) <= n else text[:n - 1] + '…'
